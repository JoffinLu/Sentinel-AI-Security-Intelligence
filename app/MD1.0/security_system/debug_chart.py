#!/usr/bin/env python3
"""
图表数据调试脚本
用于检查实时监控图表没有显示内容的问题
"""

import os
import sys
import time
import logging
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from config import Config
from models.database import DatabaseManager
from models.roen_detector import ROENDetector
from modules.security_controller import SecurityController

def setup_logging():
    """设置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

def debug_chart_data():
    """调试图表数据问题"""
    logger = setup_logging()
    
    try:
        logger.info("=== 开始调试图表数据问题 ===")
        
        # 1. 检查安全控制器初始化
        logger.info("1. 初始化安全控制器...")
        security_controller = SecurityController()
        
        # 2. 检查监控状态
        logger.info("2. 检查监控状态...")
        logger.info(f"  监控是否激活: {security_controller.is_monitoring}")
        logger.info(f"  数据库是否锁定: {security_controller.is_locked}")
        logger.info(f"  模拟模式: {security_controller.simulation_mode}")
        
        # 3. 检查统计数据
        logger.info("3. 检查当前统计数据...")
        logger.info(f"  总检测次数: {security_controller.total_detections}")
        logger.info(f"  总攻击次数: {security_controller.total_attacks}")
        logger.info(f"  加密图像数: {security_controller.total_encrypted_images}")
        logger.info(f"  最后检测时间: {security_controller.last_detection_time}")
        
        # 4. 手动执行几次检测
        logger.info("4. 手动执行检测...")
        for i in range(5):
            logger.info(f"  执行第 {i+1} 次检测...")
            detection_result = security_controller.perform_detection()
            
            if detection_result:
                logger.info(f"    检测结果: {detection_result.get('attack_type', 'Unknown')}")
                logger.info(f"    是否攻击: {detection_result.get('is_attack', False)}")
                logger.info(f"    置信度: {detection_result.get('confidence_score', 0):.3f}")
            else:
                logger.warning("    检测结果为空!")
            
            time.sleep(1)
        
        # 5. 检查更新后的统计数据
        logger.info("5. 检查更新后的统计数据...")
        logger.info(f"  总检测次数: {security_controller.total_detections}")
        logger.info(f"  总攻击次数: {security_controller.total_attacks}")
        
        # 6. 获取系统状态（模拟WebSocket发送的数据）
        logger.info("6. 获取系统状态（WebSocket数据）...")
        status = security_controller.get_system_status()
        
        logger.info("  系统状态数据:")
        for key, value in status.items():
            if key != 'recent_logs':  # 跳过日志详情
                logger.info(f"    {key}: {value}")
        
        # 7. 检查数据库中的安全日志
        logger.info("7. 检查数据库中的安全日志...")
        db_manager = DatabaseManager()
        recent_logs = db_manager.get_recent_logs(5)
        
        logger.info(f"  最近 {len(recent_logs)} 条安全日志:")
        for log in recent_logs:
            logger.info(f"    {log.get('timestamp', 'Unknown')} - {log.get('attack_type', 'Unknown')} - {log.get('threat_level', 'Unknown')}")
        
        # 8. 测试ROEN检测器
        logger.info("8. 直接测试ROEN检测器...")
        roen_detector = ROENDetector()
        
        for i in range(3):
            logger.info(f"  直接检测第 {i+1} 次...")
            result = roen_detector.simulate_traffic_detection()
            logger.info(f"    结果: {result.get('attack_type')} - 攻击: {result.get('is_attack')} - 置信度: {result.get('confidence_score', 0):.3f}")
        
        # 9. 模拟前端图表更新逻辑
        logger.info("9. 模拟前端图表更新逻辑...")
        
        # 模拟前端接收到的数据
        chart_data = {
            'labels': [],
            'threat_data': [],
            'normal_data': []
        }
        
        for i in range(10):
            # 获取当前状态
            status = security_controller.get_system_status()
            
            # 模拟前端处理
            now = time.strftime("%H:%M:%S")
            total_attacks = status.get('total_attacks', 0)
            total_detections = status.get('total_detections', 0)
            normal_traffic = total_detections - total_attacks
            
            chart_data['labels'].append(now)
            chart_data['threat_data'].append(total_attacks)
            chart_data['normal_data'].append(normal_traffic)
            
            logger.info(f"  时间: {now}, 威胁: {total_attacks}, 正常: {normal_traffic}")
            
            # 执行一次检测以更新数据
            security_controller.perform_detection()
            time.sleep(1)
        
        # 10. 总结
        logger.info("10. 调试总结...")
        final_status = security_controller.get_system_status()
        
        if final_status.get('total_detections', 0) > 0:
            logger.info("✅ 检测系统正常工作")
        else:
            logger.error("❌ 检测系统没有产生数据")
        
        if final_status.get('total_attacks', 0) > 0:
            logger.info("✅ 攻击检测正常")
        else:
            logger.warning("⚠️  没有检测到攻击（可能是正常的）")
        
        logger.info(f"最终统计: 检测 {final_status.get('total_detections', 0)} 次, 攻击 {final_status.get('total_attacks', 0)} 次")
        
        # 11. 检查WebSocket相关配置
        logger.info("11. 检查WebSocket配置...")
        logger.info(f"  Web主机: {Config.WEB_HOST}")
        logger.info(f"  Web端口: {Config.WEB_PORT}")
        logger.info(f"  监控间隔: {Config.MONITORING_INTERVAL}秒")
        
        logger.info("=== 调试完成 ===")
        
    except Exception as e:
        logger.error(f"调试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_chart_data()
