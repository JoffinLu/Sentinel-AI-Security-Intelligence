"""
LLM 客户端（双路降级 + 自愈）

设计目标：LLM 是唯一可能因外部依赖失败的环节，任何模块都不能因它崩溃。
降级链：主路径(DeepSeek API) → 本地模型(Ollama) → 规则引擎兜底。

每次调用都记录实际生效的后端与降级状态，对应优化指标中的"系统自愈能力"证据。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from .config import get_settings
from .logger import get_logger

logger = get_logger(__name__)


class LLMBackend(str, Enum):
    """实际生效的 LLM 后端"""

    PRIMARY = "primary"
    FALLBACK = "fallback"
    DEGRADED = "degraded"  # 规则引擎兜底，无 LLM 参与


@dataclass(slots=True)
class LLMResponse:
    """
    LLM 统一响应

    携带降级信息，调用方可据此决定是否提示用户"当前为降级模式回答"。
    """

    content: str
    backend: LLMBackend
    model: str
    latency_ms: int
    degraded: bool = False
    error: str | None = None
    attempts: int = 1
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient:
    """
    异步 LLM 客户端

    特性：
    - 三级降级链，LLM 不可用时系统仍可运行
    - 指数退避重试
    - 全链路耗时埋点
    - 规则引擎兜底，保证返回非空结果
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._primary_client: httpx.AsyncClient | None = None
        self._fallback_client: httpx.AsyncClient | None = None
        self._primary_healthy: bool | None = None
        self._last_error: str | None = None
        self._available_models_cache: tuple[float, list[str]] | None = None
        # 并发闸门：免费网关在并发压力下易返回 5xx/429，限制并发保证稳定性
        self._semaphore = asyncio.Semaphore(2)
        # 请求节流：保证两次调用之间有最小间隔
        self._min_interval = 1.0
        self._last_call_at = 0.0
        # 429 熔断：连续限流后暂停一段时间
        self._rate_limited_until = 0.0
        self._stats: dict[str, int] = {
            "primary_success": 0,
            "fallback_success": 0,
            "degraded_count": 0,
            "failure_count": 0,
            "rate_limited": 0,
        }

    async def _get_primary(self) -> httpx.AsyncClient:
        """惰性初始化主客户端"""
        if self._primary_client is None:
            self._primary_client = httpx.AsyncClient(
                base_url=self._settings.llm_api_base,
                headers={
                    "Authorization": f"Bearer {self._settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._settings.llm_timeout_seconds),
            )
        return self._primary_client

    async def _get_fallback(self) -> httpx.AsyncClient:
        """惰性初始化降级客户端"""
        if self._fallback_client is None:
            self._fallback_client = httpx.AsyncClient(
                base_url=self._settings.llm_fallback_base,
                headers={"Content-Type": "application/json"},
                timeout=httpx.Timeout(120.0),
            )
        return self._fallback_client

    async def close(self) -> None:
        """关闭连接池"""
        if self._primary_client:
            await self._primary_client.aclose()
            self._primary_client = None
        if self._fallback_client:
            await self._fallback_client.aclose()
            self._fallback_client = None

    async def available_model_ids(self) -> list[str] | None:
        now = time.monotonic()
        if self._available_models_cache and now-self._available_models_cache[0]<300:
            return self._available_models_cache[1]
        try:
            client = await self._get_primary()
            response = await client.get('/models', timeout=5)
            response.raise_for_status()
            ids = [row['id'] for row in response.json().get('data',[]) if isinstance(row.get('id'),str)]
            self._available_models_cache = (now,ids)
            return ids
        except Exception:
            return None

    def _provider_error(self, response) -> str:
        try: message = response.json().get('error',{}).get('message','')
        except Exception: message = response.text[:500]
        key = self._settings.llm_api_key
        if key: message = str(message).replace(key,'[redacted]')
        return f'HTTP {response.status_code}: {message}'[:700]

    async def apply_config(
        self,
        api_base: str,
        api_key: str,
        model: str,
    ) -> None:
        """
        热更新主后端配置并**立即生效**

        ----------
        为什么必须关掉旧连接池 ----------
        `_get_primary` 是惰性缓存：首次调用时用当时的
        base_url / api_key 建httpx.Client，之后一直复用。
        只改 settings 的话，旧 client 的 Authorization 头
        仍是旧 key —— 表现为「保存成功但还是用旧配置」，
        而且 /api/health 探测的还是旧地址，极难排查。
        所以必须 close 让下次调用重建。

        同时清掉限流冷却状态：换服务商后旧的 429 冷却期
        不该继续拖累新配置（用户换了 key 就是要继续用）。
        """
        self._settings.llm_api_base = api_base.rstrip("/")
        self._settings.llm_api_key = api_key.strip()
        self._settings.llm_model = model.strip()
        self._available_models_cache = None

        # 重建连接池：让新的 base_url / Authorization 生效
        if self._primary_client:
            await self._primary_client.aclose()
            self._primary_client = None

        # 换配置即视为重新开始，不继承旧服务的限流冷却
        self._rate_limited_until = 0.0
        self._last_error = None
        self._primary_healthy = None

        logger.info(
            "llm_config_applied",
            base=self._settings.llm_api_base,
            model=self._settings.llm_model,
            key_tail=self._settings.llm_api_key[-4:] if self._settings.llm_api_key else "",
        )

    async def probe(
        self, api_base: str, api_key: str, model: str
    ) -> dict[str, Any]:
        """
        试连测试：用一个**独立的**临时客户端，不动当前配置。

        这样用户在弹窗里填错 key 时不会打断正在服务的 LLM，
        也不会污染限流状态 —— 相当于「试跑一次再决定要不要保存」。
        """
        if not api_key.strip():
            return {"ok": False, "message": "API Key 为空", "models": []}
        try:
            async with httpx.AsyncClient(
                base_url=api_base.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {api_key.strip()}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            ) as probe_client:
                resp = await probe_client.get("/models")
                if resp.status_code != 200:
                    return {
                        "ok": False,
                        "message": f"HTTP {resp.status_code}："
                        f"{resp.text[:120]}",
                        "models": [],
                    }
                data = resp.json()
                names = [
                    m.get("id", "")
                    for m in (data.get("data") or [])
                    if isinstance(m, dict)
                ]
                hint = ""
                if model and names and model not in names:
                    # 不阻断 —— 有些网关 /models 不含全部模型，
                    # 但chat 仍可用，所以只提示不判失败
                    hint = f"（模型 {model} 不在 /models 列表中，可仍尝试保存）"
                return {
                    "ok": True,
                    "message": f"连接成功，可用模型 {len(names)} 个{hint}",
                    "models": names[:50],
                }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)[:200], "models": []}

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 2048,
        tool_choice: str | None = None,
        timeout: float = 120.0,
        on_delta: Any = None,
        on_reasoning: Any = None,
    ) -> tuple[str, list[dict[str, Any]], str, int]:
        """
        流式调用（OpenAI SSE 格式），逐段回调文本增量。

        ⚠️ 注意：这里用**回调**而不是 yield ——
        含 yield 的函数会被Python 视为异步生成器，
        而生成器里`return 值` 是语法错误（'return' with value）。
        回调写法还能让调用方决定「哪些增量推给用户」。

        Args:
            on_delta: 可选回调，收到每段文本增量时调用

        Returns:
            (完整文本, tool_calls, backend, 耗时ms)

        与 `chat_with_tools` 并存而不改动它 ——
        现有问答链路（非流式）不能因为加了个参数而冒风险。

        ----------
        为什么工具调用场景也能流式 ----------
        工具调用轮的「正文」通常很短（模型只输出 tool_calls 结构），
        真正需要流式的是**最后一轮**（拿到数据后组织答案）。
        所以中途即使收到 content 也照常 yield，不影响正确性。

        ⚠️ 不支持的情况：上游网关不支持 SSE 时会自动降级为
        「一次性返回」——由调用方通过 delta 是否为空来判断。
        """
        start = time.perf_counter()
        target_model = model or self._settings.llm_model
        temp = temperature if temperature is not None else self._settings.llm_temperature

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"

        if self.is_rate_limited():
            return "", [], "rate_limited", 0

        pieces: list[str] = []
        calls: list[dict[str, Any]] = []
        try:
            await self._throttle()
            async with self._semaphore:
                client = await self._get_primary()
                async with client.stream(
                    "POST", "/chat/completions", json=payload, timeout=timeout
                ) as resp:
                    if resp.status_code != 200:
                        await resp.aread()
                        self._last_error = self._provider_error(resp)
                        return (
                            "",
                            [],
                            f"error: HTTP {resp.status_code}",
                            int((time.perf_counter() - start) * 1000),
                        )
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue  # 心跳/注释行，跳过
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        hidden_piece = delta.get('reasoning_content')
                        if hidden_piece and on_reasoning:
                            callback = on_reasoning(hidden_piece)
                            if inspect.isawaitable(callback): await callback
                        # 文本增量
                        piece = delta.get("content")
                        if piece:
                            pieces.append(piece)
                            if on_delta is not None:
                                # ⚠️ on_delta 可能是协程函数
                                # （SSE 端点里要 await queue.put）。
                                # 一律同步调用只会留下一条
                                # RuntimeWarning: coroutine ... never awaited，
                                # 回调**从未真正执行**，增量一个都发不出去。
                                res = on_delta(piece)
                                if inspect.isawaitable(res):
                                    await res
                        # 工具调用增量（分片到达，需累积）
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            while len(calls) <= idx:
                                calls.append({"id":"", "name": "", "arguments": ""})
                            tgt = calls[idx]
                            if tc.get('id'): tgt['id'] = tc['id']
                            if tc.get("function", {}).get("name"):
                                tgt["name"] = tc["function"]["name"]
                            arg = tc.get("function", {}).get("arguments")
                            if arg:
                                tgt["arguments"] += json.dumps(arg, ensure_ascii=False) if isinstance(arg,dict) else arg
            return (
                "".join(pieces),
                [c for c in calls if c.get("name")],
                "primary",
                int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return (
                "".join(pieces),
                [],
                f"error: {type(exc).__name__}",
                int((time.perf_counter() - start) * 1000),
            )

    @property
    def stats(self) -> dict[str, int]:
        """调用统计，用于监控面板"""
        return dict(self._stats)

    @property
    def last_error(self) -> str | None:
        """最近一次错误"""
        return self._last_error

    def is_rate_limited(self) -> bool:
        """
        是否处于限流冷却期

        供上层（如问答模块）提前判断并短路，
        避免在必然失败的调用上浪费响应预算。
        """
        loop_time = asyncio.get_event_loop().time()
        return loop_time < self._rate_limited_until

    async def health_check(self) -> dict[str, Any]:
        """
        LLM 可用性探测

        供 /api/health 端点使用，实现"系统健康状态可见"。
        """
        result: dict[str, Any] = {
            "primary": {"available": False, "model": self._settings.llm_model},
            "fallback": {"available": False, "model": self._settings.llm_fallback_model},
            "degraded_mode": False,
        }

        if not self._settings.has_llm_key:
            result["primary"]["error"] = "未配置 API Key"
        else:
            try:
                client = await self._get_primary()
                resp = await client.get("/models", timeout=15.0)
                result["primary"]["available"] = resp.status_code == 200
                if resp.status_code != 200:
                    result["primary"]["error"] = f"HTTP {resp.status_code}"
            except Exception as exc:  # noqa: BLE001
                result["primary"]["error"] = str(exc)[:200]

        try:
            client = await self._get_fallback()
            resp = await client.get("/models", timeout=5.0)
            result["fallback"]["available"] = resp.status_code == 200
        except Exception:
            pass  # 降级模型未部署属正常情况

        result["degraded_mode"] = not (
            result["primary"]["available"] or result["fallback"]["available"]
        )
        return result

    async def _call_backend_raw(
        self,
        client: httpx.AsyncClient,
        model: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        调用后端并返回**完整的 assistant message 对象**。

        为什么需要它：`_call_backend` 只返回 content 字符串，
        会把 `tool_calls` 字段丢掉 —— 而工具调用正是
        「让 LLM 自己查数据库」的关键，丢了就等于没接工具。
        """
        resp = await client.post("/chat/completions", json=payload, timeout=timeout or self._settings.llm_timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        return {
            "content": content,
            "tool_calls": message.get("tool_calls") or [],
            "reasoning_content": message.get('reasoning_content'),
        }

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 2048,
        tool_choice: str | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """
        带工具声明的调用，返回 {"content", "tool_calls", "backend", "latency_ms"}。

        与 `chat` 分开而不是加参数：这个方法**不降级到规则兜底** ——
        工具调用场景下拿不到 tool_calls 就等于失败，
        静默退回模板答案会掩盖问题。返回 backend 供调用方判断。
        """
        start = time.perf_counter()
        target_model = model or self._settings.llm_model
        temp = temperature if temperature is not None else self._settings.llm_temperature

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tokens,
            "tools": tools,
            "tool_choice": tool_choice or "auto",
        }

        # 限流冷却期：直接告诉调用方，不要空跑
        if self.is_rate_limited():
            return {
                "content": "",
                "tool_calls": [],
                "backend": "rate_limited",
                "latency_ms": 0,
            }

        try:
            await self._throttle()
            async with self._semaphore:
                client = await self._get_primary()
                data = await self._call_backend_raw(client, target_model, payload, timeout=timeout)
            return {
                **data,
                "backend": "primary",
                "latency_ms": int((time.perf_counter() - start) * 1000),
            }
        except httpx.HTTPStatusError as exc:
            self._last_error = self._provider_error(exc.response)
            return {'content':'','tool_calls':[], 'backend':f'error: HTTP {exc.response.status_code}',
                    'latency_ms':int((time.perf_counter()-start)*1000),'error':self._last_error}
        except Exception as exc:  # noqa: BLE001
            return {
                "content": "",
                "tool_calls": [],
                "backend": f"error: {type(exc).__name__}",
                "latency_ms": int((time.perf_counter() - start) * 1000),
                "error": str(exc)[:200],
            }

    async def _call_backend(
        self,
        client: httpx.AsyncClient,
        model: str,
        payload: dict[str, Any],
    ) -> str:
        """
        调用指定后端并提取文本

        兼容推理模型：Qwen3-8/DeepSeek-R1 等会返回 reasoning_content 字段，
        且思考过程会占用大量 max_tokens，导致 content 为空字符串。
        此时需回退使用 reasoning_content，或提示调用方提高 max_tokens。
        """
        resp = await client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        if not content:
            # 推理模型思考耗尽 token 预算时正文为空，回退取思考内容
            content = (message.get("reasoning_content") or "").strip()
        return content

    async def _throttle(self) -> None:
        """请求节流 + 熔断检查"""
        loop = asyncio.get_running_loop()
        now = loop.time()

        # 熔断期未到则跳过 LLM，直接走降级
        if now < self._rate_limited_until:
            raise RuntimeError("LLM 处于限流冷却期")

        # 保证最小调用间隔
        elapsed = now - self._last_call_at
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_call_at = loop.time()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 2048,
        json_mode: bool = False,
        fallback_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        """
        执行一次 LLM 调用，自动完成降级

        Args:
            messages: OpenAI 格式的消息列表
            model: 指定模型，缺省用主模型
            temperature: 采样温度，缺省用全局配置
            max_tokens: 最大生成 token
            json_mode: 是否要求 JSON 输出
            fallback_prompt: 规则兜底时使用的保守回答
            tools: OpenAI 格式的函数声明列表。传入后模型可发起
                function_call —— 这是「让 LLM 自己查数据库」的前提。
                注意与 json_mode 互斥（部分网关两者同传会报错）。
            tool_choice: 工具选择策略，如 "auto" / "none"

        Returns:
            LLMResponse，content 永不为空
        """
        start = time.perf_counter()
        target_model = model or self._settings.llm_model
        temp = temperature if temperature is not None else self._settings.llm_temperature

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            # json_mode 与 tools 同传时，部分网关会报
            # "response_format 与 tools 不兼容"，故以 tools 优先。
            payload.pop("response_format", None)
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"

        # 第一级：主路径
        if self._settings.has_llm_key:
            for attempt in range(1, self._settings.llm_max_retries + 1):
                try:
                    await self._throttle()
                    async with self._semaphore:
                        client = await self._get_primary()
                        content = await self._call_backend(client, target_model, payload)
                    latency = int((time.perf_counter() - start) * 1000)
                    self._stats["primary_success"] += 1
                    self._primary_healthy = True
                    return LLMResponse(
                        content=content,
                        backend=LLMBackend.PRIMARY,
                        model=target_model,
                        latency_ms=latency,
                    )
                except httpx.HTTPStatusError as exc:
                    self._last_error = f"HTTP {exc.response.status_code}"
                    # 429 为免费网关速率限制，需较长冷却
                    if exc.response.status_code == 429:
                        retry_after = exc.response.headers.get("Retry-After")
                        remain = (
                            float(retry_after)
                            if retry_after and retry_after.isdigit()
                            else min(15 * (2 ** (attempt - 1)), 120)
                        )
                        # 本协程实际等待封顶 30 秒——实测网关给出过
                        # Retry-After: 2044 秒，直接 sleep 会让请求假死
                        # 34 分钟。超出部分由 _rate_limited_until 拦截
                        # 后续调用（快速降级），不需要本协程苦等。
                        wait = min(remain, 30.0)
                        self._stats["rate_limited"] += 1
                        logger.warning(
                            "llm_rate_limited",
                            attempt=attempt,
                            wait_seconds=remain,
                            capped_wait=wait,
                        )
                        # 触发熔断：冷却期内直接降级，不再冲击网关
                        self._rate_limited_until = (
                            asyncio.get_running_loop().time() + remain
                        )
                        # 长冷却（>5 分钟）时重试本身无意义，直接放弃本轮
                        if remain > 300 or attempt >= self._settings.llm_max_retries:
                            break
                        await asyncio.sleep(wait)
                        continue

                    logger.warning(
                        "llm_primary_http_error",
                        attempt=attempt,
                        status=exc.response.status_code,
                    )
                    # 其余 4xx 多为参数或权限问题，重试无意义
                    if exc.response.status_code in (400, 401, 402, 403, 404):
                        break
                    # 5xx 为网关侧临时故障，值得退避后重试
                    await asyncio.sleep(min(2**attempt, 10))
                except Exception as exc:  # noqa: BLE001
                    self._last_error = str(exc)[:200]
                    logger.warning("llm_primary_error", attempt=attempt, error=str(exc)[:200])
                    # 指数退避，避免在网关不稳时持续施压
                    await asyncio.sleep(min(2**attempt, 10))

        # 第二级：本地降级模型
        try:
            client = await self._get_fallback()
            content = await self._call_backend(
                client, self._settings.llm_fallback_model, payload
            )
            latency = int((time.perf_counter() - start) * 1000)
            self._stats["fallback_success"] += 1
            logger.info("llm_degraded_to_fallback")
            return LLMResponse(
                content=content,
                backend=LLMBackend.FALLBACK,
                model=self._settings.llm_fallback_model,
                latency_ms=latency,
                degraded=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_fallback_failed", error=str(exc)[:200])

        # 第三级：规则引擎兜底
        latency = int((time.perf_counter() - start) * 1000)
        self._stats["degraded_count"] += 1
        self._stats["failure_count"] += 1
        logger.error("llm_all_backends_failed", primary_error=self._last_error)
        return LLMResponse(
            content=fallback_prompt
            or "LLM 服务当前不可用，已切换至规则模式。检索到的相关情报已列出，请参考下方引用来源。",
            backend=LLMBackend.DEGRADED,
            model="rule-engine",
            latency_ms=latency,
            degraded=True,
            error=self._last_error,
        )

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1024,
        fallback: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], LLMResponse]:
        """
        要求 JSON 输出

        解析失败会重试一次，仍失败则返回兜底数据。
        返回 (解析结果, 原始响应)
        """
        # 强化 JSON 约束提示
        reinforced = list(messages)
        if not any("JSON" in str(m.get("content", "")) for m in reinforced):
            reinforced = [
                {
                    "role": "system",
                    "content": "你是一个 JSON 输出引擎。只返回合法 JSON，不含任何解释文字或 markdown 代码块标记。",
                },
                *reinforced,
            ]

        # 先用 json_mode；部分模型不支持该参数会返回 400，
        # 快速探测后去掉参数重试一次，避免走完整降级链浪费时间。
        json_mode_supported = True
        if self._settings.has_llm_key:
            try:
                client = await self._get_primary()
                async with self._semaphore:
                    probe = await client.post(
                        "/chat/completions",
                        json={
                            "model": model or self._settings.llm_model,
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 1,
                            "response_format": {"type": "json_object"},
                        },
                    )
                if probe.status_code == 400:
                    json_mode_supported = False
            except Exception:  # noqa: BLE001
                json_mode_supported = False

        resp = await self.chat(
            reinforced,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode_supported,
        )

        if resp.backend == LLMBackend.DEGRADED:
            return (fallback or {}), resp

        try:
            return (json.loads(resp.content), resp)
        except json.JSONDecodeError:
            # 剥离可能的 markdown 代码块后重试
            cleaned = resp.content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            try:
                return (json.loads(cleaned), resp)
            except json.JSONDecodeError:
                logger.warning("llm_json_parse_failed", raw=resp.content[:200])
                return (fallback or {}), resp


# 全局单例
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
