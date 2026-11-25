# 数据库模型定义
import mysql.connector
from mysql.connector import pooling
from datetime import datetime
import logging
from config import Config

class DatabaseManager:
    def __init__(self):
        self.config = {
            'host': Config.MYSQL_HOST,
            'port': Config.MYSQL_PORT,
            'user': Config.MYSQL_USER,
            'password': Config.MYSQL_PASSWORD,
            'database': Config.MYSQL_DATABASE,
            'pool_name': 'security_pool',
            'pool_size': 10,
            'pool_reset_session': True,
            'autocommit': True
        }
        self.connection_pool = None
        self.logger = logging.getLogger(__name__)
        self.init_database()
    
    def init_database(self):
        """初始化数据库连接池和表结构"""
        try:
            # 创建连接池
            self.connection_pool = pooling.MySQLConnectionPool(**self.config)

            # 创建表结构
            self.create_tables()
            self.logger.info("数据库初始化成功")
        except Exception as e:
            self.logger.error(f"数据库初始化失败: {e}")
            # 不抛出异常，允许系统在没有数据库的情况下运行（仅用于演示）
            print(f"警告: 数据库连接失败，系统将在模拟模式下运行: {e}")
    
    def get_connection(self):
        """获取数据库连接"""
        return self.connection_pool.get_connection()
    
    def create_tables(self):
        """创建数据库表结构"""
        connection = self.get_connection()
        cursor = connection.cursor()
        
        try:
            # 检查表是否存在以及结构是否正确
            cursor.execute("SHOW TABLES LIKE 'images'")
            table_exists = cursor.fetchone() is not None

            if table_exists:
                # 检查是否有旧的字段结构
                cursor.execute("SHOW COLUMNS FROM images LIKE 'original_path'")
                has_old_structure = cursor.fetchone() is not None

                # 检查encryption_key_hash字段长度是否足够
                cursor.execute("SHOW COLUMNS FROM images WHERE Field = 'encryption_key_hash'")
                key_hash_column = cursor.fetchone()
                needs_key_hash_update = False
                if key_hash_column:
                    column_type = key_hash_column[1]  # Type字段
                    if 'varchar(128)' in column_type.lower():
                        needs_key_hash_update = True
                        self.logger.info("检测到encryption_key_hash字段长度不足，需要更新...")

                if has_old_structure or needs_key_hash_update:
                    self.logger.info("检测到需要更新的表结构，正在重新创建...")
                    # 删除旧表
                    cursor.execute("DROP TABLE images")
                    self.logger.info("已删除旧的images表")

            # 创建新的图像存储表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS images (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    image_path VARCHAR(500) NOT NULL,
                    is_encrypted BOOLEAN DEFAULT FALSE,
                    encryption_key_hash TEXT,
                    upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    file_size BIGINT,
                    file_type VARCHAR(50),
                    INDEX idx_filename (filename),
                    INDEX idx_encrypted (is_encrypted)
                )
            """)
            
            # 创建安全日志表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS security_logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    source_ip VARCHAR(45) NOT NULL,
                    target_ip VARCHAR(45) NOT NULL,
                    attack_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    attack_type VARCHAR(50) NOT NULL,
                    threat_level VARCHAR(20) NOT NULL,
                    confidence_score FLOAT,
                    action_taken VARCHAR(100),
                    additional_info TEXT,
                    INDEX idx_source_ip (source_ip),
                    INDEX idx_attack_time (attack_time),
                    INDEX idx_threat_level (threat_level)
                )
            """)
            
            # 创建系统状态表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_status (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    status_key VARCHAR(50) UNIQUE NOT NULL,
                    status_value TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_status_key (status_key)
                )
            """)
            
            # 创建数据库锁定状态表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS database_locks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    table_name VARCHAR(100) NOT NULL,
                    lock_type VARCHAR(20) NOT NULL,
                    locked_by VARCHAR(100),
                    lock_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    INDEX idx_table_name (table_name),
                    INDEX idx_active (is_active)
                )
            """)
            
            connection.commit()
            self.logger.info("数据库表创建成功")
            
        except Exception as e:
            connection.rollback()
            self.logger.error(f"创建数据库表失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()
    
    def insert_security_log(self, source_ip, target_ip, attack_type, threat_level, 
                           confidence_score=None, action_taken=None, additional_info=None):
        """插入安全日志"""
        connection = self.get_connection()
        cursor = connection.cursor()
        
        try:
            query = """
                INSERT INTO security_logs 
                (source_ip, target_ip, attack_type, threat_level, confidence_score, action_taken, additional_info)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(query, (source_ip, target_ip, attack_type, threat_level, 
                                 confidence_score, action_taken, additional_info))
            connection.commit()
            return cursor.lastrowid
        except Exception as e:
            connection.rollback()
            self.logger.error(f"插入安全日志失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()
    
    def get_recent_logs(self, limit=50):
        """获取最近的安全日志"""
        connection = self.get_connection()
        cursor = connection.cursor(dictionary=True)
        
        try:
            query = """
                SELECT * FROM security_logs 
                ORDER BY attack_time DESC 
                LIMIT %s
            """
            cursor.execute(query, (limit,))
            return cursor.fetchall()
        except Exception as e:
            self.logger.error(f"获取安全日志失败: {e}")
            return []
        finally:
            cursor.close()
            connection.close()
    
    def insert_image_record(self, filename, image_path, file_size=None, file_type=None):
        """插入图像记录"""
        connection = self.get_connection()
        cursor = connection.cursor()

        try:
            query = """
                INSERT INTO images (filename, image_path, file_size, file_type)
                VALUES (%s, %s, %s, %s)
            """
            cursor.execute(query, (filename, image_path, file_size, file_type))
            connection.commit()
            return cursor.lastrowid
        except Exception as e:
            connection.rollback()
            self.logger.error(f"插入图像记录失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()
    
    def update_image_encryption(self, image_id, encrypted_path, encryption_key_hash):
        """更新图像加密状态和密钥哈希"""
        connection = self.get_connection()
        cursor = connection.cursor()

        try:
            query = """
                UPDATE images
                SET image_path = %s, is_encrypted = TRUE, encryption_key_hash = %s
                WHERE id = %s
            """
            cursor.execute(query, (encrypted_path, encryption_key_hash, image_id))
            connection.commit()
            self.logger.debug(f"更新图像加密状态成功: image_id={image_id}, path={encrypted_path}, key_hash={encryption_key_hash[:16]}...")
        except Exception as e:
            connection.rollback()
            self.logger.error(f"更新图像加密状态失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()

    def update_image_encryption_status(self, image_id, is_encrypted, new_image_path):
        """更新图像加密状态（通用方法）"""
        connection = self.get_connection()
        cursor = connection.cursor()

        try:
            query = """
                UPDATE images
                SET is_encrypted = %s, image_path = %s
                WHERE id = %s
            """
            cursor.execute(query, (is_encrypted, new_image_path, image_id))
            connection.commit()
            self.logger.info(f"图像状态更新成功: ID {image_id}, 加密: {is_encrypted}, 路径: {new_image_path}")

        except Exception as e:
            connection.rollback()
            self.logger.error(f"更新图像状态失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()

    def update_image_decryption(self, image_id, decrypted_path):
        """更新图像解密状态（清除加密标志和密钥哈希）"""
        connection = self.get_connection()
        cursor = connection.cursor()

        try:
            query = """
                UPDATE images
                SET is_encrypted = FALSE, image_path = %s, encryption_key_hash = NULL
                WHERE id = %s
            """
            cursor.execute(query, (decrypted_path, image_id))
            connection.commit()
            self.logger.info(f"图像解密状态更新成功: ID {image_id}, 路径: {decrypted_path}")

        except Exception as e:
            connection.rollback()
            self.logger.error(f"更新图像解密状态失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()
    
    def clear_all_images(self):
        """清空所有图像记录"""
        connection = self.get_connection()
        cursor = connection.cursor()

        try:
            # 清空图像表
            query = "DELETE FROM images"
            cursor.execute(query)
            connection.commit()

            # 重置自增ID
            reset_query = "ALTER TABLE images AUTO_INCREMENT = 1"
            cursor.execute(reset_query)
            connection.commit()

            self.logger.info("已清空所有图像记录")

        except Exception as e:
            connection.rollback()
            self.logger.error(f"清空图像记录失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()

    def get_all_images(self):
        """获取所有图像记录"""
        connection = self.get_connection()
        cursor = connection.cursor(dictionary=True)

        try:
            query = "SELECT * FROM images ORDER BY upload_time DESC"
            cursor.execute(query)
            return cursor.fetchall()
        except Exception as e:
            self.logger.error(f"获取图像记录失败: {e}")
            return []
        finally:
            cursor.close()
            connection.close()
    
    def lock_table(self, table_name, lock_type='WRITE'):
        """锁定数据库表"""
        connection = self.get_connection()
        cursor = connection.cursor()
        
        try:
            # 记录锁定状态
            query = """
                INSERT INTO database_locks (table_name, lock_type, locked_by)
                VALUES (%s, %s, %s)
            """
            cursor.execute(query, (table_name, lock_type, 'SECURITY_SYSTEM'))
            
            # 执行表锁定
            cursor.execute(f"LOCK TABLES {table_name} {lock_type}")
            connection.commit()
            self.logger.info(f"表 {table_name} 已锁定")
        except Exception as e:
            connection.rollback()
            self.logger.error(f"锁定表失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()
    
    def unlock_tables(self):
        """解锁所有表"""
        connection = self.get_connection()
        cursor = connection.cursor()
        
        try:
            cursor.execute("UNLOCK TABLES")
            
            # 更新锁定状态
            query = "UPDATE database_locks SET is_active = FALSE WHERE is_active = TRUE"
            cursor.execute(query)
            connection.commit()
            self.logger.info("所有表已解锁")
        except Exception as e:
            connection.rollback()
            self.logger.error(f"解锁表失败: {e}")
            raise
        finally:
            cursor.close()
            connection.close()

    def get_image_records(self) -> list:
        """获取所有图像记录"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor(dictionary=True)

            query = """
                SELECT id, filename, original_path, encrypted_path, file_size,
                       file_type, is_encrypted, created_at, encrypted_at
                FROM images
                ORDER BY created_at DESC
            """

            cursor.execute(query)
            records = cursor.fetchall()

            return records

        except Exception as e:
            self.logger.error(f"获取图像记录失败: {e}")
            return []
        finally:
            if 'connection' in locals():
                cursor.close()
                connection.close()
