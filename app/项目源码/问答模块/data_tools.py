"""Generic querying, immutable result snapshots, document reading and cross-table search."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import math
from typing import Any
from sqlalchemy import String, case, cast, func, or_, select, text
from .data_catalog import COLUMNS, KEYS, LABELS, MODELS, TEXT_COLUMNS, catalog
from .safe_sql import run_query
from ..知识库.database import get_session

MAX_DATASETS = 8
CONTEXT_BUDGET = 140000


def json_value(value):
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, dict): return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value, (tuple, list)): return [json_value(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)): return value
    return str(value)


def _types(columns, rows):
    out = {}
    for index, name in enumerate(columns):
        sample = next((row[index] for row in rows if row[index] is not None), None)
        if isinstance(sample, bool): kind = 'BOOLEAN'
        elif isinstance(sample, int): kind = 'BIGINT'
        elif isinstance(sample, (float, Decimal)): kind = 'DOUBLE PRECISION'
        elif isinstance(sample, datetime): kind = 'TIMESTAMPTZ'
        elif isinstance(sample, date): kind = 'DATE'
        elif isinstance(sample, (dict, list)): kind = 'JSONB'
        elif name in ('cvss_score','confidence','ai_relevance_score'): kind = 'DOUBLE PRECISION'
        else: kind = 'TEXT'
        out[name] = kind
    return out


def remember_result(context: dict, result: dict, *, description='', parent=None) -> dict:
    columns, rows = result['columns'], result['rows']
    if len(set(columns)) != len(columns):
        raise ValueError('结果字段重名，请给同名列使用不同AS别名后重新查询')
    serial = context.get('dataset_sequence', 0) + 1
    context['dataset_sequence'] = serial
    dataset_id = f'result_{serial}'
    typed = _types(columns, rows)
    converted = json_value(rows)
    # Preserve row membership and scalar values. Very large text cells are explicit excerpts.
    truncated_cells = []
    for i, row in enumerate(converted):
        for j, value in enumerate(row):
            if isinstance(value, str) and len(value) > 8000:
                row[j] = value[:8000]
                truncated_cells.append({'row':i+1, 'column':columns[j], 'original_characters':len(value)})
    dataset = {'dataset_id':dataset_id, 'description':description, 'sql':result.get('sql'),
               'columns':columns, 'rows':converted, 'types':typed, 'row_count':len(rows),
               'truncated':bool(result.get('truncated')), 'sources':result.get('sources', []),
               'parent_dataset':parent, 'text_excerpts':truncated_cells,
               'created_at':datetime.now().astimezone().isoformat()}
    datasets = context.setdefault('datasets', {})
    datasets[dataset_id] = dataset
    while len(datasets) > MAX_DATASETS:
        datasets.pop(next(iter(datasets)))
    while len(json.dumps(datasets, ensure_ascii=False)) > CONTEXT_BUDGET and len(datasets) > 1:
        datasets.pop(next(iter(datasets)))
    # A single extremely wide result is bounded by returning fewer complete rows, never partial rows.
    while len(json.dumps(dataset, ensure_ascii=False)) > CONTEXT_BUDGET and len(dataset['rows']) > 1:
        dataset['rows'].pop()
        dataset['truncated'] = True
        dataset['row_count'] = len(dataset['rows'])
    context['active_dataset'] = dataset_id
    context['last_query'] = dataset
    context.setdefault('_turn_datasets', []).append(dataset_id)
    return public_result(dataset)


def public_result(dataset, *, offset=0, limit=25):
    rows = dataset['rows'][offset:offset+limit]
    return {k:v for k,v in dataset.items() if k not in ('types','rows')} | {
        'rows':rows, 'offset':offset, 'returned':len(rows),
        'has_more_saved_rows':offset+len(rows)<len(dataset['rows']),
        'scope_note':'这是已执行查询返回的结果集。返回/展示行数不等于全库总数；truncated=true时需缩小范围、分页或用COUNT求总数。',
    }


async def query_database(context, sql):
    result = await run_query(sql)
    return remember_result(context, result, description='数据库只读查询')


async def query_saved(context, dataset_id, sql):
    dataset = context.get('datasets', {}).get(dataset_id or context.get('active_dataset'))
    if dataset is None: raise ValueError('结果集不存在或已过期；请根据历史条件重新query_db，不表示知识库为空')
    snapshot = {**dataset, 'records':[dict(zip(dataset['columns'], row)) for row in dataset['rows']]}
    result = await run_query(sql, snapshot=snapshot)
    return remember_result(context, result, description=f'基于{dataset["dataset_id"]}实际已返回行继续分析', parent=dataset['dataset_id'])


def get_saved(context, dataset_id=None, offset=0, limit=50):
    dataset = context.get('datasets', {}).get(dataset_id or context.get('active_dataset'))
    if dataset is None: raise ValueError('结果集不存在或已过期，请重新执行相同条件的数据库查询')
    return public_result(dataset, offset=max(0,int(offset)), limit=min(200,max(1,int(limit))))


async def read_record(table, key):
    if table not in MODELS: raise ValueError('请先get_schema选择业务表')
    model = MODELS[table]
    column = getattr(model, KEYS[table])
    if KEYS[table] == 'id': key = int(key)
    async with get_session() as session:
        async with session.begin():
            await session.execute(text('SET TRANSACTION READ ONLY'))
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            row = (await session.execute(select(model).where(column == key))).scalars().first()
            if row is None:
                return {'found':False,'table':table,'key':key,'note':'仅表示这个编号未找到，不能据此判断整个知识库为空'}
            record = {c:getattr(row,c) for c in COLUMNS[table]}
            if table == 'vulnerabilities':
                for related in ('assets','enrichments'):
                    related_model = MODELS[related]
                    children = (await session.execute(select(related_model).where(related_model.vulnerability_id == row.id))).scalars().all()
                    record[related] = [{c:getattr(child,c) for c in COLUMNS[related]} for child in children]
    record = json_value(record)
    excerpts = []
    for name, value in record.items():
        if isinstance(value, str) and len(value) > 16000:
            record[name] = value[:16000]
            excerpts.append(name)
    return {'found':True,'table':table,'key':key,'record':record,'excerpt_fields':excerpts,
            'reading_boundary':'仅阅读知识库实际保存的字段；论文abstract和政策summary不是完整外部PDF，富化候选不等于已验证事实'}


async def search_documents(context, query, tables=None, limit=12):
    tables = tables or list(TEXT_COLUMNS)
    if set(tables) - TEXT_COLUMNS.keys(): raise ValueError('可搜索vulnerabilities/papers/policies/techniques')
    if not isinstance(query, str) or not query.strip(): raise ValueError('检索词不能为空')
    terms = list(dict.fromkeys(query.strip().split()))[:8]
    limit = min(40,max(1,int(limit)))
    hits = []
    async with get_session() as session:
        async with session.begin():
            await session.execute(text('SET TRANSACTION READ ONLY'))
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            for table in tables:
                model = MODELS[table]
                fields = [cast(getattr(model,c),String) for c in TEXT_COLUMNS[table]]
                predicates = [or_(*(field.ilike('%'+term+'%') for field in fields)) for term in terms]
                score = sum(case((p, 1), else_=0) for p in predicates)
                rows = (await session.execute(select(model,score.label('match_score')).where(or_(*predicates))
                                              .order_by(score.desc(),getattr(model, KEYS[table]))
                                              .limit(limit))).all()
                for row, match_score in rows:
                    title = getattr(row,'title',None) or getattr(row,'name','')
                    content = getattr(row,'description',None) or getattr(row,'abstract',None) or getattr(row,'summary','')
                    url = getattr(row,'source_url',None) or getattr(row,'pdf_url',None)
                    if table == 'techniques': url = f'https://atlas.mitre.org/techniques/{row.atlas_id}'
                    hits.append([table,str(getattr(row,KEYS[table])),title,(content or '')[:5000],
                                 getattr(row,'published_at',None),url,int(match_score)])
    # Keep each requested document type represented before filling remaining top matches.
    hits.sort(key=lambda row:-row[-1])
    balanced, selected = [], set()
    for table in tables:
        for index, row in enumerate(hits):
            if row[0] == table:
                balanced.append(row); selected.add(index); break
    balanced += [row for i,row in enumerate(hits) if i not in selected]
    result = {'columns':['table','record_key','title','text','published_at','url','matched_terms'],
              'rows':balanced[:limit], 'truncated':len(hits)>limit,
              'sources':[{'table':t,'label':LABELS[t]} for t in tables]}
    return remember_result(context, result, description=f'跨文档检索：{query}')


def result_citations(payload, context=None):
    sources = []
    if payload.get('found'):
        table, key, row = payload['table'], str(payload['key']), payload['record']
        sources.append(_citation(table,key,row))
    elif payload.get('columns'):
        for values in payload.get('rows', []):
            row = dict(zip(payload['columns'],values))
            table = row.get('table')
            if table in MODELS and row.get('record_key'):
                sources.append(_citation(table,str(row['record_key']),row))
                continue
            for name in ('vulnerabilities','papers','policies','techniques'):
                if row.get(KEYS[name]) is not None:
                    sources.append(_citation(name,str(row[KEYS[name]]),row)); break
        sources += [{'cve_id':None,'title':s.get('label',s['table']), 'url':None,
                     'source_name':s.get('label',s['table']), 'origin_source':s['table'], 'evidence_type':'query'}
                    for s in payload.get('sources',[])]
    return sources


def _citation(table,key,row):
    url = row.get('source_url') or row.get('pdf_url') or row.get('url')
    if not url and table == 'vulnerabilities' and key.upper().startswith('CVE-'):
        url = 'https://nvd.nist.gov/vuln/detail/'+key
    if not url and table == 'papers': url = 'https://arxiv.org/abs/'+key
    if not url and table == 'techniques': url = 'https://atlas.mitre.org/techniques/'+key
    return {'cve_id':key, 'title':row.get('title') or row.get('name') or key, 'url':url,
            'source_name':LABELS.get(table,table), 'origin_source':table,
            'published_at':row.get('published_at'), 'ingest_at':row.get('ingest_at'), 'evidence_type':'record'}


def result_tables(context):
    blocks = []
    def cell(value):
        if isinstance(value,(list,dict)): value = json.dumps(value,ensure_ascii=False)
        return str('未记录' if value is None else value).replace('|','\\|').replace('\n',' ').replace('\r',' ')[:400]
    for dataset_id in list(dict.fromkeys(context.get('_turn_datasets', []))):
        dataset = context.get('datasets',{}).get(dataset_id)
        if not dataset: continue
        cols = dataset['columns']
        visible = cols[:10]
        rows = dataset['rows'][:100]
        block = [f'\n\n### 查询结果 · {dataset_id}',
                 '| '+' | '.join(cell(c) for c in visible)+' |', '| '+' | '.join('---' for _ in visible)+' |']
        block += ['| '+' | '.join(cell(v) for v in row[:10])+' |' for row in rows]
        if dataset['truncated'] or len(dataset['rows'])>len(rows):
            block.append(f'\n当前展示{len(rows)}行，已保存{len(dataset["rows"])}行；这不是全库总数。可继续分页、筛选或统计。')
        if dataset.get('text_excerpts'):
            block.append('\n长文本以节选展示；可按编号读取详情。')
        blocks.append('\n'.join(block))
    return ''.join(blocks)
