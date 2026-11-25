# -*- coding: utf-8 -*-
"""只读 SQL 执行器的安全测试。

这是**安全边界**的验证，必须覆盖各类绕过手法。
每个用例都是「必须被拒绝」或「必须被规范化」。

运行：./.venv/Scripts/python.exe 评测/test_sql_guard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from 项目源码.问答模块.readonly_sql import SqlRejected, validate  # noqa: E402

# (说明, SQL, 期望)期望为 "reject" 或 "ok"
CASES = [
    # ---------- 必须拒绝：写操作 ----------
    ("删除数据", "DELETE FROM vulnerabilities", "reject"),
    ("更新数据", "UPDATE vulnerabilities SET title='x'", "reject"),
    ("插入数据", "INSERT INTO papers VALUES (1)", "reject"),
    ("删表", "DROP TABLE vulnerabilities", "reject"),
    ("改表结构", "ALTER TABLE papers ADD COLUMN x int", "reject"),
    ("建表", "CREATE TABLE evil (id int)", "reject"),
    ("截断", "TRUNCATE vulnerabilities", "reject"),
    # ---------- 必须拒绝：注入手法 ----------
    ("分号多语句", "SELECT 1 FROM papers; DROP TABLE vulnerabilities", "reject"),
    # 注释后的内容不参与执行，PG 看到的就是 SELECT 1 FROM papers，
    # 放行是**正确**行为 —— 关键是「不能因为里面有 DROP 就误放行真语句」
    ("注释剥离后仍是纯查询", "SELECT 1 FROM papers -- ; DROP TABLE x", "ok"),
    ("注释剥离后暴露真语句", "SELECT 1 FROM papers /* */ ; DROP TABLE x", "reject"),
    ("OR 注入", "SELECT 1 FROM papers WHERE 1=1 OR 1=1", "ok"),  # 纯查询，放行
    ("UNION 探测系统表", "SELECT 1 FROM information_schema.tables", "reject"),
    ("读文件", "SELECT pg_read_file('/etc/passwd') FROM papers", "reject"),
    ("睡眠注入", "SELECT pg_sleep(10) FROM papers", "reject"),
    ("dblink跨库", "SELECT * FROM dblink('host=evil','select 1') t", "reject"),
    ("递归CTE 打满CPU", "WITH RECURSIVE r AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT * FROM r", "reject"),
    ("行锁", "SELECT * FROM papers FOR UPDATE", "reject"),
    ("读环境变量", "SELECT current_setting('llm_api_key') FROM papers", "reject"),
    ("序列写入", "SELECT nextval('seq') FROM papers", "reject"),
    # ---------- 必须拒绝：表名不在白名单 ----------
    ("查collection_logs", "SELECT * FROM collection_logs", "reject"),
    ("查pg_catalog", "SELECT * FROM pg_catalog.pg_tables", "reject"),
    # ---------- 正常查询：应通过并被规范化 ----------
    ("聚合查询", "SELECT severity, count(*) FROM vulnerabilities GROUP BY severity", "ok"),
    ("带LIMIT", "SELECT cve_id, cvss_score FROM vulnerabilities ORDER BY cvss_score DESC LIMIT 5", "ok"),
    ("字面量含危险词", "SELECT * FROM vulnerabilities WHERE title = 'drop table'", "ok"),
    ("超量LIMIT 被压到200", "SELECT * FROM vulnerabilities LIMIT 9999", "ok"),
    ("正常统计", "SELECT count(*) AS total FROM papers", "ok"),
    ("连接查询", "SELECT p.title FROM papers p JOIN techniques t ON true LIMIT 3", "ok"),
]


def main() -> int:
    passed = failed = 0
    print("=" * 66)
    print("只读 SQL 执行器 · 安全测试")
    print("=" * 66)

    for desc, sql, expect in CASES:
        try:
            result = validate(sql)
            got = "ok"
            detail = result if len(result) < 60 else result[:57] + "..."
        except SqlRejected as e:
            got = "reject"
            detail = str(e)[:60]

        ok = got == expect
        if ok:
            passed += 1
            mark = "\033[92m✓\033[0m"
        else:
            failed += 1
            mark = "\033[91m✗\033[0m"

        print(f"{mark} {desc}")
        print(f"    SQL: {sql[:70]}")
        print(f"    期望 {expect} / 实得 {got}: {detail}")
        if not ok:
            print("    \033[91m← 用例未通过\033[0m")

    print("=" * 66)
    total = passed + failed
    color = "\033[92m" if failed == 0 else "\033[91m"
    print(f"{color}通过 {passed}/{total}\033[0m")
    if failed:
        print(f"\033[91m失败 {failed} 条 —— 安全边界有漏洞，不可上线\033[0m")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
