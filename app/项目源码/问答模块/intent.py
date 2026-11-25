"""
查询意图识别与路由

赛题要求LLM 意图分类器，路由至 4 类查询处理器。
意图识别的作用不只是"分类"，更重要的是**决定检索策略**——
不同意图需要召回完全不同的内容：

  FACT      事实查询   "CVE-2026-1234 是什么"          → 精确检索单个漏洞
  TREND     趋势分析   "最近有哪些 RAG 投毒漏洞"        → 检索+聚合统计
  RELATION  关联推理   "哪些 MCP 漏洞已经有可用 POC"    → 图谱多跳+组合过滤
  DETAIL    处置建议   "这个漏洞怎么修"                 → 富化字段优先

设计上采用「规则前置 + LLM 兜底」的双层判定：
  规则层基于正则与关键词，零成本、响应快（<1ms），
  覆盖 80%+ 的确定性场景（如 CVE 编号、"怎么修"这类强信号）。
  规则无法定论时才调 LLM——这与 P1 的 AI 分类器是同一设计哲学，
  目的是把 LLM 调用控制在必要时刻，避免网关限流拖垮响应速度
  （赛题要求 P95 ≤5s）。

指代消解：
  多轮对话中"它""这个漏洞"需要回指上一轮实体。
  规则层通过提取上一轮的核心实体（CVE 编号/组件名）并回填到查询中实现。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..后端服务.llm_client import get_llm_client
from ..后端服务.logger import get_logger

logger = get_logger("intent")

# 意图识别的 LLM 兜底硬超时（秒）
# 意图识别是问答链路第一步，此处超时会拖累整个响应。
# 实测规则未覆盖的查询在限流时能拖到 12 秒以上，
# 1.5 秒足够正常响应，超时即走关键词兜底。
INTENT_LLM_TIMEOUT = 1.5


class Intent(str, Enum):
    """查询意图"""

    FACT = "fact"  # 事实查询：具体是什么
    TREND = "trend"  # 趋势分析：整体态势
    RELATION = "relation"  # 关联推理：多跳关系
    DETAIL = "detail"  # 处置建议：怎么修


INTENT_LABELS = {
    Intent.FACT: "事实查询",
    Intent.TREND: "趋势分析",
    Intent.RELATION: "关联推理",
    Intent.DETAIL: "处置建议",
}


@dataclass(slots=True)
class QueryContext:
    """
    会话上下文

    支撑多轮对话：指代消解需要知道上一轮聊过什么。
    """

    session_id: str
    history: list[dict[str, str]] = field(default_factory=list)
    # 上一轮的核心实体，供指代消解回填
    last_entities: list[str] = field(default_factory=list)
    last_intent: str | None = None
    turn: int = 0

    def add_turn(self, question: str, answer_summary: str = "") -> None:
        """记录一轮对话"""
        self.history.append({"role": "user", "content": question})
        if answer_summary:
            self.history.append({"role": "assistant", "content": answer_summary[:500]})
        # 只保留最近 6 条，避免上下文无限增长
        if len(self.history) > 6:
            self.history = self.history[-6:]
        self.turn += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "history": self.history,
            "last_entities": self.last_entities,
            "last_intent": self.last_intent,
            "turn": self.turn,
        }


@dataclass(slots=True)
class IntentResult:
    """意图识别结果"""

    intent: Intent
    confidence: float
    method: str  # rule / llm / fallback
    rewritten_query: str  # 指代消解后的完整查询
    entities: list[str] = field(default_factory=list)  # 抽取的关键实体
    reasoning: str = ""
    filters: dict[str, Any] = field(default_factory=dict)  # 建议的检索过滤条件

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "intent_label": INTENT_LABELS[self.intent],
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "rewritten_query": self.rewritten_query,
            "entities": self.entities,
            "reasoning": self.reasoning,
            "filters": self.filters,
        }


# ========== 实体抽取 ==========

# CVE / GHSA 编号
_CVE_PATTERN = re.compile(r"\b(?:CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})\b", re.I)

# AI 组件名（用于「哪些 MCP 漏洞」这类查询的实体识别）
_KNOWN_COMPONENTS = [
    "transformers", "torch", "pytorch", "tensorflow", "vllm", "sglang", "ollama",
    "langchain", "llama", "huggingface", "chromadb", "chroma", "qdrant", "faiss",
    "mlflow", "gradio", "streamlit", "langgraph", "autogen", "haystack",
    "diffusers", "accelerate", "datasets", "safetensors", "mcp", "mcp server",
    "openai", "anthropic", "numpy", "pillow", "opencv", "nltk",
]

# 攻击技术关键词
_TECHNIQUE_TERMS = [
    "提示词注入", "prompt injection", "越狱", "jailbreak", "模型投毒", "data poisoning",
    "训练数据投毒", "模型后门", "backdoor", "对抗样本", "adversarial", "模型窃取",
    "model extraction", "成员推断", "membership inference", "RAG", "知识库投毒",
    "智能体", "agent", "工具投毒", "供应链", "supply chain", "数据泄露",
    "路径穿越", "path traversal", "SSRF", "注入", "injection",
]

# 时间范围（趋势分析）
_TIME_PATTERNS = [
    (r"(最近|近|过去|last)\s*(\d+)\s*(天|日|day)", "days"),
    (r"(最近|近|过去|last)\s*(\d+)\s*(周|周内|week)", "weeks"),
    (r"(最近|近|过去|last)\s*(\d+)\s*(个?月|month)", "months"),
    (r"(\d+)\s*(天|日|days?)\s*(以内|内|内的时间)", "days"),
]


def extract_entities(text: str) -> list[str]:
    """抽取查询中的关键实体"""
    entities: list[str] = []

    # CVE / GHSA 编号
    entities.extend(m.group(0).upper() for m in _CVE_PATTERN.finditer(text))

    lowered = text.lower()
    # AI 组件
    for comp in _KNOWN_COMPONENTS:
        if re.search(rf"\b{re.escape(comp)}\b", lowered):
            entities.append(comp)
            break  # 组件只取一个，避免噪声

    return entities


def extract_technique(text: str) -> str | None:
    """识别查询中的攻击技术主题"""
    lowered = text.lower()
    for term in _TECHNIQUE_TERMS:
        if term.lower() in lowered:
            return term
    return None


def extract_time_range(text: str) -> dict[str, int] | None:
    """解析时间范围"""
    for pattern, unit in _TIME_PATTERNS:
        m = re.search(pattern, text, flags=re.I)
        if m:
            value = int(m.group(2))
            if unit == "days":
                return {"days": value}
            if unit == "weeks":
                return {"days": value * 7}
            if unit == "months":
                return {"days": value * 30}
    # 默认"最近"
    if re.search(r"(最近|近期|latest|recent|new)", text, flags=re.I):
        return {"days": 30}
    return None


# ========== 指代消解 ==========

# 指代词
_PRONOUNS = ["它", "这个", "那个", "该漏洞", "此漏洞", "这条", "上面提到的", "刚才说的", "他"]


# ---------------------------------------------------------------------------
# 寒暄/ 元指令识别
# ---------------------------------------------------------------------------
# 为什么要单独识别：
# 「你好」「谢谢」「你是谁」这类输入没有任何情报检索意图，
# 但规则层兜底会把它们归入某个实体意图（实测「你好」→ fact），
# 结果 AI 硬套一份漏洞详情回答 —— 用户看到的是答非所问。
#
# 与「通用技术问题」要区分开：
#   · 「你好」      → 寒暄，应礼貌回应
#   · 「FastAPI 怎么用」→ 通用知识，应正常回答并标注
# 后者由 free_agent 处理，不走这里。
_GREETING_PATTERNS = [
    r"^\s*(你好|您好|哈喽|哈啰|hi|hello|hey|yo)\s*[\s!！。.，,~？?]*$",
    r"^\s*(谢谢|多谢|感谢|thank\s*you|thanks)\s*[\s!！。.，,~？?]*$",
    r"^\s*(再见|拜拜|bye|goodbye)\s*[\s!！。.，,~？?]*$",
    r"^\s*(你是谁|你叫什么|你能做什么|你能干什么|你有什么功能)\s*[\s?？]*$",
    r"^\s*(在吗|在么|忙吗)\s*[\s?？]*$",
    r"^\s*(测试|test|ping)\s*[\s!！.。]*$",
    r"^\s*(好的|好|知道了|明白了|收到|嗯|哦|ok)\s*[\s!！.。]*$",
]


# 「数据新鲜度」问句的判据（时间点语义，而非统计语义）
_FRESHNESS_PATTERN = (
    r"(什么时候|何时|多新|多久|新旧|时间点|数据.{0,3}新鲜度|"
    r"最近.{0,6}(采集|收录|入库|更新)|最新.{0,4}(采集|收录|入库|更新)|"
    r"(采集|收录|入库|更新)(时间|日期)|"
    r"when\s+(was|were)|how\s+(recent|fresh|old))"
)
# 统计语义 —— 出现这些就说明用户要的是「有多少」而不是「什么时候」
_STAT_PATTERN = r"(多少个|多少条|分布|排名|占比|统计)"


def is_freshness_query(query: str) -> bool:
    """判断是否为「数据新鲜度」问句。

    两处共用（规则层 + LLM 降级层），避免两套正则不同步 ——
    实测踩过：规则层改了、降级层没改，结果 LLM 一超时就答非所问。
    """
    q = query.strip()
    if not q:
        return False
    if re.search(_STAT_PATTERN, q, re.I):
        return False
    return bool(re.search(_FRESHNESS_PATTERN, q, re.I))


def is_greeting(query: str) -> bool:
    """判断是否为寒暄/元指令（不应触发情报检索）。"""
    q = query.strip()
    if not q:
        return False
    # 长度兜底：「你好，顺便问下 CVE-2026-1234 怎么修」不该被当成寒暄
    if len(q) > 20:
        return False
    return any(re.match(p, q, re.I) for p in _GREETING_PATTERNS)


def needs_coreference(query: str) -> bool:
    """判断查询是否含指代"""
    return any(p in query for p in _PRONOUNS)


def resolve_coreference(query: str, context: QueryContext | None) -> tuple[str, list[str]]:
    """
    指代消解

    把"它怎么修"这类查询补全为"CVE-2026-xxxx 怎么修"。
    实现方式：将上一轮的核心实体回填到查询开头。

    Returns:
        (补全后的查询, 使用的实体列表)
    """
    if not context or not context.last_entities:
        return query, []
    if not needs_coreference(query):
        return query, []

    # 取最相关的实体（优先 CVE 编号）
    entity = context.last_entities[0]
    # 若查询已有明确实体则不覆盖
    if extract_entities(query):
        return query, []

    return f"{entity} {query}", [entity]


# ========== 意图分类器 ==========


class IntentClassifier:
    """
    意图分类器

    规则前置、LLM 兜底。规则层覆盖确定性强的信号，
    这样绝大多数查询无需 LLM 参与——既是性能优化，
    也降低了免费网关限流带来的不稳定风险。
    """

    def __init__(self) -> None:
        self.log = logger
        self._llm = get_llm_client()
    def classify_by_rule(self, query: str) -> IntentResult | None:
        """
        规则层判定

        Returns:
            有明确结论时返回结果，否则 None 表示需 LLM 判定
        """
        q = query.strip()
        if not q:
            return None

        # 寒暄优先短路：必须在任何实体/关键词判定之前。
        # 否则「你好」会落到规则兜底被判成 fact，
        # 检索不到就硬套模板 → 答非所问（实测确认）。
        # 返回 None 表示「无需检索」，由上层给礼貌回应。
        if is_greeting(q):
            return None

        entities = extract_entities(q)
        technique = extract_technique(q)
        time_range = extract_time_range(q)

        filters: dict[str, Any] = {}
        if entities:
            filters["entities"] = entities
        if technique:
            filters["technique"] = technique
        if time_range:
            filters["time_range"] = time_range

        # 处置/关联类关键词——提前识别，避免被规则 1 覆盖。
        # 这是多轮场景的必需品：指代消解会把上一轮的 CVE 编号补进
        # 查询（如「CVE-xxx 怎么修复」），若无此判断，
        # 所有后续追问都会因含编号而被误判为事实查询。
        has_detail_keyword = re.search(
            r"(怎么修|如何修|如何修复|怎么修复|修复方案|修复建议|怎么处置|"
            r"如何处置|怎么缓解|如何缓解|缓解措施|缓解|补丁|升级到|如何升级|"
            r"怎么升级|如何处理|怎么加固|处置建议|"
            r"how to fix|how to mitigate|remediat|mitigat)",
            q,
            flags=re.I,
        )
        # 事实类信号：明确的定义/介绍型问法
        # 事实类信号词。
        # 「有什么漏洞」是典型的详情问法（问单个 CVE 的内容），
        # 之前只收录了「是什么」，导致「CVE-xxx 有什么漏洞」被
        # relation 规则抢走（它同时命中「有什么」与「漏洞」）。
        #
        # ⚠️ 但「vllm 有什么安全风险？」这类**组件维度**的提问，
        # 「有什么」并不是事实问法 —— 它问的是「该组件关联了哪些风险」，
        # 应归 relation。若不排除，relation 规则里的
        # `and not has_fact_keyword` 会把它整个挡掉 → 落入 LLM 兜底
        # → 被限流拖到 12 秒以上（实测 P95 因此超标）。
        # 判据：**带 CVE/GHSA 编号**时，「有什么漏洞」才是详情问法。
        # 不带编号的「有什么 + 风险/漏洞」是关系问法。
        #
        # 而「介绍/解释/描述」本身就是纯介绍型问法，与编号无关，
        # 不该被这个条件约束。
        _is_detail_question = bool(_CVE_PATTERN.search(q))
        has_fact_keyword = (
            re.search(
                r"(是什么|什么是|有什么|有啥|what is|what are|what)",
                q, flags=re.I,
            ) and _is_detail_question
        ) or re.search(
            r"(介绍|解释|描述|describe)", q, flags=re.I
        )
        # 趋势类信号：时间限定 + 统计/趋势词
        # 趋势类信号。时间限定词（最近/近期）本身就很强，
        # 不再排除「哪些」——「最近有哪些 X 漏洞」问的是整体态势，
        # 排除后会被误判为关系查询。
        has_trend_keyword = re.search(
            r"(趋势|态势|统计|分布|排名|top|最多|多少条|多少个|占比|"
            r"最近|近期|latest|recent|new|"
            r"trend|statistics|how many)",
            q,
            flags=re.I,
        ) and not re.search(
            # 但「关联/映射/POC」是明确的关系信号，优先走 relation
            r"(关联|映射|poc|对应|有哪些漏洞有)",
            q,
            flags=re.I,
        )

        has_relation_keyword = (
            re.search(r"(哪些|什么|有几个|which|what|list)", q, flags=re.I)
            and re.search(
                # 第二组词表需覆盖关系查询的常见表达：
                # 早期只收录「关联/POC/利用」等窄词，导致
                # 「X 组件出现过哪些安全问题」这类最典型问法落入 LLM 兜底，
                # 被限流拖到 12 秒以上。
                "(关联|关系|相关|poc|利用|攻击技术|影响|涉及|映射|对应|"
                "漏洞|安全问题|安全风险|风险|威胁|隐患|风险点|"
                "暴露|攻击面|缺陷|有哪些|有吗)",
                q,
                flags=re.I,
            )
            # 事实/趋势信号优先——「是什么漏洞」含"漏洞"但不是关系查询，
            # 不加此排除会全部误判为 relation
            and not has_fact_keyword
            and not has_trend_keyword
        )

        # POC 可用性问法——含编号时也必须优先识别。
        # 「CVE-xxx 有没有可用的 POC」表面是事实查询（含编号），
        # 实质问的是「该漏洞与 POC 之间存在什么关系」，
        # 误判会导致答案不给出 POC 信息（评测中 20 例因此失分）。
        wants_poc = re.search(
            r"(有没有|是否有|有无|存在的?|可用的?)?\s*"
            r"(poc|exploit|利用|概念验证|验证代码|攻击方法|怎么打)",
            q,
            flags=re.I,
        )
        if _CVE_PATTERN.search(q) and wants_poc and not has_detail_keyword:
            return IntentResult(
                intent=Intent.RELATION,
                confidence=0.9,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询涉及漏洞与 POC 的可用性关系",
                filters=filters,
            )

        # 规则 1：查询含 CVE 编号且无其他意图信号 → 事实查询
        if _CVE_PATTERN.search(q) and not has_detail_keyword and not has_relation_keyword:
            return IntentResult(
                intent=Intent.FACT,
                confidence=0.95,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询包含漏洞编号，属于精确查询",
                filters=filters,
            )

        # 规则 1b：含 CVE 编号但有明确意图信号 → 按信号判定
        if _CVE_PATTERN.search(q) and has_detail_keyword:
            return IntentResult(
                intent=Intent.DETAIL,
                confidence=0.88,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询含漏洞编号且询问处置措施",
                filters=filters,
            )

        # 规则 2：关联推理优先判定
        # 必须排在处置建议之前——「哪些 MCP 漏洞有 POC」这类查询
        # 表面含处置类关键词（POC/利用），本质是关系查询。
        # 若先判处置类，会把这类问题全部误路由。
        # 无编号的 POC/可利用性问法——多轮对话中常见
        # （指代消解后实体可能已不在问句里）
        if wants_poc and re.search(
            r"(能不能|能否|可不可以|可以吗|怎么|如何|有没有|有)", q
        ):
            return IntentResult(
                intent=Intent.RELATION,
                confidence=0.8,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询涉及可利用性/概念验证代码",
                filters=filters,
            )

        if has_relation_keyword:
            return IntentResult(
                intent=Intent.RELATION,
                confidence=0.85,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询涉及实体间关系（带条件筛选）",
                filters=filters,
            )

        # 规则 3：处置类关键词 → 处置建议
        # 注意：这里的关键词必须是真的处置诉求，
        # 不能包含 poc/利用（那些属关联查询的筛选条件）
        if has_detail_keyword:
            return IntentResult(
                intent=Intent.DETAIL,
                confidence=0.85,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询涉及修复或处置措施",
                filters=filters,
            )

        # 规则 2.5：数据新鲜度 → 事实查询（**必须排在趋势规则之前**）
        #
        # 为什么必须前置：
        #   「最近采集到的一条数据是什么时候的？」里含「最近」，
        #   会被下面的趋势规则（最近|近期|latest → TREND）捞走，
        #   于是去统计「风险等级分布」—— 答非所问。
        #   用户要的是**时间点**，不是统计口径。
        #
        # 判据：问「什么时候/ 何时 / 多新 / 最新采集时间」
        #   这类「时间点」语义，而非「有多少 / 分布 / 排名」统计语义。
        if is_freshness_query(q):
            return IntentResult(
                intent=Intent.FACT,
                confidence=0.85,
                # 用独立 method 而非仍叫 "rule"：
                # 上层据此走「直接答时间」的专用分支，不套模板、不调 LLM。
                method="freshness",
                rewritten_query=q,
                entities=entities,
                reasoning="查询数据新鲜度（采集时间），直接作答",
                filters=filters,
            )

        # 规则 3：趋势类关键词 → 趋势分析
        if re.search(
            r"(趋势|态势|统计|多少个|多少条|分布|排名|top|最多|最近|近期|"
            r"trend|statistics|how many|top |latest)",
            q,
            flags=re.I,
        ) and not re.search(r"(哪些漏洞|哪些组件)", q):
            return IntentResult(
                intent=Intent.TREND,
                confidence=0.8,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询涉及统计或整体态势",
                filters=filters,
            )

        # 规则 4：关联推理关键词 → 关联推理
        if re.search(
            r"(关联|关系|相关|哪些.*有|哪些.*已|映射|对应|涉及|组合|"
            r"relation|related|associate|mapping|which .* have)",
            q,
            flags=re.I,
        ) or (technique and re.search(r"(哪些|什么|which|what)", q, flags=re.I)):
            return IntentResult(
                intent=Intent.RELATION,
                confidence=0.8,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询涉及多实体之间的关系",
                filters=filters,
            )

        # 规则 5：纯事实性提问（"X 是什么"）
        if re.search(r"(是什么|什么是|是什么漏洞|介绍|解释|what is|describe)", q, flags=re.I):
            return IntentResult(
                intent=Intent.FACT,
                confidence=0.75,
                method="rule",
                rewritten_query=q,
                entities=entities,
                reasoning="查询为定义性提问",
                filters=filters,
            )

        return None

    async def classify(
        self, query: str, context: QueryContext | None = None
    ) -> IntentResult:
        """
        完整意图识别流程

        指代消解 → 规则判定 → LLM 兜底
        """
        # 第一步：指代消解
        resolved, used_entities = resolve_coreference(query, context)

        # 寒暄短路：规则层已识别为寒暄，这里直接给结论，
        # 不再走 LLM 兜底 —— 否则「你好」仍会被 LLM 判成 fact，
        # 然后检索不到 → 硬套漏洞详情（实测确认）。
        if is_greeting(resolved):
            return IntentResult(
                intent=Intent.FACT,
                confidence=1.0,
                method="greeting",
                # 寒暄无需改写，原样带回即可
                # （这一项是必填 positional，漏了会 TypeError → 500）
                rewritten_query=resolved,
                reasoning="寒暄/元指令，无需检索知识库",
                filters={},
            )

        # 第二步：规则判定
        rule_result = self.classify_by_rule(resolved)
        if rule_result is not None:
            if context is not None:
                context.last_intent = rule_result.intent.value
            return rule_result

        # 第三步：LLM 兜底
        return await self._classify_by_llm(resolved, context, used_entities)

    async def _classify_by_llm(
        self, query: str, context: QueryContext | None, used_entities: list[str]
    ) -> IntentResult:
        """LLM 判定意图（带超时与熔断保护）"""
        client = self._llm

        # 熔断期直接跳过——此时任何调用都必然失败并浪费响应预算。
        # 实测规则未覆盖的查询会在这里被限流拖到 12 秒以上。
        if client.is_rate_limited():
            logger.info("intent_llm_skipped_circuit_break")
            return self._fallback_result(query, context, used_entities, "熔断中")

        try:
            result, resp = await asyncio.wait_for(
                self._call_llm_for_intent(client, query),
                timeout=INTENT_LLM_TIMEOUT,
            )
        except TimeoutError:
            logger.info("intent_llm_timeout", query=query[:40])
            return self._fallback_result(query, context, used_entities, "LLM 超时")
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent_llm_error", error=str(exc)[:150])
            return self._fallback_result(query, context, used_entities, "LLM 异常")

        raw = str(result.get("intent", "fact")).lower()
        try:
            intent = Intent(raw)
        except ValueError:
            intent = Intent.FACT

        if resp.degraded:
            # LLM 降级：按关键词兜底，宁可保守也不误判
            intent = self._fallback_intent(query)
            confidence = 0.4
            method = "fallback"
            reasoning = "LLM 不可用，按关键词保守判定"
        else:
            confidence = float(result.get("confidence", 0.7))
            method = "llm"
            reasoning = str(result.get("reason", ""))[:200]

        if context is not None:
            context.last_intent = intent.value

        return IntentResult(
            intent=intent,
            confidence=confidence,
            method=method,
            rewritten_query=query,
            entities=extract_entities(query) + used_entities,
            reasoning=reasoning,
            filters=self._build_filters(query),
        )

    async def _call_llm_for_intent(self, client, query: str):
        """调用 LLM 判定意图（独立方法便于包 wait_for）"""
        return await client.chat_json(
            [
                {
                    "role": "user",
                    "content": f"""判断以下安全情报查询的意图类型。

