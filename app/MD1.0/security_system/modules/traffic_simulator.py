# 网络流量数据模拟器
import pandas as pd
import numpy as np
import threading
import time
import logging
import os
from typing import Callable, Optional
from config import Config

class TrafficSimulator:
    """网络流量数据模拟器 - 从CSV数据集中模拟实时流量"""
    
    def __init__(self, csv_path: str = None, callback: Callable = None):
        """
        初始化流量模拟器

        Args:
            csv_path: CSV数据集路径
            callback: 数据回调函数，接收模拟的流量数据
        """
        self.csv_path = csv_path or r"C:\Users\Aron\Desktop\Tuesday-WorkingHours.pcap_ISCX.csv"
        self.callback = callback
        self.logger = logging.getLogger(__name__)
        
        # 模拟器状态
        self.is_running = False
        self.simulation_thread = None
        self.data = None
        self.current_index = 0
        
        # 配置参数
        self.interval = 1.0  # 发送间隔（秒）
        self.batch_size = 10  # 每次发送的流量条数
        self.attack_probability = 0.3  # 攻击流量概率
        
        # 加载数据
        self.load_dataset()
    
    def load_dataset(self):
        """加载CSV数据集（分批加载以节省内存）"""
        try:
            if os.path.exists(self.csv_path):
                self.logger.info(f"正在分批加载数据集: {self.csv_path}")

                # 首先读取一小部分来获取列信息和数据统计
                sample_data = pd.read_csv(self.csv_path, encoding='latin1', nrows=1000)
                sample_data.columns = sample_data.columns.str.strip()

                self.logger.info(f"数据集列数: {len(sample_data.columns)}")
                self.logger.info(f"攻击类型: {sample_data['Label'].unique()}")

                # 分批读取并采样数据（只保留一部分数据以节省内存）
                chunk_size = 10000
                sampled_data = []
                total_rows = 0

                for chunk in pd.read_csv(self.csv_path, encoding='latin1', chunksize=chunk_size):
                    chunk.columns = chunk.columns.str.strip()
                    total_rows += len(chunk)

                    # 从每个chunk中随机采样一部分数据
                    if len(chunk) > 100:
                        sample_size = min(100, len(chunk) // 10)  # 采样10%或最多100条
                        chunk_sample = chunk.sample(n=sample_size)
                        sampled_data.append(chunk_sample)

                    # 限制总采样数量以控制内存使用
                    if len(sampled_data) > 50:  # 最多50个chunk的采样
                        break

                # 合并采样数据
                if sampled_data:
                    self.data = pd.concat(sampled_data, ignore_index=True)

                    # 数据预处理
                    self.data = self.data.ffill().bfill()

                    # 解析时间戳
                    if 'Timestamp' in self.data.columns:
                        self.data['Timestamp'] = pd.to_datetime(self.data['Timestamp'], errors='coerce')

                    # 分离攻击和正常流量
                    self.normal_traffic = self.data[self.data['Label'] == 'BENIGN'].copy()
                    self.attack_traffic = self.data[self.data['Label'] != 'BENIGN'].copy()

                    self.logger.info(f"数据集采样加载成功:")
                    self.logger.info(f"  原始总流量: ~{total_rows} 条")
                    self.logger.info(f"  采样流量: {len(self.data)} 条")
                    self.logger.info(f"  正常流量: {len(self.normal_traffic)} 条")
                    self.logger.info(f"  攻击流量: {len(self.attack_traffic)} 条")
                    self.logger.info(f"  攻击类型: {self.data['Label'].unique()}")
                else:
                    raise Exception("无法采样数据")

            else:
                self.logger.warning(f"数据集文件不存在: {self.csv_path}")
                self.create_mock_data()

        except Exception as e:
            self.logger.error(f"加载数据集失败: {e}")
            self.create_mock_data()
    
    def create_mock_data(self):
        """创建模拟数据（当真实数据集不可用时）"""
        self.logger.info("创建模拟网络流量数据")
        
        # 生成模拟数据
        n_samples = 1000
        
        # 基础IP池
        source_ips = [f"192.168.1.{i}" for i in range(1, 51)]
        target_ips = [f"10.0.0.{i}" for i in range(1, 21)]
        
        # 生成模拟流量
        mock_data = []
        for i in range(n_samples):
            # 随机选择攻击类型
            if np.random.random() < 0.7:
                label = 'BENIGN'
                flow_duration = np.random.normal(5000, 1000)
                total_fwd_packets = np.random.poisson(10)
                total_bwd_packets = np.random.poisson(8)
            elif np.random.random() < 0.5:
                label = 'DDoS'
                flow_duration = np.random.normal(100, 50)
                total_fwd_packets = np.random.poisson(100)
                total_bwd_packets = np.random.poisson(5)
            else:
                label = 'PortScan'
                flow_duration = np.random.normal(50, 20)
                total_fwd_packets = np.random.poisson(5)
                total_bwd_packets = np.random.poisson(1)
            
            record = {
                'Flow ID': f"flow_{i}",
                'Source IP': np.random.choice(source_ips),
                'Source Port': np.random.randint(1024, 65535),
                'Destination IP': np.random.choice(target_ips),
                'Destination Port': np.random.choice([80, 443, 22, 21, 25, 53]),
                'Protocol': np.random.choice([6, 17]),  # TCP, UDP
                'Timestamp': pd.Timestamp.now() + pd.Timedelta(seconds=i),
                'Flow Duration': max(1, flow_duration),
                'Total Fwd Packets': max(0, total_fwd_packets),
                'Total Backward Packets': max(0, total_bwd_packets),
                'Total Length of Fwd Packets': max(0, total_fwd_packets * np.random.normal(500, 200)),
                'Total Length of Bwd Packets': max(0, total_bwd_packets * np.random.normal(300, 150)),
                'Fwd Packet Length Max': np.random.normal(1500, 300),
                'Fwd Packet Length Min': np.random.normal(64, 20),
                'Fwd Packet Length Mean': np.random.normal(500, 200),
                'Fwd Packet Length Std': np.random.normal(200, 100),
                'Bwd Packet Length Max': np.random.normal(1500, 300),
                'Bwd Packet Length Min': np.random.normal(64, 20),
                'Bwd Packet Length Mean': np.random.normal(300, 150),
                'Bwd Packet Length Std': np.random.normal(150, 75),
                'Flow Bytes/s': np.random.normal(10000, 5000),
                'Flow Packets/s': np.random.normal(10, 5),
                'Flow IAT Mean': np.random.normal(1000, 500),
                'Flow IAT Std': np.random.normal(500, 250),
                'Flow IAT Max': np.random.normal(5000, 2000),
                'Flow IAT Min': np.random.normal(1, 0.5),
                'Fwd IAT Total': np.random.normal(5000, 2000),
                'Fwd IAT Mean': np.random.normal(1000, 500),
                'Fwd IAT Std': np.random.normal(500, 250),
                'Fwd IAT Max': np.random.normal(5000, 2000),
                'Fwd IAT Min': np.random.normal(1, 0.5),
                'Bwd IAT Total': np.random.normal(3000, 1500),
                'Bwd IAT Mean': np.random.normal(800, 400),
                'Bwd IAT Std': np.random.normal(400, 200),
                'Bwd IAT Max': np.random.normal(3000, 1500),
                'Bwd IAT Min': np.random.normal(1, 0.5),
                'Label': label
            }
            
            # 添加更多特征以达到80个边特征
            for j in range(50):
                record[f'Feature_{j}'] = np.random.normal(0, 1)
            
            mock_data.append(record)
        
        self.data = pd.DataFrame(mock_data)
        
        # 分离攻击和正常流量
        self.normal_traffic = self.data[self.data['Label'] == 'BENIGN'].copy()
        self.attack_traffic = self.data[self.data['Label'] != 'BENIGN'].copy()
        
        self.logger.info(f"模拟数据创建完成: {len(self.data)} 条记录")
    
    def generate_traffic_batch(self) -> pd.DataFrame:
        """生成一批流量数据"""
        if self.data is None or len(self.data) == 0:
            return pd.DataFrame()
        
        # 决定是否包含攻击流量
        include_attack = np.random.random() < self.attack_probability
        
        if include_attack and len(self.attack_traffic) > 0:
            # 混合攻击和正常流量
            attack_count = np.random.randint(1, max(2, self.batch_size // 2))
            normal_count = self.batch_size - attack_count
            
            # 随机选择攻击流量
            if len(self.attack_traffic) >= attack_count:
                attack_sample = self.attack_traffic.sample(n=attack_count)
            else:
                attack_sample = self.attack_traffic.sample(n=len(self.attack_traffic))
            
            # 随机选择正常流量
            if len(self.normal_traffic) >= normal_count:
                normal_sample = self.normal_traffic.sample(n=normal_count)
            else:
                normal_sample = self.normal_traffic.sample(n=len(self.normal_traffic))
            
            # 合并数据
            batch_data = pd.concat([attack_sample, normal_sample], ignore_index=True)
            
        else:
            # 只包含正常流量
            if len(self.normal_traffic) >= self.batch_size:
                batch_data = self.normal_traffic.sample(n=self.batch_size)
            else:
                batch_data = self.normal_traffic.sample(n=len(self.normal_traffic))
        
        # 更新时间戳为当前时间
        batch_data = batch_data.copy()
        batch_data['Timestamp'] = pd.Timestamp.now()
        
        return batch_data
    
    def simulation_loop(self):
        """模拟器主循环"""
        self.logger.info("流量模拟器开始运行")
        
        while self.is_running:
            try:
                # 生成流量批次
                traffic_batch = self.generate_traffic_batch()
                
                if not traffic_batch.empty and self.callback:
                    # 发送数据到回调函数
                    self.callback(traffic_batch)
                    
                    attack_count = len(traffic_batch[traffic_batch['Label'] != 'BENIGN'])
                    self.logger.info(f"发送流量批次: {len(traffic_batch)} 条记录 (攻击: {attack_count})")
                
                # 等待下一次发送
                time.sleep(self.interval)
                
            except Exception as e:
                self.logger.error(f"流量模拟器运行错误: {e}")
                time.sleep(1)
        
        self.logger.info("流量模拟器已停止")
    
    def start_simulation(self):
        """启动流量模拟"""
        if self.is_running:
            self.logger.warning("流量模拟器已在运行")
            return
        
        self.is_running = True
        self.simulation_thread = threading.Thread(target=self.simulation_loop, daemon=True)
        self.simulation_thread.start()
        self.logger.info("流量模拟器已启动")
    
    def stop_simulation(self):
        """停止流量模拟"""
        if not self.is_running:
            self.logger.warning("流量模拟器未在运行")
            return
        
        self.is_running = False
        if self.simulation_thread:
            self.simulation_thread.join(timeout=5)
        self.logger.info("流量模拟器已停止")
    
    def set_attack_probability(self, probability: float):
        """设置攻击流量概率"""
        self.attack_probability = max(0.0, min(1.0, probability))
        self.logger.info(f"攻击流量概率设置为: {self.attack_probability:.2f}")
    
    def set_interval(self, interval: float):
        """设置发送间隔"""
        self.interval = max(0.1, interval)
        self.logger.info(f"发送间隔设置为: {self.interval} 秒")
    
    def set_batch_size(self, batch_size: int):
        """设置批次大小"""
        self.batch_size = max(1, batch_size)
        self.logger.info(f"批次大小设置为: {self.batch_size}")
    
    def get_status(self) -> dict:
        """获取模拟器状态"""
        return {
            'is_running': self.is_running,
            'csv_path': self.csv_path,
            'total_records': len(self.data) if self.data is not None else 0,
            'normal_records': len(self.normal_traffic) if hasattr(self, 'normal_traffic') else 0,
            'attack_records': len(self.attack_traffic) if hasattr(self, 'attack_traffic') else 0,
            'interval': self.interval,
            'batch_size': self.batch_size,
            'attack_probability': self.attack_probability
        }
