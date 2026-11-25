# 发布目录说明

本目录存放 **exe 打包工程** 与组装产物。

## ⬇️ 打包好的系统在哪里下载？

**不在本仓库**——exe 发布包（约 400MB）超过 GitHub 单文件 100MB 的仓库上限，
因此通过 **GitHub Releases** 分发：

→ **https://github.com/JoffinLu/ICT-WB/releases**

| 资产 | 解压后文件夹 | 说明 |
|---|---|---|
| `Sentinel-Full-v1.1.zip` | `Sentinel/` | **推荐**，附带 12,000+ 条演示数据，首次启动自动导入 |
| `Sentinel-Lite-v1.1.zip` | `Sentinel/` | 空库启动，用于测试从零采集 |

解压后双击 `Sentinel.exe` 即可（**免安装**：数据库引擎已内嵌，无需 Docker / Python /
数据库；详见压缩包内 `使用说明.txt` 或 [仓库 README](../README.md)）。

> v1.0 旧版本资产仍保留在 Releases 页（对应本地 `Sentinel-系统-*-v1.0.zip`）。

## 本目录各文件用途

| 文件/目录 | 用途 |
|---|---|
| `组装发布包.sh` | 从 PyInstaller 产物组装出两份发布包（完整版/纯净版）并打 zip、算 SHA256 |
| `_build/` | PyInstaller 输出目录（`Sentinel.exe + _internal`），组装脚本的输入 |
| `_emb/` | 内嵌 PostgreSQL 组件（bin/lib/share）+ 数据快照 `snapshot.sql` |
| `Sentinel-系统-完整版-v1.1/` | 组装好的完整版（含 `data/snapshot.sql`），发布包解压前的原样 |
| `Sentinel-系统-纯净版-v1.1/` | 组装好的纯净版（不含快照） |
| `使用说明-内嵌版.txt` | 随包分发的用户说明（组装时复制为包内 `使用说明.txt`） |
| `release-notes.md` | GitHub Release 页面的说明文档（含 SHA256 校验值） |
| `upload_release.py` | 将发布包上传到 GitHub Releases 的工具（`TAG` 控制目标版本） |
| `logo.ico` | 程序图标（PyInstaller spec 引用） |

## 自行打包（改代码后重新出包）

```bash
# ① 构建 exe（约 10~15 分钟）
./.venv/Scripts/pyinstaller.exe Sentinel.spec --noconfirm \
    --distpath 发布/_build --workpath build/pyinstaller

# ② 刷新数据快照（可选；完整版首启导入的就是它）
docker exec -e PGCLIENTENCODING=UTF8 aiintel-postgres \
    pg_dump -U aiintel -d ai_security_intel --no-owner --no-privileges > 发布/_emb/snapshot.sql

# ③ 组装两份发布包 + zip + 校验值
bash 发布/组装发布包.sh v1.1
```
