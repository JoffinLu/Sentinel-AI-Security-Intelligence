# 安全控制器 - 协调各个模块
import threading
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional
import os

from models.database import DatabaseManager
from models.roen_detector import ROENDetector
from modules.chaos_encryption import ChaosEncryption
from modules.traffic_simulator import TrafficSimulator
from config import Config

class SecurityController:
    """安全控制器 - 系统核心协调器"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # 初始化各个模块
        self.db_manager = None
        self.roen_detector = ROENDetector()
        self.chaos_encryption = ChaosEncryption()
        self.traffic_simulator = TrafficSimulator(callback=self.handle_simulated_traffic)
        
        # 系统状态
        self.is_monitoring = False
        self.is_locked = False
        self.monitoring_thread = None
        self.last_detection_time = None
        self.simulation_mode = True  # 默认使用模拟模式
        
        # 统计信息
        self.total_detections = 0
        self.total_attacks = 0
        self.total_encrypted_images = 0

        # 自动启动监控
        self.start_monitoring()

        self.logger.info("安全控制器初始化完成")

    def handle_simulated_traffic(self, traffic_data):
        """处理模拟的网络流量数据"""
        try:
            if not self.is_monitoring:
                return

            self.logger.info(f"接收到模拟流量数据: {len(traffic_data)} 条记录")

            # 使用ROEN检测器分析流量
            detection_result = self.roen_detector.detect_intrusion(traffic_data)

            if detection_result:
                self.total_detections += 1

                # 详细日志调试
                self.logger.info(f"检测结果详情:")
                self.logger.info(f"  - 攻击类型: {detection_result.get('attack_type', 'Unknown')}")
                self.logger.info(f"  - 预测类别: {detection_result.get('predicted_class', 'Unknown')}")
                self.logger.info(f"  - 是否攻击: {detection_result.get('is_attack', False)}")
                self.logger.info(f"  - 置信度: {detection_result.get('confidence_score', 0):.3f}")

                # 检查是否检测到攻击
                if detection_result.get('is_attack', False):
                    self.total_attacks += 1
                    self.logger.warning(f"检测到攻击: {detection_result['attack_type']}")

                    # 触发安全响应
                    self.trigger_security_response(detection_result)
                else:
                    self.logger.info(f"检测为正常流量: {detection_result['attack_type']}")

                # 记录检测日志
                self.log_security_event(detection_result)

        except Exception as e:
            self.logger.error(f"处理模拟流量失败: {e}")
    
    def start_monitoring(self):
        """启动实时监控"""
        if self.is_monitoring:
            self.logger.warning("监控已在运行中")
            return

        self.is_monitoring = True

        if self.simulation_mode:
            # 启动流量模拟器
            self.traffic_simulator.start_simulation()
            self.logger.info("实时监控已启动 (模拟模式)")
        else:
            # 启动传统监控线程
            self.monitoring_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
            self.monitoring_thread.start()
            self.logger.info("实时监控已启动 (真实模式)")
    
    def stop_monitoring(self):
        """停止实时监控"""
        self.is_monitoring = False

        if self.simulation_mode:
            # 停止流量模拟器
            self.traffic_simulator.stop_simulation()
            self.logger.info("实时监控已停止 (模拟模式)")
        else:
            # 停止传统监控线程
            if self.monitoring_thread:
                self.monitoring_thread.join(timeout=5)
            self.logger.info("实时监控已停止 (真实模式)")
    
    def _monitoring_loop(self):
        """监控循环"""
        while self.is_monitoring:
            try:
                # 执行检测
                detection_result = self.perform_detection()
                
                if detection_result:
                    self.total_detections += 1
                    self.last_detection_time = datetime.now()
                    
                    # 如果检测到攻击，触发安全响应
                    if detection_result.get('is_attack', False):
                        self.total_attacks += 1
                        self.trigger_security_response(detection_result)
                
                # 等待下一次检测
                time.sleep(Config.MONITORING_INTERVAL)
                
            except Exception as e:
                self.logger.error(f"监控循环错误: {e}")
                time.sleep(Config.MONITORING_INTERVAL)
    
    def perform_detection(self) -> Optional[Dict]:
        """执行入侵检测"""
        try:
            # 使用模拟检测（在实际部署中应该使用真实的网络流量数据）
            detection_result = self.roen_detector.simulate_traffic_detection()
            
            # 记录检测日志
            if detection_result.get('is_attack', False):
                self.log_security_event(detection_result)
            
            return detection_result
            
        except Exception as e:
            self.logger.error(f"执行检测失败: {e}")
            return None
    
    def trigger_security_response(self, detection_result: Dict):
        """触发安全响应"""
        try:
            self.logger.warning(f"检测到攻击: {detection_result['attack_type']}")
            
            # 1. 锁定数据库
            if not self.is_locked:
                self.lock_database()
            
            # 2. 加密图像
            self.encrypt_all_images()
            
            # 3. 记录安全事件
            self.log_security_event(detection_result)
            
            self.logger.info("安全响应已触发")
            
        except Exception as e:
            self.logger.error(f"安全响应失败: {e}")
    
    def lock_database(self):
        """锁定数据库"""
        try:
            self.db_manager.lock_table('images', 'WRITE')
            self.is_locked = True
            self.logger.info("数据库已锁定")
        except Exception as e:
            self.logger.error(f"数据库锁定失败: {e}")
    
    def unlock_database(self):
        """解锁数据库"""
        try:
            self.db_manager.unlock_tables()
            self.is_locked = False
            self.logger.info("数据库已解锁")
        except Exception as e:
            self.logger.error(f"数据库解锁失败: {e}")
    
    def encrypt_all_images(self):
        """加密所有图像"""
        try:
            # 获取所有未加密的图像
            images = self.db_manager.get_all_images()
            unencrypted_images = [img for img in images if not img['is_encrypted']]

            if not unencrypted_images:
                self.logger.info("没有需要加密的图像")
                return

            self.logger.info(f"开始批量加密 {len(unencrypted_images)} 张图像")

            # 逐个加密图像（使用新的单张加密逻辑）
            encrypted_count = 0
            for image_record in unencrypted_images:
                try:
                    # 当前原始图像路径
                    original_path = image_record['image_path']

                    # 生成加密文件路径
                    filename = image_record['filename']
                    name, ext = os.path.splitext(filename)
                    encrypted_filename = f"{name}_encrypted{ext}"
                    encrypted_path = os.path.join(Config.IMAGE_STORAGE_PATH, encrypted_filename)

                    # 执行加密
                    success, key_hash, error_msg = self.chaos_encryption.encrypt_image(
                        original_path,
                        encrypted_path
                    )

                    if success:
                        # 删除原始文件
                        try:
                            if os.path.exists(original_path):
                                os.remove(original_path)
                                self.logger.debug(f"已删除原始文件: {original_path}")
                        except Exception as e:
                            self.logger.warning(f"删除原始文件失败: {e}")

                        # 更新数据库：设为加密状态，路径指向加密文件，保存密钥哈希
                        self.db_manager.update_image_encryption(image_record['id'], encrypted_path, key_hash)

                        encrypted_count += 1
                        self.logger.info(f"图像加密成功: {filename}")
                    else:
                        self.logger.error(f"图像加密失败: {filename} - {error_msg}")

                except Exception as img_error:
                    self.logger.error(f"加密图像 {image_record['filename']} 时发生错误: {img_error}")

            self.total_encrypted_images += encrypted_count
            self.logger.info(f"批量加密完成，成功加密 {encrypted_count}/{len(unencrypted_images)} 张图像")

        except Exception as e:
            self.logger.error(f"批量加密失败: {e}")
    
    def decrypt_all_images(self, verification_key: str = None) -> bool:
        """批量解密所有图像（逐个调用单张解密）"""
        try:
            # 获取所有已加密的图像
            images = self.db_manager.get_all_images()
            encrypted_images = [img for img in images if img['is_encrypted']]

            if not encrypted_images:
                self.logger.info("没有需要解密的图像")
                return True

            success_count = 0

            # 逐个解密图像（使用单张解密逻辑）
            for img in encrypted_images:
                try:
                    if self.decrypt_single_image(img['id']):
                        success_count += 1
                        self.logger.info(f"图像解密成功: {img['filename']}")
                    else:
                        self.logger.error(f"图像解密失败: {img['filename']}")

                except Exception as e:
                    self.logger.error(f"解密图像时出错: {img['filename']} - {e}")

            self.logger.info(f"批量解密完成，成功解密 {success_count} 张图像")
            return success_count > 0

        except Exception as e:
            self.logger.error(f"批量解密失败: {e}")
            return False

    def decrypt_single_image(self, image_id: int) -> bool:
        """解密单张图像"""
        try:
            # 获取图像记录
            images = self.db_manager.get_all_images()
            image_record = next((img for img in images if img['id'] == image_id), None)

            if not image_record:
                self.logger.error(f"图像不存在: ID {image_id}")
                return False

            if not image_record['is_encrypted']:
                self.logger.info(f"图像未加密，无需解密: {image_record['filename']}")
                return True

            # 当前加密图像路径
            encrypted_path = image_record['image_path']

            # 生成解密后的原始文件路径（恢复到原始位置）
            filename = image_record['filename']
            name, ext = os.path.splitext(filename)
            # 移除可能的加密后缀
            if name.endswith('_encrypted'):
                name = name[:-10]  # 移除 '_encrypted'
            original_filename = f"{name}{ext}"
            decrypted_path = os.path.join(Config.IMAGE_STORAGE_PATH, original_filename)

            # 检查目标文件是否已存在，如果存在则生成新名称
            counter = 1
            while os.path.exists(decrypted_path):
                name_part, ext_part = os.path.splitext(original_filename)
                new_filename = f"{name_part}_decrypted_{counter}{ext_part}"
                decrypted_path = os.path.join(Config.IMAGE_STORAGE_PATH, new_filename)
                counter += 1

            # 执行解密（使用存储的密钥哈希）
            key_hash = image_record.get('encryption_key_hash')

            # 验证密钥哈希是否存在
            if not key_hash:
                self.logger.error(f"图像缺少加密密钥哈希: {image_record['filename']}")
                return False

            success, error_msg = self.chaos_encryption.decrypt_image(
                encrypted_path,
                decrypted_path,
                key_hash
            )

            if success:
                # 删除加密文件
                try:
                    if os.path.exists(encrypted_path):
                        os.remove(encrypted_path)
                        self.logger.debug(f"已删除加密文件: {encrypted_path}")
                except Exception as e:
                    self.logger.warning(f"删除加密文件失败: {e}")

                # 更新数据库：设为未加密状态，路径指向解密后的文件，清除密钥哈希
                self.db_manager.update_image_decryption(image_id, decrypted_path)

                self.logger.info(f"单张图像解密成功: {image_record['filename']}")
                return True
            else:
                self.logger.error(f"单张图像解密失败: {error_msg}")
                return False

        except Exception as e:
            self.logger.error(f"单张图像解密失败: {e}")
            return False

    def encrypt_single_image(self, image_id: int) -> bool:
        """加密单张图像"""
        try:
            # 获取图像记录
            images = self.db_manager.get_all_images()
            image_record = next((img for img in images if img['id'] == image_id), None)

            if not image_record:
                self.logger.error(f"图像不存在: ID {image_id}")
                return False

            if image_record['is_encrypted']:
                self.logger.info(f"图像已加密: {image_record['filename']}")
                return True

            # 当前原始图像路径
            original_path = image_record['image_path']

            # 生成加密文件路径
            filename = image_record['filename']
            name, ext = os.path.splitext(filename)
            encrypted_filename = f"{name}_encrypted{ext}"
            encrypted_path = os.path.join(Config.IMAGE_STORAGE_PATH, encrypted_filename)

            # 执行加密
            success, key_hash, error_msg = self.chaos_encryption.encrypt_image(
                original_path,
                encrypted_path
            )

            if success:
                # 删除原始文件
                try:
                    if os.path.exists(original_path):
                        os.remove(original_path)
                        self.logger.debug(f"已删除原始文件: {original_path}")
                except Exception as e:
                    self.logger.warning(f"删除原始文件失败: {e}")

                # 更新数据库：设为加密状态，路径指向加密文件，保存密钥哈希
                self.db_manager.update_image_encryption(image_id, encrypted_path, key_hash)

                self.logger.info(f"单张图像加密成功: {image_record['filename']}")
                return True
            else:
                self.logger.error(f"单张图像加密失败: {error_msg}")
                return False

        except Exception as e:
            self.logger.error(f"单张图像加密失败: {e}")
            return False
    
    def log_security_event(self, detection_result: Dict):
        """记录安全事件"""
        try:
            # 提取信息并确保数据类型兼容MySQL
            source_ips = detection_result.get('source_ips', [])
            target_ips = detection_result.get('target_ips', [])
            attack_type = str(detection_result.get('attack_type', 'Unknown'))
            threat_level = str(detection_result.get('threat_level', 'Unknown'))

            # 确保confidence_score是Python原生float类型
            confidence_score = detection_result.get('confidence_score', 0.0)
            if hasattr(confidence_score, 'item'):  # 如果是numpy/torch类型
                confidence_score = float(confidence_score.item())
            else:
                confidence_score = float(confidence_score)

            # 确保IP列表不为空
            if not source_ips:
                source_ips = ['Unknown']
            if not target_ips:
                target_ips = ['Unknown']

            # 记录每个源IP的攻击
            for source_ip in source_ips:
                for target_ip in target_ips:
                    self.db_manager.insert_security_log(
                        source_ip=str(source_ip),
                        target_ip=str(target_ip),
                        attack_type=attack_type,
                        threat_level=threat_level,
                        confidence_score=confidence_score,
                        action_taken="DATABASE_LOCKED_AND_ENCRYPTED" if self.is_locked else "LOGGED_ONLY",
                        additional_info=f"Total flows: {detection_result.get('total_flows', 0)}"
                    )

        except Exception as e:
            self.logger.error(f"记录安全事件失败: {e}")
            # 记录详细的错误信息以便调试
            self.logger.error(f"detection_result内容: {detection_result}")
            import traceback
            self.logger.error(f"错误堆栈: {traceback.format_exc()}")
    
    def get_system_status(self) -> Dict:
        """获取系统状态"""
        try:
            recent_logs = self.db_manager.get_recent_logs(10)

            # 处理日志中的datetime对象
            serializable_logs = []
            for log in recent_logs:
                serializable_log = {}
                for key, value in log.items():
                    if hasattr(value, 'isoformat'):  # datetime对象
                        serializable_log[key] = value.isoformat()
                    else:
                        serializable_log[key] = value
                serializable_logs.append(serializable_log)

            status = {
                'monitoring_active': self.is_monitoring,
                'database_locked': self.is_locked,
                'last_detection_time': self.last_detection_time.isoformat() if self.last_detection_time else None,
                'total_detections': self.total_detections,
                'total_attacks': self.total_attacks,
                'total_encrypted_images': self.total_encrypted_images,
                'recent_logs': serializable_logs,
                'system_health': 'NORMAL' if not self.is_locked else 'LOCKED'
            }

            return status

        except Exception as e:
            self.logger.error(f"获取系统状态失败: {e}")
            return {
                'error': str(e),
                'system_health': 'ERROR'
            }

    def reset_counters(self):
        """重置统计计数器"""
        try:
            self.total_detections = 0
            self.total_attacks = 0
            self.last_detection_time = None
            self.logger.info("统计计数器已重置")
            return True
        except Exception as e:
            self.logger.error(f"重置计数器失败: {e}")
            return False

    def manual_unlock_system(self, admin_key: str = None) -> bool:
        """手动解锁系统"""
        try:
            # 在实际部署中，这里应该验证管理员密钥
            if admin_key != "admin123":  # 简单的密钥验证
                self.logger.warning("管理员密钥验证失败")
                return False
            
            # 解锁数据库
            self.unlock_database()
            
            # 可选：解密图像
            # self.decrypt_all_images()
            
            self.logger.info("系统已手动解锁")
            return True
            
        except Exception as e:
            self.logger.error(f"手动解锁失败: {e}")
            return False
    
    def add_sample_images(self):
        """添加示例图像到数据库（先清空再初始化）"""
        try:
            # 先清空数据库中的所有图像记录
            self.logger.info("正在清空数据库中的图像记录...")
            self.db_manager.clear_all_images()

            # 创建图像存储目录
            image_dir = Config.IMAGE_STORAGE_PATH
            os.makedirs(image_dir, exist_ok=True)

            # 指定测试图片目录
            test_image_dir = "static/images/"

            # 查找测试图像目录中的图像文件
            sample_images = []
            if os.path.exists(test_image_dir):
                for filename in os.listdir(test_image_dir):
                    if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
                        full_path = os.path.join(test_image_dir, filename)
                        sample_images.append((filename, full_path))
                        self.logger.info(f"发现测试图像: {filename}")
            else:
                self.logger.warning(f"测试图像目录不存在: {test_image_dir}")
                return

            # 重新添加所有测试图像
            added_count = 0
            for filename, full_path in sample_images:
                try:
                    # 获取文件信息
                    file_size = os.path.getsize(full_path) if os.path.exists(full_path) else 0
                    file_type = os.path.splitext(filename)[1].lower()

                    # 添加到数据库
                    self.db_manager.insert_image_record(
                        filename=filename,
                        image_path=full_path,
                        file_size=file_size,
                        file_type=file_type
                    )

                    added_count += 1
                    self.logger.info(f"成功添加图像: {filename} (大小: {file_size} bytes)")

                except Exception as img_error:
                    self.logger.error(f"添加图像 {filename} 失败: {img_error}")

            self.logger.info(f"图像初始化完成: 清空数据库后重新添加 {added_count} 张图像，总共发现 {len(sample_images)} 张测试图像")

        except Exception as e:
            self.logger.error(f"添加示例图像失败: {e}")

    def set_simulation_mode(self, enabled: bool):
        """设置模拟模式"""
        if self.is_monitoring:
            self.logger.warning("请先停止监控再切换模式")
            return False

        self.simulation_mode = enabled
        mode_str = "模拟模式" if enabled else "真实模式"
        self.logger.info(f"已切换到{mode_str}")
        return True

    def configure_simulator(self, attack_probability: float = None,
                          interval: float = None, batch_size: int = None):
        """配置流量模拟器参数"""
        if attack_probability is not None:
            self.traffic_simulator.set_attack_probability(attack_probability)

        if interval is not None:
            self.traffic_simulator.set_interval(interval)

        if batch_size is not None:
            self.traffic_simulator.set_batch_size(batch_size)

        self.logger.info("流量模拟器配置已更新")

    def get_simulator_status(self):
        """获取模拟器状态"""
        return self.traffic_simulator.get_status()
