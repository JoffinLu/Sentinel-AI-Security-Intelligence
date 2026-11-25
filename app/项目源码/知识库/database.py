"""
数据库连接与会话管理

同时提供异步引擎（应用运行时）、同步引擎（迁移脚本），
以及 pgvector 扩展与索引的自动初始化。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from ..后端服务.config import get_settings
from ..后端服务.logger import get_logger
from .models import Base

logger = get_logger(__name__)

_settings = get_settings()

# 异步引擎（应用运行时）
_async_engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.postgres_pool_size,
    max_overflow=_settings.postgres_max_overflow,
    pool_pre_ping=True,  # 自动检测失效连接，提升稳定性
    pool_recycle=3600,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=_async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# 同步引擎（迁移与数据初始化脚本使用）
_sync_engine = create_engine(_settings.sync_database_url, pool_pre_ping=True, echo=False)
SyncSessionLocal = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """异步会话上下文，自动提交或回滚"""
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用"""
    async with get_session() as session:
        yield session


async def init_database() -> None:
    """
    初始化数据库结构

    创建扩展、建表、建索引。幂等设计，可重复执行。
    """
    async with _async_engine.begin() as conn:
        # pgvector 扩展是向量检索的前提
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # pg_trgm 支撑标题/描述的模糊全文检索
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        logger.info("extensions_ready")

    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("tables_created", tables=len(Base.metadata.tables))

    await _create_vector_index()
async def _create_vector_index() -> None:
    """
    创建向量索引

    优先使用 HNSW（更快、内存可控），失败则回退到 IVFFlat。

    手册 4.6：DDL 回退**必须使用独立事务**。
    PostgreSQL 中一条语句失败后当前事务立即进入 aborted 状态，
    后续语句只会报 "current transaction is aborted"——
    在同一个 begin() 里 try/except 后继续建 IVFFlat，
    等于回退逻辑从未真正执行过。
    """
    try:
        async with _async_engine.begin() as conn:
            # HNSW 索引：m=16, ef_construction=64 是性能与内存的平衡点
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_vuln_embedding_hnsw "
                    "ON vulnerabilities USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_paper_embedding_hnsw "
                    "ON papers USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
        logger.info("vector_index_ready", index_type="hnsw")
    except Exception as exc:  # noqa: BLE001
        logger.warning("hnsw_failed_fallback_to_ivfflat", error=str(exc)[:200])
        try:
            # 独立事务：上面那个事务已 aborted，必须重开一个干净的
            async with _async_engine.begin() as conn:
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_vuln_embedding_ivf "
                        "ON vulnerabilities USING ivfflat (embedding vector_cosine_ops) "
                        "WITH (lists = 100)"
                    )
                )
            logger.info("vector_index_ready", index_type="ivfflat")
        except Exception as exc2:  # noqa: BLE001
                logger.warning("ivfflat_failed", error=str(exc2)[:200])


async def health_check() -> dict[str, object]:
    """数据库健康探测，供 /api/health 使用"""
    try:
        async with _async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            version = await conn.execute(text("SHOW server_version"))
            return {
                "available": True,
                "version": version.scalar(),
                "pool_size": _settings.postgres_pool_size,
            }
    except Exception as exc:  # noqa: BLE001
        # 手册 4.1：返回给浏览器的信息必须脱敏。
        # SQLAlchemy 异常文本里常含连接串、主机名与端口，
        # 原样返回等于把内网拓扑交出去——只回固定文案 + 异常类型名，
        # 完整堆栈进日志。
        logger.warning("database_health_failed", error_type=type(exc).__name__)
        return {
            "available": False,
            "error": "数据库探测超时或暂不可用",
            "error_type": type(exc).__name__,
        }


async def close_database() -> None:
    """关闭引擎，释放连接"""
    await _async_engine.dispose() 
    _sync_engine.dispose() 
    logger.info("database_disposed")
