# -*- coding: utf-8 -*-
"""把「采集动态」卡片从指标卡之后挪到指标卡之前。

目标顺序：数据源状态+定时调度 → 采集动态 → 关键指标 → 时效性 → 图表
"""
import io

P = r"D:\Desktop\Data\项目类\2026年9月27日——第三届 “中国电子杯” 高校 ICT 产教融合创新大赛\ICT-WB\前端界面\src\pages\DashboardPage.tsx"
lines = io.open(P, encoding="utf-8").read().split("\n")

feed_i = next(i for i, l in enumerate(lines) if "CollectFeedCard sourceColor" in l)
# 连同上面的注释块（通常 3 行）一起搬
start = feed_i
while start > 0 and lines[start - 1].strip().startswith("{/*"):
    start -= 1
end = feed_i  # 单行组件

block = lines[start:end + 1]
rest = lines[:start] + lines[end + 1:]

# 插到「关键指标」注释块之前
key_i = next(i for i, l in enumerate(rest) if "关键指标" in l and l.strip().startswith("{/*"))
insert_at = key_i

new_lines = rest[:insert_at] + block + [""] + rest[insert_at:]
io.open(P, "w", encoding="utf-8").write("\n".join(new_lines))
print(f"已搬移 {len(block)} 行：采集动态 → 指标卡之前")