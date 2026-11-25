"""拓展模块配置。"""

from __future__ import annotations

from pathlib import Path

from ..运行时 import resource

# 模型权重：优先 数据/模型缓存/roen/（随仓库与发布包分发）；
# 其次 MD1.0/models-new/2017/（原项目位置，开发期兼容）。
_MODEL_CANDIDATES = [
    resource("数据", "模型缓存", "roen", "model_epoch_150.pth"),
    resource("MD1.0", "models-new", "2017", "model_epoch_150.pth"),
]
MODEL_PATH: Path = next((p for p in _MODEL_CANDIDATES if p.exists()), _MODEL_CANDIDATES[0])

# 演示图像目录（加密/解密演示的素材）
IMAGE_DIR: Path = resource("数据", "拓展资源")

# ---- 模型结构超参（与训练时一致，改错会加载失败）----
NODE_IN_CHANNELS = 1
EDGE_IN_CHANNELS = 80
HIDDEN_NODE = 128
HIDDEN_EDGE = 128
MLP_HIDDEN = 128
NUM_CLASSES = 3

# 类别与威胁等级（与原项目口径一致）
CLASS_NAMES = {0: "BENIGN", 1: "DDoS", 2: "PortScan"}
THREAT_LEVELS = {0: "NORMAL", 1: "SUSPICIOUS", 2: "MALICIOUS"}

# 演练解锁口令（原项目同款；仅演示用）
ADMIN_KEY = "admin123"

# 历史记录上限（内存 deque）
HISTORY_MAX = 200

# 论文引用（展示用）
PAPER = {
    "title": "ROEN: Universal dynamic topology-adaptive evolution model "
             "for multi-modal mixed network traffic detection",
    "venue": "Computer Networks (Elsevier), 2025",
    "authors": "Ren, Linghao; Wang, Sijia; Zhong, Shengwei; Li, Yiyuan; Tang, Bo",
}
