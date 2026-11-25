# pytest 单元测试
#
# 覆盖拓展模块（ROEN 网络入侵检测，移植自 MD1.0）：
#   · 混沌加密往返 / 密文差异 / 坏密钥拒绝
#   · 流量模拟器批次生成
#   · ROEN 检测器推理（模型缺失时跳过）
#   · 控制器状态机（锁定/解锁/加密/解密/监控）
#   · /api/roen 路由冒烟

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from 项目源码.拓展模块.chaos_encryption import ChaosEncryption  # noqa: E402
from 项目源码.拓展模块.config import MODEL_PATH  # noqa: E402
from 项目源码.拓展模块.controller import ExtensionController  # noqa: E402
from 项目源码.拓展模块.roen_detector import ROENDetector  # noqa: E402
from 项目源码.拓展模块.traffic_simulator import TrafficSimulator  # noqa: E402

# ---------------------------------------------------------------- 加密 ----


@pytest.fixture()
def sample_image(tmp_path: Path) -> Path:
    """造一张确定性灰度小图（避免依赖仓库素材）。"""
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, (48, 64), dtype=np.uint8)
    p = tmp_path / "sample.png"
    Image.fromarray(arr, mode="L").save(p)
    return p


def test_chaos_encryption_roundtrip(tmp_path: Path, sample_image: Path):
    enc = ChaosEncryption()
    enc_path = tmp_path / "enc.png"
    dec_path = tmp_path / "dec.png"

    ok, key_hash, err = enc.encrypt_image(str(sample_image), str(enc_path))
    assert ok, err
    assert len(key_hash.split(",")) == 128

    ok2, err2 = enc.decrypt_image(str(enc_path), str(dec_path), key_hash)
    assert ok2, err2

    a = np.array(Image.open(sample_image).convert("L"))
    b = np.array(Image.open(dec_path).convert("L"))
    assert a.shape == b.shape
    assert (a == b).all(), "解密结果必须与原文逐位一致"


def test_chaos_encryption_changes_pixels(tmp_path: Path, sample_image: Path):
    enc = ChaosEncryption()
    enc_path = tmp_path / "enc.png"
    ok, _, err = enc.encrypt_image(str(sample_image), str(enc_path))
    assert ok, err
    a = np.array(Image.open(sample_image).convert("L"))
    e = np.array(Image.open(enc_path).convert("L"))
    diff = (a != e).mean()
    assert diff > 0.9, f"密文应与原文几乎完全不同（实际差异 {diff:.1%}）"


def test_decrypt_with_wrong_key_produces_garbage(tmp_path: Path, sample_image: Path):
    """错误密钥不抛异常，但解不出原文（XOR 密钥错 → 输出即噪声）。"""
    enc = ChaosEncryption()
    enc_path = tmp_path / "enc.png"
    dec_path = tmp_path / "dec_bad.png"
    ok, _, err = enc.encrypt_image(str(sample_image), str(enc_path))
    assert ok, err

    bad_key = ",".join(["1"] * 128)  # 长度对但内容错
    ok2, err2 = enc.decrypt_image(str(enc_path), str(dec_path), bad_key)
    assert ok2, err2

    a = np.array(Image.open(sample_image).convert("L"))
    b = np.array(Image.open(dec_path).convert("L"))
    assert not (a == b).all(), "错误密钥不应解出原文"


def test_decrypt_rejects_short_key(tmp_path: Path, sample_image: Path):
    enc = ChaosEncryption()
    enc_path = tmp_path / "enc.png"
    ok, _, err = enc.encrypt_image(str(sample_image), str(enc_path))
    assert ok, err
    ok2, err2 = enc.decrypt_image(str(enc_path), str(tmp_path / "x.png"), ",".join(["1"] * 64))
    assert ok2 is False
    assert "密钥长度错误" in err2


# -------------------------------------------------------------- 模拟器 ----


def test_simulator_batches():
    sim = TrafficSimulator(n_samples=200, seed=42)
    assert sim.status()["total_records"] == 200
    assert sim.status()["attack_records"] > 0

    batch = sim.generate_batch(10, attack_probability=0.0)
    assert len(batch) <= 10
    for col in ("Source IP", "Destination IP", "Source Port", "Destination Port", "Label"):
        assert col in batch.columns
    assert (batch["Label"] == "BENIGN").all(), "注入比例为 0 时不应混入攻击样本"


