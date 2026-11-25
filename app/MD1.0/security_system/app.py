# 主应用程序 - Flask Web服务器
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, send_file
from flask_socketio import SocketIO, emit
import logging
import threading
import time
import os
import sys

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import Config
from modules.security_controller import SecurityController

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('security_system.log'),
        logging.StreamHandler()
    ]
)

# 创建Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = Config.SECRET_KEY
app.config['DEBUG'] = Config.DEBUG

# 创建SocketIO实例用于实时通信
socketio = SocketIO(app, cors_allowed_origins="*")

# 全局安全控制器实例
security_controller = None

def init_security_system():
    """初始化安全系统"""
    global security_controller
    try:
        security_controller = SecurityController()
        security_controller.add_sample_images()  # 添加示例图像
        app.logger.info("安全系统初始化成功")
        return True
    except Exception as e:
        app.logger.error(f"安全系统初始化失败: {e}")
        return False

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    """获取系统状态API"""
    try:
        if security_controller:
            status = security_controller.get_system_status()
            return jsonify(status)
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"获取状态失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/start_monitoring', methods=['POST'])
def start_monitoring():
    """启动监控API"""
    try:
        if security_controller:
            security_controller.start_monitoring()
            return jsonify({'success': True, 'message': '监控已启动'})
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"启动监控失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop_monitoring', methods=['POST'])
def stop_monitoring():
    """停止监控API"""
    try:
        if security_controller:
            security_controller.stop_monitoring()
            return jsonify({'success': True, 'message': '监控已停止'})
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"停止监控失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/manual_detection', methods=['POST'])
def manual_detection():
    """手动执行检测API"""
    try:
        if security_controller:
            detection_result = security_controller.manual_detection()
            if detection_result:
                return jsonify({
                    'success': True,
                    'detection_result': detection_result
                })
            else:
                return jsonify({'error': '检测失败'}), 500
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"手动检测失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/unlock_system', methods=['POST'])
def unlock_system():
    """解锁系统API"""
    try:
        data = request.get_json()
        admin_key = data.get('admin_key', '')
        
        if security_controller:
            success = security_controller.manual_unlock_system(admin_key)
            if success:
                return jsonify({'success': True, 'message': '系统已解锁'})
            else:
                return jsonify({'error': '解锁失败，请检查管理员密钥'}), 400
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"解锁系统失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/encrypt_images', methods=['POST'])
def encrypt_images():
    """手动加密图像API"""
    try:
        if security_controller:
            security_controller.encrypt_all_images()
            return jsonify({'success': True, 'message': '图像加密完成'})
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"加密图像失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/decrypt_images', methods=['POST'])
def decrypt_images():
    """手动解密图像API"""
    try:
        data = request.get_json()
        verification_key = data.get('verification_key', '')
        
        if security_controller:
            success = security_controller.decrypt_all_images(verification_key)
            if success:
                return jsonify({'success': True, 'message': '图像解密完成'})
            else:
                return jsonify({'error': '解密失败'}), 400
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"解密图像失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs')
def get_logs():
    """获取安全日志API"""
    try:
        if security_controller:
            logs = security_controller.db_manager.get_recent_logs(50)
            return jsonify(logs)
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"获取日志失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/images')
def get_images():
    """获取图像列表API"""
    try:
        if security_controller:
            images = security_controller.db_manager.get_all_images()
            return jsonify(images)
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"获取图像列表失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/images/<int:image_id>')
def get_image_file(image_id):
    """获取图像文件API"""
    try:
        if security_controller:
            # 获取图像记录
            images = security_controller.db_manager.get_all_images()
            image_record = next((img for img in images if img['id'] == image_id), None)

            if not image_record:
                return jsonify({'error': '图像不存在'}), 404

            # 直接使用数据库中的图像路径
            image_path = image_record['image_path']

            # 检查文件是否存在
            if not os.path.exists(image_path):
                return jsonify({'error': '图像文件不存在'}), 404

            # 返回图像文件
            return send_file(image_path, as_attachment=False)
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"获取图像文件失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/decrypt-single-image', methods=['POST'])
def decrypt_single_image():
    """解密单张图像API"""
    try:
        data = request.get_json()
        image_id = data.get('image_id')

        if not image_id:
            return jsonify({'error': '缺少图像ID'}), 400

        if security_controller:
            success = security_controller.decrypt_single_image(image_id)
            if success:
                return jsonify({'success': True, 'message': '图像解密完成'})
            else:
                return jsonify({'error': '解密失败'}), 400
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"解密单张图像失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/encrypt-single-image', methods=['POST'])
def encrypt_single_image():
    """加密单张图像API"""
    try:
        data = request.get_json()
        image_id = data.get('image_id')

        if not image_id:
            return jsonify({'error': '缺少图像ID'}), 400

        if security_controller:
            success = security_controller.encrypt_single_image(image_id)
            if success:
                return jsonify({'success': True, 'message': '图像加密完成'})
            else:
                return jsonify({'error': '加密失败'}), 400
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"加密单张图像失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/simulator/status')
def get_simulator_status():
    """获取模拟器状态API"""
    try:
        if security_controller:
            status = security_controller.get_simulator_status()
            status['simulation_mode'] = security_controller.simulation_mode
            return jsonify(status)
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"获取模拟器状态失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/simulator/configure', methods=['POST'])
def configure_simulator():
    """配置模拟器API"""
    try:
        data = request.get_json()

        if security_controller:
            attack_probability = data.get('attack_probability')
            interval = data.get('interval')
            batch_size = data.get('batch_size')

            security_controller.configure_simulator(
                attack_probability=attack_probability,
                interval=interval,
                batch_size=batch_size
            )

            return jsonify({'success': True, 'message': '模拟器配置已更新'})
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"配置模拟器失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/simulator/mode', methods=['POST'])
def set_simulation_mode():
    """设置模拟模式API"""
    try:
        data = request.get_json()
        enabled = data.get('enabled', True)

        if security_controller:
            success = security_controller.set_simulation_mode(enabled)
            if success:
                mode_str = "模拟模式" if enabled else "真实模式"
                return jsonify({'success': True, 'message': f'已切换到{mode_str}'})
            else:
                return jsonify({'error': '请先停止监控再切换模式'}), 400
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"设置模拟模式失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/system_status', methods=['GET'])
def get_system_status():
    """获取系统状态API"""
    try:
        if security_controller:
            status = security_controller.get_system_status()
            return jsonify({'success': True, 'status': status})
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"获取系统状态失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/reset_counters', methods=['POST'])
def reset_counters():
    """重置计数器API"""
    try:
        if security_controller:
            success = security_controller.reset_counters()
            if success:
                return jsonify({'success': True, 'message': '计数器已重置'})
            else:
                return jsonify({'error': '重置计数器失败'}), 500
        else:
            return jsonify({'error': '安全系统未初始化'}), 500
    except Exception as e:
        app.logger.error(f"重置计数器失败: {e}")
        return jsonify({'error': str(e)}), 500

