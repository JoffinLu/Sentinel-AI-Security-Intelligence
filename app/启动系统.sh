#!/usr/bin/env bash
# ============================================================
# AI 安全知识情报系统 —— 本地一键启动脚本
#
# 用法：
#   bash 启动系统.sh          启动全部服务
#   bash 启动系统.sh stop     停止全部服务
#   bash 启动系统.sh status   查看运行状态
#   bash 启动系统.sh restart  重启
#   bash 启动系统.sh pause    暂停自动采集（系统仍可用，手动采集不受影响）
#   bash 启动系统.sh resume   恢复自动采集
#   bash 启动系统.sh collect  立即采集一轮全部数据源
#   bash 启动系统.sh enrich   批量富化 AI 安全漏洞
#
# 关于自动采集：
#   启动服务后，定时调度器会自动开始采集（这是赛题要求的
#   「自动化监测与采集」）。各数据源按差异化周期运行：
#   RSS 1 小时 / NVD、GHSA 6 小时 / 其余 12 小时 / ATLAS 7 天。
#   若不希望自动采集（例如演示前冻结数据、或担心上游限流），
#   用 pause 暂停——它只挂起定时任务，系统照常可查询与问答。
#   若只是临时采一次，用 collect 或界面上的「采集」按钮。
# ============================================================

set -u

# ---------- 路径与常量 ----------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT/.venv/Scripts/python.exe"
VENV_PY_ALT="$ROOT/.venv/bin/python"
NODE_BIN="D:/nodejs/node.exe"
FRONTEND_DIR="$ROOT/前端界面"
LOG_DIR="$ROOT/数据"
BACKEND_PORT=8000
FRONTEND_PORT=5173

# ---------- 颜色输出 ----------
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
    BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; NC=''
fi

info()  { echo -e "${BLUE}[信息]${NC} $*"; }
ok()    { echo -e "${GREEN}[成功]${NC} $*"; }
warn()  { echo -e "${YELLOW}[警告]${NC} $*"; }
error() { echo -e "${RED}[错误]${NC} $*"; }

# ---------- 定位 Python ----------
find_python() {
    if [ -f "$VENV_PY" ]; then
        echo "$VENV_PY"
    elif [ -f "$VENV_PY_ALT" ]; then
        echo "$VENV_PY_ALT"
    else
        echo ""
    fi
}

# ---------- 端口占用检测 ----------
port_pid() {
    netstat -ano 2>/dev/null | grep ":$1 " | grep LISTENING | awk '{print $5}' | head -1
}

is_port_busy() {
    local pid
    pid=$(port_pid "$1")
    [ -n "$pid" ] && [ "$pid" != "0" ]
}

free_port() {
    local pid
    pid=$(port_pid "$1")
    if [ -n "$pid" ] && [ "$pid" != "0" ]; then
        taskkill //F //PID "$pid" >/dev/null 2>&1 && ok "已释放端口 $1（进程 $pid）"
        sleep 1
    fi
}

# ---------- 依赖检查 ----------
check_deps() {
    local missing=0

    if [ -z "$(find_python)" ]; then
        error "未找到 Python 虚拟环境"
        echo "       请先执行：$ROOT/.venv/Scripts/python.exe -m venv .venv（Windows）"
        missing=1
    fi

    if ! command -v docker >/dev/null 2>&1; then
        error "未找到 Docker，请安装 Docker Desktop"
        missing=1
    fi

    if [ ! -f "$NODE_BIN" ]; then
        warn "未找到 Node.js（$NODE_BIN），前端将无法启动"
        warn "如已安装在其他位置，请修改本脚本中的 NODE_BIN 变量"
    fi

    if [ ! -f "$ROOT/.env" ]; then
        warn "未找到 .env 配置文件"
        if [ -f "$ROOT/.env.example" ]; then
            cp "$ROOT/.env.example" "$ROOT/.env"
            ok "已从 .env.example 创建 .env（请检查 LLM_API_KEY 配置）"
        fi
    fi

    [ "$missing" -eq 0 ] && return 0 || return 1
}

# ---------- 启动数据层 ----------
start_infra() {
    info "检查数据层容器…"
    cd "$ROOT/运维" || return 1

    export COMPOSE_PROJECT_NAME=aiintel
    if docker compose -f docker-compose.基础设施.yml up -d 2>&1 | tail -3; then
        ok "数据层容器已启动"
    else
        error "数据层启动失败"
        return 1
    fi

    # 等待数据库就绪
    info "等待数据库就绪…"
    for i in $(seq 1 30); do
        if docker exec aiintel-postgres pg_isready -U aiintel -d ai_security_intel >/dev/null 2>&1; then
            ok "数据库就绪"
            return 0
        fi
        sleep 2
    done
    warn "数据库等待超时，请检查容器状态"
    return 1
}

