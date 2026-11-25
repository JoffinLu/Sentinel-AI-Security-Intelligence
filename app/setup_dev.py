#!/usr/bin/env python
"""Sentinel 首次安装（新机器初始化，源码包专用）。

在一台新机器上装好 Python 3.13 / Docker Desktop（Node.js 可选）之后，
双击「首次安装.bat」运行本脚本，会自动完成：

  [1/6] 生成配置文件 .env（从 .env.example 复制）
  [2/6] 创建虚拟环境 .venv
  [3/6] 安装 Python 依赖（pip install -r requirements.txt）
  [4/6] 安装前端依赖（检测到 Node 且 node_modules 缺失时 npm install）
  [5/6] 启动 / 创建数据层容器（PostgreSQL / Redis / Neo4j）
  [6/6] 可选：导入数据快照（数据/snapshot.sql，如作者随包提供）

全部完成后，日常启动只需双击「启动系统.bat」。

用法：
    setup_dev.py                 交互式（导入快照前会询问）
    setup_dev.py --yes           全部自动（检测到快照即导入）
    setup_dev.py --check         只检测环境，不安装 / 不修改任何东西
    setup_dev.py --recreate      强制重建虚拟环境（删除 .venv 后重装）
    setup_dev.py --no-snapshot   不导入数据快照
    setup_dev.py --pip-index U   自定义 pip 源（默认清华镜像，失败自动换官方源）
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import start_dev as sd

ROOT = sd.ROOT
PY = sd.PY
REQ = ROOT / "requirements.txt"
SNAPSHOT = sd.DATA_DIR / "snapshot.sql"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_OFFICIAL = "https://pypi.org/simple"
NPM_MIRROR = "https://registry.npmmirror.com"


def run_stream(cmd: list, cwd: Path | None = None) -> int:
    """透传输出的执行（pip / npm 等长任务，让用户看到进度）。"""
    pretty = " ".join(str(c) for c in cmd[:3])
    sd.say(f"    {pretty} …")
    try:
        return subprocess.run(
            [str(c) for c in cmd], cwd=str(cwd) if cwd else None
        ).returncode
    except FileNotFoundError:
        return 127


# ------------------------------------------------------------------ 各步骤 ----

def step_env_file() -> None:
    if ENV_FILE.exists():
        sd.say(".env 已存在（跳过）")
        return
    if ENV_EXAMPLE.is_file():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        sd.say("已从 .env.example 生成 .env（可在应用内「LLM 模型设置」中调整模型）")
    else:
        sd.say("未找到 .env 与 .env.example —— 后端将使用内置默认配置")


def step_venv(recreate: bool) -> bool:
    if not recreate and sd.venv_usable():
        sd.say(".venv 已可用（跳过）")
        return True
    venv_dir = ROOT / ".venv"
    if venv_dir.exists():
        sd.say("删除不可用的 .venv（来自原开发机，本机无法使用）…")
        shutil.rmtree(venv_dir, ignore_errors=True)
    sd.say(f"创建虚拟环境（Python {sys.version.split()[0]}）…")
    r = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)])
    if r.returncode != 0 or not sd.venv_usable():
        sd.say("虚拟环境创建失败。")
        return False
    sd.say("虚拟环境已创建")
    return True


def step_pip(index_url: str) -> bool:
    if not REQ.is_file():
        sd.say(f"未找到 {REQ.name}，无法安装依赖")
        return False
    sd.say("安装 Python 依赖（含深度学习组件，首次约 5~20 分钟）…")
    base = [str(PY), "-m", "pip", "install", "-r", str(REQ), "--disable-pip-version-check"]
    rc = run_stream([*base, "-i", index_url])
    if rc != 0 and index_url != PIP_OFFICIAL:
        sd.say("镜像源失败，改用官方源重试…")
        rc = run_stream([*base, "-i", PIP_OFFICIAL])
    if rc != 0:
        sd.say("依赖安装失败 —— 请检查网络后重新运行本脚本。")
        return False
    sd.say("Python 依赖就绪")
    return True


def step_frontend() -> None:
    node = sd.find_node()
    if node is None:
        sd.say("未找到 Node.js —— 跳过（可选组件）")
        sd.say("  影响：没有 vite 开发服务器；:8000 仍提供已构建界面，功能不受限。")
        return
    nm = ROOT / "前端界面" / "node_modules"
    if nm.is_dir():
        sd.say("前端依赖已存在（跳过）")
        return
    npm: list | None = None
    for cand in (Path(node).parent / "npm.cmd", Path(node).parent / "npm"):
        if cand.exists():
            npm = [str(cand)]
            break
    if npm is None:
        cli = Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if cli.is_file():
            npm = [node, str(cli)]
    if npm is None:
        sd.say("未找到 npm —— 跳过（:8000 仍可用）")
        return
    sd.say("安装前端依赖（npm install，国内镜像）…")
    rc = run_stream(
        [*npm, "install", "--no-audit", "--no-fund", f"--registry={NPM_MIRROR}"],
        cwd=ROOT / "前端界面",
    )
    sd.say("前端依赖就绪" if rc == 0 else "npm install 失败（可稍后重试；不影响 :8000）")


# ------------------------------------------------------------- 数据 / 快照 ----

def psql_out(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "aiintel-postgres", "psql", "-U", "aiintel",
         "-d", "ai_security_intel", "-tAc", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (r.stdout or "").strip()


def psql_exec(sql: str) -> int:
    return subprocess.run(
        ["docker", "exec", "aiintel-postgres", "psql", "-U", "aiintel",
         "-d", "ai_security_intel", "-c", sql],
        capture_output=True,
    ).returncode


def step_snapshot(auto_yes: bool) -> None:
    if not SNAPSHOT.exists():
        sd.say("未发现数据快照（数据/snapshot.sql）—— 跳过，系统将从零开始采集。")
        return
    mb = SNAPSHOT.stat().st_size / 1048576
    if not auto_yes:
        ans = input(f"检测到数据快照（{mb:.0f} MB）。导入本机数据库？[Y/n] ").strip().lower()
        if ans not in ("", "y", "yes"):
            sd.say("已跳过数据快照导入。")
            return
    # 空库判定（与 exe 启动器同口径：主表有数据行才跳过）
    probe = psql_out(
        "SELECT CASE WHEN to_regclass('public.vulnerabilities') IS NULL THEN 0 "
        "ELSE (SELECT count(*) FROM public.vulnerabilities) END"
    )
    if probe.isdigit() and int(probe) > 0:
        sd.say(f"数据库已有 {probe} 条数据 —— 跳过导入（保护现有数据）")
        return
    if probe != "0":
        sd.say(f"无法确认数据库状态（{probe[:60]!r}）—— 跳过导入以免误伤。")
        return
    sd.say("数据库为空 —— 重置结构…")
    if psql_exec(
        "DROP SCHEMA public CASCADE; CREATE SCHEMA public; "
        "CREATE EXTENSION IF NOT EXISTS vector;"
    ) != 0:
        sd.say("重置结构失败 —— 跳过导入。")
        return
    sd.say(f"正在导入数据快照（{mb:.0f} MB，约 1~3 分钟）…")
    with open(SNAPSHOT, "rb") as f:
        r = subprocess.run(
            ["docker", "exec", "-i", "-e", "PGCLIENTENCODING=UTF8", "aiintel-postgres",
             "psql", "-U", "aiintel", "-d", "ai_security_intel", "-v", "ON_ERROR_STOP=1"],
            stdin=f, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    if r.returncode != 0:
        sd.say("数据导入失败。输出末尾：")
        sd.say(((r.stderr or "") + (r.stdout or ""))[-300:])
        return
    cnt = psql_out("SELECT count(*) FROM public.vulnerabilities")
    sd.say(f"数据导入完成（vulnerabilities = {cnt} 行）")


# ------------------------------------------------------------------ 检测模式 ----

def run_check() -> int:
    sd.say(sd.BAR)
    sd.say("  Sentinel 环境检测（只读，不安装、不修改任何东西）")
    sd.say(sd.BAR)
    v = ".".join(map(str, sys.version_info[:3]))
    tag = "（版本 OK）" if sys.version_info >= (3, 13) else "（建议 3.13）"
    sd.say(f"运行本脚本的 Python：{v}{tag}")

    if sd.venv_usable():
        sd.say(".venv：已可用")
    elif PY.exists():
        sd.say(".venv：存在但本机不可用（原开发机残留）→ 首次安装时会重建")
    else:
        sd.say(".venv：未创建 → 首次安装时会创建")

    node = sd.find_node()
    sd.say(f"Node.js：{node or '未找到（可选；不影响 :8000 访问）'}")
    nm = ROOT / "前端界面" / "node_modules"
    if nm.is_dir():
        sd.say("前端依赖：已存在")
    else:
        sd.say("前端依赖：缺失（有 Node 时首次安装会 npm install）")

    if sd.docker_ok():
        sd.say("Docker 引擎：运行中")
    else:
        sd.say("Docker 引擎：未运行（首次安装会尝试启动 Docker Desktop）")
    states = {c: sd.container_ready(c) for c in sd.CONTAINERS}
    joined = "、".join(f"{c}={'就绪' if ok else '未就绪'}" for c, ok in states.items())
    sd.say("数据层容器：" + joined)

    if SNAPSHOT.exists():
        mb = SNAPSHOT.stat().st_size / 1048576
        sd.say(f"数据快照：{SNAPSHOT.name}（{mb:.0f} MB）→ 首次安装可导入")
    else:
        sd.say("数据快照：无（可从作者处获取 snapshot.sql 放入 数据/ 目录）")

    sd.say(sd.BAR)
    ready = sd.venv_usable() and sd.docker_ok() and all(states.values())
    sd.say("结论：" + ("环境已就绪 —— 直接双击「启动系统.bat」即可。"
                       if ready else "尚有未完成项 —— 双击「首次安装.bat」自动处理。"))
    return 0


# --------------------------------------------------------------------- 主流程 ----

def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel 首次安装（新机器初始化）")
    parser.add_argument("--yes", action="store_true", help="全程自动（快照存在即导入，不询问）")
    parser.add_argument("--check", action="store_true", help="只检测环境，不安装不修改")
    parser.add_argument("--recreate", action="store_true", help="强制重建虚拟环境")
    parser.add_argument("--no-snapshot", action="store_true", help="不导入数据快照")
    parser.add_argument("--pip-index", default=PIP_MIRROR, help="pip 源（默认清华镜像）")
    args = parser.parse_args()

    if args.check:
        return run_check()

    sd.say(sd.BAR)
    sd.say("  Sentinel 首次安装（新机器初始化）")
    sd.say(sd.BAR)

    if sys.version_info < (3, 10):  # noqa: UP036 —— 引导脚本需能跑在旧解释器上
        sd.say(f"Python 版本过低（{sys.version.split()[0]}）—— 请安装 Python 3.13 后重试。")
        return 1
    if sys.version_info < (3, 13):  # noqa: UP036 —— 同上，仅提示不阻断
        ver = sys.version.split()[0]
        sd.say(f"提示：当前 Python {ver}，建议 3.13（与开发环境一致）；继续尝试…")

    sd.say("\n[1/6] 配置文件")
    step_env_file()

    sd.say("\n[2/6] 虚拟环境")
    if not step_venv(args.recreate):
        return 1

    sd.say("\n[3/6] Python 依赖")
    if not step_pip(args.pip_index):
        return 1

    sd.say("\n[4/6] 前端依赖")
    step_frontend()

    sd.say("\n[5/6] 数据层容器（PostgreSQL / Redis / Neo4j）")
    if not sd.ensure_docker() or not sd.ensure_containers():
        sd.say("数据层未就绪 —— 请处理上方提示后重新运行本脚本。")
        return 1

    sd.say("\n[6/6] 数据快照")
    if args.no_snapshot:
        sd.say("按参数跳过数据快照。")
    else:
        step_snapshot(args.yes)

    sd.say("\n" + sd.BAR)
    sd.say("安装完成。")
    sd.say("下一步：双击「启动系统.bat」启动系统（首次后端预热约 30~110 秒）。")
    sd.say("提示：可将「启动系统.bat」发送快捷方式到桌面，日常一键启动。")
    sd.say(sd.BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
