# -*- coding: utf-8 -*-
"""
按真实库数据生成 ATLAS 中文映射表（严格按英文名匹配，杜绝编号错位）

用法：读入 psql 导出的 atlas_all.txt（atlas_id|name|tactic|maturity），
输出可直接粘进 atlasTaxonomy.ts 的 TS 常量。
"""
import json
import sys
from collections import OrderedDict

SRC = r"C:/Users/29582/AppData/Local/Temp/atlas_all.txt"
OUT = r"C:/Users/29582/AppData/Local/Temp/atlas_tech_zh.ts"

# 战术编号 -> 中文名（ATLAS 官方 tactic 语义的标准译法）
TACTIC_ZH = {
    "AML.TA0000": "访问 AI 系统",
    "AML.TA0001": "准备攻击能力",
    "AML.TA0002": "侦察",
    "AML.TA0003": "资源开发",
    "AML.TA0004": "初始访问",
    "AML.TA0005": "执行",
    "AML.TA0006": "持久化",
    "AML.TA0007": "权限提升",
    "AML.TA0008": "防御规避",
    "AML.TA0009": "凭据访问",
    "AML.TA0010": "信息发现",
    "AML.TA0011": "横向移动",
    "AML.TA0012": "数据收集",
    "AML.TA0013": "数据外泄",
    "AML.TA0014": "产生影响",
    "AML.TA0015": "备用认证材料",
}

