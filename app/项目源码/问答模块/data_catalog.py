"""One catalog of readable business data, actual columns and relationships."""
from __future__ import annotations

from typing import Any
from ..知识库.models import Asset, CollectionLog, Enrichment, Paper, Policy, Technique, Vulnerability

MODELS = {
    'vulnerabilities': Vulnerability, 'papers': Paper, 'policies': Policy,
    'techniques': Technique, 'assets': Asset, 'enrichments': Enrichment,
    'collection_logs': CollectionLog,
}
LABELS = {'vulnerabilities':'漏洞情报', 'papers':'学术论文', 'policies':'政策法规',
          'techniques':'ATLAS技术与案例', 'assets':'受影响资产', 'enrichments':'自动富化结果',
          'collection_logs':'采集日志'}
KEYS = {'vulnerabilities':'cve_id', 'papers':'arxiv_id', 'policies':'doc_key',
        'techniques':'atlas_id', 'assets':'id', 'enrichments':'id', 'collection_logs':'id'}
PROTECTED_COLUMNS = frozenset({'embedding', 'raw_data'})
COLUMNS = {name: tuple(c.name for c in model.__table__.columns if c.name not in PROTECTED_COLUMNS)
           for name, model in MODELS.items()}
TEXT_COLUMNS = {'vulnerabilities':('cve_id','title','description','affected_assets','ai_keywords'),
                'papers':('arxiv_id','title','abstract','ai_topics'),
                'policies':('doc_key','title','summary','org','doc_numbers','ai_keywords'),
                'techniques':('atlas_id','name','description','tactic','attack_refs')}
MEANINGS = {
    'id':'数据库内部主键，关联资产和富化时使用', 'cve_id':'漏洞/情报业务编号，可能是CVE/GHSA或rss:/poc:内部键',
    'origin_source':'规范化采集来源代码，例如nvd、ghsa、osv、rss；按“数据源/来源”分组通常使用此列',
    'source_name':'原始来源项目或公告名称，可能是仓库URL；不是规范化采集来源，不用它替代origin_source分组',
    'source_code':'采集日志中的规范化采集来源代码',
    'cvss_score':'0至10的CVSS评分，NULL表示未评分；平均值和评分排名排除NULL',
    'severity':'原始严重度标签CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN，与CVSS区间并非总是一致',
    'published_at':'对外发布日期时间', 'ingest_at':'首次写入本库的时间，不是最后一次采集或更新',
    'modified_at':'源记录修改时间', 'finished_at':'本次采集任务的完成时间',
    'vulnerability_id':'外键，关联vulnerabilities.id，不是cve_id',
    'dimension':'富化维度代码，如cvss/asset/poc/paper/fix/technique/risk',
    'confidence':'自动富化置信度0至1，不等于外部评测准确率',
    'status':'任务状态；enrichments.status=success表示富化成功，不是completed；采集成功状态也是success',
    'enrichment_status':'漏洞整体富化状态，completed表示已完成；与enrichments每维status=success区分',
    'poc_refs':'JSON数组，保存POC候选引用，不等于已验证可利用；非空可用jsonb_array_length检查',
    'paper_refs':'论文引用JSON数组', 'technique_refs':'ATLAS技术引用JSON数组',
    'abstract':'本库已保存的论文摘要，不是完整论文PDF', 'summary':'本库已保存的政策摘要，不是完整外部文档',
    'is_case_study':'true为案例研究，false为技术基线；统计口径需要区分',
    'content':'富化内容JSON，需同时检查status和confidence',
}


def catalog(tables: list[str] | None = None) -> dict[str, Any]:
    selected = tables or list(MODELS)
    unknown = set(selected) - MODELS.keys()
    if unknown:
        raise ValueError('未知业务表：' + ', '.join(sorted(unknown)))
    return {name: {
        'label': LABELS[name], 'key': KEYS[name],
        'columns': {c.name: {'type':str(c.type), 'nullable':c.nullable,'meaning':MEANINGS.get(c.name,c.comment or '')}
                    for c in MODELS[name].__table__.columns if c.name in COLUMNS[name]},
        'relationships': [{'column':c.name, 'references':f.target_fullname}
                          for c in MODELS[name].__table__.columns for f in c.foreign_keys],
    } for name in selected}


def catalog_text() -> str:
    lines = ['【可读取的真实业务表】']
    for name, info in catalog().items():
        fields = ', '.join(f'{k}:{v["type"]}'+(f'（{v["meaning"]}）' if v['meaning'] else '') for k,v in info['columns'].items())
        lines.append(f'{name}（{info["label"]}，业务编号{info["key"]}）：{fields}')
    lines.extend([
        '关联：assets.vulnerability_id、enrichments.vulnerability_id -> vulnerabilities.id。',
        'vulnerabilities.paper_refs、poc_refs、technique_refs、references 和富化 content 是JSON，关联需核对实际编号或链接，不能只按主题相近声称存在确定关系。',
        'techniques.is_case_study=false为技术基线，true为案例；行数实时查询，不能使用固定历史数量。',
        '论文已保存摘要 abstract，政策已保存摘要 summary；不能把摘要当成已读取整篇PDF。',
        'collection_logs.metrics.record_ids是完整批次ID列表（需检查record_ids_complete）；旧sample_ids只是通知样本，不能视为全批次。',
        '发布时间published_at、首次入库时间ingest_at、采集完成时间finished_at含义不同。CVSS空值是未评分。',
    ])
    return '\n'.join(lines)