# ---------- 启动后端 ----------
start_backend() {
    local py
    py=$(find_python)
    if [ -z "$py" ]; then
        error "Python 环境不存在"
        return 1
    fi

    is_port_busy "$BACKEND_PORT" && info "后端已在运行（端口 $BACKEND_PORT）" && return 0

    info "启动后端 API 服务…"
    cd "$ROOT" || return 1
    nohup "$py" -m uvicorn 项目源码.后端服务.app:app \
        --host 0.0.0.0 --port "$BACKEND_PORT" \
        > "$LOG_DIR/api.log" 2>&1 &

    # 等待就绪
    for i in $(seq 1 25); do
        sleep 1
        if curl -s -m 3 "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
            ok "后端已就绪 → http://localhost:$BACKEND_PORT"
            return 0
        fi
    done
    error "后端启动超时，请查看 $LOG_DIR/api.log"
    return 1
}

# ---------- 启动前端 ----------
start_frontend() {
    if [ ! -f "$NODE_BIN" ]; then
        warn "跳过前端启动（未找到 Node.js）"
        return 0
    fi

    is_port_busy "$FRONTEND_PORT" && info "前端已在运行（端口 $FRONTEND_PORT）" && return 0

    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        info "首次运行，安装前端依赖（需几分钟）…"
        cd "$FRONTEND_DIR" || return 1
        "$NODE_BIN" /d/nodejs/node_modules/npm/bin/npm-cli.js install \
            --no-audit --no-fund --registry=https://registry.npmmirror.com 2>&1 | tail -2
    fi

    info "启动前端界面…"
    cd "$FRONTEND_DIR" || return 1
    PATH="D:/nodejs:$PATH" nohup "$NODE_BIN" ./node_modules/vite/bin/vite.js dev \
        --host 0.0.0.0 --port "$FRONTEND_PORT" \
        > "$LOG_DIR/前端.log" 2>&1 &

    for i in $(seq 1 20); do
        sleep 1
        if curl -s -m 3 "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null 2>&1; then
            ok "前端已就绪 → http://localhost:$FRONTEND_PORT"
            return 0
        fi
    done
    error "前端启动超时，请查看 $LOG_DIR/前端.log"
    return 1
}

# ---------- 停止 ----------
stop_all() {
    info "停止前端与后端服务…"
    free_port "$FRONTEND_PORT"
    free_port "$BACKEND_PORT"
    ok "前端与后端已停止（数据层容器保持运行，数据不会丢失）"
}