# 英文名 -> 中文意译。key 用英文名而非编号：编号容易错位，英文名是权威锚点。
NAME_ZH = {
    # 侦察
    "Search Open Technical Databases": "检索公开技术数据库",
    "Journals and Conference Proceedings": "检索学术期刊与会议论文",
    "Pre-Print Repositories": "检索预印本平台",
    "Technical Blogs": "检索技术博客",
    "Search Open AI Vulnerability Analysis": "检索公开 AI 漏洞分析",
    "Acquire Public AI Artifacts": "获取公开 AI 制品",
    "Datasets": "数据集",
    "Models": "模型",
    "AI Agent Configuration": "智能体配置",
    "Search Victim-Owned Websites": "检索受害方自有站点",
    "Search Application Repositories": "检索应用代码仓库",
    "Active Scanning": "主动扫描",
    "Search Open Websites/Domains": "检索公开网站/域名",
    "Code Repositories": "代码仓库",
    "Gather RAG-Indexed Targets": "搜集 RAG 索引目标",
    "Gather Victim Identity Information": "搜集受害者身份信息",
    # 资源开发
    "Create Proxy AI Model": "搭建代理 AI 模型",
    "Train Proxy via Gathered AI Artifacts": "用收集到的 AI 制品训练代理模型",
    "Train Proxy via Replication": "通过复现训练代理模型",
    "Use Pre-Trained Model": "使用预训练模型",
    "Acquire Infrastructure": "获取基础设施",
    "AI Development Workspaces": "AI 开发工作区",
    "Consumer Hardware": "消费级硬件",
    "Domains": "域名",
    "Physical Countermeasures": "物理防护措施",
    "Serverless": "无服务器架构",
    "AI Service Proxies": "AI 服务代理",
    "Obtain Capabilities": "获取攻击能力",
    "Adversarial AI Attack Implementations": "对抗攻击实现",
    "Software Tools": "软件工具",
    "Generative AI": "生成式 AI",
    "Develop Capabilities": "开发攻击能力",
    "Adversarial AI Attacks": "对抗式 AI 攻击",
    "Establish Accounts": "搭建行动账号",
    "Publish Poisoned Datasets": "发布投毒数据集",
    "Poison Training Data": "投毒训练数据",
    "Publish Poisoned Models": "发布被投毒模型",
    "Publish Hallucinated Entities": "发布幻觉实体",
    "LLM Prompt Crafting": "构造 LLM 提示词",
    "Retrieval Content Crafting": "构造检索内容",
    "Stage Capabilities": "暂存攻击能力",
    "Publish Poisoned AI Agent Tool": "发布被投毒的智能体工具",
    "Generate Deepfakes": "生成深度伪造内容",
    "Generate Malicious Commands": "生成恶意指令",
    "Craft Adversarial Data": "构造对抗数据",
    "White-Box Optimization": "白盒优化",
    "Black-Box Optimization": "黑盒优化",
    "Black-Box Transfer": "黑盒迁移",
    "Manual Modification": "手工修改",
    "Insert Backdoor Trigger": "植入后门触发器",
    "Verify Attack": "验证攻击效果",
    # 初始访问
    "AI Supply Chain Compromise": "入侵 AI 供应链",
    "Hardware": "硬件",
    "AI Software": "AI 软件",
    "Data": "数据",
    "Model": "模型",
    "Container Registry": "容器镜像仓库",
    "AI Agent Tool": "智能体工具",
    "AI Agent Tools": "智能体工具集",
    "AI Artifacts": "AI 制品",
    "Valid Accounts": "使用有效账号",
    "Exploit Public-Facing Application": "利用面向公网的应用",
    "Phishing": "网络钓鱼",
    "Spearphishing via Social Engineering LLM": "借助 LLM 社会工程学定向钓鱼",
    "Deepfake-Assisted Phishing": "深度伪造辅助钓鱼",
    "Drive-by Compromise": "路过式攻击",
    "Prompt Infiltration via Public-Facing Application": "经公网应用渗透提示词",
    "Evade AI Model": "规避 AI 模型检测",
    # 执行
    "User Execution": "诱导用户执行",
    "Unsafe AI Artifacts": "不安全 AI 制品",
    "Malicious Package": "恶意软件包",
    "Poisoned AI Agent Tool": "被投毒的智能体工具",
    "Malicious Link": "恶意链接",
    "Command and Scripting Interpreter": "命令与脚本解释器",
    "LLM Prompt Injection": "LLM 提示词注入",
    "Direct": "直接注入",
    "Indirect": "间接注入",
    "Triggered": "触发式注入",
    "AI Agent Tool Invocation": "调用智能体工具",
    "AI Agent Clickbait": "智能体诱导点击",
    "Deploy AI Agent": "部署 AI 智能体",
    # 持久化
    "Manipulate AI Model": "操纵 AI 模型",
    "Poison AI Model": "投毒 AI 模型",
    "Modify AI Model Architecture": "篡改模型架构",
    "Embed Malware": "嵌入恶意代码",
    "LLM Prompt Self-Replication": "LLM 提示词自复制",
    "RAG Poisoning": "RAG 投毒",
    "AI Agent Context Poisoning": "智能体上下文投毒",
    "Memory": "记忆",
    "Thread": "会话线程",
    "Modify AI Agent Configuration": "篡改智能体配置",
    "AI Agent Tool Data Poisoning": "智能体工具数据投毒",
    "AI Agent Tool Poisoning": "智能体工具投毒",
    # 权限提升 / 防御规避
    "LLM Trusted Output Components Manipulation": "操纵 LLM 可信输出组件",
    "Citations": "伪造引用",
    "LLM Prompt Obfuscation": "LLM 提示词混淆",
    "False RAG Entry Injection": "注入伪造 RAG 条目",
    "Impersonation": "身份冒充",
    "Masquerading": "伪装",
    "Corrupt AI Model": "破坏 AI 模型",
    "Manipulate User LLM Chat History": "操纵用户 LLM 对话历史",
    "Delay Execution of LLM Instructions": "延迟执行 LLM 指令",
    "Virtualization/Sandbox Evasion": "虚拟化/沙箱逃逸",
    "Exploitation for Defense Evasion": "利用漏洞实施防御规避",
    "AI Supply Chain Rug Pull": "AI 供应链「拉 Rug」",
    "AI Supply Chain Reputation Inflation": "AI 供应链声誉注水",
    # 信息发现
    "Discover AI Artifacts": "发现 AI 制品",
    "Discover AI Model Ontology": "发现 AI 模型本体",
    "Discover AI Model Family": "发现 AI 模型家族",
    "Discover LLM Hallucinations": "探测 LLM 幻觉",
    "Discover AI Model Outputs": "发现 AI 模型输出",
    "Discover LLM System Information": "探测 LLM 系统信息",
    "Special Character Sets": "特殊字符集",
    "System Instruction Keywords": "系统指令关键词",
    "System Prompt": "系统提示词",
    "Cloud Service Discovery": "云服务发现",
    "Discover AI Agent Configuration": "发现智能体配置",
    "Embedded Knowledge": "内嵌知识",
    "Tool Definitions": "工具定义",
    "Activation Triggers": "激活触发器",
    "Call Chains": "调用链",
    "Process Discovery": "进程发现",
    "AI Model Inference API Access": "访问 AI 模型推理 API",
    "Physical Environment Access": "访问物理环境",
    "Full AI Model Access": "获取完整 AI 模型",
    "AI-Enabled Product or Service": "AI 赋能产品或服务",
    # 凭据访问
    "Unsecured Credentials": "未加固的凭据",
    "RAG Credential Harvesting": "RAG 凭据收割",
    "Credentials from AI Agent Configuration": "从智能体配置中获取凭据",
    "OS Credential Dumping": "操作系统凭据转储",
    "Exploitation for Credential Access": "利用漏洞获取凭据",
    "AI Agent Tool Credential Harvesting": "智能体工具凭据收割",
    "Use Alternate Authentication Material": "使用备用认证材料",
    "Application Access Token": "应用访问令牌",
    # 数据收集
    "AI Artifact Collection": "收集 AI 制品",
    "Data from Information Repositories": "从信息仓库收集数据",
    "Data from Local System": "从本地系统收集数据",
    "Data from AI Services": "从 AI 服务收集数据",
    "RAG Databases": "RAG 数据库",
    # 外泄
    "Exfiltration via AI Inference API": "经 AI 推理 API 外泄",
    "Infer Training Data Membership": "推断训练数据成员关系",
    "Invert AI Model": "反演 AI 模型",
    "Extract AI Model": "提取 AI 模型",
    "Exfiltration via Cyber Means": "经网络手段外泄",
    "Extract LLM System Prompt": "提取 LLM 系统提示词",
    "LLM Data Leakage": "LLM 数据泄露",
    "LLM Response Rendering": "LLM 响应渲染外泄",
    "Exfiltration via AI Agent Tool Invocation": "经智能体工具外泄",
    # 产生影响
    "Denial of AI Service": "拒止 AI 服务",
    "Erode AI Model Integrity": "侵蚀 AI 模型完整性",
    "Cost Harvesting": "成本榨取",
    "Excessive Queries": "过量查询",
    "Resource-Intensive Queries": "资源密集型查询",
    "Agentic Resource Consumption": "智能体资源消耗",
    "Spamming AI System with Chaff Data": "用垃圾数据灌满 AI 系统",
    "External Harms": "外部危害",
    "Financial Harm": "经济损失",
    "Reputational Harm": "声誉损害",
    "Societal Harm": "社会危害",
    "User Harm": "用户危害",
    "AI Intellectual Property Theft": "AI 知识产权窃取",
    "Erode Dataset Integrity": "侵蚀数据集完整性",
    "Data Destruction via AI Agent Tool Invocation": "经智能体工具破坏数据",
    "LLM Jailbreak": "LLM 越狱",
    "Escape to Host": "逃逸至宿主机",
    "Reverse Shell": "反向 Shell",
    "AI Service API": "AI 服务 API",
    "AI Agent": "AI 智能体",
    "Machine Compromise": "主机沦陷",
    "Local AI Agent": "本地 AI 智能体",
}


def main() -> None:
    rows = []
    with open(SRC, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            rows.append(parts)

    tech = OrderedDict()
    missing = []
    unknown_tactic = set()
    for atlas_id, name, tactic, maturity in rows:
        zh = NAME_ZH.get(name)
        if zh is None:
            missing.append((atlas_id, name))
        else:
            tech[atlas_id] = zh
        if tactic and tactic not in TACTIC_ZH:
            unknown_tactic.add(tactic)

    print(f"总行数{len(rows)}  已翻译 {len(tech)}  未收录 {len(missing)}")
    if unknown_tactic:
        print("!! 未收录战术:", sorted(unknown_tactic))
    if missing:
        print("\n--- 未收录（将回退英文原名） ---")
        for aid, nm in missing:
            print(f"  {aid} = {nm}")

    # 输出 TS 片段
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("export const TECH_ZH: Record<string, string> = {\n")
        for aid, zh in tech.items():
            f.write(f"  '{aid}': '{zh}',\n")
        f.write("}\n")
    print(f"\n已写出 /tmp/atlas_tech_zh.ts （{len(tech)} 条）")


if __name__ == "__main__":
    main()
