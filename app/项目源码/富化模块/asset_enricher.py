"""
维度二：受影响资产

产出赛题要求的"受影响资产"富化，包含：
- 资产类型（组件 / 模型 / 插件 / 服务）
- 组件名与生态（PyPI / npm / HuggingFace / Maven 等）
- 受影响版本范围（精确到区间）
- 是否为 AI 组件（支撑 AI 供应链分析）
- 依赖关系推断（为知识图谱的多跳查询提供边）

版本范围处理是关键：不同数据源的表达方式不同
（NVD 用 versionStartIncluding，OSV 用 introduced/fixed 事件流），
需要统一为可比较的结构化表示。
"""

from __future__ import annotations

import re
from typing import Any

from ..数据采集.ai_classifier import AI_COMPONENT_NAMES
from .base_enricher import BaseEnricher, VulnContext

# 生态规范化映射（各源叫法不一）
_ECOSYSTEM_MAP = {
    "pip": "PyPI",
    "pypi": "PyPI",
    "npm": "npm",
    "go": "Go",
    "maven": "Maven",
    "nuget": "NuGet",
    "rubygems": "RubyGems",
    "cargo": "crates.io",
    "composer": "Packagist",
    "gem": "RubyGems",
    "huggingface": "HuggingFace",
    "docker": "容器镜像",
    "debian": "Debian",
    "ubuntu": "Ubuntu",
}

# PyPI 生态的 AI 组件（其余归为 npm）
_PYPI_PACKAGES = {
    "transformers", "torch", "pytorch", "tensorflow", "jax", "flax",
    "langchain", "langgraph", "llama-index", "llama_index", "autogen",
    "vllm", "sglang", "ollama", "llama.cpp", "llama-cpp", "diffusers",
    "accelerate", "datasets", "safetensors", "tokenizers", "sentence-transformers",
    "timm", "peft", "trl", "bitsandbytes", "openai", "anthropic", "haystack-ai",
    "chromadb", "qdrant-client", "faiss-cpu", "onnxruntime", "mlflow", "ray",
    "optuna", "gradio", "streamlit", "spacy", "nltk", "gensim", "sentencepiece",
    "huggingface-hub", "safetensors", "pillow", "numpy", "pandas", "comet-ml", "wandb",
}

# 资产类型识别特征
# 键为资产类型，值为特征词。匹配时用词边界，避免
# "modelcontextprotocol" 这类含 "model" 的专有名词被误判为"模型"。
_TYPE_HINTS: dict[str, list[str]] = {
    "模型": ["huggingface", "safetensors", "checkpoint", "model-weights", "权重"],
    "数据集": ["dataset", "corpus", "数据集"],
    "推理框架": ["vllm", "sglang", "triton", "onnxruntime", "tensorflow", "pytorch", "torch"],
    "应用框架": ["langchain", "llama-index", "llamaindex", "autogen", "haystack", "chainlit"],
    "智能体组件": ["mcp", "agent", "tool-server", "插件", "plugin"],
    "向量数据库": ["chroma", "qdrant", "faiss", "milvus", "weaviate", "pinecone"],
    "服务框架": ["gradio", "streamlit", "fastapi", "flask", "django", "express"],
}


def parse_cpe(cpe: str) -> dict[str, Any]:
    """
    解析 CPE 2.3 标识符

    格式：cpe:2.3:part:vendor:product:version:update:edition:language:sw_edition:target_sw:target_hw:other
    取前 5 段（part/vendor/product/version）即可满足资产识别需求。
    """
    if not cpe or not cpe.startswith("cpe:"):
        return {}
    parts = cpe.split(":")
    if len(parts) < 5:
        return {}
    return {
        "part": parts[2] if len(parts) > 2 else None,  # a=应用, o=操作系统
        "vendor": parts[3] if len(parts) > 3 else None,
        "product": parts[4] if len(parts) > 4 else None,
        "version": parts[5] if len(parts) > 5 and parts[5] != "*" else None,
    }


# AI 生态的强特征词——这些词本身不在组件词表中，
# 但出现在包名里几乎必然是 AI 相关（mcp、llm、gpt、agent 等）。
_AI_ECOSYSTEM_MARKERS = [
    "mcp", "modelcontextprotocol", "model-context-protocol", "llm", "llama", "gpt", "openai",
    "anthropic", "prompt", "embedding", "vector", "rag", "agent",
    "langchain", "transformer", "diffusion", "huggingface", "pytorch",
    "tensorflow", "vllm", "ollama", "chromadb", "pinecone", "qdrant",
    "milvus", "weaviate", "faiss", "gradio", "streamlit", "mlflow",
    "langgraph", "autogen", "haystack", "llamaindex", "comfyui",
]

