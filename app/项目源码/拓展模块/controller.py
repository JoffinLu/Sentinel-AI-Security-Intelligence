"""拓展模块控制器 —— 监控 / 安全响应 / 图像加密演示（内存态单例）。

演示叙事（对评委如实）：
  · 实时监控：模拟流量批次 → ROEN 真实推理 → 结果如实上屏
  · 安全响应：模型检出攻击，或点击「模拟一次攻击事件」→ 锁定系统 + 加密全部演示图像
  · 解锁：管理员口令解锁（恢复可操作状态，不解密图像）
  · 解密：使用加密时保存的密钥还原图像

与原项目（MD1.0）的差异：去掉从未接通的 MySQL 层，全部状态内存化。
"""

from __future__ import annotations

import logging
import random
import tempfile
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from .chaos_encryption import ChaosEncryption
from .config import ADMIN_KEY, CLASS_NAMES, HISTORY_MAX, IMAGE_DIR, THREAT_LEVELS
from .roen_detector import ROENDetector
from .traffic_simulator import TrafficSimulator

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


class ExtensionController:
    """拓展模块运行时（进程内单例）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._detector: ROENDetector | None = None
        self._sim: TrafficSimulator | None = None
        self._enc = ChaosEncryption()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model_error: str | None = None

        # 运行态
        self.is_monitoring = False
        self.is_locked = False
        self.total_detections = 0
        self.total_attacks = 0
        self.simulated_events = 0
        self.total_encrypted = 0
        self.last_detection: dict | None = None
        self.history: deque[dict] = deque(maxlen=HISTORY_MAX)
        self.cfg = {"interval": 1.0, "batch_size": 10, "attack_probability": 0.3}
        self.images: list[dict] = []
        self._load_images()

    # ---------------- 初始化 ----------------

    def ensure_ready(self) -> bool:
        """懒加载模型与模拟器（首次调用约 1~2 秒）。"""
        with self._lock:
            if self._detector is not None:
                return True
            if self._model_error:
                return False
            try:
                self._detector = ROENDetector()
                self._sim = TrafficSimulator()
                return True
            except Exception as e:  # noqa: BLE001
                self._model_error = str(e)
                logger.error("ROEN 模块初始化失败: %s", e)
                return False

    def _load_images(self) -> None:
        self.images = []
        if not IMAGE_DIR.is_dir():
            return
        for p in sorted(IMAGE_DIR.iterdir()):
            if p.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            self.images.append(
                {
                    "id": p.stem,
                    "filename": p.name,
                    "original": p.read_bytes(),
                    "encrypted": None,
                    "encrypted_name": None,
                    "key_hash": None,
                }
            )
        logger.info("演示图像加载：%d 张", len(self.images))

    # ---------------- 监控 ----------------

    def start_monitoring(
        self,
        interval: float | None = None,
        batch_size: int | None = None,
        attack_probability: float | None = None,
    ) -> tuple[bool, str]:
        if not self.ensure_ready():
            return False, f"模型未就绪：{self._model_error or '加载失败'}"
        with self._lock:
            if self.is_monitoring:
                return True, "监控已在运行"
            self.cfg.update(
                {
                    "interval": self._clamp(interval, self.cfg["interval"], 0.5, 10.0),
                    "batch_size": int(self._clamp(batch_size, self.cfg["batch_size"], 5, 50)),
                    "attack_probability": self._clamp(
                        attack_probability, self.cfg["attack_probability"], 0.0, 1.0
                    ),
                }
            )
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="roen-monitor", daemon=True)
            self._thread.start()
            self.is_monitoring = True
        return True, "监控已启动"

    def stop_monitoring(self) -> tuple[bool, str]:
        with self._lock:
            if not self.is_monitoring:
                return True, "监控未在运行"
            self.is_monitoring = False
        self._stop.set()
        return True, "监控已停止"

    def configure(
        self,
        interval: float | None = None,
        batch_size: int | None = None,
        attack_probability: float | None = None,
    ) -> tuple[bool, str]:
        with self._lock:
            if interval is not None:
                self.cfg["interval"] = self._clamp(interval, self.cfg["interval"], 0.5, 10.0)
            if batch_size is not None:
                self.cfg["batch_size"] = int(self._clamp(batch_size, self.cfg["batch_size"], 5, 50))
            if attack_probability is not None:
                self.cfg["attack_probability"] = self._clamp(
                    attack_probability, self.cfg["attack_probability"], 0.0, 1.0
                )
        return True, "参数已更新"

    def _loop(self) -> None:
        logger.info("ROEN 监控循环启动")
        while not self._stop.is_set():
            try:
                batch = self._sim.generate_batch(
                    self.cfg["batch_size"], self.cfg["attack_probability"]
                )
                result = self._detector.detect_intrusion(batch)
                self._record(result, simulation=False)
                if result["is_attack"]:
                    with self._lock:
                        self.total_attacks += 1
                    self._trigger_response(result)
            except Exception as e:  # noqa: BLE001
                logger.warning("监控循环异常: %s", e)
            self._stop.wait(self.cfg["interval"])
        logger.info("ROEN 监控循环退出")

    def detect_once(self) -> tuple[bool, object]:
        if not self.ensure_ready():
            return False, f"模型未就绪：{self._model_error or '加载失败'}"
        batch = self._sim.generate_batch(self.cfg["batch_size"], self.cfg["attack_probability"])
        result = self._detector.detect_intrusion(batch)
        self._record(result, simulation=False)
        if result["is_attack"]:
            with self._lock:
                self.total_attacks += 1
            self._trigger_response(result)
        return True, result

    # ---------------- 安全响应 ----------------

    def _record(self, result: dict, simulation: bool) -> None:
        entry = {
            "ts": result["timestamp"],
            "total_flows": result["total_flows"],
            "attack_type": result["attack_type"],
            "threat_level": result["threat_level"],
            "confidence_score": result["confidence_score"],
            "is_attack": result["is_attack"],
            "class_distribution": result["class_distribution"],
            "simulation": simulation,
        }
        with self._lock:
            self.history.append(entry)
            self.last_detection = entry
            if not simulation:
                self.total_detections += 1

    def _trigger_response(self, result: dict) -> None:
        """检出攻击 → 锁定系统 + 加密演示图像。"""
        with self._lock:
            if not self.is_locked:
                self.is_locked = True
                logger.warning("系统已锁定（触发源：%s）", result.get("attack_type"))
            self.total_encrypted += self._encrypt_all_locked()

    def _encrypt_all_locked(self) -> int:
        """加密全部未加密图像（持锁调用）。"""
        count = 0
        for img in self.images:
            if img["encrypted"] is not None:
                continue
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = tmp.name
                ok, key_hash, err = self._enc.encrypt_image_bytes(img["original"], tmp_path)
                if ok:
                    img["encrypted"] = Path(tmp_path).read_bytes()
                    img["encrypted_name"] = f"{img['id']}_encrypted.png"
                    img["key_hash"] = key_hash
                    count += 1
                else:
                    logger.warning("加密失败 %s: %s", img["filename"], err)
                Path(tmp_path).unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("加密异常 %s: %s", img["filename"], e)
        return count

    def simulate_attack_event(self) -> tuple[bool, object]:
        """模拟一次攻击事件（演练用，界面明确标注）。

        原项目的攻击告警叙事即由随机模拟驱动；这里保留该能力，
        但会在记录里打上 simulation 标记，界面如实展示为「演练」。
        """
        if not self.ensure_ready():
            return False, f"模型未就绪：{self._model_error or '加载失败'}"
        predicted_class = random.choices([1, 2], weights=[0.5, 0.5])[0]
        attack_type = CLASS_NAMES[predicted_class]
        result = {
            "timestamp": _now_iso(),
            "total_flows": int(random.randint(20, 80)),
            "source_ips": [f"203.0.113.{random.randint(2, 250)}"],
            "target_ips": [f"10.0.0.{random.randint(2, 20)}"],
            "predicted_class": predicted_class,
            "attack_type": attack_type,
            "threat_level": THREAT_LEVELS[predicted_class],
            "confidence_score": round(random.uniform(0.82, 0.98), 4),
            "class_distribution": {attack_type: random.randint(12, 60)},
            "is_attack": True,
        }
        self._record(result, simulation=True)
        with self._lock:
            self.simulated_events += 1
        self._trigger_response(result)
        return True, result

    def unlock(self, admin_key: str) -> tuple[bool, str]:
        if admin_key != ADMIN_KEY:
            return False, "口令错误"
        with self._lock:
            self.is_locked = False
        return True, "系统已解锁"

    def encrypt_images(self) -> tuple[bool, str]:
        with self._lock:
            n = self._encrypt_all_locked()
        return True, f"已加密 {n} 张图像"

    def decrypt_images(self) -> tuple[bool, str]:
        """使用保存的密钥还原全部图像。"""
        count = 0
        with self._lock:
            for img in self.images:
                if img["encrypted"] is None or not img["key_hash"]:
                    continue
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_in:
                        tmp_in.write(img["encrypted"])
                        enc_path = tmp_in.name
                    out_path = enc_path + ".dec.png"
                    ok, err = self._enc.decrypt_image(enc_path, out_path, img["key_hash"])
                    if ok:
                        img["original"] = Path(out_path).read_bytes()
                        img["encrypted"] = None
                        img["encrypted_name"] = None
                        img["key_hash"] = None
                        count += 1
                    else:
                        logger.warning("解密失败 %s: %s", img["filename"], err)
                    Path(enc_path).unlink(missing_ok=True)
                    Path(out_path).unlink(missing_ok=True)
                except Exception as e:  # noqa: BLE001
                    logger.warning("解密异常 %s: %s", img["filename"], e)
        return True, f"已还原 {count} 张图像"

    # ---------------- 查询 ----------------

    def status(self) -> dict:
        with self._lock:
            return {
                "model_ready": self._detector is not None,
                "model_error": self._model_error,
                "monitoring": self.is_monitoring,
                "locked": self.is_locked,
                "total_detections": self.total_detections,
                "total_attacks": self.total_attacks,
                "simulated_events": self.simulated_events,
                "total_encrypted": self.total_encrypted,
                "config": dict(self.cfg),
                "simulator": self._sim.status() if self._sim else None,
                "images": [
                    {
                        "id": img["id"],
                        "filename": img["filename"],
                        "is_encrypted": img["encrypted"] is not None,
                        "size": len(img["encrypted"] or img["original"]),
                    }
                    for img in self.images
                ],
                "last_detection": self.last_detection,
            }

    def history_list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            items = list(self.history)[-limit:]
        items.reverse()  # 最新在前
        return items

    def image_preview(self, image_id: str) -> tuple[bytes, str] | None:
        """返回 (图片字节, 文件名)。加密状态下返回密文。"""
        with self._lock:
            for img in self.images:
                if img["id"] == image_id:
                    if img["encrypted"] is not None:
                        return img["encrypted"], img["encrypted_name"]
                    return img["original"], img["filename"]
        return None

    def model_info(self) -> dict:
        info = {
            "classes": [CLASS_NAMES[i] for i in range(len(CLASS_NAMES))],
            "threat_levels": [THREAT_LEVELS[i] for i in range(len(THREAT_LEVELS))],
            "ready": self._detector is not None,
        }
        if self._detector is not None:
            info.update(self._detector.info())
        return info

    # ---------------- 工具 ----------------

    @staticmethod
    def _clamp(value, default, lo, hi):
        if value is None:
            return default
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat()


_controller: ExtensionController | None = None
_controller_lock = threading.Lock()


def get_controller() -> ExtensionController:
    """进程内单例。"""
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = ExtensionController()
        return _controller
