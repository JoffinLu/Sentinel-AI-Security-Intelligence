#!/usr/bin/env python
"""Sentinel 导出源码分发包（发给队友 / 开发者的 zip）。

自动剔除「对方必须重建 / 隐私 / 无用重物」的内容，打出一个干净、可直接
分发的源码包（对方解压后先跑「首次安装.bat」，再跑「启动系统.bat」）：

  已排除：.venv、node_modules、__pycache__、.git、.tmp、.workbuddy、
          .env（隐私！）、所有 *.log、发布/、归档/、build/、根目录 *.zip
  默认包含：项目源码 / 前端界面（含已构建 dist）/ 评测 / 运维 /
          数据（模型缓存 96MB，可用 --no-model 去掉）/ 全部启动脚本 /
          README、requirements.txt、.env.example
  可选包含：数据快照（数据/snapshot.sql）—— 对方首次安装时一键导入，
          数据库直接带数据（体育场般空库 → 有数据的区别）

用法：
    export_package.py                   交互式（询问是否带数据快照）
    export_package.py --snapshot        带数据库快照（导出当前数据层）
    export_package.py --no-snapshot     不带快照
    export_package.py --no-model        不带模型缓存（对方首次联网自动下载）
    export_package.py -o 文件名.zip      指定输出路径
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import start_dev as sd

ROOT = sd.ROOT

EXCLUDE_DIRS = {
    ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".git", ".tmp", ".workbuddy", "build", "发布", "归档",
    ".idea", ".vscode", ".ipynb_checkpoints",
}
EXCLUDE_SUFFIXES = {".log", ".pyc", ".pyo", ".bak", ".old", ".orig", ".tmp"}

# 根目录的历史调试残留（探针/自检文件），永远不进包
_ROOT_JUNK_FILES = {".deltest", "probe_root.txt"}


def _is_secret(name: str) -> bool:
    """排除一切 .env* 变体（.env / .env.bak / .env.local…）；仅保留模板 .env.example。"""
    return name.startswith(".env") and name != ".env.example"


def _is_root_hidden_junk(rel: Path) -> bool:
    """根目录隐藏文件除 .env.example / .gitignore 外一律不进包。

    这类文件基本是工具/调试残留（.deltest / .tmp_extract.py / .wbpapp_*.genie），
    对收包人无意义；真需要随包发的配置请用无点前缀命名。
    """
    name = rel.name
    if len(rel.parts) != 1 or not name.startswith("."):
        return False
    return name not in (".env.example", ".gitignore")

ZIP_PREFIX = "ICT-WB"


def collect_files(skip_dirs: set[str], out_path: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    out_resolved = out_path.resolve()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            p = Path(dirpath) / fn
            if _is_secret(fn):
                continue
            if p.suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            rel = p.relative_to(ROOT)
            if _is_root_hidden_junk(rel) or fn in _ROOT_JUNK_FILES:
                continue
            if fn.endswith(".genie"):
                continue
            if len(rel.parts) == 1 and p.suffix.lower() == ".zip":
                continue  # 根目录的 zip（含本脚本自己的输出）
            if p.resolve() == out_resolved:
                continue
            files.append((p, rel.as_posix()))
    files.sort(key=lambda t: t[1])
    return files


def psql_out(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "aiintel-postgres", "psql", "-U", "aiintel",
         "-d", "ai_security_intel", "-tAc", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (r.stdout or "").strip()


def dump_snapshot() -> Path | None:
    if not sd.docker_ok():
        sd.say("Docker 未运行 —— 无法导出数据快照。")
        return None
    if not sd.container_ready("aiintel-postgres"):
        sd.say("PostgreSQL 容器未就绪 —— 无法导出数据快照。")
        return None
    rows = psql_out("SELECT count(*) FROM public.vulnerabilities")
    fd, name = tempfile.mkstemp(prefix="sentinel_snapshot_", suffix=".sql")
    os.close(fd)
    tmp = Path(name)
    sd.say(f"正在导出数据库快照（当前 vulnerabilities = {rows} 行，约 1~3 分钟）…")
    cmd = ["docker", "exec", "-e", "PGCLIENTENCODING=UTF8", "aiintel-postgres",
           "pg_dump", "-U", "aiintel", "-d", "ai_security_intel",
           "--no-owner", "--no-privileges"]
    with open(tmp, "wb") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
    if r.returncode != 0:
        sd.say("pg_dump 失败：" + (r.stderr or b"").decode("utf-8", "replace")[-200:])
        tmp.unlink(missing_ok=True)
        return None
    mb = tmp.stat().st_size / 1048576
    sd.say(f"快照导出完成（{mb:.0f} MB）")
    return tmp


def human(mb: float) -> str:
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def build_zip(
    files: list[tuple[Path, str]],
    snapshot: Path | None,
    out_path: Path,
) -> int:
    entries = list(files)
    used = {rel for _, rel in entries}
    extra: list[tuple[Path, str]] = []
    if snapshot is not None and "数据/snapshot.sql" not in used:
        extra.append((snapshot, "数据/snapshot.sql"))
    total_bytes = sum(p.stat().st_size for p, _ in entries + extra)

    sd.say(f"打包 {len(entries) + len(extra)} 个文件（原始共 {human(total_bytes / 1048576)}）…")
    done = 0
    last = time.time()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, rel in sorted(entries + extra, key=lambda t: t[1]):
            zf.write(p, f"{ZIP_PREFIX}/{rel}")
            done += p.stat().st_size
            if time.time() - last >= 2.5:
                pct = done / total_bytes * 100 if total_bytes else 100
                sd.say(
                    f"  压缩中… {pct:5.1f}%  "
                    f"({done / 1048576:.0f}/{total_bytes / 1048576:.0f} MB)"
                )
                last = time.time()
    return len(entries) + len(extra)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel 导出源码分发包")
    parser.add_argument("--snapshot", action="store_true", help="导出并附带数据快照")
    parser.add_argument("--no-snapshot", action="store_true", help="不附带数据快照")
    parser.add_argument(
        "--no-model", action="store_true", help="不打包模型缓存（对方首次联网下载）"
    )
    parser.add_argument("-o", "--output", default="", help="输出 zip 路径（默认根目录）")
    args = parser.parse_args()

    sd.say(sd.BAR)
    sd.say("  Sentinel 导出源码分发包")
    sd.say(sd.BAR)

    # 输出路径
    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
    else:
        stamp = time.strftime("%Y%m%d")
        out_path = ROOT / f"{ZIP_PREFIX}-源码包-{stamp}.zip"
        n = 2
        while out_path.exists():
            out_path = ROOT / f"{ZIP_PREFIX}-源码包-{stamp}-{n}.zip"
            n += 1

    # 快照决策
    snapshot: Path | None = None
    want_snapshot = args.snapshot
    if not args.snapshot and not args.no_snapshot:
        ans = input(
            "是否导出并附带当前数据库快照（对方首装可一键导入数据）？[Y/n] "
        ).strip().lower()
        want_snapshot = ans in ("", "y", "yes")
    if want_snapshot:
        snapshot = dump_snapshot()

    # 文件清点
    skip_dirs = set(EXCLUDE_DIRS)
    if args.no_model:
        skip_dirs.add("模型缓存")
    files = collect_files(skip_dirs, out_path)
    sd.say("已排除：.venv、node_modules、__pycache__、.git、.tmp、.workbuddy、"
           ".env*（除 .env.example）、*.bak/.old/.orig、日志、发布/、归档/、build/、"
           "根目录 zip、调试残留（隐藏文件/探针）")
    if args.no_model:
        sd.say("按参数排除：数据/模型缓存（对方首次联网时自动下载）")

    # 打包
    count = build_zip(files, snapshot, out_path)

    # 清理临时快照
    if snapshot is not None:
        snapshot.unlink(missing_ok=True)

    size_mb = out_path.stat().st_size / 1048576
    sd.say("\n" + sd.BAR)
    sd.say(f"打包完成：{out_path}")
    sd.say(f"  文件数：{count}（含{'数据快照 + ' if snapshot is not None else ''}源码 / dist / "
           f"{'模型缓存 / ' if not args.no_model else ''}脚本）")
    sd.say(f"  压缩后大小：{human(size_mb)}")
    sd.say("\n对方使用步骤：")
    sd.say("  1) 安装 Python 3.13 与 Docker Desktop（Node.js 可选）")
    sd.say("  2) 解压后双击「首次安装.bat」（建环境、拉容器、可导入快照）")
    sd.say("  3) 之后日常双击「启动系统.bat」")
    sd.say(sd.BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
