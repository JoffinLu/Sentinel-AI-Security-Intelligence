"""混沌加密模块（移植自 MD1.0 / Encrypt.ipynb）。

流程：图像 → SHA-512 双哈希得 128 元素密钥 K → Logistic/Tent 混沌序列
→ 灰度 XOR 加密。解密使用加密时保存的 K 还原混沌序列，逐位可逆。
"""

from __future__ import annotations

import io
import logging
import os
from hashlib import sha512

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class ChaosEncryption:
    """混沌加密系统（灰度图 XOR）。"""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)

    # ---------------- 密钥 ----------------

    def generate_hash(self, image: np.ndarray) -> list[int]:
        """SHA-512 双哈希 → 128 位散列值 K（输入 2D 灰度图）。"""
        hash_hex1 = sha512(image.tobytes()).hexdigest()
        k1 = [int(hash_hex1[i:i + 2], 16) for i in range(0, len(hash_hex1), 2)]
        hash_hex2 = sha512(bytes(k1)).hexdigest()
        k2 = [int(hash_hex2[i:i + 2], 16) for i in range(0, len(hash_hex2), 2)]
        return k1 + k2  # 128 个元素

    def initialize_chaos_systems(self, key: list[int]) -> tuple[float, float, float, float]:
        """由 K 计算 4 个混沌系统初值（确保落在 (0, 1)）。"""
        k1 = sum(key[:32]) / (32 * 255)
        k2 = sum(key[32:64]) / (32 * 255)
        k3 = sum(key[64:96]) / (32 * 255)
        k4 = sum(key[96:128]) / (32 * 255)
        return (
            max(0.001, min(0.999, k1)),
            max(0.001, min(0.999, k2)),
            max(0.001, min(0.999, k3)),
            max(0.001, min(0.999, k4)),
        )

    @staticmethod
    def logistic_map(x: float, r: float = 3.99) -> float:
        return r * x * (1 - x)

    @staticmethod
    def tent_map(x: float, a: float = 1.99) -> float:
        return a * x if x < 0.5 else a * (1 - x)

    def generate_chaos_sequence(
        self, initial_value: float, length: int, map_type: str = "logistic"
    ) -> np.ndarray:
        sequence = np.zeros(length)
        x = initial_value
        for i in range(length):
            if map_type == "logistic":
                x = self.logistic_map(x)
            elif map_type == "tent":
                x = self.tent_map(x)
            sequence[i] = x
        return sequence

    # ---------------- 加密 ----------------

    def encrypt_image(self, image_path: str, output_path: str) -> tuple[bool, str, str]:
        """从文件加密。返回 (成功, 密钥哈希, 错误信息)。"""
        try:
            if not os.path.exists(image_path):
                return False, "", f"图像文件不存在: {image_path}"
            return self._encrypt_pil(Image.open(image_path), output_path)
        except Exception as e:  # noqa: BLE001 —— 原项目即宽捕，演示优先
            self.logger.error("图像加密失败: %s", e)
            return False, "", str(e)

    def encrypt_image_bytes(self, image_bytes: bytes, output_path: str) -> tuple[bool, str, str]:
        """从内存字节加密（演示图像不走磁盘中转）。"""
        try:
            return self._encrypt_pil(Image.open(io.BytesIO(image_bytes)), output_path)
        except Exception as e:  # noqa: BLE001
            self.logger.error("图像加密失败: %s", e)
            return False, "", str(e)

    def _encrypt_pil(self, image: Image.Image, output_path: str) -> tuple[bool, str, str]:
        if image.mode != "L":
            image = image.convert("L")
        image_array = np.array(image)
        original_shape = image_array.shape

        key = self.generate_hash(image_array)
        key_hash = ",".join(map(str, key))
        k1, k2, k3, k4 = self.initialize_chaos_systems(key)

        total_pixels = image_array.size
        chaos_key1 = (self.generate_chaos_sequence(k1, total_pixels, "logistic") * 255).astype(
            np.uint8
        )
        chaos_key2 = (self.generate_chaos_sequence(k2, total_pixels, "tent") * 255).astype(np.uint8)

        encrypted_flat = image_array.flatten() ^ chaos_key1 ^ chaos_key2
        encrypted_image = encrypted_flat.reshape(original_shape)

        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        Image.fromarray(encrypted_image, mode="L").save(output_path)
        self.logger.info("图像加密成功 → %s", output_path)
        return True, key_hash, ""

    # ---------------- 解密 ----------------

    def decrypt_image(
        self, encrypted_path: str, output_path: str, key_hash: str
    ) -> tuple[bool, str]:
        """用保存的密钥哈希解密。返回 (成功, 错误信息)。"""
        try:
            if not os.path.exists(encrypted_path):
                return False, f"加密图像文件不存在: {encrypted_path}"
            if not key_hash:
                return False, "密钥哈希为空，无法解密"
            try:
                key = [int(x) for x in key_hash.split(",")]
                if len(key) != 128:
                    return False, f"密钥长度错误: 期望128，实际{len(key)}"
            except ValueError as e:
                return False, f"密钥格式错误: {e}"

            k1, k2, _, _ = self.initialize_chaos_systems(key)

            encrypted_array = np.array(Image.open(encrypted_path))
            total_pixels = encrypted_array.size
            chaos_key1 = (self.generate_chaos_sequence(k1, total_pixels, "logistic") * 255).astype(
                np.uint8
            )
            chaos_key2 = (self.generate_chaos_sequence(k2, total_pixels, "tent") * 255).astype(
                np.uint8
            )

            decrypted_flat = encrypted_array.flatten() ^ chaos_key1 ^ chaos_key2
            decrypted_image = decrypted_flat.reshape(encrypted_array.shape)

            parent = os.path.dirname(output_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            Image.fromarray(decrypted_image, mode="L").save(output_path)
            self.logger.info("图像解密成功 → %s", output_path)
            return True, ""
        except Exception as e:  # noqa: BLE001
            self.logger.error("图像解密失败: %s", e)
            return False, str(e)
