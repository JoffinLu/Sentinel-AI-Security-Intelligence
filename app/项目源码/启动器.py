"""SENTINEL 系统启动器 —— 产品化版（启动画面 + 无控制台）

双击 exe 的完整体验：
  品牌启动画面（SENTINEL 标志 + 实时进度）→ 无缝切换为系统界面。

启动流程（全部在启动画面上显示进度）：
  ① 检查是否已在运行
  ② 准备数据库运行环境（路径含中文时自动复制到 AppData，仅首次）
  ③ 初始化数据库（仅首次，30~60 秒）/ 启动 PostgreSQL
  ④ 建库 + pgvector 扩展（幂等）
  ⑤ 数据快照导入（完整版首启；已有数据自动跳过）
  ⑥ 启动内嵌后端 → 就绪后启动画面切换为系统界面
  ⑦ 关窗口 = 停止数据库 + 退出

模式开关：
  --console   开发调试（控制台输出 + 独立窗口，不显示启动画面）
  --browser   回退系统浏览器（无 WebView2 的环境）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# ------------------------------------------------------------
# 路径与常量
# ------------------------------------------------------------
_FROZEN = bool(getattr(sys, "frozen", False))

if _FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    # 开发环境：允许从项目根以 `python -m 项目源码.启动器` 运行
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    APP_DIR = _PROJECT_ROOT
    BUNDLE_DIR = _PROJECT_ROOT

URL = "http://127.0.0.1:8000"
_USE_CONSOLE = "--console" in sys.argv  # 开发调试模式
_USE_BROWSER = "--browser" in sys.argv  # 回退系统浏览器
_STDOUT_OK = sys.stdout is not None  # windowed（无控制台）模式下为 False


def _ensure_no_proxy() -> None:
    """排除本机回环地址，避免被系统代理劫持。

    ⚠️ 本机若设置了 http_proxy/https_proxy（如各类代理软件），
    Python 的 requests/asyncpg 会把对 localhost/127.0.0.1 的连接
    也交给代理 → 代理解析不了本机域名 → `socket.gaierror`。
    这正是「exe 启动后后端连不上数据库、8000 端口一直不监听」的根因。
    必须在导入任何会联网的模块之前设置。
    """
    no_proxy = os.environ.get("NO_PROXY", "")
    for host in ("localhost", "127.0.0.1", "::1"):
        if host not in no_proxy.split(","):
            no_proxy = f"{no_proxy},{host}" if no_proxy else host
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy


_ensure_no_proxy()


# ---- 数据存储位置（由应用内"设置 → 数据存储位置"修改，重启后生效）----
# 控制文件固定放在 %LOCALAPPDATA%/Sentinel/data-location.json：
#   configured_dir —— 用户设定的位置（重启后由本启动器执行迁移）
#   last_used      —— 上次实际使用的位置（迁移判定的来源）
_CONTROL_DIR = (
    Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Sentinel"
)
_SETTINGS_FILE = _CONTROL_DIR / "data-location.json"
# 数据目录来源说明（供启动日志/启动画面输出）
DATA_DIR_NOTE = ""


def _read_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _write_settings(data: dict) -> None:
    try:
        _CONTROL_DIR.mkdir(parents=True, exist_ok=True)
        _SETTINGS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _default_data_dir() -> Path:
    """默认数据目录：按顺序取**第一个纯 ASCII** 的位置。

    ① exe 旁 data/（便携模式）
    ② %LOCALAPPDATA%/Sentinel——**仅当用户名是纯 ASCII**
       （中文 Windows 账户时 C:\\Users\\XXX 本身含中文，AppData 不可用！）
    ③ C:\\ProgramData\\Sentinel——**必然 ASCII**（所有用户的兜底）
    """
    portable = APP_DIR / "data"
    if str(portable).isascii():
        return portable
    if str(_CONTROL_DIR).isascii():
        return _CONTROL_DIR
    return Path("C:/ProgramData/Sentinel")


def _data_dir() -> Path:
    """数据库文件目录（可写）。

    优先级：
      ① 应用内"设置 → 数据存储位置"保存的 configured_dir
      ② exe 旁 data/（纯 ASCII 时）
      ③ %LOCALAPPDATA%/Sentinel（必然 ASCII）

    ⚠️ PostgreSQL on Windows 的 initdb **不支持非 ASCII 路径**：
    设置界面已做拦截，此处再兜底——含非 ASCII 时回退默认位置
    （原因会写入启动日志与启动画面）。
    """
    global DATA_DIR_NOTE
    configured = str(_read_settings().get("configured_dir") or "").strip()
    if configured:
        p = Path(configured)
        if str(p).isascii():
            DATA_DIR_NOTE = "（自定义位置 · 来自应用设置）"
            return p
        DATA_DIR_NOTE = "设置的数据位置含非 ASCII 字符（数据库引擎不支持）——已回退默认位置"
    return _default_data_dir()


DATA_DIR = _data_dir()
# 告知后端当前实际数据目录（设置接口据此展示"当前位置"）
os.environ["SENTINEL_ACTIVE_DATA_DIR"] = str(DATA_DIR)
PGDATA_DIR = DATA_DIR / "pgdata"
PG_LOG = DATA_DIR / "pg.log"
LOG_FILE = DATA_DIR / "sentinel-launch.log"
# 快照是只读文件（由 Python 读入经 stdin 交给 psql），路径不受该限制
SNAPSHOT_FILE = APP_DIR / "data" / "snapshot.sql"

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
# 子进程不弹窗（GUI 模式下 pg_ctl/initdb 不应闪出控制台窗口）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_APP_TITLE = "SENTINEL · AI 安全知识情报系统"

# ------------------------------------------------------------
# 日志（文件 + 可选控制台 + 启动画面状态行）
# ------------------------------------------------------------
_splash_window = None
_log_ready = False


def log(msg: str) -> None:
    global _log_ready
    line = f"[SENTINEL] {msg}"
    try:
        if not _log_ready:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _log_ready = True
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except OSError:
        pass
    if _STDOUT_OK:
        try:
            print(line, flush=True)
        except Exception:  # noqa: BLE001
            pass
    splash_status(msg)


def splash_status(msg: str) -> None:
    """把进度写到启动画面（存在时）。"""
    w = _splash_window
    if w is None:
        return
    try:
        w.evaluate_js(f"window.__setStatus({json.dumps(msg)})")
    except Exception:  # noqa: BLE001
        pass


def _message_box(text: str, title: str = "SENTINEL 启动失败") -> None:
    """系统弹窗（无控制台模式下用户唯一能看到的错误出口）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001
        pass


