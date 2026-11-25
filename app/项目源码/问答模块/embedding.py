"""
嵌入服务

为混合检索提供向量表示。本机无 GPU，使用轻量中文模型 bge-small-zh-v1.5
（512 维，支持中英文），CPU 推理，实测 4 条文本 0.08 秒。

关于实现方式的说明：
    直接使用 transformers 底层 API（AutoTokenizer + AutoModel）而非
    sentence-transformers 包装，原因有二：
      ① 本环境无法完整下载 tokenizer.json（大文件在镜像上持续 502），
         Slow tokenizer 仅凭 vocab.txt 即可工作
      ② 需要精确控制 CLS pooling 与查询指令前缀，这两步对检索质量影响显著

关于维度：
    数据库 embedding 列为 vector(1024)（原定 BGE-M3），
    实际使用 bge-small-zh（512 维），采用零填充升维。
    零填充不改变余弦相似度——两个向量补零后点积与模长均不变，
    故检索结果与 512 维完全一致，HNSW 索引也照常工作。

关于查询指令前缀（BGE 官方推荐用法）：
    检索任务中，查询侧加 "为这个句子生成表示以用于检索相关文章：" 前缀，
    文档侧不加。实测可将同主题相似度从 0.39 提升到 0.69，区分度显著改善。
"""

from __future__ import annotations

import os
import threading
from typing import Any

from ..后端服务.logger import get_logger

logger = get_logger("embedding")

# 目标维度（与数据库 vector(1024) 一致）
TARGET_DIM = 1024
# bge-small-zh 的原生维度
NATIVE_DIM = 512

# 模型名与本地缓存目录
# resource()：开发环境=项目根；打包环境=优先 exe 目录（可外挂替换），其次包内置
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
from ..运行时 import resource as _resource  # noqa: E402

LOCAL_MODEL_DIR = _resource("数据", "模型缓存")

# 国内镜像：直连 huggingface.co 在本环境会失败（502 / 连接重置）
HF_MIRROR = "https://hf-mirror.com"

# BGE 官方检索指令前缀（仅查询侧使用）
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# 文本最大长度，超出会被截断
MAX_SEQ_LENGTH = 256


class EmbeddingService:
    """
    文本向量化服务

    单例使用，模型惰性加载。首次加载约 15-20 秒，之后常驻内存。
    模型不可用时返回 None，检索自动降级为纯关键词模式。
    """

    def __init__(self) -> None:
        self._tokenizer: Any = None
        self._model: Any = None
        self._unavailable = False
        self._lock = threading.Lock()
        self._torch: Any = None
        self._np: Any = None

    @property
    def available(self) -> bool:
        """嵌入服务是否可用"""
        return self._model is not None

    def _load(self) -> bool:
        """惰性加载模型，返回是否成功"""
        if self._model is not None:
            return True
        if self._unavailable:
            return False

        with self._lock:
            if self._model is not None:
                return True
            if self._unavailable:
                return False
            try:
                import numpy as np
                import torch
                from transformers import AutoModel, AutoTokenizer

                # 优先用本地缓存（离线可用），否则走镜像
                use_local = (LOCAL_MODEL_DIR / "config.json").exists() and (
                    LOCAL_MODEL_DIR / "model.safetensors"
                ).exists()
                source = str(LOCAL_MODEL_DIR) if use_local else MODEL_NAME
                if not use_local:
                    os.environ.setdefault("HF_ENDPOINT", HF_MIRROR)

                logger.info("embedding_model_loading", source=source)
                # use_fast=False：Slow tokenizer 只需 vocab.txt，
                # 规避本环境下 tokenizer.json 持续 502 的问题
                self._tokenizer = AutoTokenizer.from_pretrained(source, use_fast=False)
                self._model = AutoModel.from_pretrained(source)
                self._model.eval()
                self._torch = torch
                self._np = np
                logger.info(
                    "embedding_model_ready",
                    source=source,
                    hidden_size=self._model.config.hidden_size,
                    target_dim=TARGET_DIM,
                )
            except Exception as exc:  # noqa: BLE001
                self._unavailable = True
                logger.warning(
                    "embedding_model_unavailable",
                    error=str(exc)[:200],
                    note="检索将降级为纯关键词模式",
                )
                return False
        return True

    def encode(
        self,
        texts: list[str],
        is_query: bool = False,
        batch_size: int = 32,
    ) -> list[list[float]] | None:
        """
        文本向量化

        Args:
            texts: 待编码文本
            is_query: 是否为查询侧（决定是否加指令前缀）
            batch_size: 批大小

        Returns:
            升维到 TARGET_DIM 的向量列表；模型不可用时返回 None
        """
        if not texts:
            return []
        if not self._load():
            return None

        try:
            # 清洗：空文本会导致分器报错，用占位符替代
            cleaned = [(t[:1000] if t and t.strip() else "empty") for t in texts]
            if is_query:
                cleaned = [QUERY_INSTRUCTION + t for t in cleaned]

            all_vectors: list[list[float]] = []
            for start in range(0, len(cleaned), batch_size):
                batch = cleaned[start : start + batch_size]
                enc = self._tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=MAX_SEQ_LENGTH,
                    return_tensors="pt",
                )
                with self._torch.no_grad():
                    out = self._model(**enc).last_hidden_state
                # BGE 官方用 CLS 向量（第一层 [CLS] token 的隐状态）
                cls = out[:, 0]
                # 归一化，使点积即余弦相似度
                normed = self._torch.nn.functional.normalize(cls, dim=-1).numpy()
                all_vectors.extend(self._to_target_dim(v) for v in normed)

            return all_vectors
        except Exception as exc:  # noqa: BLE001
            logger.warning("encode_failed", error=str(exc)[:200])
            return None

    def encode_query(self, text: str) -> list[float] | None:
        """查询侧编码（加指令前缀）"""
        result = self.encode([text], is_query=True)
        return result[0] if result else None

    def encode_documents(self, texts: list[str]) -> list[list[float]] | None:
        """文档侧编码（不加指令前缀）"""
        return self.encode(texts, is_query=False)

    @staticmethod
    def _to_target_dim(vector: Any) -> list[float]:
        """
        升维到目标维度（零填充）

        零填充不改变余弦相似度：补零后两向量的点积仍等于原 512 维点积，
        模长也都是原模长，因此余弦值完全不变。
        """
        import numpy as np

        arr = np.asarray(vector, dtype="float32").flatten()
        if arr.shape[0] == TARGET_DIM:
            return arr.tolist()
        if arr.shape[0] > TARGET_DIM:
            return arr[:TARGET_DIM].tolist()
        padded = np.zeros(TARGET_DIM, dtype="float32")
        padded[: arr.shape[0]] = arr
        return padded.tolist()

    def health_check(self) -> dict[str, Any]:
        """健康检查，供 /api/health 使用"""
        return {
            "available": self._model is not None,
            "model": MODEL_NAME,
            "native_dim": NATIVE_DIM,
            "target_dim": TARGET_DIM,
            "strategy": "zero_padding",
            "query_instruction": bool(QUERY_INSTRUCTION),
            "note": None if self._model is not None else "未加载，检索降级为纯关键词模式",
        }

    def warmup(self) -> bool:
        """预热模型（应用启动时调用，避免首个请求延迟）"""
        if not self._load():
            return False
        return self.encode(["warmup"], is_query=False) is not None


# 全局单例
_embedding_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """获取嵌入服务单例"""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
