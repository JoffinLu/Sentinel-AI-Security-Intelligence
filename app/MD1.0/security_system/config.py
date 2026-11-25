# 系统配置文件
import os

class Config:
    # 数据库配置
    MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
    MYSQL_USER = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', 'wyn987654')
    MYSQL_DATABASE = os.getenv('MYSQL_DATABASE', 'security_system')
    
    # ROEN模型配置
    ROEN_MODEL_PATH = r'C:\Users\Aron\Desktop\MD1.0\models-new\2017\model_epoch_150.pth'
    ROEN_NODE_CHANNELS = 1
    ROEN_EDGE_CHANNELS = 80
    ROEN_HIDDEN_CHANNELS_NODE = 128
    ROEN_HIDDEN_CHANNELS_EDGE = 128
    ROEN_MLP_HIDDEN_CHANNELS = 128
    ROEN_NUM_CLASSES = 3
    
    # 加密配置
    ENCRYPTION_KEY_LENGTH = 128
    IMAGE_STORAGE_PATH = 'static/images/'
    ENCRYPTED_STORAGE_PATH = 'static/encrypted/'
    
    # 系统配置
    SECRET_KEY = os.getenv('SECRET_KEY', '123456')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    
    # 威胁等级定义
    THREAT_LEVELS = {
        0: 'NORMAL',      # 正常流量
        1: 'SUSPICIOUS',  # 可疑流量
        2: 'MALICIOUS'    # 恶意攻击
    }
    
    # 攻击类型定义
    ATTACK_TYPES = {
        0: 'BENIGN',
        1: 'DDoS',
        2: 'PortScan'
    }
    
    # 监控配置
    MONITORING_INTERVAL = 2  # 秒 (加快检测频率)
    MAX_LOG_ENTRIES = 1000
    
    # Web配置
    WEB_HOST = '0.0.0.0'
    WEB_PORT = 5001
