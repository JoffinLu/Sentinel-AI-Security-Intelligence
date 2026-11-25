"""拓展模块 API 路由（/api/roen/*）。

约定与主系统一致：业务校验失败返回 200 + ok:false + reason（不用 4xx）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from .config import PAPER
from .controller import get_controller

router = APIRouter(tags=["拓展"], prefix="/api/roen")


class MonitorConfig(BaseModel):
    """监控参数（缺省字段保持当前值）。"""

    interval: float | None = Field(default=None, description="检测间隔（秒，0.5~10）")
    batch_size: int | None = Field(default=None, description="每批流量条数（5~50）")
    attack_probability: float | None = Field(
        default=None, description="攻击样本注入比例（0~1，演示）"
    )


class UnlockRequest(BaseModel):
    admin_key: str = Field(..., description="管理员口令")


# ---------------------------------------------------------------- 查询 ----


@router.get("/status")
def get_status() -> dict:
    """系统状态（监控/锁定/统计/图像）。"""
    return get_controller().status()


@router.get("/history")
def get_history(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    """最近检测记录（最新在前）。"""
    return get_controller().history_list(limit)


@router.get("/model-info")
def get_model_info() -> dict:
    """模型信息与论文引用。"""
    return {
        **get_controller().model_info(),
        "paper": PAPER,
        "figures": {
            "scenario": "/roen/scenario.png",
            "architecture": "/roen/architecture.png",
        },
    }


@router.get("/images")
def get_images() -> list[dict]:
    c = get_controller()
    return [
        {
            "id": img["id"],
            "filename": img["filename"],
            "is_encrypted": img["encrypted"] is not None,
            "size": len(img["encrypted"] or img["original"]),
        }
        for img in c.images
    ]


@router.get("/images/{image_id}/preview")
def get_image_preview(image_id: str) -> Response:
    """图像预览（加密状态下返回密文）。"""
    found = get_controller().image_preview(image_id)
    if found is None:
        return Response(status_code=404)
    data, _name = found
    # 按字节魔数判定 MIME：原图 JPEG，密文/解密产物为 PNG——
    # 文件名在加解密状态切换后不可靠，魔数永远真实
    mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return Response(content=data, media_type=mime)


# ---------------------------------------------------------------- 监控 ----


@router.post("/monitor/start")
def start_monitor(cfg: MonitorConfig | None = None) -> dict:
    c = get_controller()
    ok, message = c.start_monitoring(
        interval=cfg.interval if cfg else None,
        batch_size=cfg.batch_size if cfg else None,
        attack_probability=cfg.attack_probability if cfg else None,
    )
    return {"ok": ok, "message": message, "monitoring": c.is_monitoring}


@router.post("/monitor/stop")
def stop_monitor() -> dict:
    c = get_controller()
    ok, message = c.stop_monitoring()
    return {"ok": ok, "message": message, "monitoring": c.is_monitoring}


@router.post("/monitor/configure")
def configure_monitor(cfg: MonitorConfig) -> dict:
    c = get_controller()
    ok, message = c.configure(
        interval=cfg.interval,
        batch_size=cfg.batch_size,
        attack_probability=cfg.attack_probability,
    )
    return {"ok": ok, "message": message, "config": c.cfg}


@router.post("/detect/once")
def detect_once() -> dict:
    c = get_controller()
    ok, result = c.detect_once()
    return {"ok": ok, "result": result}


# ------------------------------------------------------------ 安全响应 ----


@router.post("/response/simulate-attack")
def simulate_attack() -> dict:
    """模拟一次攻击事件（演练；界面会标注）。"""
    c = get_controller()
    ok, result = c.simulate_attack_event()
    return {"ok": ok, "result": result, "locked": c.is_locked}


@router.post("/unlock")
def unlock(req: UnlockRequest) -> dict:
    c = get_controller()
    ok, message = c.unlock(req.admin_key)
    return {"ok": ok, "message": message, "locked": c.is_locked}


@router.post("/images/encrypt")
def encrypt_images() -> dict:
    c = get_controller()
    ok, message = c.encrypt_images()
    return {"ok": ok, "message": message}


@router.post("/images/decrypt")
def decrypt_images() -> dict:
    c = get_controller()
    ok, message = c.decrypt_images()
    return {"ok": ok, "message": message}