def is_ai_component_name(product: str | None) -> bool:
    """
    判断组件名是否为 AI 组件

    不能只做精确匹配——生态里的包名常带作用域前缀或变体形式：
        @langchain/core        （npm 作用域包）
        @modelcontextprotocol/sdk
        chromadb                （库名 + 后缀）
        blender-mcp             （工具名 + mcp 后缀）
        vllm-core
    实测图谱构建时，精确匹配导致 64 个组件里只有 4 个被识别为 AI 组件，
    供应链传导推理因此失效。

    故这里做「分段包含」判断：把包名按分隔符切开，
    任一段命中 AI 组件词表即视为 AI 组件。
    """
    if not product:
        return False
    name = str(product).lower().strip()

    # 直接命中
    if name in AI_COMPONENT_NAMES:
        return True

    # 分段匹配：处理 @scope/name、name-core、name_core 等形态
    segments = re.split(r"[@/_.:-]+", name)
    for seg in segments:
        if seg and seg in AI_COMPONENT_NAMES:
            return True

    # 复合词匹配：如 "chromadb" 含 "chroma"、"blender-mcp" 含 "mcp"
    # 长度门槛设为 3——"mcp" 只有 3 字符但语义明确，
    # 若按 4 字符过滤会漏掉大量 MCP 相关组件。
    for known in AI_COMPONENT_NAMES:
        if len(known) >= 3 and known in name:
            return True

    # 补充：AI 生态的强特征词（不在主词表中但辨识度高）
    for marker in _AI_ECOSYSTEM_MARKERS:
        if marker in name:
            return True

    return False


def _parse_version_constraint(text: str) -> dict[str, Any]:
    """
    解析版本范围表达式

    支持 npm 的 `>=1.0.0 <2.0.0`、OSV 的 `introduced:0 fixed:2.0.0` 等常见格式。
    """
    result: dict[str, Any] = {"raw": text, "introduced": None, "fixed": None, "last_affected": None}

    if not text:
        return result

    # OSV 风格：introduced: x / fixed: y
    m = re.search(r"introduced[:=]\s*([\d.]+)", text)
    if m:
        result["introduced"] = m.group(1)
    m = re.search(r"fixed[:=]\s*([\d.]+)", text)
    if m:
        result["fixed"] = m.group(1)
    m = re.search(r"last_affected[:=]\s*([\d.]+)", text)
    if m:
        result["last_affected"] = m.group(1)

    # npm 风格：>=1.0.0 <2.0.0
    if not result["introduced"]:
        m = re.search(r">=?(\d[\d.]*)", text)
        if m:
            result["introduced"] = m.group(1)
    if not result["fixed"] and not result["last_affected"]:
        m = re.search(r"<=?\s*(\d[\d.]*)", text)
        if m:
            result["fixed"] = m.group(1)

    return result


def _version_tuple(v: str) -> tuple[int, ...]:
    """版本号转为可比较的元组"""
    if not v:
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def guess_asset_type(product: str, ecosystem: str | None) -> str:
    """
    推断资产类型

    比单纯返回"组件"更有分析价值——
    回答了"这是什么东西"的问题。
    """
    if not product:
        return "未知"
    text = f"{product} {ecosystem or ''}".lower()

    for asset_type, hints in _TYPE_HINTS.items():
        if any(h in text for h in hints):
            return asset_type

    if ecosystem == "PyPI" or ecosystem == "npm":
        return "组件"
    if ecosystem in ("Go", "Maven", "NuGet", "RubyGems", "crates.io", "Packagist"):
        return "组件"
    return "服务" if "server" in text or "service" in text else "组件"