def die(msg: str, hint: str = "") -> None:
    """致命错误：写日志 + 启动画面错误页 + 系统弹窗（不停留等待，窗口由用户关闭）"""
    log(f"启动失败：{msg}" + (f" | {hint}" if hint else ""))
    if _USE_CONSOLE:
        # 控制台模式：保持传统行为（打印指引 + 等待回车）
        print("", flush=True)
        print("=" * 60, flush=True)
        print(f"启动失败：{msg}", flush=True)
        if hint:
            print("", flush=True)
            print(hint, flush=True)
        print("=" * 60, flush=True)
        try:
            input("\n按回车键退出…")
        except (EOFError, RuntimeError):
            time.sleep(5)
        sys.exit(1)

    w = _splash_window
    if w is not None:
        try:
            w.load_html(_error_html(msg, hint))
        except Exception:  # noqa: BLE001
            pass
    _message_box(f"{msg}\n\n{hint}\n\n详细日志：{LOG_FILE}")
    # 停在错误页（用户关窗后进程退出）；不再继续启动流程
    raise SystemExit(1)


# ------------------------------------------------------------
# 启动画面 / 错误页（内嵌 HTML，深绿品牌风）
# ------------------------------------------------------------
_LOGO_SVG = """
<svg width="140" height="140" viewBox="0 0 48 48" fill="none"
     style="filter:drop-shadow(0 0 22px rgba(182,243,74,.32));">
  <defs>
    <linearGradient id="sb" x1="12" y1="40" x2="36" y2="8" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#8ed321"/><stop offset="55%" stop-color="#b6f34a"/>
      <stop offset="100%" stop-color="#4be1ec"/>
    </linearGradient>
    <linearGradient id="sr" x1="20" y1="20" x2="8" y2="8" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#4be1ec" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="#4be1ec" stop-opacity="0.12"/>
    </linearGradient>
  </defs>
  <g stroke="url(#sr)" stroke-width="2.4" stroke-linecap="round" fill="none">
    <path d="M19 21 A 7 7 0 0 0 14 16" opacity="0.9"/>
    <path d="M19 14 A 14 14 0 0 0 8 9" opacity="0.6"/>
    <path d="M20 7 A 21 21 0 0 0 2 2" opacity="0.35"/>
  </g>
  <path d="M26 13 L34 40 L16 40 Z" fill="url(#sb)" fill-opacity="0.22"
        stroke="url(#sb)" stroke-width="2" stroke-linejoin="round"/>
  <circle cx="25.5" cy="10" r="2.6" fill="#b6f34a"/>
  <path d="M11 40 L38 40" stroke="url(#sb)" stroke-width="2.4" stroke-linecap="round"/>
  <path d="M16 44 L33 44" stroke="#b6f34a" stroke-width="2" stroke-linecap="round" stroke-opacity="0.55"/>
</svg>
"""

