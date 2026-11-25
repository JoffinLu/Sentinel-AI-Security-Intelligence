"""
AI 安全相关性分类器

本项目的核心技术难点。实测发现：NVD 7 天窗口的 2563 条 CVE 中，
真正属于 AI 安全范畴的不足 1%。若不过滤，系统将退化为普通漏洞库，
完全偏离赛题要求"AI 安全知识情报"的定位。

采用三级过滤，逐级递进以控制 LLM 调用成本：

第一级 规则层：关键词黑白名单，零成本，命中即判定
    ├ 高置信关键词（prompt injection / jailbreak 等）→ 1.0 直接通过
    └ 强排除词（wordpress / drupal 等）→ 0.0 直接拒绝

第二级 嵌入层：BGE-M3 语义相似度，与 AI 安全基准句库比对
    适用于规则层无法定论的灰区样本

第三级 LLM 层：仅对灰区（0.3 < 得分 < 0.7）调用大模型复核
    按证据分级赋值，避免二元判定丢失信息

分类体系采用赛题给定的四类框架，并做工程化扩展。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..后端服务.config import get_settings
from ..后端服务.llm_client import LLMBackend, get_llm_client
from ..后端服务.logger import get_logger

logger = get_logger(__name__)


# ========== 第一级：规则词库 ==========

# 高置信 AI 安全关键词——命中任一即可判定为 AI 安全
AI_SECURITY_KEYWORDS: dict[str, list[str]] = {
    "提示词注入": [
        "prompt injection", "prompt-injection", "indirect prompt",
        "prompt hijacking", "instruction injection", "prompt leaking",
    ],
    "越狱攻击": [
        "jailbreak", "jailbreaking", "jail broken", "guardrail bypass",
        "safety bypass", "content policy bypass",
    ],
    "对抗样本": [
        "adversarial example", "adversarial attack", "adversarial sample",
        "adversarial perturbation", "adversarial example attack",
    ],
    "模型后门": [
        "neural backdoor", "model backdoor", "trojan model", "backdoor attack",
        "weight backdoor", "trojaned model", "badnet",
    ],
    "数据投毒": [
        "data poisoning", "poisoning attack", "training data poisoning",
        "label poisoning", "dataset poisoning", "knowledge poisoning",
    ],
    "模型窃取": [
        "model stealing", "model extraction", "extraction attack",
        "steal model", "model theft", "api extraction",
    ],
    "成员推断": [
        "membership inference", "membership disclosure",
    ],
    "隐私泄露": [
        "training data extraction", "verbatim memorization", "data exfiltration",
        "privacy leakage", "privacy attack",
    ],
    "RAG安全": [
        "retrieval augmented generation", "rag poisoning", "rag attack",
        "retrieval poisoning", "vector database attack", "vector store poisoning",
        "indirect retrieval", "context poisoning",
    ],
    "智能体安全": [
        "agent hijacking", "agent security", "llm agent attack",
        "tool poisoning", "tool call injection", "mcp server",
        "model context protocol", "agentic ai security", "multi-agent attack",
        "agent misalignment", "function calling attack",
    ],
    "AI供应链": [
        "model supply chain", "ml supply chain", "pytorch model",
        "hugging face model", "model repository", "pickle deserialization",
        "torch.load", "unsafe deserialization model", "model zoo",
    ],
    "提示词泄露": [
        "system prompt", "system prompt leak", "prompt extraction",
        "instruction leak", "hidden instruction",
    ],
    "模型输出攻击": [
        "toxicity", "toxic generation", "harmful output", "llm safety",
        "guardrail", "alignment attack", "llm jailbreak", "refusal bypass",
    ],
}

# AI 组件名称——受影响资产命中即强证据
AI_COMPONENT_NAMES: set[str] = {
    "transformers", "torch", "pytorch", "tensorflow", "jax", "flax",
    "langchain", "langgraph", "llama-index", "llama_index", "autogen",
    "semantic-kernel", "vllm", "sglang", "ollama", "llama.cpp", "llama-cpp",
    "huggingface", "hugging-face", "diffusers", "accelerate", "datasets",
    "sentence-transformers", "timm", "peft", "trl", "bitsandbytes",
    "openai", "anthropic", "haystack", "chainlit", "gradio",
    "triton", "tensorrt", "mlflow", "ray", "raylib", "kernels",
    "opencv", "numpy", "pandas", "pillow", "torchvision",
    "sentencepiece", "tokenizers", "safetensors", "onnxruntime",
    "spacy", "nltk", "gensim", "optuna", "wandb", "comet-ml",
}

# 强排除词——命中即判定与 AI 安全无关
EXCLUSION_KEYWORDS: set[str] = {
    "wordpress", "drupal", "joomla", "magento", "typo3", "apache", "nginx", "tomcat", "struts", "spring framework",
    "windows", "kernel", "android", "ios", "iphone",
    "router", "firewall", "switch", "cisco", "juniper",
    "banking", "erp", "crm", "sap", "oracle database",
    "phpmyadmin", "phpunit", "laravel", "symfony",
    "git", "openssl", "curl", "zlib",
    "moodle", "canvas", "blackboard", "zoom", "webex",
}

# AI 安全基准句库（嵌入层比对用）——覆盖四类风险的典型表述
# 嵌入层「排除」阈值：相似度低于此值即判为非 AI，不再占用 LLM 配额。
# 刻意取低值（正判定阈值通常 0.5+），避免误杀中间地带的样本。
EMBEDDING_REJECT_THRESHOLD = 0.25

BENCHMARK_SENTENCES: list[str] = [
    "A prompt injection vulnerability allows an attacker to manipulate the language model's system instructions",
    "The model can be jailbroken to bypass its safety guardrails and produce harmful content",
    "Adversarial examples crafted with small perturbations cause the model to misclassify inputs",
    "A neural backdoor is implanted in the weights, triggering malicious behavior on a specific trigger pattern",
    "Training data poisoning degrades model performance and can implant targeted backdoors",
    "Model extraction attack queries the API to reconstruct the proprietary model weights",
    "Membership inference attack determines whether a specific record was included in training data",
    "RAG retrieval poisoning inserts malicious documents to manipulate generated answers",
    "Agent hijacking exploits tool-calling capabilities to execute unauthorized actions",
    "A malicious pickle file in a model repository enables arbitrary code execution on deserialization",
    "System prompt extraction reveals the hidden instructions used to configure the AI assistant",
    "Indirect prompt injection through web content hijacks an LLM agent that reads the page",
]


# ========== 分类结果 ==========


@dataclass(slots=True)
class ClassificationResult:
    """
    AI 安全相关性分类结果

    category 为赛题四分类之一（一级），subcategory 为技术细类（二级）。
    两级并存既保证与赛题框架对齐，又保留技术细节。
    """

    is_ai_security: bool
    score: float  # 0-1 置信度
    category: str | None = None  # 赛题四分类（一级）
    method: str = "rule"  # rule / rule-exclude / embedding / llm
    keywords: list[str] = field(default_factory=list)  # 命中的关键词
    reasoning: str = ""  # LLM 判定理由
    subcategory: str | None = None  # 技术细类（二级）

    def __post_init__(self) -> None:
        """归一一级分类"""
        if self.category:
            self.category = to_primary_category(self.category)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_ai_security": self.is_ai_security,
            "score": round(self.score, 3),
            "category": self.category,
            "subcategory": self.subcategory or self.keywords[0] if self.keywords else None,
            "method": self.method,
            "keywords": self.keywords,
            "reasoning": self.reasoning,
        }


# 赛题给定的四类框架
CATEGORIES = [
    "AI基础设施/推理框架风险",
    "模型与数据层风险",
    "AI应用与智能体风险",
    "供应链风险",
]

# 二级细类 → 赛题四分类的映射
# 保留细粒度标签有助于展示专业度，但一级分类必须对齐赛题框架，
# 否则评审无法将系统输出与赛题要求对应起来。
CATEGORY_MAPPING: dict[str, str] = {
    # --- AI 应用与智能体风险：直接作用于应用与交互层 ---
    "提示词注入": "AI应用与智能体风险",
    "越狱攻击": "AI应用与智能体风险",
    "提示词泄露": "AI应用与智能体风险",
    "RAG安全": "AI应用与智能体风险",
    "智能体安全": "AI应用与智能体风险",
    "模型输出攻击": "AI应用与智能体风险",
    # --- 模型与数据层风险：作用于模型与训练数据 ---
    "对抗样本": "模型与数据层风险",
    "模型后门": "模型与数据层风险",
    "数据投毒": "模型与数据层风险",
    "模型窃取": "模型与数据层风险",
    "成员推断": "模型与数据层风险",
    "隐私泄露": "模型与数据层风险",
    # --- 供应链风险：作用于分发与依赖环节 ---
    "AI供应链": "供应链风险",
}


def to_primary_category(category: str | None) -> str | None:
    """
    将二级细类归一到赛题四分类

    已是四分类之一的原样返回；未知分类原样返回以便发现新增类别。
    LLM 返回的中文标签可能存在空格差异（如"AI 应用与智能体风险"），
    此处做归一化处理。
    """
    if not category:
        return None
    if category in CATEGORY_MAPPING:
        return CATEGORY_MAPPING[category]
    # 容忍 LLM 生成的空格变体
    compact = category.replace(" ", "")
    for primary in CATEGORIES:
        if compact == primary:
            return primary
    return category


class AISecurityClassifier:
    """
    AI 安全相关性分类器

    三级递进过滤：规则 → 嵌入 → LLM
    每级只处理上一级无法定论的样本，控制成本。
    """

    def __init__(self, use_llm: bool | None = None) -> None:
        self._settings = get_settings()
        self._llm = get_llm_client()
        self._use_llm = (
            self._settings.enable_llm_review if use_llm is None else use_llm
        )
        self._embedder = None  # 惰性加载
        self._benchmark_embeddings: list[list[float]] | None = None
        self.log = get_logger("ai_classifier")
        self._stats = {
            "total": 0,
            "rule_pass": 0,
            "rule_exclude": 0,
            "embedding_pass": 0,
            "llm_pass": 0,
            "llm_reject": 0,
        }

    # ---------- 第一级：规则层 ----------

    def _classify_by_rule(
        self, title: str, description: str, affected_assets: list[dict[str, Any]] | None
    ) -> ClassificationResult | None:
        """
        规则层判定

        Returns:
            有明确结论时返回结果，否则返回 None 表示需进入下一级
        """
        text = f"{title} {description}".lower()

        # 强排除优先（避免 "AI" 等泛词误命中）
        for word in EXCLUSION_KEYWORDS:
            if re.search(rf"\b{re.escape(word)}\b", text):
                # 但如果同时命中高置信 AI 关键词，则不排除
                has_ai_keyword = any(
                    kw in text
                    for kws in AI_SECURITY_KEYWORDS.values()
                    for kw in kws
                )
                if not has_ai_keyword:
                    self._stats["rule_exclude"] += 1
                    return ClassificationResult(
                        is_ai_security=False,
                        score=0.05,
                        category=None,
                        method="rule-exclude",
                        keywords=[word],
                        reasoning=f"命中强排除词「{word}」且无 AI 安全特征",
                    )

        # 高置信关键词
        hit_category: str | None = None
        hit_keywords: list[str] = []
        for category, keywords in AI_SECURITY_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    hit_keywords.append(kw)
                    if hit_category is None:
                        hit_category = category
                    break

        # 受影响资产为 AI 组件
        ai_asset_hit = False
        for asset in affected_assets or []:
            product = str(asset.get("product", "")).lower()
            if product in AI_COMPONENT_NAMES:
                ai_asset_hit = True
                hit_keywords.append(f"asset:{product}")
                break

        if hit_keywords:
            # AI 组件 + 关键词 = 极高置信
            if ai_asset_hit and hit_category:
                score = 0.98
            elif ai_asset_hit:
                score = 0.85
            else:
                score = 0.9
            self._stats["rule_pass"] += 1
            return ClassificationResult(
                is_ai_security=True,
                score=score,
                # 一级为赛题四分类（__post_init__ 自动归一），
                # 二级保留命中的细类标签
                category=hit_category or self._guess_category(text),
                method="rule",
                keywords=hit_keywords[:8],
                subcategory=hit_category,
                reasoning=f"命中 {len(hit_keywords)} 个 AI 安全特征词",
            )

        # 仅命中 AI 组件名，判定为灰区需进一步确认
        if ai_asset_hit:
            return ClassificationResult(
                is_ai_security=False,
                score=0.5,
                category=None,
                method="rule",
                keywords=hit_keywords[:8],
                reasoning="受影响资产为 AI 组件，但描述无 AI 攻击特征，需复核",
            )

        return None  # 交给下一级

    def _guess_category(self, text: str) -> str:
        """按关键词推断赛题四分类"""
        if any(k in text for k in ("vllm", "ollama", "triton", "framework", "serving", "推理")):
            return "AI基础设施/推理框架风险"
        if any(k in text for k in ("prompt", "jailbreak", "agent", "rag", "retrieval")):
            return "AI应用与智能体风险"
        if any(k in text for k in ("supply chain", "pickle", "repository", "model zoo", "hugging")):
            return "供应链风险"
        return "模型与数据层风险"

    # ---------- 第二级：嵌入层 ----------

    def _load_embedder(self):
        """惰性获取嵌入服务

        为什么改用「问答模块」的 EmbeddingService，而不是直接
        `SentenceTransformer("BAAI/bge-small-zh-v1.5")`：

          · 后者依赖 HF 缓存里的 `modules.json`，而本机该文件是 **0 字节**
            （实测加载必然抛 JSONDecodeError）。也就是说旧的嵌入层
            **从未真正生效过** —— 灰区样本实际全部跳过了语义判定。
          · EmbeddingService 用 AutoModel + 均值池化直接从
            `数据/模型缓存/` 加载，该路径已被检索模块长期验证可用。
          · 它是单例，与检索共用同一个模型实例，不额外占内存。

        实测编码速度：批量 5.8 ms/条（200 条 1.17 秒），
        1739 条约 10 秒 —— 而等量的 LLM 复核要 120 秒以上。
        """
        if self._embedder is None:
            try:
                from ..问答模块.embedding import get_embedding_service

                svc = get_embedding_service()
                if not svc.available:
                    svc.warmup()
                if svc.available:
                    self._embedder = svc
                    logger.info("embedder_loaded", backend="EmbeddingService", dim=512)
                else:
                    logger.warning("embedder_unavailable", reason="service not ready")
                    self._embedder = False
            except Exception as exc:  # noqa: BLE001
                logger.warning("embedder_unavailable", error=str(exc)[:200])
                self._embedder = False  # 标记为不可用，避免重复加载
        return self._embedder or None

    def _classify_by_embedding(self, text: str) -> ClassificationResult | None:
        """嵌入层判定（语义相似度）"""
        embedder = self._load_embedder()
        if embedder is None:
            return None

        try:
            if self._benchmark_embeddings is None:
                # 基准语句按「文档」编码（不加查询指令前缀），
                # 与 encode_query 形成 BGE 标准的非对称检索配对
                vecs = embedder.encode_documents(BENCHMARK_SENTENCES)
                if not vecs:
                    return None
                self._benchmark_embeddings = vecs

            vec = embedder.encode_query(text)
            if not vec:
                return None

            # 余弦相似度（两侧均已归一化，点积即为余弦值）
            import numpy as np

            sims = np.dot(np.array(self._benchmark_embeddings), vec)
            best_sim = float(np.max(sims))
            best_idx = int(np.argmax(sims))
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding_classify_failed", error=str(exc)[:200])
            return None

        threshold = self._settings.ai_relevance_embedding_threshold
        if best_sim >= threshold:
            self._stats["embedding_pass"] += 1
            return ClassificationResult(
                is_ai_security=True,
                score=min(0.95, 0.6 + best_sim * 0.35),
                category=self._guess_category(text),
                method="embedding",
                keywords=[f"语义相似:{best_sim:.2f}"],
                reasoning=f"与 AI 安全基准语句相似度 {best_sim:.3f}",
                subcategory=BENCHMARK_SENTENCES[best_idx][:80],
            )

        # 相似度极低 → 与所有 AI 安全基准语句都不沾边，直接判为非 AI，
        # 不必占用宝贵的 LLM 配额。
        # 阈值取 0.25（远低于正判定阈值）是刻意保守：宁可漏判、让 LLM
        # 去复核中间地带，也不要误杀真正相关的样本。
        if best_sim < EMBEDDING_REJECT_THRESHOLD:
            self._stats["embedding_reject"] = self._stats.get("embedding_reject", 0) + 1
            return ClassificationResult(
                is_ai_security=False,
                score=0.1,
                category=None,
                method="embedding-negative",
                keywords=[f"语义不相关:{best_sim:.2f}"],
                reasoning=(
                    f"与全部 AI 安全基准语句相似度均 < {EMBEDDING_REJECT_THRESHOLD}"
                    f"（最高 {best_sim:.3f}）"
                ),
            )
        return None

    # ---------- 第三级：LLM 层（批量） ----------

    async def classify_batch_llm(
        self, items: list[tuple[str, str]]
    ) -> list[ClassificationResult]:
        """
        批量 LLM 复核

        逐条调用 LLM 会因网络往返成为性能瓶颈（实测灰区样本量大时必然超时），
        故打包为单次请求，显著降低调用次数与总耗时。

        Args:
            items: [(标题, 描述), ...]

        Returns:
            与输入等长的分类结果列表
        """
        if not items:
            return []

        listing = "\n\n".join(
            f"[{i + 1}] 标题：{title[:200]}\n描述：{desc[:400]}"
            for i, (title, desc) in enumerate(items)
        )

        prompt = f"""判断以下 {len(items)} 个漏洞是否属于 AI 安全范畴，逐条判断。

