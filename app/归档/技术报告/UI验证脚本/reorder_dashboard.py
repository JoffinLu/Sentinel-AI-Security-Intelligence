# -*- coding: utf-8 -*-
"""把「数据源状态 + 定时调度」整块从看板末尾移到最前。

用行号切片做搬移，避免手工复制 200+ 行出错。
原：688..917（在 return 内的末尾）
新：紧跟 return ( 之后，作为第一块
"""
import io

P = r"D:\Desktop\Data\项目类\2026年9月27日——第三届 “中国电子杯” 高校 ICT 产教融合创新大赛\ICT-WB\前端界面\src\pages\DashboardPage.tsx"
lines = io.open(P, encoding="utf-8").read().split("\n")

# 行号从 1 开始 → 索引减1
START = 688  # 注释块起始「{/* ---- 数据源与调度 ----」在 688
END = 917    # </Row>

# 校验边界（防止行号漂移导致搬错块）
assert "数据源与调度" in lines[START - 1], f"起始行不符: {lines[START-1][:60]}"
assert lines[END - 1].strip() == "</Row>", f"结束行不符: {lines[END-1][:60]}"
assert "title=\"数据源状态\"" in "\n".join(lines[START - 1:END]), "块内缺数据源状态卡"
assert "title=\"定时调度\"" in "\n".join(lines[START - 1:END]), "块内缺定时调度卡"

block = lines[START - 1:END]
rest = lines[:START - 1] + lines[END:]

# 在 return ( 之后插入（rest 里的第一个 "  return (" 就是它）
idx = next(i for i, l in enumerate(rest) if l.rstrip() == "  return (")
# 跳过 return( 后的第一行（通常是空行或外层 div）
insert_at = idx + 1

new_lines = rest[:insert_at] + [""] + block + [""] + rest[insert_at:]
io.open(P, "w", encoding="utf-8").write("\n".join(new_lines))
print(f"已搬移 {len(block)} 行：数据源状态+定时调度 → 首位")