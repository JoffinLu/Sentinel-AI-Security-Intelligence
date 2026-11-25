"""
用户自定义 LLM 配置的持久化
================================================================
方案 A：写入项目根目录的 `.env`。

----------
为什么不直接改 .env ----------
1. **不能整体重写**：.env 里有大量注释（如「# ===== LLM 配置 =====」）、
   字段顺序、以及 pydantic-settings 的解析假设。
   整体重写会丢注释、丢顺序，还可能踩到 Windows 换行符问题。
2. **不能简单拼字符串**：值里可能有 `#`（被当注释）、
   空格、引号，需要按 dotenv 规范转义。
3. **要能回滚**：保存前先备份，失败能恢复。

所以做法：**按行扫描，只替换目标键那一行，其余原样保留**。

----------
安全说明 ----------
API Key 以明文存在 .env —— 这是所有本地部署方案的通病，无法根除。
缓解措施：
  · 文件权限设为 600（仅当前用户可读写，Windows 下被忽略但不更糟）
  · 接口永不返回完整 Key，只返回掩码
  · .env 已在 .gitignore 中（若项目用 git）
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# .env 位置 = 应用根目录（开发=项目根；打包=exe 所在目录，用户可编辑）
from ..运行时 import app_root as _app_root  # noqa: E402

ENV_PATH = _app_root() / ".env"

# 允许用户改的键 —— 白名单，避免用户借这个接口改数据库密码之类
ALLOWED_KEYS = ("LLM_API_BASE", "LLM_API_KEY", "LLM_MODEL")

# 备份文件后缀
_BACKUP_SUFFIX = ".bak"


def mask_key(key: str) -> str:
    """
    把 API Key 掩码化，用于回显给前端。

    只保留末尾 4 位 —— 足够用户确认「填的是哪一把」，
    又不至于泄露。短于 8 位的（明显不是真 key）一律全掩。
    """
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "*" * len(k)
    return f"{k[:3]}...{k[-4:]}"


def read_env_values(keys: tuple[str, ...] = ALLOWED_KEYS) -> dict[str, str]:
    """从 .env 读取指定键（不经过 pydantic，直接读文件真实值）。"""
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    try:
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in keys:
                out[k] = v.strip().strip('"').strip("'")
    except OSError as exc:
        logger.warning("env_read_failed", error=str(exc)[:150])
    return out


def _format_value(v: str) -> str:
    """
    按 dotenv 规范格式化值。

    含 # 或空格时必须加引号，否则 # 之后会被当注释丢掉。
    """
    if v == "":
        return ""
    if any(c in v for c in "# '\"\t"):
        escaped = v.replace('"', '\\"')
        return f'"{escaped}"'
    return v


def write_env_values(values: dict[str, str]) -> tuple[bool, str]:
    """
    写入 .env，只替换白名单内的键，其余内容原样保留。

    Returns:
        (是否成功, 提示信息)
    """
    unknown = set(values) - set(ALLOWED_KEYS)
    if unknown:
        return False, f"不允许修改这些键：{', '.join(sorted(unknown))}"
    if not ENV_PATH.exists():
        return False, f"未找到 {ENV_PATH.name} 文件"
    if not values:
        return False, "没有要写入的内容"

    try:
        original = ENV_PATH.read_text(encoding="utf-8")
        # 备份：写失败时可回滚
        backup = ENV_PATH.with_suffix(ENV_PATH.suffix + _BACKUP_SUFFIX)
        shutil.copy2(ENV_PATH, backup)

        lines = original.splitlines()
        remaining = dict(values)
        out: list[str] = []
        # 逐行匹配 key = value（允许行尾注释）
        pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

        for line in lines:
            m = pattern.match(line)
            if m and m.group(1) in remaining:
                key = m.group(1)
                out.append(f"{key}={_format_value(remaining.pop(key))}")
            else:
                out.append(line)

        # .env 里没有的键，追加到末尾（便于用户看到新增项）
        if remaining:
            out.append("")
            out.append("# ===== 用户自定义 LLM 配置（由系统设置写入）=====")
            for k, v in remaining.items():
                out.append(f"{k}={_format_value(v)}")

        new_content = "\n".join(out) + "\n"

        # 手册 4.3：**原子写入**。
        # 直接 write_text 是「打开→截断→写」，进程被杀/磁盘满时会留下
        # 半截 .env——那等于把 API Key 与全部配置写坏，服务再也起不来。
        # 正确做法：同目录写临时文件 → fsync 落盘 → os.replace 原子替换，
        # 替换本身在同文件系统内是原子的，失败时原文件完好无损。
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                delete=False,
                dir=ENV_PATH.parent,
                prefix=".env.tmp.",
            ) as handle:
                temporary = Path(handle.name)
                handle.write(new_content)
                handle.flush()
                os.fsync(handle.fileno())  # 确保内容真正落盘再替换
            os.replace(temporary, ENV_PATH)  # 原子替换
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

        # 收紧权限（Windows 上基本无效，但不加重风险）
        try:
            os.chmod(ENV_PATH, 0o600)
        except OSError:
            pass

        logger.info(
            "env_written",
            keys=list(values.keys()),
            backup=backup.name,
            bytes=len(new_content),
        )
        return True, f"已写入 {ENV_PATH.name}（备份为 {backup.name}）"

    except OSError as exc:
        # 失败即回滚：把备份复制回原位，保证「要么是旧配置、要么是新配置」，
        # 不会停在半截文件这种最坏状态。
        logger.error("env_write_failed", error=str(exc)[:200])
        try:
            rollback = ENV_PATH.with_suffix(ENV_PATH.suffix + _BACKUP_SUFFIX)
            if rollback.exists():
                shutil.copy2(rollback, ENV_PATH)
                logger.warning("env_write_rolled_back", backup=rollback.name)
        except Exception as rb_exc:  # noqa: BLE001
            logger.error("env_rollback_failed", error=str(rb_exc)[:200])
        return False, f"写入失败：{exc}"


def restore_backup() -> tuple[bool, str]:
    """从备份恢复 .env（恢复默认时用）。"""
    backup = ENV_PATH.with_suffix(ENV_PATH.suffix + _BACKUP_SUFFIX)
    if not backup.exists():
        return False, "没有可用的备份文件"
    try:
        shutil.copy2(backup, ENV_PATH)
        return True, f"已从 {backup.name} 恢复"
    except OSError as exc:
        return False, f"恢复失败：{exc}"


def default_snapshot() -> dict[str, Any]:
    """
    记录「出厂默认」—— 用于前端「恢复默认」按钮。

    存在内存里而非文件：进程启动时读一次即为默认。
    （若要跨重启恢复默认，需要另存一份出厂模板。）
    """
    return {k: v for k, v in read_env_values().items()}


# 进程启动时的快照（= 默认值）
_BOOTSTRAP = default_snapshot()


def get_defaults() -> dict[str, str]:
    """出厂默认配置（启动时的值）。"""
    return dict(_BOOTSTRAP)


# 服务端时间戳，供前端提示「上次保存于」
_last_saved_at: float | None = None


def mark_saved() -> None:
    global _last_saved_at
    _last_saved_at = time.time()


def last_saved_at() -> float | None:
    return _last_saved_at