_SPLASH_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  html,body{margin:0;height:100%;overflow:hidden;user-select:none;
    background:radial-gradient(120% 120% at 30% 20%, #14482c 0%, #0b2a1a 55%, #071a10 100%);
    font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;color:#d9f2e3;}
  .wrap{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;}
  .name{font-size:24px;letter-spacing:7px;color:#b6f34a;font-weight:600;margin-top:4px;}
  .sub{font-size:12.5px;color:rgba(217,242,227,.55);letter-spacing:2.5px;}
  .bar{margin-top:16px;width:300px;height:4px;border-radius:2px;background:rgba(182,243,74,.13);
    overflow:hidden;position:relative;}
  .bar::after{content:'';position:absolute;left:-40%;width:40%;height:100%;
    background:linear-gradient(90deg,transparent,#b6f34a,transparent);animation:slide 1.3s linear infinite;}
  @keyframes slide{to{left:110%;}}
  .st{margin-top:6px;font-size:13.5px;color:#8ed321;min-height:22px;}
  .first{font-size:11.5px;color:rgba(217,242,227,.35);margin-top:2px;}
</style></head><body><div class="wrap">
  LOGO_SVG_PLACEHOLDER
  <div class="name">SENTINEL</div>
  <div class="sub">AI 安全知识情报系统</div>
  <div class="bar"></div>
  <div class="st" id="st">正在启动…</div>
  <div class="first">首次启动约需 1~3 分钟，请耐心等待</div>
</div>
<script>
  window.__setStatus = function(m){ document.getElementById('st').textContent = m; };
</script>
</body></html>"""


def _error_html(msg: str, hint: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  html,body{{margin:0;height:100%;overflow:hidden;
    background:radial-gradient(120% 120% at 30% 20%, #2a1a14 0%, #1a0f0b 55%, #100806 100%);
    font-family:"Microsoft YaHei",system-ui,sans-serif;color:#f2ddd9;}}
  .wrap{{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:0 48px;text-align:center;}}
  .t{{font-size:20px;color:#ff8a7a;font-weight:600;}}
  .m{{font-size:14px;line-height:1.7;color:#f2ddd9;}}
  .h{{font-size:12.5px;line-height:1.7;color:rgba(242,221,217,.6);white-space:pre-wrap;}}
  .log{{font-size:11.5px;color:rgba(242,221,217,.4);}}
</style></head><body><div class="wrap">
  <div class="t">启动失败</div>
  <div class="m">{msg}</div>
  <div class="h">{hint}</div>
  <div class="log">关闭本窗口后，可重新双击 Sentinel.exe 重试</div>
</div></body></html>"""


# ------------------------------------------------------------
# 通用工具
# ------------------------------------------------------------
def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def _wait_until_ready(timeout: float = 900.0) -> bool:
    """轮询等待 8000 端口可连接；等待期间定期写进度（日志+启动画面）。"""
    t0 = time.time()
    last_hint = 0.0
    while time.time() - t0 < timeout:
        if port_in_use("127.0.0.1", 8000):
            return True
        waited = time.time() - t0
        if waited - last_hint >= 20 and waited >= 20:
            last_hint = waited
            log(f"仍在等待服务就绪（已等待 {int(waited)} 秒）")
        time.sleep(1)
    return False


# ------------------------------------------------------------
# ① 定位内置 PostgreSQL + ASCII 自愈
# ------------------------------------------------------------
def find_pgsql() -> Path:
    env_dir = os.environ.get("SENTINEL_PGSQL_DIR")
    if env_dir and (Path(env_dir) / "bin" / "pg_ctl.exe").exists():
        return Path(env_dir)
    for base in (APP_DIR, BUNDLE_DIR):
        candidate = base / "pgsql"
        if (candidate / "bin" / "pg_ctl.exe").exists():
            return candidate
    die(
        "未找到内置数据库组件",
        "发布包不完整——请重新解压压缩包（不要单独移动 Sentinel.exe）。",
    )
    raise SystemExit(1)


def _migrate_data_if_needed() -> None:
    """数据目录迁移（应用内设置变更后，下次启动自动执行）。

    判定（保守，不丢数据）：
      · 目标目录（当前 _data_dir 结果）尚无数据库，
      · 而"上次使用位置"（记录于设置文件）已有数据库
        → 从旧位置复制 pgdata / pgsql-runtime（原位置保留，确认后可手动删除）。
    支持任意方向：默认 ↔ 自定义、自定义 A → 自定义 B。
    """
    last_used = str(_read_settings().get("last_used") or "").strip()
    src = Path(last_used) if last_used else _default_data_dir()
    if src == DATA_DIR:
        return
    if (DATA_DIR / "pgdata" / "PG_VERSION").exists():
        return  # 新位置已有数据，直接用
    if not (src / "pgdata" / "PG_VERSION").exists():
        return  # 没有可迁移的旧数据
    log(f"检测到数据目录变更——正在迁移数据（首次约 1~2 分钟）：{DATA_DIR}")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for sub in ("pgdata", "pgsql-runtime", "pg.log"):
            s, d = src / sub, DATA_DIR / sub
            if s.exists() and not d.exists():
                if s.is_dir():
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)
                log(f"  已迁移 {sub}")
        log("数据迁移完成 [OK]（原位置数据仍保留，确认无误后可手动删除）")
    except OSError as e:
        die("数据迁移失败", f"{e}\n（请检查目标位置的空间与写入权限）")


def _record_data_location() -> None:
    """记录"上次实际使用位置"（数据库就绪后调用），供下次启动做迁移判定。"""
    settings = _read_settings()
    if settings.get("last_used") != str(DATA_DIR):
        settings["last_used"] = str(DATA_DIR)
        _write_settings(settings)


def ensure_ascii_pgsql(pgsql: Path) -> Path:
    """PostgreSQL (Windows) 要求可执行文件与数据目录路径均为纯 ASCII。

    含中文时自动把运行必需组件复制到数据目录下 pgsql-runtime
    （DATA_DIR 已按同规则选择，必然 ASCII）并使用副本。
    首次复制约 120MB/10~30 秒，之后复用（第二次启动无感知）。
    """
    if str(pgsql).isascii():
        return pgsql

    runtime = DATA_DIR / "pgsql-runtime"
    if not (runtime / "bin" / "pg_ctl.exe").exists():
        log("正在准备运行环境（首次约 10~30 秒）…")
        if runtime.exists():
            shutil.rmtree(runtime, ignore_errors=True)
        try:
            runtime.mkdir(parents=True, exist_ok=True)
            for sub in ("bin", "lib", "share"):
                shutil.copytree(pgsql / sub, runtime / sub)
        except OSError as e:
            die("准备运行环境失败", f"{e}\n\n请确认磁盘空间充足后重试。")
        log("运行环境就绪 [OK]")
    return runtime


# ------------------------------------------------------------
# ②③④ 初始化 / 启动 / 建库
# ------------------------------------------------------------
def _run_quiet(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """执行子进程并捕获输出。

    ⚠️ 必须显式 stdin=DEVNULL：GUI（无控制台）应用 spawn 的子进程
    默认继承**无效的 stdin 句柄**，psql 等程序会挂起等待输入
    —— 这正是「exe 卡在建库检查、服务永不就绪」的根因。
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
        **kw,
    )


def ensure_postgres(pgsql: Path, user: str, db: str, port: int) -> None:
    _migrate_data_if_needed()  # 自定义数据目录首启迁移（有则搬，无则跳过）

    if not _IDENT_RE.match(user) or not _IDENT_RE.match(db):
        die("数据库配置不合法", ".env 中 POSTGRES_USER / POSTGRES_DB 只允许字母、数字、下划线。")

    bin_dir = pgsql / "bin"
    initdb = bin_dir / "initdb.exe"
    pg_ctl = bin_dir / "pg_ctl.exe"
    pg_isready = bin_dir / "pg_isready.exe"
    psql = bin_dir / "psql.exe"

    # initdb（首次，约 30~60 秒）
    if not (PGDATA_DIR / "PG_VERSION").exists():
        log("首次运行：正在初始化数据库（约 30~60 秒）…")
        PGDATA_DIR.parent.mkdir(parents=True, exist_ok=True)
        r = _run_quiet(
            [str(initdb), "-D", str(PGDATA_DIR), "-U", user, "-A", "trust",
             "-E", "UTF8", "--locale=C"]
        )
        if r.returncode != 0:
            die(
                "数据库初始化失败",
                f"数据目录：{PGDATA_DIR}\n"
                "可能原因：磁盘空间不足、目录无写入权限，或路径含不受支持的字符\n"
                "（提示：程序会自动选择纯英文路径；若仍失败，请在"
                "「设置→数据存储设置」中指定一个英文目录，如 D:\\SentinelData）\n"
                + "\n".join((r.stderr or r.stdout or "").splitlines()[-4:]),
            )
        log("数据库初始化完成 [OK]")

    # 启动（已在运行则复用）
    already = False
    if port_in_use("127.0.0.1", port):
        r = _run_quiet([str(pg_isready), "-h", "127.0.0.1", "-p", str(port), "-q"])
        if r.returncode == 0:
            already = True
            log("数据库已在运行，直接复用 [OK]")
        else:
            die(
                f"端口 {port} 被其他程序占用",
                f"内置数据库需要 127.0.0.1:{port}。\n请关闭占用该端口的程序后重试，\n"
                "或在 .env 中修改 POSTGRES_PORT。",
            )
    if not already:
        log("正在启动数据库…")
        # ⚠️ 安全软件（Defender 等）对未签名的数据库进程有**间歇性文件访问拦截**，
        # 表现为偶发 "Permission denied"/启动失败——重试通常即可恢复。
        started = False
        for attempt in range(1, 4):
            subprocess.run(
                [str(pg_ctl), "-D", str(PGDATA_DIR), "-l", str(PG_LOG), "-o", f"-p {port}", "start"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
            # 无论 pg_ctl 返回码如何，一律用 pg_isready 确认是否真的就绪
            for _ in range(30):
                if port_in_use("127.0.0.1", port):
                    rq = _run_quiet([str(pg_isready), "-h", "127.0.0.1", "-p", str(port), "-q"])
                    if rq.returncode == 0:
                        started = True
                        break
                time.sleep(2)
            if started:
                break
            if attempt < 3:
                log(f"数据库启动未就绪（第 {attempt} 次）——正在重试…")
                time.sleep(3)
        if not started:
            die("数据库启动失败", f"请查看日志：{PG_LOG}")
        log("数据库就绪 [OK]")

    # 建库 + pgvector（幂等）
    def psql_do(database: str, sql: str) -> subprocess.CompletedProcess:
        return _run_quiet(
            [str(psql), "-h", "127.0.0.1", "-p", str(port), "-U", user, "-d", database, "-tAc", sql],
            env={**os.environ, "PGCLIENTENCODING": "UTF8"},
        )

    r = psql_do("postgres", f"SELECT 1 FROM pg_database WHERE datname = '{db}'")
    if (r.stdout or "").strip() != "1":
        log(f"创建数据库 {db}…")
        r = psql_do("postgres", f'CREATE DATABASE "{db}"')
        if r.returncode != 0:
            die("创建数据库失败", (r.stderr or r.stdout or "")[-400:])
    r = psql_do(db, "CREATE EXTENSION IF NOT EXISTS vector")
    if r.returncode != 0:
        die("启用向量扩展失败", (r.stderr or r.stdout or "")[-400:])
    _record_data_location()  # 记录本次实际使用位置（供下次启动做迁移判定）


def stop_postgres(pgsql: Path) -> None:
    pg_ctl = pgsql / "bin" / "pg_ctl.exe"
    if pg_ctl.exists() and (PGDATA_DIR / "PG_VERSION").exists():
        try:
            _run_quiet([str(pg_ctl), "-D", str(PGDATA_DIR), "-m", "fast", "stop"])
            log("数据库已停止")
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------
# ⑤ 快照导入（仅首启：快照存在且数据库为空）
# ------------------------------------------------------------
def restore_snapshot_if_needed(pgsql: Path, user: str, db: str, port: int) -> None:
    if not SNAPSHOT_FILE.exists():
        return
    psql = pgsql / "bin" / "psql.exe"
    env = {**os.environ, "PGCLIENTENCODING": "UTF8"}
    # 空库判定：主表**有数据行**才跳过。
    # ⚠️ 不能只判"表是否存在"——先跑过纯净版的机器会留下空表结构，
    # 那样完整版将永远导不进数据（表存在 → 误判"已有数据"→ 跳过）。
    r = _run_quiet(
        [str(psql), "-h", "127.0.0.1", "-p", str(port), "-U", user, "-d", db, "-tAc",
         "SELECT CASE WHEN to_regclass('public.vulnerabilities') IS NULL THEN 0 "
         "ELSE (SELECT count(*) FROM public.vulnerabilities) END"],
        env=env,
    )
    probe = (r.stdout or "").strip()
    if probe.isdigit() and int(probe) > 0:
        log("检测到数据库已有数据 —— 跳过快照导入（保护现有数据）[OK]")
        return
    if probe == "0":
        # 空表结构（纯净版遗留）→ 先重置 public schema，避免导入时建表冲突
        log("数据库为空 —— 正在重置结构…")
        r = _run_quiet(
            [str(psql), "-h", "127.0.0.1", "-p", str(port), "-U", user, "-d", db, "-c",
             "DROP SCHEMA public CASCADE; CREATE SCHEMA public; "
             "CREATE EXTENSION IF NOT EXISTS vector;"],
            env=env,
        )
        if r.returncode != 0:
            die("重置数据库结构失败", (r.stderr or "")[-400:])
    size_mb = SNAPSHOT_FILE.stat().st_size / 1024 / 1024
    log(f"首次启动：正在导入数据（{size_mb:.0f} MB，约 1~3 分钟）…")
    t0 = time.time()
    with open(SNAPSHOT_FILE, "rb") as f:
        r = subprocess.run(
            [str(psql), "-h", "127.0.0.1", "-p", str(port), "-U", user, "-d", db,
             "-v", "ON_ERROR_STOP=1"],
            stdin=f,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=_NO_WINDOW,
        )
    if r.returncode != 0:
        die("数据导入失败", (r.stderr or "")[-500:])
    log(f"数据导入完成 [OK]（耗时 {time.time() - t0:.0f} 秒）")


# ------------------------------------------------------------
# 启动主体
# ------------------------------------------------------------
def _startup_sequence(window) -> None:
    """启动画面路径：数据库 → 服务 → 切换界面（运行于后台线程）。

    关键步骤均有耗时打点（写入启动日志），用于定位「启动慢」的具体环节。
    """
    t0 = time.time()
    log("正在加载服务组件…")
    from 项目源码.后端服务.config import get_settings  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    from 项目源码.后端服务.app import app  # noqa: PLC0415

    log(f"服务组件加载完成（{time.time() - t0:.1f} 秒）")

    settings = get_settings()

    if DATA_DIR_NOTE:
        log(f"数据存放位置：{DATA_DIR} {DATA_DIR_NOTE}")

    t0 = time.time()
    pgsql = find_pgsql()
    pgsql = ensure_ascii_pgsql(pgsql)
    ensure_postgres(pgsql, settings.postgres_user, settings.postgres_db, settings.postgres_port)
    restore_snapshot_if_needed(
        pgsql, settings.postgres_user, settings.postgres_db, settings.postgres_port
    )
    log(f"数据库准备完成（{time.time() - t0:.1f} 秒）")

    t0 = time.time()
    log("正在启动服务…")

    def _serve() -> None:
        try:
            uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
            log("服务线程已退出")
        except Exception as e:  # noqa: BLE001  # 服务线程崩溃要在画面上可见（无控制台）
            log(f"服务异常退出：{e}")
            splash_status(f"服务异常：{e}")

    threading.Thread(target=_serve, daemon=True).start()

    if not _wait_until_ready():
        die("服务启动超时", "请重试；若反复出现，请查看启动日志。")
    log(f"服务就绪（{time.time() - t0:.1f} 秒）")
    # 切换界面：优先用 evaluate_js 跳转（与 splash_status 同一通道，
    # 后台线程调用已被验证可靠）；失败则回退 load_url，再失败开系统浏览器
    switched = False
    try:
        splash_status("正在进入系统…")
        window.evaluate_js(f"window.location.href = {json.dumps(URL)}")
        switched = True
    except Exception as e:  # noqa: BLE001
        log(f"evaluate_js 跳转失败（{e}），尝试 load_url…")
    if not switched:
        try:
            window.load_url(URL)
            switched = True
        except Exception as e:  # noqa: BLE001
            log(f"load_url 失败（{e}），回退系统浏览器")
    if not switched:
        import webbrowser  # noqa: PLC0415

        webbrowser.open(URL)


def _run_console() -> None:
    """开发调试路径：控制台输出 + 独立窗口。"""
    import webview  # noqa: PLC0415

    from 项目源码.后端服务.config import get_settings  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    from 项目源码.后端服务.app import app  # noqa: PLC0415

    pgsql = ensure_ascii_pgsql(find_pgsql())
    settings = get_settings()
    ensure_postgres(pgsql, settings.postgres_user, settings.postgres_db, settings.postgres_port)
    restore_snapshot_if_needed(
        pgsql, settings.postgres_user, settings.postgres_db, settings.postgres_port
    )
    threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info"),
        daemon=True,
    ).start()
    log("正在启动服务——就绪后将自动弹出应用窗口，请勿关闭本窗口…")
    _wait_until_ready()
    try:
        webview.create_window(_APP_TITLE, URL, width=1280, height=800, min_size=(960, 600), js_api=_JS_BRIDGE)
        webview.start()
    finally:
        stop_postgres(pgsql)
        log("应用窗口已关闭，正在退出…")


class _JsBridge:
    """暴露给系统页面的原生能力（pywebview js_api）。

    "数据存储设置"的「浏览…」按钮通过这里调起**系统文件夹选择对话框**——
    Web 页面本身拿不到本机绝对路径，必须由原生层完成。
    仅在 pywebview 窗口中可用；浏览器模式下前端自动退回手工输入。
    """

    def pick_folder(self) -> str:
        """弹出系统"选择文件夹"对话框；返回所选路径（取消返回空串）。"""
        w = _splash_window
        if w is None:
            return ""
        try:
            import webview  # noqa: PLC0415

            result = w.create_file_dialog(webview.FOLDER_DIALOG)
            if not result:
                return ""
            first = result[0] if isinstance(result, (list, tuple)) else result
            return str(first or "")
        except Exception:  # noqa: BLE001
            return ""


_JS_BRIDGE = _JsBridge()


def _run_gui() -> None:
    """产品化路径：启动画面 → 无缝切换系统界面。"""
    global _splash_window

    import webview  # noqa: PLC0415

    html = _SPLASH_HTML.replace("LOGO_SVG_PLACEHOLDER", _LOGO_SVG)
    window = webview.create_window(
        _APP_TITLE, html=html, width=980, height=660, min_size=(780, 540), js_api=_JS_BRIDGE
    )
    _splash_window = window
    try:
        # start 阻塞直到窗口关闭；func 在 GUI 就绪后于独立线程执行
        webview.start(_startup_sequence, window)
    finally:
        try:
            stop_postgres(ensure_ascii_pgsql(find_pgsql()))
        except BaseException:  # noqa: BLE001
            pass
        log("已退出")


def _run_browser() -> None:
    """回退路径：无启动画面，就绪后开系统浏览器。"""
    import webbrowser  # noqa: PLC0415

    from 项目源码.后端服务.config import get_settings  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    from 项目源码.后端服务.app import app  # noqa: PLC0415

    pgsql = ensure_ascii_pgsql(find_pgsql())
    settings = get_settings()
    ensure_postgres(pgsql, settings.postgres_user, settings.postgres_db, settings.postgres_port)
    restore_snapshot_if_needed(
        pgsql, settings.postgres_user, settings.postgres_db, settings.postgres_port
    )
    threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning"),
        daemon=True,
    ).start()
    _wait_until_ready()
    webbrowser.open(URL)
    log("浏览器已打开——关闭本程序窗口即退出")
    try:
        while True:
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        stop_postgres(pgsql)


def main() -> None:
    # 已在运行（8000 被占用）→ 直接展示界面
    if port_in_use("127.0.0.1", 8000):
        if _USE_BROWSER or _USE_CONSOLE:
            import webbrowser  # noqa: PLC0415

            webbrowser.open(URL)
            return
        import webview  # noqa: PLC0415

        webview.create_window(_APP_TITLE, URL, width=1280, height=800, min_size=(960, 600), js_api=_JS_BRIDGE)
        webview.start()
        return

    if _USE_BROWSER:
        _run_browser()
    elif _USE_CONSOLE:
        _run_console()
    else:
        _run_gui()


if __name__ == "__main__":
    if _STDOUT_OK:
        # Windows 控制台默认 GBK：把不可编码字符替换为 ? 而不是抛异常
        for _stream in (sys.stdout, sys.stderr):
            try:
                _stream.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass
    try:
        main()
    except KeyboardInterrupt:
        if _STDOUT_OK:
            print("\n[SENTINEL] 已手动停止", flush=True)
