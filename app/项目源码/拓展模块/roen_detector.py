"""ROEN 入侵检测器（推理侧）。

流量 DataFrame → 图数据（节点=IP、边=流、边特征=统计量）→ ROEN 推理。
预处理与训练侧（MD1.0/untils.py）同构：每批次重新 fit StandardScaler。
"""

from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import LabelEncoder, StandardScaler
from torch_geometric.data import Data

from .config import (
    CLASS_NAMES,
    EDGE_IN_CHANNELS,
    HIDDEN_EDGE,
    HIDDEN_NODE,
    MLP_HIDDEN,
    MODEL_PATH,
    NODE_IN_CHANNELS,
    NUM_CLASSES,
    THREAT_LEVELS,
)
from .roen_model import ROEN

logger = logging.getLogger(__name__)


class ROENDetector:
    """ROEN 入侵检测器包装类。"""

    def __init__(self, model_path: str | None = None):
        self.logger = logging.getLogger(__name__)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.label_encoder = LabelEncoder()
        self.scaler = None
        self.is_initialized = False
        self.model_path = str(model_path or MODEL_PATH)
        self.init_model()

    def init_model(self) -> None:
        self.model = ROEN(
            node_in_channels=NODE_IN_CHANNELS,
            edge_in_channels=EDGE_IN_CHANNELS,
            hidden_channels_node=HIDDEN_NODE,
            hidden_channels_edge=HIDDEN_EDGE,
            mlp_hidden_channels=MLP_HIDDEN,
            num_edge_classes=NUM_CLASSES,
        ).to(self.device)

        if os.path.exists(self.model_path):
            checkpoint = torch.load(self.model_path, map_location=self.device)
            self.model.load_state_dict(checkpoint)
            self.model.eval()
            self.logger.info("ROEN 模型加载成功: %s", self.model_path)
        else:
            self.logger.warning("模型文件不存在: %s，使用未训练模型", self.model_path)

        self.label_encoder.fit(["BENIGN", "DDoS", "PortScan"])
        self.scaler = None  # 每批次重新 fit（与训练口径一致）
        self.is_initialized = True

    # ---------------- 预处理 ----------------

    @staticmethod
    def create_ip_mapping(traffic_data: pd.DataFrame) -> dict:
        ip_list = pd.concat(
            [traffic_data["Source IP"], traffic_data["Destination IP"]]
        ).unique()
        return {ip: i for i, ip in enumerate(ip_list)}

    def preprocess_traffic_data(self, traffic_data: pd.DataFrame) -> Data:
        data = traffic_data.copy()
        data.columns = data.columns.str.strip()

        ip_to_id = self.create_ip_mapping(data)
        data = data.replace([np.inf, -np.inf], np.nan)
        data = data.ffill().bfill()

        numeric_cols = data.select_dtypes(include=["float64", "int64"]).columns
        feature_cols = [
            col
            for col in numeric_cols
            if col not in ("Flow ID", "Source Port", "Destination Port")
        ]

        if feature_cols:
            self.scaler = StandardScaler()
            data[feature_cols] = self.scaler.fit_transform(data[feature_cols])

        source_ids = data["Source IP"].map(ip_to_id).values
        destination_ids = data["Destination IP"].map(ip_to_id).values
        num_nodes = len(ip_to_id)
        node_features = torch.zeros((num_nodes, 1), dtype=torch.float)

        # 与训练侧一致：端口值写入节点（同 IP 多流时后写覆盖）
        ports = data["Source Port"].tolist() if "Source Port" in data.columns else None
        dports = data["Destination Port"].tolist() if "Destination Port" in data.columns else None
        for i in range(len(source_ids)):
            if ports is not None:
                node_features[source_ids[i]] = float(ports[i])
            if dports is not None:
                node_features[destination_ids[i]] = float(dports[i])

        edge_index = torch.tensor([source_ids, destination_ids], dtype=torch.long)

        edge_features = []
        for col in feature_cols[:EDGE_IN_CHANNELS]:
            if col in data.columns:
                edge_features.append(data[col].values)
        while len(edge_features) < EDGE_IN_CHANNELS:
            edge_features.append(np.zeros(len(data)))
        edge_attr = torch.tensor(np.column_stack(edge_features), dtype=torch.float)

        return Data(x=node_features, edge_index=edge_index, edge_attr=edge_attr)

    # ---------------- 推理 ----------------

    def detect_intrusion(self, traffic_data: pd.DataFrame) -> dict:
        """检测一批流量的入侵类型。"""
        if not self.is_initialized:
            raise RuntimeError("ROEN 检测器未初始化")

        graph_data = self.preprocess_traffic_data(traffic_data).to(self.device)
        with torch.no_grad():
            predictions = self.model(graphs=[graph_data], seq_len=1)
            edge_predictions = predictions[0]
            probabilities = torch.softmax(edge_predictions, dim=1)
            predicted_classes = torch.argmax(probabilities, dim=1)
            confidence_scores = torch.max(probabilities, dim=1)[0]

            unique_classes, counts = torch.unique(predicted_classes, return_counts=True)
            class_distribution = {
                CLASS_NAMES.get(int(cls), f"Unknown_{int(cls)}"): int(count)
                for cls, count in zip(
                    unique_classes.cpu().numpy(), counts.cpu().numpy(), strict=False
                )
            }

            attack_predictions = predicted_classes[predicted_classes > 0]
            if len(attack_predictions) > 0:
                max_threat_class = int(torch.max(predicted_classes).item())
                attack_confidences = confidence_scores[predicted_classes > 0]
                overall_confidence = float(torch.mean(attack_confidences).item())
            else:
                max_threat_class = int(torch.mode(predicted_classes)[0].item())
                overall_confidence = float(torch.mean(confidence_scores).item())

        source_ips = [str(ip) for ip in traffic_data["Source IP"].unique().tolist()]
        target_ips = [str(ip) for ip in traffic_data["Destination IP"].unique().tolist()]

        return {
            "timestamp": pd.Timestamp.now().isoformat(),
            "total_flows": int(len(traffic_data)),
            "source_ips": source_ips,
            "target_ips": target_ips,
            "predicted_class": max_threat_class,
            "attack_type": CLASS_NAMES.get(max_threat_class, "Unknown"),
            "threat_level": THREAT_LEVELS.get(max_threat_class, "Unknown"),
            "confidence_score": round(overall_confidence, 4),
            "class_distribution": class_distribution,
            "is_attack": max_threat_class > 0,
        }

    def info(self) -> dict:
        return {
            "model_path": self.model_path,
            "device": str(self.device),
            "num_classes": NUM_CLASSES,
            "classes": [CLASS_NAMES[i] for i in range(NUM_CLASSES)],
            "edge_in_channels": EDGE_IN_CHANNELS,
            "params": int(sum(p.numel() for p in self.model.parameters())),
        }
