"""PostgreSQL read-only business queries validated by syntax tree and DB transaction."""
from __future__ import annotations

import asyncio
from typing import Any
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from .data_catalog import COLUMNS, LABELS, MODELS, PROTECTED_COLUMNS

ALLOWED_TABLES = frozenset(MODELS)
MAX_ROWS = 200
MAX_SQL_LEN = 16000
SAFE_FUNCTIONS = frozenset('''COUNT SUM AVG MIN MAX ROUND FLOOR CEIL CEILING ABS MOD POWER SQRT
AND OR NOT CASE IF IFNULL
COALESCE NULLIF GREATEST LEAST LOWER UPPER TRIM LTRIM RTRIM LENGTH CHAR_LENGTH CHARACTER_LENGTH
CONCAT CONCAT_WS SUBSTRING SUBSTR LEFT RIGHT REPLACE SPLIT_PART POSITION STRPOS STR_POSITION
CAST TRY_CAST CURRENT_DATE CURRENT_TIMESTAMP CURRENT_TIME NOW DATE_TRUNC TIMESTAMP_TRUNC
EXTRACT DATE_PART DATE DIFF DATE_DIFF DATEDIFF TO_CHAR TO_DATE TO_TIMESTAMP AGE
ROW_NUMBER RANK DENSE_RANK NTILE LAG LEAD FIRST_VALUE LAST_VALUE NTH_VALUE
STRING_AGG GROUP_CONCAT ARRAY_AGG ARRAY_LENGTH CARDINALITY UNNEST
JSONB_ARRAY_LENGTH JSON_ARRAY_LENGTH JSONB_ARRAY_ELEMENTS JSONB_ARRAY_ELEMENTS_TEXT
JSON_ARRAY_ELEMENTS JSON_ARRAY_ELEMENTS_TEXT JSONB_OBJECT_KEYS JSONB_TYPEOF JSON_TYPEOF
JSONB_EXTRACT_PATH JSONB_EXTRACT_PATH_TEXT JSON_EXTRACT JSON_EXTRACT_SCALAR JSONB_BUILD_OBJECT
JSON_BUILD_OBJECT JSONB_AGG JSON_AGG JSONB_OBJECT_AGG JSON_OBJECT_AGG
REGEXP_REPLACE REGEXP_MATCH REGEXP_MATCHES REGEXP_LIKE STARTS_WITH ENDS_WITH
PERCENTILE_CONT PERCENTILE_DISC PERCENT_RANK CUME_DIST STDDEV STDDEV_POP STDDEV_SAMP
VARIANCE VAR_POP VAR_SAMP BOOL_OR BOOL_AND EVERY FILTER ARRAY_TO_STRING ARRAY_CONTAINS
JSONB_CONTAINS JSONB_EXISTS JSONB_EXISTS_ANY JSONB_EXISTS_ALL JSONB_PATH_EXISTS
JSONB_TO_RECORDSET
'''.split())


class SqlRejected(Exception):
    pass


