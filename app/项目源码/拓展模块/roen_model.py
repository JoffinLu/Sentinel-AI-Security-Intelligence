"""ROEN 网络架构（Recurrent Feature Fusion Evolution Network）。

双线程 + 全局融合：
  · 隐式线程（IFE）：MLP → GCN×2 → LSTM（节点/拓扑演化）
  · 显式线程（EFE）：MLP → FC×2 → LSTM（边/统计特征循环优化）
  · 全局融合（GFF）：拼接节点对特征与边特征 → MLP 分类

与训练时结构逐字一致（权重按此加载）。
"""

from __future__ import annotations

import torch
import torch.nn as nn
from torch_geometric.nn import GCNConv


class ROEN(nn.Module):
    def __init__(
        self,
        node_in_channels: int,
        edge_in_channels: int,
        hidden_channels_node: int,
        hidden_channels_edge: int,
        mlp_hidden_channels: int,
        num_edge_classes: int,
    ):
        super().__init__()

        # GCN 前的节点/边 MLP
        self.mlp_node_fc1 = nn.Linear(node_in_channels, hidden_channels_node)
        self.mlp_node_fc2 = nn.Linear(hidden_channels_node, hidden_channels_node)

        self.mlp_edge_fc1 = nn.Linear(edge_in_channels, hidden_channels_edge)
        self.mlp_edge_fc2 = nn.Linear(hidden_channels_edge, hidden_channels_edge)

        # GCN（节点特征）
        self.gcn_node_layers1 = GCNConv(hidden_channels_node, hidden_channels_node)
        self.gcn_node_layers2 = GCNConv(hidden_channels_node, hidden_channels_node)

        # 边特征全连接
        self.edge_fc1 = nn.Linear(hidden_channels_edge, hidden_channels_edge)
        self.edge_fc2 = nn.Linear(hidden_channels_edge, hidden_channels_edge)

        # LSTM（时序）
        self.lstm_node = nn.LSTM(
            input_size=hidden_channels_node, hidden_size=hidden_channels_node, batch_first=True
        )
        self.lstm_edge = nn.LSTM(
            input_size=hidden_channels_edge, hidden_size=hidden_channels_edge, batch_first=True
        )

        # 边分类 MLP（节点对特征 2×node + 边特征 edge）
        self.mlp_classifier_fc1 = nn.Linear(
            2 * hidden_channels_node + hidden_channels_edge, mlp_hidden_channels
        )
        self.mlp_classifier_fc2 = nn.Linear(mlp_hidden_channels, num_edge_classes)

        self.lstm_hidden_node = hidden_channels_node
        self.lstm_hidden_edge = hidden_channels_edge

    def forward(self, graphs: list, seq_len: int) -> list:
        batch_edge_predictions = []

        # LSTM 隐状态初始化
        h_0_node = torch.zeros(1, seq_len, self.lstm_hidden_node).to(graphs[0].x.device)
        c_0_node = torch.zeros(1, seq_len, self.lstm_hidden_node).to(graphs[0].x.device)
        h_0_edge = torch.zeros(1, seq_len, self.lstm_hidden_edge).to(graphs[0].x.device)
        c_0_edge = torch.zeros(1, seq_len, self.lstm_hidden_edge).to(graphs[0].x.device)

        node_features_seq = []
        edge_features_seq = []

        for t in range(seq_len):
            x_t = graphs[t].x
            edge_index_t = graphs[t].edge_index
            edge_attr_t = graphs[t].edge_attr

            # MLP 提特征
            x_t = torch.relu(self.mlp_node_fc1(x_t))
            x_t = torch.relu(self.mlp_node_fc2(x_t))
            edge_attr_t = torch.relu(self.mlp_edge_fc1(edge_attr_t))
            edge_attr_t = torch.relu(self.mlp_edge_fc2(edge_attr_t))

            # GCN 传播拓扑信息
            x_t = torch.relu(self.gcn_node_layers1(x_t, edge_index_t))
            x_t = torch.relu(self.gcn_node_layers2(x_t, edge_index_t))

            # 边特征
            edge_attr_t = torch.relu(self.edge_fc1(edge_attr_t))
            edge_attr_t = torch.relu(self.edge_fc2(edge_attr_t))

            node_features_seq.append(x_t)
            edge_features_seq.append(edge_attr_t)

        # LSTM 时序融合
        node_features_seq = torch.stack(node_features_seq, dim=0)
        edge_features_seq = torch.stack(edge_features_seq, dim=0)

        lstm_out_node, _ = self.lstm_node(node_features_seq, (h_0_node, c_0_node))
        lstm_out_edge, _ = self.lstm_edge(edge_features_seq, (h_0_edge, c_0_edge))

        # 逐时间步做边分类
        for t in range(seq_len):
            edge_index_t = graphs[t].edge_index
            edge_src = edge_index_t[0]
            edge_dst = edge_index_t[1]
            node_pair_features = torch.cat(
                [lstm_out_node[t][edge_src], lstm_out_node[t][edge_dst]], dim=1
            )
            edge_features = torch.cat([node_pair_features, lstm_out_edge[t]], dim=1)

            edge_preds = torch.relu(self.mlp_classifier_fc1(edge_features))
            edge_preds = self.mlp_classifier_fc2(edge_preds)
            batch_edge_predictions.append(edge_preds)

        return batch_edge_predictions
