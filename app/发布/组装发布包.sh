#!/usr/bin/env bash
# 发布包组装（内嵌数据库免安装版）
# ============================================================
# 前提：
#   ① PyInstaller 已完成：发布/_build/Sentinel/Sentinel.exe 存在
#      （构建命令见 Sentinel.spec 顶部注释）
#   ② 数据快照已导出：发布/_emb/snapshot.sql（完整版首启自动导入）
#
# 产物：
#   发布/Sentinel-系统-完整版-<版本>/Sentinel/   ← 含 data/snapshot.sql
#   发布/Sentinel-系统-纯净版-<版本>/Sentinel/   ← 空库启动
#   以及对应的两个 .zip（zip 内顶层为 Sentinel/ 文件夹）
#
# 用法：bash 发布/组装发布包.sh [版本号，默认 v1.1]
set -e
cd "$(dirname "$0")/.."

VERSION="${1:-v1.1}"
BUILD="发布/_build/Sentinel"
SNAP="发布/_emb/snapshot.sql"
FULL="发布/Sentinel-系统-完整版-${VERSION}"
PURE="发布/Sentinel-系统-纯净版-${VERSION}"
ZIPF="发布/Sentinel-系统-完整版-${VERSION}.zip"
ZIPP="发布/Sentinel-系统-纯净版-${VERSION}.zip"
PY="./.venv/Scripts/python.exe"

[ -f "$BUILD/Sentinel.exe" ] || { echo "[!] 未找到打包产物 $BUILD（先跑 PyInstaller）"; exit 1; }
[ -f "$SNAP" ] || { echo "[!] 未找到数据快照 $SNAP"; exit 1; }

echo "[1/4] 清理旧的组装目录与 zip…"
rm -rf "$FULL" "$PURE" "$ZIPF" "$ZIPP"

echo "[2/4] 组装完整版（含快照）…"
mkdir -p "$FULL/Sentinel/data"
cp -r "$BUILD/." "$FULL/Sentinel/"
cp "发布/使用说明-内嵌版.txt" "$FULL/Sentinel/使用说明.txt"
cp "$SNAP" "$FULL/Sentinel/data/snapshot.sql"

echo "[3/4] 组装纯净版（不含快照）…"
cp -r "$FULL" "$PURE"
rm -f "$PURE/Sentinel/data/snapshot.sql"
rmdir "$PURE/Sentinel/data"

echo "[4/4] 打 zip 并计算校验值…"
"$PY" - "$FULL" "$ZIPF" "$PURE" "$ZIPP" <<'PYEOF'
import hashlib
import pathlib
import shutil
import sys

full, zipf, pure, zipp = sys.argv[1:5]
for src, out in ((full, zipf), (pure, zipp)):
    out_path = pathlib.Path(out)
    # root_dir=外层（Sentinel-系统-*-v1.1）目录本身，base_dir=Sentinel
    # —— 这样 zip 内顶层就是 Sentinel/ 文件夹
    shutil.make_archive(str(out_path.with_suffix("")), "zip",
                        root_dir=str(src), base_dir="Sentinel")
    size_mb = out_path.stat().st_size / 1024 / 1024
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"  {out_path.name}  {size_mb:.0f} MB  sha256={digest}")
PYEOF

echo ""
echo "完成。发布包位于 发布/ 目录（可上传 GitHub Releases）。"
