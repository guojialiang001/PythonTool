#!/bin/bash

# ============================================================================
# Orchestrator Proxy Service 启动脚本 (Linux)
#
# 代理转发服务，只中转以下两个请求到后端 110.42.62.193:8000
#
# 转发规则:
#   #1 HTTP:  POST /endpoint/chat/conversations/start
#             -> http://110.42.62.193:8000/endpoint/chat/conversations/start
#   #2 WS:    /endpoint/ws/chat?token=xxx
#             -> ws://110.42.62.193:8000/endpoint/ws/chat?token=xxx
#
# 使用方法:
#   ./start_orchestrator.sh              # 前台启动
#   ./start_orchestrator.sh --daemon     # 后台启动 (PM2)
#   ./start_orchestrator.sh --stop       # 停止服务
#   ./start_orchestrator.sh --restart    # 重启服务
#   ./start_orchestrator.sh --status     # 查看状态
#   ./start_orchestrator.sh --logs       # 查看日志
# ============================================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 服务配置
SERVICE_NAME="orchestrator-proxy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="orchestrator_service.py"
LOG_DIR="${SCRIPT_DIR}/logs"
PID_FILE="${LOG_DIR}/${SERVICE_NAME}.pid"

# 环境变量配置
export PROXY_HOST="${PROXY_HOST:-0.0.0.0}"
export PROXY_PORT="${PROXY_PORT:-8001}"
export BACKEND_HOST="${BACKEND_HOST:-110.42.62.193}"
export BACKEND_PORT="${BACKEND_PORT:-8000}"
export ENDPOINT_PREFIX="${ENDPOINT_PREFIX:-/endpoint}"
export BACKEND_API_PREFIX="${BACKEND_API_PREFIX:-/endpoint}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export HTTP_TIMEOUT="${HTTP_TIMEOUT:-30}"
export WS_TIMEOUT="${WS_TIMEOUT:-60}"

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 打印横幅
print_banner() {
    echo -e "${CYAN}"
    echo "╔════════════════════════════════════════════════════════════════════════════╗"
    echo "║        Orchestrator Proxy Service - 代理转发服务                           ║"
    echo "╠════════════════════════════════════════════════════════════════════════════╣"
    echo "║  说明: 只中转以下两个指定请求                                               ║"
    echo "╠════════════════════════════════════════════════════════════════════════════╣"
    echo "║  配置:                                                                      ║"
    echo "║    - 监听地址: ${PROXY_HOST}:${PROXY_PORT}                                             ║"
    echo "║    - 后端地址: ${BACKEND_HOST}:${BACKEND_PORT}                                       ║"
    echo "║    - 本地前缀: ${ENDPOINT_PREFIX}                                                ║"
    echo "║    - 后端前缀: ${BACKEND_API_PREFIX}                                               ║"
    echo "╠════════════════════════════════════════════════════════════════════════════╣"
    echo "║  转发规则:                                                                  ║"
    echo "║  #1 HTTP POST (开始新对话，获取 Token):                                     ║"
    echo "║      本地: POST ${ENDPOINT_PREFIX}/chat/conversations/start                      ║"
    echo "║      后端: POST http://${BACKEND_HOST}:${BACKEND_PORT}${BACKEND_API_PREFIX}/chat/conversations/start ║"
    echo "║  #2 WebSocket (对话):                                                       ║"
    echo "║      本地: WS ${ENDPOINT_PREFIX}/ws/chat?token=xxx                               ║"
    echo "║      后端: WS ws://${BACKEND_HOST}:${BACKEND_PORT}${BACKEND_API_PREFIX}/ws/chat?token=xxx ║"
    echo "╚════════════════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# 检查 Python 环境
check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        print_error "未找到 Python，请先安装 Python 3.8+"
        exit 1
    fi
    
    # 检查 Python 版本
    PYTHON_VERSION=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    print_info "Python 版本: $PYTHON_VERSION"
}

# 检查依赖
check_dependencies() {
    print_info "检查依赖..."
    
    # 检查必要的 Python 包
    $PYTHON_CMD -c "import fastapi" 2>/dev/null || {
        print_warning "缺少 fastapi，正在安装..."
        pip install fastapi
    }
    
    $PYTHON_CMD -c "import uvicorn" 2>/dev/null || {
        print_warning "缺少 uvicorn，正在安装..."
        pip install uvicorn
    }
    
    $PYTHON_CMD -c "import httpx" 2>/dev/null || {
        print_warning "缺少 httpx，正在安装..."
        pip install httpx
    }
    
    $PYTHON_CMD -c "import websockets" 2>/dev/null || {
        print_warning "缺少 websockets，正在安装..."
        pip install websockets
    }
    
    print_success "依赖检查完成"
}

