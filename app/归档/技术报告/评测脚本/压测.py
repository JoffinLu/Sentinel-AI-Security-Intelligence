"""
性能压测脚本

赛题要求「压测报告 P50/P95/P99」。
用 Locust 模拟真实用户行为：查询、问答、检索混合负载。

运行：
    pip install locust
    locust -f 评测/压测.py --host http://127.0.0.1:8000

无头模式（直接出报告）：
    locust -f 评测/压测.py --headless -u 20 -r 5 -t 120 --host http://127.0.0.1:8000 \
        --csv 文档/压测结果
"""

from __future__ import annotations

import random

from locust import HttpUser, between, task


class SecurityQueryUser(HttpUser):
    """
    安全情报查询用户

    权重按真实使用分布设定：检索查询最多，问答次之，
    监控类请求最少（运维行为）。
    """

    # 等待时间模拟人类思考间隔（0.5~3 秒）
    wait_time = between(0.5, 3.0)

    # ---------- 任务权重 ----------

    @task(5)
    def search_vulns(self):
        """漏洞检索——最高频操作"""
        keywords = [
            "prompt injection",
            "model poisoning",
            "MCP",
            "mlflow",
            "gradio",
            "路径穿越",
            "jailbreak",
            "transformers",
        ]
        kw = random.choice(keywords)
        with self.client.get(
            "/api/qa/search",
            params={"q": kw, "top_k": 5},
            name="/api/qa/search",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"HTTP {r.status_code}")

    @task(3)
    def ask_question(self):
        """智能问答——核心能力，P95 需 ≤5 秒"""
        questions = [
            "CVE-2021-43831 怎么修复？",
            "mlflow 有哪些已知漏洞？",
            "哪些 MCP 漏洞已经有可用 POC？",
            "提示词注入相关的漏洞有哪些",
            "最近有哪些高危 AI 漏洞？",
            "vllm 有什么安全风险？",
        ]
        q = random.choice(questions)
        with self.client.post(
            "/api/qa/ask",
            params={"question": q, "session_id": "loadtest"},
            name="/api/qa/ask",
            catch_response=True,
        ) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")
            else:
                data = r.json()
                elapsed_ms = data.get("elapsed_ms", 0)
                if elapsed_ms > 5000:
                    r.failure(f"超预算 {elapsed_ms}ms")
                else:
                    r.success()

    @task(2)
    def list_vulns(self):
        """情报列表分页"""
        with self.client.get(
            "/api/vulns",
            params={"ai_only": "true", "limit": 20, "offset": random.randint(0, 100)},
            name="/api/vulns",
            catch_response=True,
        ) as r:
            r.success() if r.status_code == 200 else r.failure(f"HTTP {r.status_code}")

    @task(1)
    def monitor_dashboard(self):
        """监控面板——运维视角"""
        for ep in ("/api/monitor/overview", "/api/enrich/stats", "/api/graph/stats"):
            with self.client.get(ep, name=ep, catch_response=True) as r:
                r.success() if r.status_code == 200 else r.failure(f"HTTP {r.status_code}")

    @task(1)
    def orchestration_graph(self):
        """编排结构——低频，元数据变化不频繁"""
        with self.client.get(
            "/api/orchestration/graph", name="/api/orchestration/graph",
            catch_response=True,
        ) as r:
            r.success() if r.status_code == 200 else r.failure(f"HTTP {r.status_code}")
