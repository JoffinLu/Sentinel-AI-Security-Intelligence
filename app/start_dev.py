#!/usr/bin/env python
"""Sentinel 一键启动（开发模式）。

流程：Docker 容器（PG/Redis/Neo4j）→ 后端 uvicorn :8000 → 前端 vite :5173。
特性：
  * 幂等 —— 已在运行的服务自动跳过，可随时重复运行；
  * 后端 :8000 同时托管「前端界面/dist」构建版，未启 vite 时 8000 也是完整应用；
  * Ctrl+C 或直接关闭本窗口，即可停止本次启动的服务。

用法（一般直接双击项目根目录的「启动系统.bat」即可）：
    start_dev.py                  常规启动，全部就绪后自动打开浏览器
    start_dev.py --no-browser     不自动打开浏览器
    start_dev.py --no-frontend    只启动 Docker + 后端（http://localhost:8000）
    start_dev.py --no-docker      跳过 Docker 检查（数据库由你自行管理）
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "数据"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
VITE_ENTRY = ROOT / "前端界面" / "node_modules" / "vite" / "bin" / "vite.js"
COMPOSE_FILE = ROOT / "运维" / "docker-compose.基础设施.yml"

BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173"

CONTAINERS = ["aiintel-postgres", "aiintel-redis", "aiintel-neo4j"]

DOCKER_WAIT_S = 150      # 等 Docker 引擎（冷启动 1~2 分钟）
DB_HEALTHY_WAIT_S = 90   # 等三个容器 healthy
BACKEND_WAIT_S = 180     # 等后端（首载 embedding 较慢，波动大）
FRONTEND_WAIT_S = 60     # 等 vite

BAR = "=" * 64


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, total: int, msg: str) -> None:
    say(f"\n[{n}/{total}] {msg}")
    say("-" * 48)


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    # 本机回环必须绕开系统代理（否则连 localhost:5432 都会走代理导致 degraded）
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def http_status(url: str, timeout: float = 3.0) -> int | None:
    """本机 HTTP 探测；显式禁用代理（环境里注入了 http_proxy）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def wait_for(desc: str, check, timeout_s: float, interval: float = 3.0) -> bool:
    t0 = time.time()
    last_note = t0
    while time.time() - t0 < timeout_s:
        if check():
            return True
        if time.time() - last_note >= 12:
            say(f"    等待{desc}… 已等 {int(time.time() - t0)} 秒")
            last_note = time.time()
        time.sleep(interval)
    return check()


def venv_usable() -> bool:
    """`.venv` 可能绑定原开发电脑（pyvenv.cfg 指向其基础 Python）——

    换机后仅"文件存在"不代表能用，必须实跑一次才算数。
    """
    try:
        return subprocess.run(
            [str(PY), "-V"], capture_output=True, timeout=30
        ).returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- Docker ----

def docker_ok() -> bool:
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20
        ).returncode == 0
    except Exception:
        return False


def docker_desktop_exe() -> Path | None:
    cands = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs" / "DockerDesktop" / "Docker Desktop.exe",
        Path("C:/Program Files/Docker/Docker/Docker Desktop.exe"),
        Path("C:/Program Files/Docker Desktop/Docker Desktop.exe"),
    ]
    return next((p for p in cands if p.is_file()), None)


def ensure_docker() -> bool:
    if docker_ok():
        say("Docker 引擎已在运行")
        return True
    exe = docker_desktop_exe()
    if exe is None:
        say("未找到 Docker Desktop，请手动启动 Docker 后重试")
        return False
    say("正在启动 Docker Desktop（首次约需 1~2 分钟）…")
    try:
        subprocess.Popen([str(exe)])
    except Exception as e:
        say(f"启动 Docker Desktop 失败：{e}")
        return False
    ok = wait_for("Docker 引擎", docker_ok, DOCKER_WAIT_S)
    say("Docker 引擎就绪" if ok else "Docker 引擎等待超时，请稍后重新运行本脚本")
    return ok


def container_ready(container: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format",
             "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
             container],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        s = (r.stdout or "").strip()  # 例： "running healthy" / "running starting" / "running"
        if not s.startswith("running"):
            return False
        return "starting" not in s and "unhealthy" not in s
    except Exception:
        return False


