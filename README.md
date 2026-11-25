# SENTINEL · AI 安全知识情报系统

智能体驱动的 AI 安全知识情报平台：自动采集 9 类数据源 → 三级过滤筛出 AI 安全漏洞 →
7 维富化 → 知识图谱支撑可溯源的多轮问答与多跳推理。

本仓库 = **完整源码 + 便携运行壳**。根目录是 Windows 启动器，`app/` 是完整源码工作区。

## 使用方式

| 场景 | 怎么做 |
|---|---|
| 直接运行（演示 / 评审） | 拿到完整发布包后双击 `Sentinel.exe`（需保留 `runtime/ pgsql/ neo4j/ java/ data/`，这些体积过大不入库） |
| 源码开发 | 进入 `app/`，按 [app/README.md](app/README.md) 安装依赖并启动 |
| 修改前端 | `cd app/前端界面 && npm install && npm run dev` |
| 跑测试 | `cd app && ./.venv/Scripts/python.exe -m pytest 评测/tests/ -q`（107 项） |
| 重新打包 exe | `cd app && pyinstaller Sentinel.spec --noconfirm` |

## 目录结构

```
Sentinel/
├── Sentinel.exe / 停止系统.exe     Windows 启动器（C#，源码见 portable/Launcher.cs）
├── portable_bootstrap.py           便携启动主逻辑：私有数据库 → 快照恢复 → 服务 → 窗口
├── portable/                       启动器源码、图标、构建说明
├── app/                            ★ 完整源码工作区（等同原项目根）
│   ├── 项目源码/                     FastAPI 后端（采集 / 富化 / 问答 / 智能体编排）
│   ├── 前端界面/                     React 18 + TS + Vite + AntD 5 + ECharts
│   │   ├── src/                      前端源码
│   │   └── dist/                     构建产物（后端直接托管）
│   ├── 评测/                         pytest 测试 · 性能压测 · 评测集
│   ├── 运维/                         Docker Compose · Dockerfile · Prometheus/Grafana
│   ├── 归档/                         技术报告 · 答辩 PPT · 截图
│   ├── MD1.0/                        ROEN 网络入侵检测研究子项目
│   ├── 数据/                         模型缓存（不入库）与拓展资源
│   ├── Sentinel.spec                 PyInstaller 打包配置
│   ├── 启动系统.bat / 启动系统.sh      源码模式一键启动
│   ├── 首次安装.bat                   首次安装依赖与数据层容器
│   ├── export_package.py             导出精简源码分发包
│   └── README.md                     ★ 开发文档：安装、启动、测试、架构
└── runtime/ pgsql/ neo4j/ java/ data/ 内嵌运行时与数据（.gitignore 排除，随发布包分发）
```

## 不入库的内容

`.gitignore` 已排除：`runtime/ pgsql/ neo4j/ java/ data/`（约 1.4GB 运行时）、
`Sentinel.exe`、`app/数据/模型缓存/`（91MB 嵌入模型）、`app/.env`（含密钥）、
日志与 `__pycache__`。克隆仓库后按 `app/README.md` 走源码模式即可。

## 技术栈

后端 FastAPI + SQLAlchemy + PostgreSQL(pgvector) + Neo4j + LangGraph；
前端 React 18 + TypeScript + Vite + Ant Design 5 + ECharts；
打包 PyInstaller + 内嵌 PostgreSQL/Neo4j/Java + C# 启动器。
