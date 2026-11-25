"""
全局配置管理

从环境变量或 .env 文件加载，使用 Pydantic Settings 做类型校验与默认值管理。
所有模块统一从此处获取配置，避免硬编码。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..运行时 import app_root

# 应用根目录（.env 所在位置）。
# 开发环境 = 项目根；打包环境 = exe 所在目录（用户可编辑 .env）
PROJECT_ROOT = app_root()


class Settings(BaseSettings):
    """应用全局配置"""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env.example", PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- LLM ----------
    llm_api_base: str = "https://api.openveer.com/v1"
    llm_api_key: str = ""
    llm_model: str = "DeepSeek-v4-flash-Free"
    llm_reasoning_model: str = "DeepSeek-R1-Free"
    llm_economy_model: str = "Qwen3-8-Free"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3
    llm_temperature: float = 0.2
    llm_fallback_base: str = "http://localhost:11434/v1"
    llm_fallback_model: str = "qwen2.5:7b"

    # ---------- 数据库 ----------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ai_security_intel"
    postgres_user: str = "aiintel"
    postgres_password: str = "aiintel2026"
    postgres_pool_size: int = 20
    postgres_max_overflow: int = 10
    embedding_dim: int = 1024

    # ---------- Redis ----------
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # ---------- Neo4j ----------
    neo4j_host: str = "localhost"
    neo4j_port: int = 17474
    neo4j_bolt_port: int = 17687
    neo4j_user: str = "neo4j"
    neo4j_password: str = "aiintel2026"

    # ---------- 采集器 ----------
    nvd_api_key: str = ""
    github_token: str = ""  # 可选，提升 GitHub Advisory 限流至 5000 次/小时
    collector_timeout_seconds: int = 90
    collector_max_retries: int = 3
    collector_rate_limit_rps: float = 2.0
    # 采集周期**不在此配置**：默认值见各采集器的 default_interval，
    # 运行时可在监控看板「周期」列调整，自定义值持久化在
    # scheduler_settings 表。
    # 此处原有一批 schedule_*_interval 配置，全项目零引用
    # （从未被代码读取），已于 2026-10-14 删除 ——
    # 留着只会让人以为改 .env 就能调周期，实际毫无效果。

    # ---------- AI 相关性过滤 ----------
    ai_relevance_embedding_threshold: float = 0.62
    enable_llm_review: bool = True

    # ---------- 服务 ----------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    log_format: str = "json"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    allow_degraded_start: bool = True

    @field_validator("cors_origins")
    @classmethod
    def _split_origins(cls, v: str) -> list[str]:
        """把逗号分隔的 CORS 配置解析为列表"""
        return [item.strip() for item in v.split(",") if item.strip()]

    @property
    def database_url(self) -> str:
        """异步数据库连接串"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """同步数据库连接串（迁移脚本使用）"""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Redis 连接串"""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def neo4j_uri(self) -> str:
        """Neo4j Bolt 连接串"""
        return f"bolt://{self.neo4j_host}:{self.neo4j_bolt_port}"

    @property
    def has_llm_key(self) -> bool:
        """是否配置了主 LLM 的 API Key"""
        return bool(self.llm_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