def ensure_containers() -> bool:
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.State}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        states = {}
        for ln in (r.stdout or "").splitlines():
            name, _, state = ln.partition("|")
            states[name.strip()] = state.strip()
    except Exception:
        states = {}

    if all(states.get(c) == "running" for c in CONTAINERS):
        say("PG / Redis / Neo4j 容器已在运行")
        return True

    missing = [c for c in CONTAINERS if c not in states]
    for c in CONTAINERS:
        if states.get(c) == "running":
            continue
        if c in states:
            say(f"启动容器 {c} …")
            subprocess.run(["docker", "start", c], capture_output=True, timeout=90)
    if missing:
        say(f"容器不存在：{'、'.join(missing)}")
        if COMPOSE_FILE.is_file():
            say("尝试用 compose 创建…（首次会拉取镜像，可能需 5~10 分钟）")
            compose_env = os.environ.copy()
            compose_env["COMPOSE_PROJECT_NAME"] = "aiintel"  # 与既有脚本保持一致
            try:
                r = subprocess.run(
                    ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=900, env=compose_env,
                )
                rc, out = r.returncode, (r.stderr or "") + (r.stdout or "")
            except subprocess.TimeoutExpired:
                rc, out = 1, "compose 等待超时（首次拉镜像较慢；可稍后重跑本脚本）"
            if rc != 0:
                say("compose 创建失败，请手动处理。输出末尾：")
                say(out[-300:])

    ok = wait_for(
        "数据库容器健康检查",
        lambda: all(container_ready(c) for c in CONTAINERS),
        DB_HEALTHY_WAIT_S,
    )
    say(
        "PG / Redis / Neo4j 已就绪"
        if ok
        else "容器健康检查等待超时（后端可能降级），稍后可重跑本脚本"
    )
    return ok


# --------------------------------------------------------------- 后端/前端 ----

