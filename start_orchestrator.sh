#!/bin/bash

# ============================================================================
# Orchestrator Service 启动脚本 (Linux)
# 
# 这是一个代理服务，将请求转发到后端 8.136.32.51
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
SERVICE_NAME="orchestrator-service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="orchestrator_service.py"
LOG_DIR="${SCRIPT_DIR}/logs"
PID_FILE="${LOG_DIR}/${SERVICE_NAME}.pid"

# 环境变量配置
export ORCHESTRATOR_HOST="${ORCHESTRATOR_HOST:-0.0.0.0}"
export ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8001}"
export SERVER_DOMAIN="${SERVER_DOMAIN:-sandbox.toproject.cloud}"
export SERVER_IP="${SERVER_IP:-8.136.32.51}"
export API_PREFIX="${API_PREFIX:-/orchestrator}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export SESSION_TIMEOUT="${SESSION_TIMEOUT:-3600}"

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
    echo "║        Orchestrator Service - 代理服务启动脚本                             ║"
    echo "╠════════════════════════════════════════════════════════════════════════════╣"
    echo "║  服务说明:                                                                  ║"
    echo "║    - 这是一个代理服务，转发请求到后端 ${SERVER_IP}                    ║"
    echo "║    - 对外域名: ${SERVER_DOMAIN}                                ║"
    echo "║    - 监听端口: ${ORCHESTRATOR_PORT}                                                    ║"
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
    
    $PYTHON_CMD -c "import pydantic" 2>/dev/null || {
        print_warning "缺少 pydantic，正在安装..."
        pip install pydantic
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

# 前台启动
start_foreground() {
    print_banner
    check_python
    check_dependencies
    create_log_dir
    
    if is_running; then
        print_warning "服务已在运行 (PID: $(cat $PID_FILE))"
        exit 1
    fi
    
    print_info "启动服务 (前台模式)..."
    print_info "监听地址: ${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}"
    print_info "API 前缀: ${API_PREFIX}"
    print_info "后端地址: ${SERVER_IP}"
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
    
    if check_pm2; then
        start_with_pm2
    else
        start_with_nohup
    fi
}

# 使用 PM2 启动
start_with_pm2() {
    print_info "使用 PM2 启动服务..."
    
    # 检查是否已经在 PM2 中运行
    if pm2 list | grep -q "$SERVICE_NAME"; then
        print_warning "服务已在 PM2 中运行"
        pm2 restart "$SERVICE_NAME"
        print_success "服务已重启"
    else
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
      ORCHESTRATOR_HOST: '${ORCHESTRATOR_HOST}',
      ORCHESTRATOR_PORT: '${ORCHESTRATOR_PORT}',
      SERVER_DOMAIN: '${SERVER_DOMAIN}',
      SERVER_IP: '${SERVER_IP}',
      API_PREFIX: '${API_PREFIX}',
      LOG_LEVEL: '${LOG_LEVEL}',
      SESSION_TIMEOUT: '${SESSION_TIMEOUT}'
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
        
        pm2 start "${SCRIPT_DIR}/ecosystem.config.js"
        print_success "服务已通过 PM2 启动"
    fi
    
    pm2 save
    
    echo ""
    print_info "服务状态:"
    pm2 show "$SERVICE_NAME"
    
    echo ""
    print_info "访问地址:"
    echo "  - 健康检查: http://localhost:${ORCHESTRATOR_PORT}${API_PREFIX}/health"
    echo "  - 服务信息: http://localhost:${ORCHESTRATOR_PORT}${API_PREFIX}/info"
    echo "  - API 文档: http://localhost:${ORCHESTRATOR_PORT}${API_PREFIX}/docs"
    echo "  - WebSocket: ws://localhost:${ORCHESTRATOR_PORT}${API_PREFIX}/ws?user_id=test&session_id=test"
}

# 使用 nohup 启动
start_with_nohup() {
    print_info "使用 nohup 启动服务..."
    
    if is_running; then
        print_warning "服务已在运行 (PID: $(cat $PID_FILE))"
        exit 1
    fi
    
    cd "$SCRIPT_DIR"
    nohup $PYTHON_CMD "$PYTHON_SCRIPT" > "${LOG_DIR}/${SERVICE_NAME}.log" 2>&1 &
    echo $! > "$PID_FILE"
    
    sleep 2
    
    if is_running; then
        print_success "服务已启动 (PID: $(cat $PID_FILE))"
        echo ""
        print_info "访问地址:"
        echo "  - 健康检查: http://localhost:${ORCHESTRATOR_PORT}${API_PREFIX}/health"
        echo "  - 服务信息: http://localhost:${ORCHESTRATOR_PORT}${API_PREFIX}/info"
        echo "  - API 文档: http://localhost:${ORCHESTRATOR_PORT}${API_PREFIX}/docs"
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
        echo "  监听: ${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}"
        echo "  后端: ${SERVER_IP}"
        echo ""
        
        # 尝试获取健康状态
        if command -v curl &> /dev/null; then
            print_info "健康检查:"
            curl -s "http://localhost:${ORCHESTRATOR_PORT}${API_PREFIX}/health" | python3 -m json.tool 2>/dev/null || echo "  无法获取健康状态"
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
        tail -f "${LOG_DIR}/${SERVICE_NAME}.log"
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

# 显示帮助
show_help() {
    echo "Orchestrator Service 启动脚本"
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
    echo "  --help        显示此帮助信息"
    echo ""
    echo "环境变量:"
    echo "  ORCHESTRATOR_HOST   监听地址 (默认: 0.0.0.0)"
    echo "  ORCHESTRATOR_PORT   监听端口 (默认: 8001)"
    echo "  SERVER_DOMAIN       对外域名 (默认: sandbox.toproject.cloud)"
    echo "  SERVER_IP           后端 IP (默认: 8.136.32.51)"
    echo "  API_PREFIX          API 前缀 (默认: /orchestrator)"
    echo "  LOG_LEVEL           日志级别 (默认: INFO)"
    echo "  SESSION_TIMEOUT     会话超时秒数 (默认: 3600)"
    echo ""
    echo "示例:"
    echo "  $0                           # 前台启动"
    echo "  $0 --daemon                  # 后台启动"
    echo "  ORCHESTRATOR_PORT=9000 $0    # 使用自定义端口启动"
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