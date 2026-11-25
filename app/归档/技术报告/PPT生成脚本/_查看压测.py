# -*- coding: utf-8 -*-
import json
d = json.load(open('归档/技术报告/数据/性能压测.json', encoding='utf-8'))
api = d['api']
for c in (10, 50, 100, 200):
    row = [a for a in api if a['concurrency'] == c]
    print(f"C{c:>3}:")
    for a in row:
        print(f"   {a['endpoint']:<8} P95={a['p95_ms']:>7.0f}ms  QPS={a['qps']:>6.1f}  err={a['errors']}")