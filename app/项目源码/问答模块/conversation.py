"""Bounded, isolated Agent conversation state, committed only after a full turn."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import json
import time
from typing import Any

MAX_SESSIONS = 128
SESSION_TTL = 6 * 3600
HISTORY_CHAR_BUDGET = 20_000


def clean_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    budget = HISTORY_CHAR_BUDGET
    for message in reversed((history or [])[-24:]):
        if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
            continue
        content = message.get('content')
        if not isinstance(content, str) or not content.strip():
            continue
        if message['role'] == 'assistant':
            # Structured results are retained separately from rendered tables.
            content = content.split('\n\n### 查询结果 · ', 1)[0]
        if budget <= 0:
            break
        content = content[:min(8000, budget)]
        result.append({'role': message['role'], 'content': content})
        budget -= len(content)
    result.reverse()
    return result


@dataclass
class Conversation:
    messages: list[dict[str, str]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    touched: float = field(default_factory=time.monotonic)


_sessions: OrderedDict[str, Conversation] = OrderedDict()


def get_conversation(session_id: str) -> Conversation:
    now = time.monotonic()
    for key, value in list(_sessions.items()):
        if now - value.touched > SESSION_TTL and not value.lock.locked():
            _sessions.pop(key, None)
    current = _sessions.get(session_id)
    if current is None:
        while len(_sessions) >= MAX_SESSIONS:
            idle = next((k for k, v in _sessions.items() if not v.lock.locked()), None)
            if idle is None:
                raise RuntimeError('问答会话繁忙，请稍后重试')
            _sessions.pop(idle)
        current = Conversation()
        _sessions[session_id] = current
    current.touched = now
    _sessions.move_to_end(session_id)
    return current


def clear_conversation(session_id: str) -> None:
    # In-flight requests hold their old object, and cannot commit into a new chat.
    _sessions.pop(session_id, None)


def commit_conversation(session_id: str, session: Conversation, history, context) -> None:
    if _sessions.get(session_id) is session:
        session.messages = clean_history(history)
        session.context = deepcopy(context)
        session.touched = time.monotonic()


def context_for_model(context: dict[str, Any], mode: str = 'agent') -> str:
    summary: dict[str, Any] = {}
    datasets = context.get('datasets') or {}
    if datasets:
        summary['当前结果集'] = context.get('active_dataset')
        summary['可继续分析的已保存结果'] = [
            {k:v for k,v in dataset.items() if k in ('dataset_id','description','sql','columns','row_count','truncated','parent_dataset','created_at')}
            | {'预览':dataset.get('rows',[])[:3]}
            for dataset in datasets.values()
        ]
        summary['操作'] = ('get_result可读取确切已返回行并分页，read_record读取记录全文/摘要；新话题用search_kb重新检索。精确统计或自由分析需开启Agent自主查询。'
                         if mode == 'retrieval' else 'query_result(dataset_id, sql)对确切已返回行继续计算；get_result分页；query_db查询全库或另一类数据。每轮可自由选择，不受旧批次锁定。')
    collection = context.get('collection')
    if collection and not datasets:
        summary['固定采集批次'] = {k: v for k, v in collection.items() if k != 'item_ids'}
        summary['批次操作'] = ('这是历史已核验资料；当前检索模式无采集批次工具，需精确重查时开启Agent自主查询。'
                            if mode == 'retrieval' else 'get_collection_batch 不传 batch_id 时继续查询这个批次，不能换成全库。')
        if context.get('batch_facts'):
            summary['已核验的批次统计'] = context['batch_facts']
    previous = context.get('last_query')
    if previous and not datasets:
        summary['上次查询'] = {k: v for k, v in previous.items() if k != 'rows'}
        summary['上次查询']['结果示例'] = previous.get('rows', [])[:3]
        summary['查询操作'] = ('这是历史已核验资料；当前检索模式使用search_kb/read_record补充文档依据。'
                           if mode == 'retrieval' else 'get_previous_query 可读取上次真实结果；它并非空知识库。')
    evidence = context.get('evidence') or []
    if evidence:
        summary['最近工具依据'] = evidence[-4:]
    if not summary:
        return ''
    return '\n\n【本会话已核验的查询范围与工具依据】\n' + json.dumps(summary, ensure_ascii=False, default=str)