# 创建日志目录
create_log_dir() {
    if [ ! -d "$LOG_DIR" ]; then
        mkdir -p "$LOG_DIR"
        print_info "创建日志目录: $LOG_DIR"
    fi
}

# 检查服务是否运行
is_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# 检查 PM2 是否可用
check_pm2() {
    if command -v pm2 &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# 清理占用端口的进程
kill_port_process() {
    local port=$1
    print_info "检查端口 ${port} 占用情况..."
    
    # 查找占用端口的进程
    local pids=$(lsof -t -i:${port} 2>/dev/null || netstat -tlnp 2>/dev/null | grep ":${port}" | awk '{print $7}' | cut -d'/' -f1 | grep -v '-')
    
    if [ -n "$pids" ]; then
        print_warning "端口 ${port} 被以下进程占用: $pids"
        for pid in $pids; do
            if [ -n "$pid" ] && [ "$pid" != "-" ]; then
                print_info "终止进程 PID: $pid"
                kill -9 "$pid" 2>/dev/null || true
            fi
        done
        sleep 1
        print_success "端口 ${port} 已清理"
    else
        print_info "端口 ${port} 未被占用"
    fi
}

# 清理 PM2 同名进程
cleanup_pm2_process() {
    if check_pm2; then
        if pm2 list 2>/dev/null | grep -q "$SERVICE_NAME"; then
            print_info "清理 PM2 中的同名进程: $SERVICE_NAME"
            pm2 stop "$SERVICE_NAME" 2>/dev/null || true
            pm2 delete "$SERVICE_NAME" 2>/dev/null || true
            sleep 1
            print_success "PM2 同名进程已清理"
        else
            print_info "PM2 中无同名进程"
        fi
    fi
}

# 启动前清理
pre_start_cleanup() {
    print_info "执行启动前清理..."
    
    # 清理 PM2 同名进程
    cleanup_pm2_process
    
    # 清理端口占用
    kill_port_process "$PROXY_PORT"
    
    print_success "启动前清理完成"
    echo ""
}

# 前台启动
start_foreground() {
    print_banner
    check_python
    check_dependencies
    create_log_dir
    
    # 执行启动前清理
    pre_start_cleanup
    
    print_info "启动代理服务 (前台模式)..."
    print_info "监听地址: ${PROXY_HOST}:${PROXY_PORT}"
    print_info "后端地址: ${BACKEND_HOST}:${BACKEND_PORT}"
    echo ""
    
    cd "$SCRIPT_DIR"
    $PYTHON_CMD "$PYTHON_SCRIPT"
}

# 后台启动 (使用 PM2 或 nohup)
start_daemon() {
    print_banner
    check_python
    check_dependencies
    create_log_dir
    
    # 执行启动前清理
    pre_start_cleanup
    
    if check_pm2; then
        start_with_pm2
    else
        start_with_nohup
    fi
}

# 使用 PM2 启动
start_with_pm2() {
    print_info "使用 PM2 启动服务..."
    
    # 创建 PM2 配置文件
    cat > "${SCRIPT_DIR}/ecosystem.config.js" << EOF
module.exports = {
  apps: [{
    name: '${SERVICE_NAME}',
    script: '${PYTHON_CMD}',
    args: '${PYTHON_SCRIPT}',
    cwd: '${SCRIPT_DIR}',
    interpreter: 'none',
    env: {
      PROXY_HOST: '${PROXY_HOST}',
      PROXY_PORT: '${PROXY_PORT}',
      BACKEND_HOST: '${BACKEND_HOST}',
      BACKEND_PORT: '${BACKEND_PORT}',
      ENDPOINT_PREFIX: '${ENDPOINT_PREFIX}',
      BACKEND_API_PREFIX: '${BACKEND_API_PREFIX}',
      LOG_LEVEL: '${LOG_LEVEL}',
      HTTP_TIMEOUT: '${HTTP_TIMEOUT}',
      WS_TIMEOUT: '${WS_TIMEOUT}'
    },
    log_file: '${LOG_DIR}/${SERVICE_NAME}.log',
    error_file: '${LOG_DIR}/${SERVICE_NAME}-error.log',
    out_file: '${LOG_DIR}/${SERVICE_NAME}-out.log',
    merge_logs: true,
    time: true,
    autorestart: true,
    max_restarts: 10,
    restart_delay: 5000,
    watch: false
  }]
};
EOF
    
    # 启动服务
    pm2 start "${SCRIPT_DIR}/ecosystem.config.js"
    print_success "服务已通过 PM2 启动"
    
    pm2 save
    
    echo ""
    print_info "服务状态:"
    pm2 show "$SERVICE_NAME"
    
    echo ""
    print_info "测试地址:"
    echo "  - 健康检查: curl http://localhost:${PROXY_PORT}/health"
    echo "  - 服务信息: curl http://localhost:${PROXY_PORT}/"
    echo "  - API 文档: http://localhost:${PROXY_PORT}/docs"
}

# 使用 nohup 启动
start_with_nohup() {
    print_info "使用 nohup 启动服务..."
    
    cd "$SCRIPT_DIR"
    nohup $PYTHON_CMD "$PYTHON_SCRIPT" > "${LOG_DIR}/${SERVICE_NAME}.log" 2>&1 &
    echo $! > "$PID_FILE"
    
    sleep 2
    
    if is_running; then
        print_success "服务已启动 (PID: $(cat $PID_FILE))"
        echo ""
        print_info "测试地址:"
        echo "  - 健康检查: curl http://localhost:${PROXY_PORT}/health"
        echo "  - 服务信息: curl http://localhost:${PROXY_PORT}/"
        echo "  - API 文档: http://localhost:${PROXY_PORT}/docs"
        echo ""
        print_info "日志文件: ${LOG_DIR}/${SERVICE_NAME}.log"
    else
        print_error "服务启动失败，请查看日志"
        cat "${LOG_DIR}/${SERVICE_NAME}.log"
        exit 1
    fi
}

# 停止服务
stop_service() {
    print_info "停止服务..."
    
    if check_pm2 && pm2 list | grep -q "$SERVICE_NAME"; then
        pm2 stop "$SERVICE_NAME"
        pm2 delete "$SERVICE_NAME"
        print_success "服务已停止 (PM2)"
    elif is_running; then
        PID=$(cat "$PID_FILE")
        kill "$PID" 2>/dev/null || true
        rm -f "$PID_FILE"
        print_success "服务已停止 (PID: $PID)"
    else
        print_warning "服务未运行"
    fi
}

# 重启服务
restart_service() {
    print_info "重启服务..."
    stop_service
    sleep 2
    start_daemon
}

# 查看状态
show_status() {
    echo ""
    print_info "服务状态:"
    echo ""
    
    if check_pm2 && pm2 list | grep -q "$SERVICE_NAME"; then
        pm2 show "$SERVICE_NAME"
    elif is_running; then
        PID=$(cat "$PID_FILE")
        echo "  状态: 运行中"
        echo "  PID: $PID"
        echo "  监听: ${PROXY_HOST}:${PROXY_PORT}"
        echo "  后端: ${BACKEND_HOST}:${BACKEND_PORT}"
        echo ""
        
        # 尝试获取健康状态
        if command -v curl &> /dev/null; then
            print_info "健康检查:"
            curl -s "http://localhost:${PROXY_PORT}/health" | python3 -m json.tool 2>/dev/null || echo "  无法获取健康状态"
        fi
    else
        echo "  状态: 未运行"
    fi
}

# 查看日志
show_logs() {
    if check_pm2 && pm2 list | grep -q "$SERVICE_NAME"; then
        pm2 logs "$SERVICE_NAME" --lines 100
    elif [ -f "${LOG_DIR}/${SERVICE_NAME}.log" ]; then
        tail -100 "${LOG_DIR}/${SERVICE_NAME}.log"
    else
        print_warning "未找到日志文件"
    fi
}

# 实时日志
follow_logs() {
    if check_pm2 && pm2 list | grep -q "$SERVICE_NAME"; then
        pm2 logs "$SERVICE_NAME"
    elif [ -f "${LOG_DIR}/${SERVICE_NAME}.log" ]; then
        tail -f "${LOG_DIR}/${SERVICE_NAME}.log"
    else
        print_warning "未找到日志文件"
    fi
}

# PM2 监控
show_monit() {
    if check_pm2; then
        pm2 monit
    else
        print_error "PM2 未安装，无法使用监控功能"
        exit 1
    fi
}

# 测试代理
test_proxy() {
    print_info "测试代理服务..."
    echo ""
    
    # 测试健康检查
    print_info "1. 健康检查:"
    curl -s "http://localhost:${PROXY_PORT}/health" | python3 -m json.tool 2>/dev/null || echo "  请求失败"
    echo ""
    
    # 测试服务信息
    print_info "2. 服务信息:"
    curl -s "http://localhost:${PROXY_PORT}/" | python3 -m json.tool 2>/dev/null || echo "  请求失败"
    echo ""
    
    # 测试后端连接
    print_info "3. 后端连接测试:"
    curl -s "http://${BACKEND_HOST}:${BACKEND_PORT}/health" | python3 -m json.tool 2>/dev/null || echo "  后端不可达"
}

# 显示帮助
show_help() {
    echo "Orchestrator Proxy Service 启动脚本"
    echo ""
    echo "说明: 只中转以下两个指定请求"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  (无参数)      前台启动服务"
    echo "  --daemon      后台启动服务 (使用 PM2 或 nohup)"
    echo "  --stop        停止服务"
    echo "  --restart     重启服务"
    echo "  --reload      重新加载服务 (PM2)"
    echo "  --status      查看服务状态"
    echo "  --logs        查看最近日志"
    echo "  --follow      实时查看日志"
    echo "  --monit       PM2 监控面板"
    echo "  --test        测试代理服务"
    echo "  --help        显示此帮助信息"
    echo ""
    echo "环境变量:"
    echo "  PROXY_HOST         代理监听地址 (默认: 0.0.0.0)"
    echo "  PROXY_PORT         代理监听端口 (默认: 8001)"
    echo "  BACKEND_HOST       后端服务器地址 (默认: 110.42.62.193)"
    echo "  BACKEND_PORT       后端服务器端口 (默认: 8000)"
    echo "  ENDPOINT_PREFIX    本地端点前缀 (默认: /endpoint)"
    echo "  BACKEND_API_PREFIX 后端 API 前缀 (默认: /endpoint)"
    echo "  LOG_LEVEL          日志级别 (默认: INFO)"
    echo "  HTTP_TIMEOUT       HTTP 超时秒数 (默认: 30)"
    echo "  WS_TIMEOUT         WebSocket 超时秒数 (默认: 60)"
    echo ""
    echo "转发规则:"
    echo "  #1 HTTP POST (开始新对话，获取 Token):"
    echo "     本地: POST \${ENDPOINT_PREFIX}/chat/conversations/start"
    echo "     后端: POST http://\${BACKEND_HOST}:\${BACKEND_PORT}\${BACKEND_API_PREFIX}/chat/conversations/start"
    echo ""
    echo "  #2 WebSocket (对话):"
    echo "     本地: WS \${ENDPOINT_PREFIX}/ws/chat?token=<jwt_token>"
    echo "     后端: WS ws://\${BACKEND_HOST}:\${BACKEND_PORT}\${BACKEND_API_PREFIX}/ws/chat?token=<jwt_token>"
    echo ""
    echo "示例:"
    echo "  $0                              # 前台启动"
    echo "  $0 --daemon                     # 后台启动"
    echo "  PROXY_PORT=9000 $0              # 使用自定义端口启动"
    echo "  ENDPOINT_PREFIX=/api/proxy $0   # 使用自定义前缀"
}

# 主入口
case "${1:-}" in
    --daemon|-d)
        start_daemon
        ;;
    --stop|-s)
        stop_service
        ;;
    --restart|-r)
        restart_service
        ;;
    --reload)
        if check_pm2; then
            pm2 reload "$SERVICE_NAME"
            print_success "服务已重新加载"
        else
            restart_service
        fi
        ;;
    --status)
        show_status
        ;;
    --logs|-l)
        show_logs
        ;;
    --follow|-f)
        follow_logs
        ;;
    --monit|-m)
        show_monit
        ;;
    --test|-t)
        test_proxy
        ;;
    --help|-h)
        show_help
        ;;
    "")
        start_foreground
        ;;
    *)
        print_error "未知选项: $1"
        show_help
        exit 1
        ;;
esac