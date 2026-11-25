"""网络流量模拟器 —— 无数据集时自造 CICIDS2017 风格的流量样本。

与 MD1.0 的 mock 数据同构（列名、特征数量一致，保证检测器预处理
无需任何改动），但改为**可复现的种子随机**，方便测试与演示。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_ATTACK_LABELS = ("DDoS", "PortScan")
_DEST_PORTS = (80, 443, 22, 21, 25, 53)


class TrafficSimulator:
    """模拟流量数据生成器（内存态，无外部依赖）。"""

    def __init__(self, n_samples: int = 1000, seed: int = 20251014):
        self.logger = logging.getLogger(__name__)
        self.rng = np.random.default_rng(seed)
        self.data = self._create_mock_data(n_samples)
        self.normal_traffic = self.data[self.data["Label"] == "BENIGN"].copy()
        self.attack_traffic = self.data[self.data["Label"] != "BENIGN"].copy()
        self.logger.info(
            "模拟流量数据就绪：%d 条（正常 %d / 攻击 %d）",
            len(self.data), len(self.normal_traffic), len(self.attack_traffic),
        )

    def _create_mock_data(self, n_samples: int) -> pd.DataFrame:
        rng = self.rng
        source_ips = [f"192.168.1.{i}" for i in range(1, 51)]
        target_ips = [f"10.0.0.{i}" for i in range(1, 21)]

        # 每行的标签先抽好（70% BENIGN），再按标签抽特征分布
        labels = np.where(rng.random(n_samples) < 0.7, "BENIGN", "")
        rest = np.where(rng.random(n_samples) < 0.5, "DDoS", "PortScan")
        labels = np.where(labels == "", rest, labels)

        is_attack = labels != "BENIGN"
        is_ddos = labels == "DDoS"

        def _normal_for(mean, std, lo=0.0):
            v = rng.normal(mean, std)
            return np.maximum(lo, v)

        records = {
            "Flow ID": [f"flow_{i}" for i in range(n_samples)],
            "Source IP": rng.choice(source_ips, n_samples),
            "Source Port": rng.integers(1024, 65535, n_samples),
            "Destination IP": rng.choice(target_ips, n_samples),
            "Destination Port": rng.choice(_DEST_PORTS, n_samples),
            "Protocol": rng.choice([6, 17], n_samples),
            "Flow Duration": np.where(
                is_attack,
                np.where(is_ddos, _normal_for(100, 50, 1), _normal_for(50, 20, 1)),
                _normal_for(5000, 1000, 1),
            ),
            "Total Fwd Packets": np.where(
                is_attack,
                np.where(is_ddos, rng.poisson(100, n_samples), rng.poisson(5, n_samples)),
                rng.poisson(10, n_samples),
            ),
            "Total Backward Packets": np.where(
                is_attack, rng.poisson(2, n_samples), rng.poisson(8, n_samples)
            ),
            "Total Length of Fwd Packets": None,  # 占位，下面填
            "Total Length of Bwd Packets": None,
            "Fwd Packet Length Max": _normal_for(1500, 300),
            "Fwd Packet Length Min": _normal_for(64, 20),
            "Fwd Packet Length Mean": _normal_for(500, 200),
            "Fwd Packet Length Std": _normal_for(200, 100),
            "Bwd Packet Length Max": _normal_for(1500, 300),
            "Bwd Packet Length Min": _normal_for(64, 20),
            "Bwd Packet Length Mean": _normal_for(300, 150),
            "Bwd Packet Length Std": _normal_for(150, 75),
            "Flow Bytes/s": _normal_for(10000, 5000),
            "Flow Packets/s": _normal_for(10, 5),
            "Flow IAT Mean": _normal_for(1000, 500),
            "Flow IAT Std": _normal_for(500, 250),
            "Flow IAT Max": _normal_for(5000, 2000),
            "Flow IAT Min": _normal_for(1, 0.5),
            "Fwd IAT Total": _normal_for(5000, 2000),
            "Fwd IAT Mean": _normal_for(1000, 500),
            "Fwd IAT Std": _normal_for(500, 250),
            "Fwd IAT Max": _normal_for(5000, 2000),
            "Fwd IAT Min": _normal_for(1, 0.5),
            "Bwd IAT Total": _normal_for(3000, 1500),
            "Bwd IAT Mean": _normal_for(800, 400),
            "Bwd IAT Std": _normal_for(400, 250),
            "Bwd IAT Max": _normal_for(5000, 2000),
            "Bwd IAT Min": _normal_for(1, 0.5),
            "Label": labels,
        }
        data = pd.DataFrame(records)

        # 长度类特征与前向/后向包数挂钩（保持量级合理）
        fwd = data["Total Fwd Packets"].to_numpy()
        bwd = data["Total Backward Packets"].to_numpy()
        data["Total Length of Fwd Packets"] = np.maximum(
            0, fwd * rng.normal(500, 200, n_samples)
        )
        data["Total Length of Bwd Packets"] = np.maximum(
            0, bwd * rng.normal(300, 150, n_samples)
        )

        # 补足 80 个边特征通道
        for j in range(50):
            data[f"Feature_{j}"] = rng.normal(0, 1, n_samples)
        return data

    def generate_batch(self, batch_size: int, attack_probability: float) -> pd.DataFrame:
        """按攻击注入比例生成一批流量。"""
        batch_size = max(1, int(batch_size))
        include_attack = self.rng.random() < attack_probability and len(self.attack_traffic) > 0

        if include_attack:
            attack_count = min(
                int(self.rng.integers(1, max(2, batch_size // 2 + 1))),
                len(self.attack_traffic),
            )
            normal_count = min(batch_size - attack_count, len(self.normal_traffic))
            attack_sample = self.attack_traffic.sample(
                n=attack_count, random_state=int(self.rng.integers(0, 2**31 - 1))
            )
            normal_sample = self.normal_traffic.sample(
                n=normal_count, random_state=int(self.rng.integers(0, 2**31 - 1))
            )
            batch = pd.concat([attack_sample, normal_sample], ignore_index=True)
        else:
            n = min(batch_size, len(self.normal_traffic))
            batch = self.normal_traffic.sample(
                n=n, random_state=int(self.rng.integers(0, 2**31 - 1))
            )
        batch = batch.copy()
        batch["Timestamp"] = pd.Timestamp.now()
        return batch

    def status(self) -> dict:
        return {
            "total_records": int(len(self.data)),
            "normal_records": int(len(self.normal_traffic)),
            "attack_records": int(len(self.attack_traffic)),
        }
