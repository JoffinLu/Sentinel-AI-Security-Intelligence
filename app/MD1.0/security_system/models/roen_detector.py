# ROEN检测器包装类
import torch
import torch.nn as nn
import pandas as pd
import numpy as np
from torch_geometric.data import Data
from sklearn.preprocessing import LabelEncoder, StandardScaler
import logging
import sys
import os

# 添加父目录到路径以导入ROEN模型
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from network import ROEN
except ImportError:
    # 如果无法导入ROEN，创建一个模拟类
    class ROEN(nn.Module):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.dummy = nn.Linear(1, 3)
        def forward(self, graphs, seq_len):
            return [torch.randn(10, 3)]

from config import Config

class ROENDetector:
    """ROEN入侵检测器包装类"""
    
    def __init__(self, model_path: str = None):
        self.logger = logging.getLogger(__name__)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.label_encoder = LabelEncoder()
        self.scaler = StandardScaler()
        self.is_initialized = False
        
        # 使用配置中的模型路径
        if model_path is None:
            model_path = Config.ROEN_MODEL_PATH
        
        self.model_path = model_path
        self.init_model()
    
    def init_model(self):
        """初始化ROEN模型"""
        try:
            # 创建模型实例
            self.model = ROEN(
                node_in_channels=Config.ROEN_NODE_CHANNELS,
                edge_in_channels=Config.ROEN_EDGE_CHANNELS,
                hidden_channels_node=Config.ROEN_HIDDEN_CHANNELS_NODE,
                hidden_channels_edge=Config.ROEN_HIDDEN_CHANNELS_EDGE,
                mlp_hidden_channels=Config.ROEN_MLP_HIDDEN_CHANNELS,
                num_edge_classes=Config.ROEN_NUM_CLASSES
            ).to(self.device)
            
            # 加载预训练权重
            if os.path.exists(self.model_path):
                checkpoint = torch.load(self.model_path, map_location=self.device)
                self.model.load_state_dict(checkpoint)
                self.model.eval()
                self.logger.info(f"ROEN模型加载成功: {self.model_path}")
            else:
                self.logger.warning(f"模型文件不存在: {self.model_path}, 使用未训练模型")
            
            # 初始化标签编码器（基于训练时的标签）
            self.label_encoder.fit(['BENIGN', 'DDoS', 'PortScan'])
            self.is_initialized = True
            
        except Exception as e:
            self.logger.error(f"ROEN模型初始化失败: {e}")
            raise
    
    def create_ip_mapping(self, traffic_data: pd.DataFrame) -> dict:
        """创建IP到节点ID的映射"""
        # 确保列名标准化
        data = traffic_data.copy()
        data.columns = data.columns.str.strip()

        ip_list = pd.concat([
            data['Source IP'],
            data['Destination IP']
        ]).unique()
        return {ip: i for i, ip in enumerate(ip_list)}
    
    def preprocess_traffic_data(self, traffic_data: pd.DataFrame) -> Data:
        """预处理网络流量数据为图数据格式"""
        try:
            # 标准化列名（去除前导空格）
            traffic_data = traffic_data.copy()
            traffic_data.columns = traffic_data.columns.str.strip()

            # 创建IP映射
            ip_to_id = self.create_ip_mapping(traffic_data)
            
            # 处理缺失值和无穷值
            traffic_data = traffic_data.replace([np.inf, -np.inf], np.nan)
            traffic_data = traffic_data.ffill().bfill()
            
            # 选择数值列作为特征
            numeric_cols = traffic_data.select_dtypes(include=['float64', 'int64']).columns
            feature_cols = [col for col in numeric_cols if col not in ['Flow ID', 'Source Port', 'Destination Port']]
            
            # 标准化特征
            if len(feature_cols) > 0:
                traffic_data[feature_cols] = self.scaler.fit_transform(traffic_data[feature_cols])
            
            # 构建节点特征
            source_ids = traffic_data['Source IP'].map(ip_to_id).values
            destination_ids = traffic_data['Destination IP'].map(ip_to_id).values
            
            num_nodes = len(ip_to_id)
            node_features = torch.zeros((num_nodes, 1), dtype=torch.float)
            
            # 使用端口作为节点特征
            for i, (src_id, dst_id) in enumerate(zip(source_ids, destination_ids)):
                if 'Source Port' in traffic_data.columns:
                    node_features[src_id] = traffic_data.iloc[i]['Source Port']
                if 'Destination Port' in traffic_data.columns:
                    node_features[dst_id] = traffic_data.iloc[i]['Destination Port']
            
            # 构建边索引
            edge_index = torch.tensor([source_ids, destination_ids], dtype=torch.long)
            
            # 构建边特征
            edge_features = []
            for col in feature_cols[:Config.ROEN_EDGE_CHANNELS]:  # 限制特征数量
                if col in traffic_data.columns:
                    edge_features.append(traffic_data[col].values)
            
            # 如果特征不足，用零填充
            while len(edge_features) < Config.ROEN_EDGE_CHANNELS:
                edge_features.append(np.zeros(len(traffic_data)))
            
            edge_attr = torch.tensor(np.column_stack(edge_features), dtype=torch.float)
            
            # 创建图数据
            graph_data = Data(
                x=node_features,
                edge_index=edge_index,
                edge_attr=edge_attr
            )
            
            return graph_data
            
        except Exception as e:
            self.logger.error(f"数据预处理失败: {e}")
            raise
    
    def detect_intrusion(self, traffic_data: pd.DataFrame) -> dict:
        """
        检测网络入侵
        返回检测结果字典
        """
        if not self.is_initialized:
            raise RuntimeError("ROEN检测器未初始化")
        
        try:
            # 预处理数据
            graph_data = self.preprocess_traffic_data(traffic_data)
            graph_data = graph_data.to(self.device)
            
            # 模型推理
            with torch.no_grad():
                predictions = self.model(graphs=[graph_data], seq_len=1)
                edge_predictions = predictions[0]
                
                # 获取预测结果
                probabilities = torch.softmax(edge_predictions, dim=1)
                predicted_classes = torch.argmax(probabilities, dim=1)
                confidence_scores = torch.max(probabilities, dim=1)[0]
                
                # 统计结果
                unique_classes, counts = torch.unique(predicted_classes, return_counts=True)
                class_distribution = {}
                
                for cls, count in zip(unique_classes.cpu().numpy(), counts.cpu().numpy()):
                    class_name = Config.ATTACK_TYPES.get(cls, f"Unknown_{cls}")
                    class_distribution[class_name] = int(count)

                # 计算整体威胁等级（优先检测攻击）
                # 如果有任何攻击预测，则认为存在威胁
                attack_predictions = predicted_classes[predicted_classes > 0]

                # 调试信息
                self.logger.info(f"预测分布: {class_distribution}")
                self.logger.info(f"原始预测类别: {predicted_classes.cpu().numpy()}")
                self.logger.info(f"攻击预测数量: {len(attack_predictions)}")

                if len(attack_predictions) > 0:
                    # 有攻击预测，取最高威胁等级
                    max_threat_class = torch.max(predicted_classes).item()
                    # 计算攻击预测的平均置信度
                    attack_confidences = confidence_scores[predicted_classes > 0]
                    overall_confidence = torch.mean(attack_confidences).item()
                    self.logger.info(f"检测到攻击！最高威胁等级: {max_threat_class}")
                else:
                    # 没有攻击预测，使用众数
                    max_threat_class = torch.mode(predicted_classes)[0].item()
                    overall_confidence = torch.mean(confidence_scores).item()
                    self.logger.info(f"未检测到攻击，威胁等级: {max_threat_class}")
                
                # 提取源IP和目标IP信息，确保转换为Python字符串
                source_ips = [str(ip) for ip in traffic_data['Source IP'].unique().tolist()]
                target_ips = [str(ip) for ip in traffic_data['Destination IP'].unique().tolist()]
                
                # 构建检测结果
                detection_result = {
                    'timestamp': pd.Timestamp.now(),
                    'total_flows': len(traffic_data),
                    'source_ips': source_ips,
                    'target_ips': target_ips,
                    'predicted_class': max_threat_class,
                    'attack_type': Config.ATTACK_TYPES.get(max_threat_class, 'Unknown'),
                    'threat_level': Config.THREAT_LEVELS.get(max_threat_class, 'Unknown'),
                    'confidence_score': overall_confidence,
                    'class_distribution': class_distribution,
                    'is_attack': max_threat_class > 0,  # 0为正常流量
                    'raw_predictions': predicted_classes.cpu().numpy().tolist(),
                    'raw_confidences': confidence_scores.cpu().numpy().tolist()
                }
                
                self.logger.info(f"检测完成: {detection_result['attack_type']} (置信度: {overall_confidence:.3f})")
                return detection_result
                
        except Exception as e:
            self.logger.error(f"入侵检测失败: {e}")
            raise
    
    def simulate_traffic_detection(self) -> dict:
        """
        模拟网络流量检测（用于演示）
        生成模拟的检测结果
        """
        try:
            # 模拟检测结果
            import random
            
            attack_types = list(Config.ATTACK_TYPES.values())
            threat_levels = list(Config.THREAT_LEVELS.values())
            
            # 随机生成检测结果（增加攻击概率以便演示）
            # 30% 正常流量，35% DDoS攻击，35% 入侵攻击
            rand = random.random()
            if rand < 0.3:
                predicted_class = 0  # 正常流量
            elif rand < 0.65:
                predicted_class = 1  # DDoS攻击
            else:
                predicted_class = 2  # 入侵攻击

            attack_type = Config.ATTACK_TYPES[predicted_class]
            threat_level = Config.THREAT_LEVELS[predicted_class]
            confidence = random.uniform(0.7, 0.95)
            
            # 模拟IP地址
            source_ips = [f"192.168.1.{random.randint(1, 254)}" for _ in range(random.randint(1, 5))]
            target_ips = [f"10.0.0.{random.randint(1, 254)}" for _ in range(random.randint(1, 3))]
            
            detection_result = {
                'timestamp': pd.Timestamp.now(),
                'total_flows': random.randint(10, 100),
                'source_ips': source_ips,
                'target_ips': target_ips,
                'predicted_class': predicted_class,
                'attack_type': attack_type,
                'threat_level': threat_level,
                'confidence_score': confidence,
                'class_distribution': {
                    attack_type: random.randint(5, 20)
                },
                'is_attack': predicted_class > 0,
                'simulation': True
            }
            
            self.logger.info(f"模拟检测: {attack_type} (置信度: {confidence:.3f})")
            return detection_result
            
        except Exception as e:
            self.logger.error(f"模拟检测失败: {e}")
            raise