# WebSocket事件处理
@socketio.on('connect')
def handle_connect():
    """客户端连接事件"""
    app.logger.info('客户端已连接')
    emit('status', {'message': '已连接到安全系统'})

@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开连接事件"""
    app.logger.info('客户端已断开连接')

def broadcast_status():
    """广播系统状态"""
    while True:
        try:
            if security_controller:
                status = security_controller.get_system_status()
                socketio.emit('status_update', status)
            time.sleep(3)  # 每3秒广播一次状态（加快更新频率）
        except Exception as e:
            app.logger.error(f"广播状态失败: {e}")
            time.sleep(3)

# 启动状态广播线程
def start_status_broadcast():
    """启动状态广播线程"""
    broadcast_thread = threading.Thread(target=broadcast_status, daemon=True)
    broadcast_thread.start()

if __name__ == '__main__':
    # 初始化安全系统
    if init_security_system():
        # 启动状态广播
        start_status_broadcast()
        
        # 启动Flask应用
        app.logger.info(f"启动安全防护系统 - http://{Config.WEB_HOST}:{Config.WEB_PORT}")
        socketio.run(
            app,
            host=Config.WEB_HOST,  # 注意：配置文件用的是 WEB_HOST
            port=Config.WEB_PORT,  # 注意：配置文件用的是 WEB_PORT
            debug=Config.DEBUG,
            allow_unsafe_werkzeug=True  # 添加这行
        )
    else:
        app.logger.error("安全系统启动失败")
        sys.exit(1)