查询：{query[:400]}

意图类型：
1. fact（事实查询）—— 询问某个具体漏洞是什么、有什么特征
2. trend（趋势分析）—— 询问整体态势、统计数量、排名、近期趋势
3. relation（关联推理）—— 询问多个实体之间的关系，如"哪些 A 有 B"
4. detail（处置建议）—— 询问如何修复、缓解、处置或利用

严格输出 JSON：{{"intent": "fact|trend|relation|detail", "confidence": 0.0-1.0, "reason": "一句话依据"}}""",
                }
            ],
            temperature=0.0,
            max_tokens=300,
            fallback={"intent": "fact", "confidence": 0.4},
        )

    def _fallback_result(
        self,
        query: str,
        context: QueryContext | None,
        used_entities: list[str],
        reason: str,
    ) -> IntentResult:
        """
        LLM 不可用时的兜底结果

        宁可给出保守的关键词判定，也不能让整条问答链路卡住。
        """
        intent = self._fallback_intent(query)
        if context is not None:
            context.last_intent = intent.value
        # 降级路径也要保留 freshness 标记 ——
        # 否则 LLM 超时这类「时间点」问题会退化成模板答案，
        # 恰恰是最容易答非所问的场景（实测截图就是这个组合）。
        method = "freshness" if self._is_freshness_query(query) else "fallback"
        return IntentResult(
            intent=intent,
            confidence=0.4,
            method=method,
            rewritten_query=query,
            entities=extract_entities(query) + used_entities,
            reasoning=f"{reason}，按关键词保守判定为「{INTENT_LABELS[intent]}」",
            filters=self._build_filters(query),
        )

    @staticmethod
    @staticmethod
    def _is_freshness_query(query: str) -> bool:
        """实例侧的薄封装，转发到模块级函数。"""
        return is_freshness_query(query)

    @staticmethod
    def _fallback_intent(query: str) -> Intent:
        """LLM 不可用时的关键词兜底"""
        q = query.lower()
        if re.search(r"(怎么修|如何修|修复|缓解|补丁|处置)", q):
            return Intent.DETAIL
        # 数据新鲜度（时间点问句）—— 必须排在 TREND 之前，
        # 否则「最近采集的数据是什么时候」会被「最近」带进趋势统计，
        # 答成「风险等级分布」这种与问题无关的内容。
        # 与规则层的新鲜度规则保持一致。
        if is_freshness_query(q):
            return Intent.FACT
        if re.search(r"(多少|统计|趋势|排名|top|分布)", q):
            return Intent.TREND
        # 「最近/ 近期 / latest」也算统计语义。
        # 之前漏了这条，导致「最近有哪些组件受影响」在 LLM 超时降级时
        # 落到默认 FACT，而不是本该的 TREND。
        if re.search(r"(最近|近期|latest)", q):
            return Intent.TREND
        if re.search(r"(关联|关系|哪些.*有|映射)", q):
            return Intent.RELATION
        return Intent.FACT

    @staticmethod
    def _build_filters(query: str) -> dict[str, Any]:
        """从查询中抽取检索过滤条件"""
        filters: dict[str, Any] = {}
        entities = extract_entities(query)
        if entities:
            filters["entities"] = entities
        technique = extract_technique(query)
        if technique:
            filters["technique"] = technique
        time_range = extract_time_range(query)
        if time_range:
            filters["time_range"] = time_range
        return filters


# 会话上下文存储（内存，生产环境可换 Redis）
_SESSIONS: dict[str, QueryContext] = {}


def get_session_context(session_id: str) -> QueryContext:
    """获取或创建会话上下文"""
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = QueryContext(session_id=session_id)
    return _SESSIONS[session_id]


def clear_session(session_id: str) -> None:
    """清空会话"""
    _SESSIONS.pop(session_id, None)


def get_session_count() -> int:
    """当前会话数（监控用）"""
    return len(_SESSIONS)


# 分类器单例
_classifier: IntentClassifier | None = None


def get_intent_classifier() -> IntentClassifier:
    """获取意图分类器单例"""
    global _classifier
    if _classifier is None:
        _classifier = IntentClassifier()
    return _classifier
