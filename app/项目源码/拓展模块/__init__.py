"""拓展模块 —— 网络入侵检测（ROEN）演示。

移植自已归档的 MD1.0 / ROEN 科研项目（论文：Ren et al., "ROEN: Universal
dynamic topology-adaptive evolution model for multi-modal mixed network
traffic detection", Computer Networks, 2025），保留三块硬核能力：

  · roen_model        —— ROEN 双线程网络（显式统计 + 隐式动态拓扑）
  · roen_detector     —— 流量图构建与推理（与训练侧预处理同构）
  · traffic_simulator —— 模拟流量生成（无数据集时自造 CICIDS2017 风格样本）
  · chaos_encryption  —— SHA-512 + Logistic/Tent 混沌映射图像加密

原项目的 Flask / MySQL 外壳没有移植（数据库层在原项目里从未接通），
改为内存态控制器 + FastAPI 路由，与 Sentinel 主系统共进程运行。
"""
