#!/usr/bin/env python3
"""
单个图片解密逻辑测试脚本
用于验证解密功能是否正确工作
"""

import os
import sys
import logging
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from config import Config
from models.database import DatabaseManager
from modules.chaos_encryption import ChaosEncryption
from modules.security_controller import SecurityController

def setup_logging():
    """设置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

def test_single_image_decrypt():
    """测试单个图片解密逻辑"""
    logger = setup_logging()
    
    try:
        # 初始化组件
        logger.info("初始化数据库管理器...")
        db_manager = DatabaseManager()
        
        logger.info("初始化混沌加密模块...")
        chaos_encryption = ChaosEncryption()
        
        logger.info("初始化安全控制器...")
        security_controller = SecurityController(db_manager, None, chaos_encryption, None)
        
        # 获取所有图像
        logger.info("获取图像列表...")
        images = db_manager.get_all_images()
        
        if not images:
            logger.warning("数据库中没有图像记录")
            return
        
        logger.info(f"找到 {len(images)} 张图像")
        
        # 显示图像状态
        for img in images:
            status = "已加密" if img['is_encrypted'] else "未加密"
            key_hash_status = "有密钥" if img.get('encryption_key_hash') else "无密钥"
            file_exists = "存在" if os.path.exists(img['image_path']) else "不存在"
            
            logger.info(f"图像 ID:{img['id']} - {img['filename']} - {status} - {key_hash_status} - 文件{file_exists}")
            logger.info(f"  路径: {img['image_path']}")
            
            if img.get('encryption_key_hash'):
                logger.info(f"  密钥哈希: {img['encryption_key_hash'][:50]}...")
        
        # 查找第一张加密的图像进行测试
        encrypted_images = [img for img in images if img['is_encrypted']]
        
        if not encrypted_images:
            logger.warning("没有找到加密的图像进行测试")
            
            # 查找未加密的图像，先加密一张
            unencrypted_images = [img for img in images if not img['is_encrypted']]
            if unencrypted_images:
                test_img = unencrypted_images[0]
                logger.info(f"先加密图像: {test_img['filename']}")
                
                if security_controller.encrypt_single_image(test_img['id']):
                    logger.info("加密成功，现在测试解密...")
                    
                    # 重新获取图像信息
                    images = db_manager.get_all_images()
                    encrypted_images = [img for img in images if img['is_encrypted']]
                else:
                    logger.error("加密失败")
                    return
            else:
                logger.error("没有可用的图像进行测试")
                return
        
        if encrypted_images:
            test_img = encrypted_images[0]
            logger.info(f"测试解密图像: {test_img['filename']} (ID: {test_img['id']})")
            
            # 检查解密前的状态
            logger.info("解密前状态:")
            logger.info(f"  是否加密: {test_img['is_encrypted']}")
            logger.info(f"  文件路径: {test_img['image_path']}")
            logger.info(f"  文件存在: {os.path.exists(test_img['image_path'])}")
            logger.info(f"  有密钥哈希: {bool(test_img.get('encryption_key_hash'))}")
            
            # 执行解密
            logger.info("开始解密...")
            success = security_controller.decrypt_single_image(test_img['id'])
            
            if success:
                logger.info("✅ 解密成功!")
                
                # 检查解密后的状态
                updated_images = db_manager.get_all_images()
                updated_img = next((img for img in updated_images if img['id'] == test_img['id']), None)
                
                if updated_img:
                    logger.info("解密后状态:")
                    logger.info(f"  是否加密: {updated_img['is_encrypted']}")
                    logger.info(f"  文件路径: {updated_img['image_path']}")
                    logger.info(f"  文件存在: {os.path.exists(updated_img['image_path'])}")
                    logger.info(f"  密钥哈希: {updated_img.get('encryption_key_hash')}")
                    
                    # 验证解密结果
                    if not updated_img['is_encrypted']:
                        logger.info("✅ 数据库状态正确更新为未加密")
                    else:
                        logger.error("❌ 数据库状态未正确更新")
                    
                    if updated_img.get('encryption_key_hash') is None:
                        logger.info("✅ 密钥哈希已正确清除")
                    else:
                        logger.error("❌ 密钥哈希未正确清除")
                    
                    if os.path.exists(updated_img['image_path']):
                        logger.info("✅ 解密文件已创建")
                    else:
                        logger.error("❌ 解密文件未创建")
                        
            else:
                logger.error("❌ 解密失败")
                
    except Exception as e:
        logger.error(f"测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_single_image_decrypt()