class AssetEnricher(BaseEnricher):
    """维度二：受影响资产富化"""

    dimension = "asset"
    requires_llm = False  # 纯规则处理，无需 LLM

    async def enrich(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        raw_assets = ctx.affected_assets or []
        if not raw_assets:
            return self._infer_from_description(ctx)

        assets: list[dict[str, Any]] = []
        for raw in raw_assets:
            cpe = raw.get("cpe", "")
            cpe_parts = parse_cpe(cpe) if cpe else {}

            product = (
                raw.get("product")
                or cpe_parts.get("product")
                or raw.get("name")
                or ""
            )
            vendor = raw.get("vendor") or cpe_parts.get("vendor") or ""
            ecosystem_raw = raw.get("ecosystem")
            ecosystem = _ECOSYSTEM_MAP.get((ecosystem_raw or "").lower(), ecosystem_raw)

            # CPE 场景下生态信息缺失，可由 vendor 反推常见情形
            if not ecosystem:
                v = vendor.lower()
                if v in ("huggingface", "hf"):
                    ecosystem = "HuggingFace"
                elif v in ("pytorch", "facebook"):
                    ecosystem = "PyPI"
                elif cpe_parts.get("part") == "o":
                    ecosystem = "操作系统"

            # 版本范围：NVD 用 start/end，OSV 用约束表达式
            version_start = raw.get("version_start")
            version_end = raw.get("version_end")
            constraint_text = raw.get("version_constraint")
            version_info = _parse_version_constraint(constraint_text) if constraint_text else {}

            introduced = version_start or version_info.get("introduced")
            fixed = version_end or version_info.get("fixed") or version_info.get("last_affected")

            is_ai = (
                is_ai_component_name(product)
                or bool(raw.get("is_ai_component"))
                or (ecosystem or "").lower() == "huggingface"
            )

            assets.append(
                {
                    "product": product[:200] if product else None,
                    "vendor": vendor[:120] if vendor else None,
                    "ecosystem": ecosystem,
                    "asset_type": guess_asset_type(product, ecosystem),
                    "cpe": cpe[:500] if cpe else None,
                    "version_introduced": introduced,
                    "version_fixed": fixed,
                    "version_constraint": (constraint_text or "")[:300] or None,
                    "is_ai_component": is_ai,
                }
            )

        # 汇总依赖关系，支撑知识图谱的组件-漏洞边
        components = [a["product"] for a in assets if a["product"]]
        unique_components = sorted(set(components))

        confidence = 0.9 if len(assets) > 1 else 0.75
        if all(not a["version_introduced"] and not a["version_fixed"] for a in assets):
            # 无版本信息时置信度下调
            confidence = min(confidence, 0.7)

        return (
            {
                "assets": assets,
                "asset_count": len(assets),
                "unique_components": unique_components,
                "ai_components": [a["product"] for a in assets if a["is_ai_component"]],
                "ecosystems": sorted({a["ecosystem"] for a in assets if a["ecosystem"]}),
                "summary": self._summarize(assets),
            },
            confidence,
        )

    def _infer_from_description(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        """
        资产信息缺失时从描述文本推断

        置信度较低（0.5），因为文本推断存在误判风险，
        应进入待人工核验队列。
        """
        text = f"{ctx.title} {ctx.description}".lower()

        found: list[dict[str, Any]] = []
        for name in AI_COMPONENT_NAMES:
            # 用词边界匹配，避免 "torch" 命中 "pytorch" 之外的部分
            if re.search(rf"\b{re.escape(name)}\b", text):
                ecosystem = "PyPI" if name in _PYPI_PACKAGES else "npm"
                found.append(
                    {
                        "product": name,
                        "vendor": None,
                        "ecosystem": ecosystem,
                        "asset_type": guess_asset_type(name, ecosystem),
                        "cpe": None,
                        "version_introduced": None,
                        "version_fixed": None,
                        "version_constraint": None,
                        "is_ai_component": True,
                    }
                )

        if not found:
            return (
                {
                    "assets": [],
                    "asset_count": 0,
                    "unique_components": [],
                    "ai_components": [],
                    "ecosystems": [],
                    "summary": "未能识别受影响资产",
                    "note": "数据源未提供资产信息，且描述文本中无 AI 组件特征",
                },
                0.3,
            )

        return (
            {
                "assets": found[:10],
                "asset_count": len(found),
                "unique_components": sorted({a["product"] for a in found}),
                "ai_components": [a["product"] for a in found],
                "ecosystems": sorted({a["ecosystem"] for a in found if a["ecosystem"]}),
                "summary": f"从描述文本推断出 {len(found)} 个 AI 相关组件",
                "note": "基于描述文本关键词推断，需人工核验",
            },
            0.5,
        )

    @staticmethod
    def _summarize(assets: list[dict[str, Any]]) -> str:
        """生成人类可读摘要"""
        if not assets:
            return "无资产信息"
        parts = []
        ai_count = sum(1 for a in assets if a["is_ai_component"])
        if ai_count:
            parts.append(f"{ai_count} 个 AI 组件")
        ecos = {a["ecosystem"] for a in assets if a["ecosystem"]}
        if ecos:
            parts.append(f"生态 {', '.join(sorted(ecos)[:3])}")
        types = {a["asset_type"] for a in assets if a["asset_type"]}
        if types:
            parts.append(f"类型 {', '.join(sorted(types)[:3])}")
        return "；".join(parts)