AI 安全范畴：
1. AI 基础设施/推理框架风险（vLLM、Ollama、Triton 等）
2. 模型与数据层风险（提示词注入、系统提示词泄漏、数据投毒、对抗样本、模型后门、成员推断）
3. AI 应用与智能体风险（RAG 攻击、智能体劫持、工具调用滥用、MCP 风险、越狱）
4. 供应链风险（恶意模型仓库、Pickle 反序列化、PyPI 投毒）
5. 传统 Web/系统漏洞若发生在 AI 组件（transformers/torch/vllm 等）上，也属于范畴
6. 纯传统漏洞（WordPress/Drupal/Apache 等非 AI 组件）不属于范畴

待判断条目：
{listing}

严格输出 JSON 数组，长度必须为 {len(items)}，每项格式：
{{"index": 1, "is_ai": true, "category": "四分类之一或null", "confidence": 0.0-1.0, "reason": "一句话理由"}}"""

        result, resp = await self._llm.chat_json(
            [{"role": "user", "content": prompt}],
            model=self._settings.llm_economy_model,
            temperature=0.0,
            max_tokens=3000,
            fallback={},
        )

        if resp.backend == LLMBackend.DEGRADED or not result:
            return [
                ClassificationResult(
                    is_ai_security=False,
                    score=0.5,
                    category=None,
                    method="llm-degraded",
                    keywords=[],
                    reasoning="LLM 不可用，该样本标记为待人工核验",
                )
                for _ in items
            ]

        # 兼容模型返回 {"results": [...]} 或直接数组
        entries = result.get("results", result) if isinstance(result, dict) else result
        if not isinstance(entries, list):
            entries = []

        out: list[ClassificationResult] = []
        for i, (title, desc) in enumerate(items):
            match = None
            for e in entries:
                if isinstance(e, dict) and int(e.get("index", -1)) == i + 1:
                    match = e
                    break
            if match is None:
                # LLM 漏判该条时退回启发式
                out.append(
                    ClassificationResult(
                        is_ai_security=False,
                        score=0.4,
                        category=None,
                        method="llm",
                        keywords=[],
                        reasoning="批量复核未覆盖该样本",
                    )
                )
                continue

            is_ai = bool(match.get("is_ai", False))
            conf = float(match.get("confidence", 0.5))
            self._stats["llm_pass" if is_ai else "llm_reject"] += 1
            out.append(
                ClassificationResult(
                    is_ai_security=is_ai,
                    score=conf if is_ai else (1 - conf) * 0.8,
                    category=match.get("category"),
                    subcategory=match.get("subcategory"),
                    method="llm-batch",
                    keywords=[],
                    reasoning=str(match.get("reason", ""))[:200],
                )
            )
        return out

    async def _classify_by_llm(self, title: str, description: str) -> ClassificationResult:
        """LLM 单条复核（保留给实时场景使用）"""
        results = await self.classify_batch_llm([(title, description)])
        return results[0]

    # ---------- 统一入口 ----------

    async def classify_batch(
        self,
        records: list[tuple[str, str, list[dict[str, Any]] | None]],
        llm_batch_size: int = 5,
        llm_max_batches: int = 6,
    ) -> list[ClassificationResult]:
        """
        批量分类（推荐用于大规模采集场景）

        流程：全部样本先过规则层 → 剩余灰区按批送 LLM 复核。

        Args:
            records: [(标题, 描述, 受影响资产), ...]
            llm_batch_size: 单次 LLM 请求判定的样本数。
                批次过大会超出免费网关承受范围导致 5xx，故默认取 5。
            llm_max_batches: LLM 复核的最大批次数（预算上限）。
                实测 GHSA 单批 500 条会产生上百灰区样本，全部送 LLM
                需数十次网络往返，耗时不可接受。超出预算的样本按
                AI 组件启发式保留并标记待复核，不阻塞采集主流程。

        Returns:
            与输入等长的分类结果列表
        """
        results: list[ClassificationResult | None] = [None] * len(records)
        grey_indices: list[int] = []

        # 第一级 + 第二级：规则与嵌入
        for i, (title, desc, assets) in enumerate(records):
            rule_result = self._classify_by_rule(title, desc, assets)
            if rule_result is not None:
                if rule_result.is_ai_security or rule_result.score < 0.2:
                    results[i] = rule_result
                    continue
                grey_indices.append(i)
            else:
                grey_indices.append(i)

        # 第二级：嵌入层。
        #
        # 这一段原先缺失——`_classify_by_embedding` 定义了却从未被调用，
        # 注释写着「规则与嵌入」但只有规则层在跑，灰区样本被直接抛给 LLM。
        # 后果是大量样本挤在 LLM 预算外溢（实测单源溢出 1230 条），
        # 只能退化成"资产名启发式"兜底，既慢又不准。
        #
        # 现在由嵌入层先过滤：本地 CPU 5.8ms/条，而 LLM 每批要 20 秒上下。
        if grey_indices:
            still_grey: list[int] = []
            for i in grey_indices:
                emb_result = self._classify_by_embedding(records[i][0])
                if emb_result is not None:
                    results[i] = emb_result
                else:
                    still_grey.append(i)
            self.log.info(
                "embedding_layer_done",
                grey=len(grey_indices),
                resolved=len(grey_indices) - len(still_grey),
                remaining=len(still_grey),
            )
            grey_indices = still_grey

        # 第三级：灰区批量 LLM 复核（受预算上限约束）
        if grey_indices and self._use_llm:
            budget = min(len(grey_indices), llm_max_batches * llm_batch_size)
            pending = grey_indices[:budget]
            overflow = grey_indices[budget:]

            if overflow:
                self.log.info(
                    "llm_budget_exceeded",
                    overflow=len(overflow),
                    note="灰区样本超出 LLM 预算，按启发式保留待后续复核",
                )

            for start in range(0, len(pending), llm_batch_size):
                chunk = pending[start : start + llm_batch_size]
                payload = [
                    (
                        records[i][0],
                        records[i][1][:600],
                    )
                    for i in chunk
                ]
                try:
                    chunk_results = await self.classify_batch_llm(payload)
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("llm_batch_failed", error=str(exc)[:200])
                    chunk_results = [
                        ClassificationResult(
                            is_ai_security=False,
                            score=0.4,
                            category=None,
                            method="llm-failed",
                            keywords=[],
                            reasoning="批量复核异常",
                        )
                        for _ in chunk
                    ]
                for idx, res in zip(chunk, chunk_results, strict=False):
                    results[idx] = res

            # 超预算样本兜底：AI 组件相关的一律保留，标记待复核
            for idx in overflow:
                _, _, assets = records[idx]
                has_ai_asset = any(
                    str(a.get("product", "")).lower() in AI_COMPONENT_NAMES
                    for a in (assets or [])
                )
                results[idx] = ClassificationResult(
                    is_ai_security=has_ai_asset,
                    score=0.5 if has_ai_asset else 0.3,
                    category=None,
                    method="heuristic-overflow",
                    keywords=[],
                    reasoning="超出 LLM 复核预算，按 AI 组件启发式判定，待后续复核",
                )

        # 补齐未判定项
        for i, res in enumerate(results):
            if res is None:
                results[i] = ClassificationResult(
                    is_ai_security=False,
                    score=0.1,
                    category=None,
                    method="rule",
                    keywords=[],
                    reasoning="未命中任何 AI 安全特征",
                )
        return results  # type: ignore[return-value]

    async def classify(
        self,
        title: str,
        description: str = "",
        affected_assets: list[dict[str, Any]] | None = None,
    ) -> ClassificationResult:
        """
        单条分类（实时场景使用）

        内部走批量接口，保证两条路径逻辑一致。
        """
        self._stats["total"] += 1
        result = (await self.classify_batch([(title, description, affected_assets)]))[0]
        # 统计与批量路径对齐
        if result.method == "rule" and result.is_ai_security:
            self._stats["rule_pass"] += 1
        elif result.method == "rule-exclude":
            self._stats["rule_exclude"] += 1
        elif result.method == "embedding":
            self._stats["embedding_pass"] += 1
        return result

    @property
    def stats(self) -> dict[str, int]:
        """分类统计，供监控看板"""
        return dict(self._stats)


# 全局单例
_classifier: AISecurityClassifier | None = None


def get_classifier() -> AISecurityClassifier:
    """获取分类器单例"""
    global _classifier
    if _classifier is None:
        _classifier = AISecurityClassifier()
    return _classifier