def test_simulator_attack_injection():
    sim = TrafficSimulator(n_samples=400, seed=42)
    # 注入比例 1.0 时，每个批次都应含攻击样本
    for _ in range(5):
        batch = sim.generate_batch(10, attack_probability=1.0)
        assert (batch["Label"] != "BENIGN").any()


# -------------------------------------------------------------- 检测器 ----


@pytest.mark.skipif(not MODEL_PATH.exists(), reason=f"模型权重不存在: {MODEL_PATH}")
def test_detector_inference():
    det = ROENDetector()
    assert det.is_initialized
    sim = TrafficSimulator(n_samples=100, seed=42)
    batch = sim.generate_batch(10, attack_probability=0.0)
    result = det.detect_intrusion(batch)
    assert result["total_flows"] == len(batch)
    assert result["attack_type"] in ("BENIGN", "DDoS", "PortScan")
    assert 0.0 <= result["confidence_score"] <= 1.0
    assert sum(result["class_distribution"].values()) == len(batch)
    info = det.info()
    assert info["params"] > 0


# -------------------------------------------------------------- 控制器 ----


def test_controller_state_machine(tmp_path: Path):
    c = ExtensionController()
    assert c.status()["locked"] is False

    # 模拟攻击 → 锁定 + 加密
    ok, result = c.simulate_attack_event()
    assert ok
    assert result["is_attack"] is True
    assert c.is_locked is True
    st = c.status()
    assert st["simulated_events"] == 1
    assert all(img["is_encrypted"] for img in st["images"])
    assert st["total_encrypted"] == len(st["images"])

    # 密文预览可获取
    if st["images"]:
        preview = c.image_preview(st["images"][0]["id"])
        assert preview is not None and len(preview[0]) > 0

    # 口令校验
    assert c.unlock("wrong")[0] is False
    assert c.unlock("admin123")[0] is True
    assert c.is_locked is False

    # 解密还原
    ok, msg = c.decrypt_images()
    assert ok
    assert all(not img["is_encrypted"] for img in c.status()["images"])


def test_controller_monitoring_roundtrip():
    c = ExtensionController()
    ok, msg = c.start_monitoring(interval=0.5, batch_size=10, attack_probability=0.0)
    assert ok, msg
    assert c.is_monitoring is True

    import time

    time.sleep(1.2)
    c.stop_monitoring()
    assert c.is_monitoring is False
    assert len(c.history) >= 1
    entry = c.history[-1]
    assert entry["total_flows"] > 0
    assert "class_distribution" in entry


# ---------------------------------------------------------------- 路由 ----


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from 项目源码.拓展模块.routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_routes_smoke(client):
    r = client.get("/api/roen/status")
    assert r.status_code == 200
    body = r.json()
    for key in ("model_ready", "monitoring", "locked", "images", "config"):
        assert key in body

    r = client.get("/api/roen/model-info")
    assert r.status_code == 200
    info = r.json()
    assert info["paper"]["venue"].startswith("Computer Networks")
    assert "architecture" in info["figures"]

    r = client.get("/api/roen/history", params={"limit": 5})
    assert r.status_code == 200
    assert isinstance(r.json(), list)

    # 不存在的图像 → 404
    r = client.get("/api/roen/images/__nope__/preview")
    assert r.status_code == 404


def test_routes_attack_and_unlock(client):
    # 先确保未锁定（可能被上个测试置位）
    client.post("/api/roen/unlock", json={"admin_key": "admin123"})

    r = client.post("/api/roen/response/simulate-attack")
    assert r.status_code == 200
    assert r.json()["locked"] is True

    r = client.post("/api/roen/unlock", json={"admin_key": "wrong"})
    assert r.status_code == 200
    assert r.json()["ok"] is False

    r = client.post("/api/roen/unlock", json={"admin_key": "admin123"})
    assert r.json()["ok"] is True
    assert r.json()["locked"] is False

    # 加/解密一对
    r = client.post("/api/roen/images/encrypt")
    assert r.json()["ok"] is True
    r = client.post("/api/roen/images/decrypt")
    assert r.json()["ok"] is True


def test_routes_monitor_configure(client):
    r = client.post(
        "/api/roen/monitor/configure",
        json={"interval": 2.0, "batch_size": 15, "attack_probability": 0.4},
    )
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["interval"] == 2.0
    assert cfg["batch_size"] == 15
    assert cfg["attack_probability"] == 0.4

    # 越界值不报 4xx：按项目约定走控制器钳制，返回 200 + 生效配置
    r = client.post("/api/roen/monitor/configure", json={"interval": 999})
    assert r.status_code == 200
    assert r.json()["config"]["interval"] == 10.0  # 钳到上限
