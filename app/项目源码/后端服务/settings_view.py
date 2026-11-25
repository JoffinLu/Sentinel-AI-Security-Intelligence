"""系统设置接口（数据存储位置等）

数据存储位置的"控制文件"统一放在 %LOCALAPPDATA%/Sentinel/data-location.json：
  {
    "configured_dir": "D:\\SentinelData",   # 用户设定的位置（重启后生效）
    "last_used":      "D:\\SentinelData"    # 上次实际使用的位置（启动器维护，用于迁移判定）
  }

设计要点：
  · 保存设置只写文件、不搬数据——数据库运行时文件被占用，无法安全迁移；
    真正的迁移由**启动器**在下次启动时执行（数据库未启动的最安全时机）。
  · 路径必须是纯 ASCII：Windows 版 PostgreSQL 的 initdb 不支持中文路径
    （官方立场），此处提前拦截并给出可读提示，避免重启后才失败。
  · 校验可写性：保存时即试写，把问题暴露在设置界面而不是重启之后。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["设置"], prefix="/api/settings")


# ---------- 控制文件读写 ----------

def _control_dir() -> Path:
    """固定控制目录（不随数据目录变化，保证配置永远找得到）。"""
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "Sentinel"


def _settings_file() -> Path:
    custom = os.environ.get("SENTINEL_CONTROL_FILE")
    return Path(custom) if custom else _control_dir() / "data-location.json"


def _read_settings() -> dict[str, Any]:
    f = _settings_file()
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _write_settings(data: dict[str, Any]) -> None:
    d = _control_dir()
    d.mkdir(parents=True, exist_ok=True)
    _settings_file().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _infer_launcher_data_dir() -> Path:
    """与启动器同款的默认数据目录推断（后端兜底用；正常由环境变量给定）。"""
    # 优先：启动器注入的实际使用值（最准确）
    env = os.environ.get("SENTINEL_ACTIVE_DATA_DIR")
    if env:
        return Path(env)
    # 兜底：与启动器同款三级联（用户名含中文时 AppData 亦不可用 → ProgramData）
    if str(_control_dir()).isascii():
        return _control_dir()
    return Path("C:/ProgramData/Sentinel")


# ---------- 接口 ----------

@router.get("/data-location", summary="查询数据存储位置")
async def get_data_location() -> dict[str, Any]:
    """返回当前实际位置 + 用户设定值（供设置界面展示与回填）。"""
    settings = _read_settings()
    current = str(_infer_launcher_data_dir())
    configured = str(settings.get("configured_dir") or "").strip()
    return {
        "current_dir": current,
        "configured_dir": configured or current,
        "restart_required": bool(configured) and configured != current,
    }


class DataLocationBody(BaseModel):
    data_dir: str


@router.post("/data-location", summary="保存数据存储位置（重启后生效）")
async def set_data_location(body: DataLocationBody) -> dict[str, Any]:
    raw = body.data_dir.strip().strip('"').strip("'")
    if not raw:
        raise HTTPException(status_code=400, detail="路径不能为空")

    p = Path(raw)
    if not str(p).isascii():
        raise HTTPException(
            status_code=400,
            detail="路径含中文等非 ASCII 字符（数据库引擎不支持）——请选择纯英文/数字路径",
        )
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail="请输入完整路径，例如 D:\\SentinelData")

    # 可写性探测：把"目录不存在/无权限"等问题在保存时就暴露
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".sentinel-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"该目录无法写入：{exc}")

    current = str(_infer_launcher_data_dir())
    settings = _read_settings()
    if str(p) == current:
        # 改回"当前正在使用的位置" = 清除自定义设定
        settings.pop("configured_dir", None)
        _write_settings(settings)
        return {
            "ok": True,
            "restart_required": False,
            "message": "已恢复为默认位置（当前正在使用，无需重启）",
        }

    settings["configured_dir"] = str(p)
    _write_settings(settings)
    return {
        "ok": True,
        "restart_required": True,
        "message": "设置已保存 —— 关闭并重新打开程序后，数据将自动迁移到新位置",
    }