def start_backend(env: dict[str, str], children: dict, handles: list) -> bool:
    if http_status(f"{BACKEND_URL}/api/health") == 200:
        say("后端已在运行（http://localhost:8000）")
        return True
    if port_open(8000):
        say("警告：8000 端口被占用但健康检查未通过。")
        say("      可能是一个异常的后端进程；结束它后再重新运行本脚本。")
        return False

    say("启动后端 uvicorn …（首次预热 30~110 秒，偶尔更慢，请耐心）")
    log = open(DATA_DIR / "api.log", "ab")  # noqa: SIM115 —— 句柄须存活至子进程结束
    handles.append(log)
    proc = subprocess.Popen(
        [str(PY), "-u", "-m", "uvicorn", "项目源码.后端服务.app:app",
         "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    children["后端"] = proc

    def ready() -> bool:
        return proc.poll() is None and http_status(f"{BACKEND_URL}/api/health") == 200

    ok = wait_for("后端就绪", ready, BACKEND_WAIT_S)
    if ok:
        say("后端就绪 → http://localhost:8000")
    elif proc.poll() is not None:
        say(f"后端进程已退出（exit={proc.returncode}），请看日志：数据/api.log")
    else:
        say("后端等待超时（可能仍在预热），稍后刷新页面试试；日志：数据/api.log")
    return ok


def find_node() -> str | None:
    cands: list[Path] = [Path("D:/nodejs/node.exe")]
    versions_dir = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if versions_dir.is_dir():
        cands += sorted(versions_dir.glob("*/node.exe"), reverse=True)
    for c in cands:
        try:
            if c.is_file() and subprocess.run(
                [str(c), "--version"], capture_output=True, timeout=10
            ).returncode == 0:
                return str(c)
        except Exception:
            continue
    return shutil.which("node")


def start_frontend(env: dict[str, str], children: dict, handles: list) -> bool:
    if http_status(f"{FRONTEND_URL}/") is not None:
        say("前端已在运行（http://localhost:5173）")
        return True
    if port_open(5173):
        say("警告：5173 端口被占用但不是 vite 响应，跳过前端启动。")
        return False
    if not VITE_ENTRY.is_file():
        say("未找到 vite（前端依赖缺失？请先在 前端界面 目录执行 npm install）")
        return False
    node = find_node()
    if node is None:
        say("未找到可用的 node（尝试过 D:/nodejs、内置 node、PATH），跳过前端启动。")
        return False

    say(f"启动前端 vite（{node}）…")
    log = open(DATA_DIR / "vite.log", "ab")  # noqa: SIM115 —— 句柄须存活至子进程结束
    handles.append(log)
    proc = subprocess.Popen(
        [node, str(VITE_ENTRY), "dev", "--host", "0.0.0.0", "--port", "5173"],
        cwd=str(ROOT / "前端界面"), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    children["前端"] = proc

    def ready() -> bool:
        return proc.poll() is None and http_status(f"{FRONTEND_URL}/") is not None

    ok = wait_for("前端就绪", ready, FRONTEND_WAIT_S)
    if ok:
        say("前端就绪 → http://localhost:5173")
    elif proc.poll() is not None:
        say(f"前端进程已退出（exit={proc.returncode}），请看日志：数据/vite.log")
    else:
        say("前端等待超时；日志：数据/vite.log")
    return ok


# ------------------------------------------------------------------- 主流程 ----

def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel 一键启动（开发模式）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--no-frontend", action="store_true", help="只启动 Docker + 后端")
    parser.add_argument("--no-docker", action="store_true", help="跳过 Docker 检查")
    args = parser.parse_args()

    say(BAR)
    say("  Sentinel 一键启动（开发模式）")
    say(BAR)

    if not PY.exists():
        say(f"找不到虚拟环境解释器：{PY}")
        say("请先在项目根目录创建虚拟环境并安装依赖：")
        say("  python -m venv .venv")
        say("  .venv\\Scripts\\pip.exe install -r requirements.txt")
        return 1
    if not venv_usable():
        say("⚠ 本机无法运行 .venv —— 该虚拟环境绑定的是原开发电脑的 Python，换机后必须重建。")
        say("请在本机重新创建后再启动：")
        say("  1) 安装 Python 3.13（https://www.python.org/）")
        say("  2) 项目根目录执行：python -m venv .venv")
        say("  3) 执行：.venv\\Scripts\\pip.exe install -r requirements.txt")
        say("  4) 重新双击「启动系统.bat」")
        return 1

    env = child_env()
    children: dict[str, subprocess.Popen] = {}
    handles: list = []
    ok_all = True

    try:
        if args.no_docker:
            say("\n[1/3] 跳过 Docker 检查（--no-docker）")
        else:
            step(1, 3, "检查 Docker 与数据库容器")
            if not ensure_docker():
                return 1
            ok_all = ensure_containers() and ok_all

        step(2, 3, "检查 / 启动后端")
        ok_all = start_backend(env, children, handles) and ok_all

        if args.no_frontend:
            say("\n[3/3] 跳过前端（--no-frontend）")
        else:
            step(3, 3, "检查 / 启动前端")
            ok_all = start_frontend(env, children, handles) and ok_all

        say("\n" + BAR)
        say("全部就绪：" if ok_all else "部分服务未就绪（见上方提示）：")
        say(f"  前端（开发，改代码即时生效）: {FRONTEND_URL}")
        say(f"  后端 / 构建版界面:          {BACKEND_URL}")
        say("  日志: 数据/api.log ｜ 数据/vite.log")
        say("  停止: 按 Ctrl+C 或直接关闭本窗口")
        say(BAR)

        if not args.no_browser:
            target = BACKEND_URL if args.no_frontend else FRONTEND_URL
            with contextlib.suppress(Exception):
                webbrowser.open(target)

        if not children:
            say("\n没有需要新启动的服务，脚本退出。")
            return 0 if ok_all else 1

        say("\n服务运行中，保持本窗口开启（Ctrl+C 停止）…")
        while True:
            time.sleep(1)
            for name, proc in list(children.items()):
                rc = proc.poll()
                if rc is not None:
                    say(f"[警告] {name} 进程已退出（exit={rc}），详见数据目录下日志。")
                    del children[name]
                    ok_all = False
            if not children:
                say("所有服务均已退出。")
                break
    except KeyboardInterrupt:
        say("\n收到停止指令，正在结束服务…")
    finally:
        if children:
            for proc in children.values():
                with contextlib.suppress(Exception):
                    proc.terminate()
            for proc in children.values():
                try:
                    proc.wait(timeout=6)
                except Exception:
                    with contextlib.suppress(Exception):
                        proc.kill()
            say("已结束本次启动的服务。")
        for h in handles:
            with contextlib.suppress(Exception):
                h.close()
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