def _tree(sql: str, virtual_columns: dict[str, tuple[str, ...]] | None = None):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL_LEN:
        raise SqlRejected('查询为空或超过16000字符')
    try:
        statements = [s for s in sqlglot.parse(sql, read='postgres') if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise SqlRejected('SQL语法错误：' + str(exc)[:450]) from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise SqlRejected('仅允许一条SELECT/WITH只读查询')
    tree = statements[0]
    virtual_columns = virtual_columns or {}
    if any(isinstance(node, exp.Placeholder) for node in tree.walk()):
        raise SqlRejected('请使用SQL字面量；内部参数由工具管理')
    forbidden_types = tuple(getattr(exp, name) for name in
        ('DML','DDL','Command','Into','Lock','Transaction','Copy','Use','Set','Execute','Grant','Revoke') if hasattr(exp, name))
    if any(isinstance(node, forbidden_types) for node in tree.walk()):
        raise SqlRejected('查询包含写操作、行锁或非查询命令')
    if any(node.args.get('recursive') for node in tree.find_all(exp.With)):
        raise SqlRejected('不支持递归查询')
    for column in tree.find_all(exp.Column):
        if column.name.lower() in PROTECTED_COLUMNS:
            raise SqlRejected('内部向量和原始报文不可作为问答数据读取')
    for function in tree.find_all(exp.Func):
        name = function.name.upper() if isinstance(function, exp.Anonymous) else function.sql_name().upper()
        if name not in SAFE_FUNCTIONS:
            raise SqlRejected(f'不支持此查询函数：{name}；请使用常规统计、日期、字符串或JSON函数')
    tables = set()
    scopes = traverse_scope(tree)
    for scope in scopes:
        for source in scope.sources.values():
            if not isinstance(source, exp.Table):
                continue  # CTE/derived tables are checked in their own physical scopes.
            if not isinstance(source.this, exp.Identifier):
                continue  # Approved table-valued JSON functions were checked above.
            if source.catalog or (source.db and source.db.lower() != 'public') or source.name.lower() not in ALLOWED_TABLES | virtual_columns.keys():
                raise SqlRejected(f'不允许读取业务目录以外的表：{source.sql(dialect="postgres")}')
            tables.add(source.name.lower())
        if isinstance(scope.expression, exp.Select):
            expanded = []
            for projection in scope.expression.expressions:
                is_star = isinstance(projection, exp.Star) or isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
                if not is_star:
                    expanded.append(projection)
                    continue
                selected = [(alias, source) for alias, source in scope.sources.items()
                            if not getattr(projection, 'table', '') or alias == projection.table]
                if not selected:
                    raise SqlRejected('无法解析通配字段的来源')
                for alias, source in selected:
                    if isinstance(source, exp.Table) and source.name.lower() in (COLUMNS | virtual_columns):
                        expanded.extend(exp.column(c, table=alias, quoted=True) for c in (COLUMNS | virtual_columns)[source.name.lower()])
                    else:
                        expanded.append(exp.Column(this=exp.Star(), table=exp.to_identifier(alias)))
            scope.expression.set('expressions', expanded)
    if not tables:
        raise SqlRejected('查询必须读取至少一张业务表')
    limit = tree.args.get('limit')
    wanted = MAX_ROWS
    if limit is not None:
        value = limit.expression
        if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 0:
            raise SqlRejected('LIMIT需要非负整数')
        wanted = min(int(value.this), MAX_ROWS)
    tree = tree.limit(wanted, copy=False)
    return tree, sorted(tables), wanted


def validate(sql: str) -> str:
    tree, _, _ = _tree(sql)
    return tree.sql(dialect='postgres')


def _extract_sources(sql: str):
    _, tables, _ = _tree(sql)
    return [{'table':t, 'label':LABELS[t], 'freshness':None} for t in tables]


async def run_query(sql: str, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    from sqlalchemy import text
    from ..知识库.database import get_session
    import json
    virtual = {'previous_result':tuple(snapshot['columns'])} if snapshot else {}
    tree, tables, wanted = _tree(sql, virtual)
    canonical = tree.sql(dialect='postgres')
    parameters = {}
    if snapshot:
        if 'previous_result' not in tables or len(tables) != 1:
            raise SqlRejected('查询已保存结果时只能读取 previous_result；查询全库请使用query_db')
        if any(c.alias == 'previous_result' for c in tree.find_all(exp.CTE)):
            raise SqlRejected('不能重新定义 previous_result')
        def quoted(value): return '"' + value.replace('"', '""') + '"'
        definitions = ', '.join(quoted(c) + ' ' + snapshot['types'][c] for c in snapshot['columns'])
        seed = sqlglot.parse_one(f'SELECT * FROM jsonb_to_recordset(CAST(:qa_snapshot AS JSONB)) AS saved({definitions})', read='postgres')
        cte = exp.CTE(this=seed, alias=exp.TableAlias(this=exp.to_identifier('previous_result')))
        existing = tree.args.get('with_')
        tree.set('with_', exp.With(expressions=[cte] + (existing.expressions if existing else [])))
        parameters['qa_snapshot'] = json.dumps(snapshot['records'], ensure_ascii=False)
    executable = tree.limit(wanted + 1, copy=True).sql(dialect='postgres')
    executable = executable.replace('%(qa_snapshot)s', ':qa_snapshot')
    async with get_session() as session:
        async with session.begin():
            await session.execute(text('SET TRANSACTION READ ONLY'))
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            await session.execute(text('SET LOCAL search_path = public, pg_catalog'))
            result = await asyncio.wait_for(session.execute(text(executable), parameters), timeout=5.5)
            columns, raw = list(result.keys()), result.fetchall()
    return {'sql':canonical, 'columns':columns, 'rows':[list(r) for r in raw[:wanted]],
            'row_count':min(len(raw), wanted), 'truncated':len(raw) > wanted, 'limit':wanted,
            'sources':(snapshot.get('sources', []) if snapshot else [{'table':t, 'label':LABELS[t], 'freshness':None} for t in tables])}