# ---------- 状态 ----------
show_status() {
    echo ""
    echo -e "${CYAN}========== 系统运行状态 ==========${NC}"
    echo ""

    printf "%-16s" "数据层容器:"
    if command -v docker >/dev/null 2>&1; then
        local running
        running=$(docker ps --filter "name=aiintel-" --format "{{.Names}}" 2>/dev/null | wc -l)
        if [ "$running" -ge 3 ]; then
            echo -e "${GREEN}运行中（$running 个容器）${NC}"
        else
            echo -e "${YELLOW}部分未启动（$running/3）${NC}"
        fi
    else
        echo -e "${RED}Docker 未安装${NC}"
    fi

    for entry in "后端 API:$BACKEND_PORT" "前端界面:$FRONTEND_PORT"; do
        local name="${entry%%:*}" port="${entry##*:}"
        printf "%-16s" "$name:"
        if is_port_busy "$port"; then
            echo -e "${GREEN}运行中 → http://localhost:$port${NC}"
        else
            echo -e "${RED}未运行${NC}"
        fi
    done

    # 数据概览
    if is_port_busy "$BACKEND_PORT"; then
        echo ""
        echo -e "${CYAN}---------- 数据概览 ----------${NC}"
        local health
        health=$(curl -s -m 5 "http://127.0.0.1:$BACKEND_PORT/api/monitor/overview" 2>/dev/null)
        if [ -n "$health" ]; then
            echo "$health" | "$ROOT/.venv/Scripts/python.exe" -c "
import json, sys
try:
    d = json.load(sys.stdin)
    t = d.get('totals', {})
    print(f\"  情报总量: {t.get('vulnerabilities', 0)} 条\")
    print(f\"  AI 安全:  {t.get('ai_security', 0)} 条\")
    print(f\"  论文:     {t.get('papers', 0)} 篇\")
    print(f\"  攻击技:   {t.get('techniques', 0)} 项\")
    print(f\"  数据源:   {t.get('sources', 0)} 类\")
except Exception:
    pass
" 2>/dev/null
        fi

        local enrich
        enrich=$(curl -s -m 5 "http://127.0.0.1:$BACKEND_PORT/api/enrich/stats" 2>/dev/null)
        if [ -n "$enrich" ]; then
            echo "$enrich" | "$ROOT/.venv/Scripts/python.exe" -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(f\"  已富化:   {d.get('enriched_vulnerabilities', 0)} 条（7 维覆盖率 {d.get('overall_coverage', 0)}%）\")
except Exception:
    pass
" 2>/dev/null
        fi
    fi
    echo ""
}

# ---------- 批量富化 ----------
run_enrich() {
    local py
    py=$(find_python)
    [ -z "$py" ] && { error "Python 环境不存在"; return 1; }

    local limit="${1:-20}"
    info "开始批量富化（$limit 条 AI 安全漏洞，约需 $((limit * 10 / 60)) 分钟）…"
    cd "$ROOT" || return 1
    "$py" -c "
import sys, asyncio
sys.path.insert(0, '.')
from 项目源码.后端服务.logger import configure_logging
from 项目源码.知识库.database import init_database, close_database
from 项目源码.富化模块.orchestrator import get_orchestrator
configure_logging()

async def main():
    await init_database()
    batch = await get_orchestrator().enrich_batch(limit=$limit, ai_only=True, concurrency=2)
    d = batch.to_dict()
    print()
    print(f\"  完成 {d['succeeded']}/{d['total']} 条\")
    print(f\"  完整度 >=80%: {d['completeness_rate']}%\")
    print(f\"  平均置信度: {d['avg_confidence']:.3f}\")
    await close_database()

asyncio.run(main())
" 2>&1 | grep -vE '"event"' | tail -5
}

# ---------- 主流程 ----------
case "${1:-start}" in
    start)
        echo ""
        echo -e "${CYAN}========================================${NC}"
        echo -e "${CYAN}  AI 安全知识情报系统 —— 启动${NC}"
        echo -e "${CYAN}========================================${NC}"
        echo ""
        check_deps || exit 1
        start_infra && start_backend && start_frontend
        if [ $? -eq 0 ]; then
            echo ""
            echo -e "${GREEN}========================================${NC}"
            echo -e "${GREEN}  全部服务已就绪${NC}"
            echo -e "${GREEN}========================================${NC}"
            echo ""
            echo "  可视化界面  http://localhost:$FRONTEND_PORT"
            echo "  API 文档    http://localhost:$BACKEND_PORT/docs"
            echo "  健康检查    http://localhost:$BACKEND_PORT/api/health"
            echo "  监控指标    http://localhost:$BACKEND_PORT/metrics"
            echo ""
            show_status
        else
            error "部分服务启动失败，请查看日志（$LOG_DIR/）"
            exit 1
        fi
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        sleep 2
        bash "$0" start
        ;;
    status)
        show_status
        ;;
    enrich)
        run_enrich "${2:-20}"
        ;;
    pause)
        # 暂停自动采集：系统保持可用，只是不再定时发起采集
        if curl -s -m 8 -X POST "http://127.0.0.1:$BACKEND_PORT/api/monitor/scheduler/pause" >/dev/null 2>&1; then
            ok "已暂停自动采集（系统仍可查询与问答，手动采集不受影响）"
        else
            error "暂停失败，请确认后端已启动"
            exit 1
        fi
        ;;
    resume)
        if curl -s -m 8 -X POST "http://127.0.0.1:$BACKEND_PORT/api/monitor/scheduler/resume" >/dev/null 2>&1; then
            ok "已恢复自动采集"
        else
            error "恢复失败，请确认后端已启动"
            exit 1
        fi
        ;;
    collect)
        # 立即采集一轮全部数据源（不等调度周期）
        info "正在触发全部数据源采集，请稍候…"
        if curl -s -m 600 -X POST "http://127.0.0.1:$BACKEND_PORT/api/collect/all" >/dev/null 2>&1; then
            ok "采集完成，刷新监控看板查看结果"
        else
            error "采集失败或超时，请查看日志（$LOG_DIR/api.log）"
            exit 1
        fi
        ;;
    *)
        echo "用法: bash 启动系统.sh [start|stop|restart|status]"
        echo "            bash 启动系统.sh pause      # 暂停自动采集"
        echo "            bash 启动系统.sh resume     # 恢复自动采集"
        echo "            bash 启动系统.sh enrich [数量]  # 批量富化"
        exit 1
        ;;
esac
