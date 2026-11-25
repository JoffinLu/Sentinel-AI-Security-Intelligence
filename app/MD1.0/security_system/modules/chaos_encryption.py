# 混沌加密模块 - 基于现有Encrypt.ipynb实现
import numpy as np
from hashlib import sha512
from PIL import Image
import os
import logging
from typing import List, Tuple, Optional

class ChaosEncryption:
    """混沌加密系统"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def generate_hash(self, image: np.ndarray) -> List[int]:
        """
        使用 SHA-512 算法生成 128 位的散列值 K。
        输入图像应为 2D 灰度图（numpy 数组形式）。
        """
        # 第一次哈希
        hash_object1 = sha512(image.tobytes())
        hash_hex1 = hash_object1.hexdigest()
        K1 = [int(hash_hex1[i:i + 2], 16) for i in range(0, len(hash_hex1), 2)]

        # 第二次哈希，使用第一次哈希的字节作为输入
        hash_object2 = sha512(bytes(K1))
        hash_hex2 = hash_object2.hexdigest()
        K2 = [int(hash_hex2[i:i + 2], 16) for i in range(0, len(hash_hex2), 2)]

        # 合并两个哈希结果，确保 K 长度为 128
        K = K1 + K2  # 总共 128 个元素
        return K
    
    def initialize_chaos_systems(self, K: List[int]) -> Tuple[float, float, float, float]:
        """计算 ki 并初始化混沌系统"""
        # 计算 ki 值
        k1 = sum(K[:32]) / (32 * 255)
        k2 = sum(K[32:64]) / (32 * 255)
        k3 = sum(K[64:96]) / (32 * 255)
        k4 = sum(K[96:128]) / (32 * 255)
        
        # 确保 ki 在 (0, 1) 范围内
        k1 = max(0.001, min(0.999, k1))
        k2 = max(0.001, min(0.999, k2))
        k3 = max(0.001, min(0.999, k3))
        k4 = max(0.001, min(0.999, k4))
        
        return k1, k2, k3, k4
    
    def logistic_map(self, x: float, r: float = 3.99) -> float:
        """Logistic混沌映射"""
        return r * x * (1 - x)
    
    def tent_map(self, x: float, a: float = 1.99) -> float:
        """Tent混沌映射"""
        if x < 0.5:
            return a * x
        else:
            return a * (1 - x)
    
    def generate_chaos_sequence(self, initial_value: float, length: int, map_type: str = 'logistic') -> np.ndarray:
        """生成混沌序列"""
        sequence = np.zeros(length)
        x = initial_value
        
        for i in range(length):
            if map_type == 'logistic':
                x = self.logistic_map(x)
            elif map_type == 'tent':
                x = self.tent_map(x)
            sequence[i] = x
        
        return sequence
    
    def encrypt_image(self, image_path: str, output_path: str) -> Tuple[bool, str, str]:
        """
        加密图像
        返回: (成功标志, 加密密钥哈希, 错误信息)
        """
        try:
            # 读取图像
            if not os.path.exists(image_path):
                return False, "", f"图像文件不存在: {image_path}"
            
            image = Image.open(image_path)
            
            # 转换为灰度图
            if image.mode != 'L':
                image = image.convert('L')
            
            image_array = np.array(image)
            original_shape = image_array.shape
            
            # 生成密钥
            K = self.generate_hash(image_array)
            # 将密钥列表转换为字符串存储（用逗号分隔）
            key_hash = ','.join(map(str, K))

            # 初始化混沌系统
            k1, k2, k3, k4 = self.initialize_chaos_systems(K)
            
            # 生成混沌序列
            total_pixels = image_array.size
            chaos_seq1 = self.generate_chaos_sequence(k1, total_pixels, 'logistic')
            chaos_seq2 = self.generate_chaos_sequence(k2, total_pixels, 'tent')
            
            # 将混沌序列转换为0-255范围的整数
            chaos_key1 = (chaos_seq1 * 255).astype(np.uint8)
            chaos_key2 = (chaos_seq2 * 255).astype(np.uint8)
            
            # 扁平化图像
            flat_image = image_array.flatten()
            
            # 执行加密：XOR操作
            encrypted_flat = flat_image ^ chaos_key1 ^ chaos_key2
            
            # 重塑为原始形状
            encrypted_image = encrypted_flat.reshape(original_shape)
            
            # 保存加密图像
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            encrypted_pil = Image.fromarray(encrypted_image, mode='L')
            encrypted_pil.save(output_path)
            
            self.logger.info(f"图像加密成功: {image_path} -> {output_path}")
            return True, key_hash, ""
            
        except Exception as e:
            error_msg = f"图像加密失败: {str(e)}"
            self.logger.error(error_msg)
            return False, "", error_msg
    
    def decrypt_image(self, encrypted_path: str, output_path: str, key_hash: str) -> Tuple[bool, str]:
        """
        解密图像（使用存储的密钥哈希）
        返回: (成功标志, 错误信息)
        """
        try:
            # 读取加密图像
            if not os.path.exists(encrypted_path):
                return False, f"加密图像文件不存在: {encrypted_path}"

            # 验证密钥哈希
            if not key_hash:
                return False, "密钥哈希为空，无法解密"

            # 将存储的密钥字符串转换回整数列表
            try:
                K = [int(x) for x in key_hash.split(',')]
                if len(K) != 128:
                    return False, f"密钥长度错误: 期望128，实际{len(K)}"
            except ValueError as e:
                return False, f"密钥格式错误: {str(e)}"

            k1, k2, k3, k4 = self.initialize_chaos_systems(K)
            
            # 读取加密图像
            encrypted_image = Image.open(encrypted_path)
            encrypted_array = np.array(encrypted_image)
            
            # 生成相同的混沌序列
            total_pixels = encrypted_array.size
            chaos_seq1 = self.generate_chaos_sequence(k1, total_pixels, 'logistic')
            chaos_seq2 = self.generate_chaos_sequence(k2, total_pixels, 'tent')
            
            chaos_key1 = (chaos_seq1 * 255).astype(np.uint8)
            chaos_key2 = (chaos_seq2 * 255).astype(np.uint8)
            
            # 扁平化加密图像
            flat_encrypted = encrypted_array.flatten()
            
            # 执行解密：相同的XOR操作
            decrypted_flat = flat_encrypted ^ chaos_key1 ^ chaos_key2
            
            # 重塑为原始形状
            decrypted_image = decrypted_flat.reshape(encrypted_array.shape)
            
            # 保存解密图像
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            decrypted_pil = Image.fromarray(decrypted_image, mode='L')
            decrypted_pil.save(output_path)
            
            self.logger.info(f"图像解密成功: {encrypted_path} -> {output_path}")
            return True, ""
            
        except Exception as e:
            error_msg = f"图像解密失败: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg
    
    def batch_encrypt_images(self, image_list: List[str], output_dir: str) -> List[Tuple[str, bool, str, str]]:
        """
        批量加密图像
        返回: [(原始路径, 成功标志, 加密路径, 密钥哈希或错误信息)]
        """
        results = []
        
        for image_path in image_list:
            try:
                filename = os.path.basename(image_path)
                name, ext = os.path.splitext(filename)
                encrypted_filename = f"{name}_encrypted{ext}"
                encrypted_path = os.path.join(output_dir, encrypted_filename)
                
                success, key_hash, error_msg = self.encrypt_image(image_path, encrypted_path)
                
                if success:
                    results.append((image_path, True, encrypted_path, key_hash))
                else:
                    results.append((image_path, False, "", error_msg))
                    
            except Exception as e:
                results.append((image_path, False, "", str(e)))
        
        return results
    
    def verify_encryption_key(self, original_image_path: str, key_hash: str) -> bool:
        """验证加密密钥是否正确"""
        try:
            image = Image.open(original_image_path)
            if image.mode != 'L':
                image = image.convert('L')
            image_array = np.array(image)
            
            K = self.generate_hash(image_array)
            generated_hash = sha512(bytes(K)).hexdigest()
            
            return generated_hash == key_hash
        except Exception as e:
            self.logger.error(f"验证加密密钥失败: {e}")
            return False
