#!/bin/bash
#
# Orchestrator Service 启动脚本
# 远程后台代理服务 - WebSocket 优先的 AI Agent 调度服务
#
# 使用方法:
#   ./start_orchestrator.sh              # 前台运行
#   ./start_orchestrator.sh --daemon     # 后台运行
#   ./start_orchestrator.sh --stop       # 停止服务
#   ./start_orchestrator.sh --status     # 查看状态
#   ./start_orchestrator.sh --logs       # 查看日志
#

# 脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 服务配置
SERVICE_NAME="orchestrator_service"
PYTHON_SCRIPT="orchestrator_service.py"
PID_FILE="/tmp/${SERVICE_NAME}.pid"
LOG_FILE="/tmp/${SERVICE_NAME}.log"

# 环境变量配置（可根据需要修改）
export ORCHESTRATOR_HOST="${ORCHESTRATOR_HOST:-0.0.0.0}"
export ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8001}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# LLM 配置（请根据实际情况设置）
# export LLM_API_KEY="your-api-key"
# export LLM_API_BASE_URL="https://api.openai.com/v1"
# export LLM_MODEL="gpt-4"
# export LLM_MAX_TOKENS="4096"
# export LLM_TEMPERATURE="0.7"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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
    
    # 检查必需的包
    REQUIRED_PACKAGES=("fastapi" "uvicorn" "pydantic")
    MISSING_PACKAGES=()
    
    for pkg in "${REQUIRED_PACKAGES[@]}"; do
        if ! $PYTHON_CMD -c "import $pkg" 2>/dev/null; then
            MISSING_PACKAGES+=("$pkg")
        fi
    done
    
    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        print_warning "缺少以下依赖: ${MISSING_PACKAGES[*]}"
        print_info "正在安装依赖..."
        $PYTHON_CMD -m pip install fastapi uvicorn pydantic redis websockets
    fi
    
    print_success "依赖检查完成"
}

# 获取服务 PID
get_pid() {
    if [ -f "$PID_FILE" ]; then
        cat "$PID_FILE"
    else
        # 尝试通过进程名查找
        pgrep -f "$PYTHON_SCRIPT" 2>/dev/null | head -1
    fi
}

# 检查服务是否运行
is_running() {
    local pid=$(get_pid)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

# 启动服务（前台）
start_foreground() {
    if is_running; then
        print_warning "服务已在运行中 (PID: $(get_pid))"
        exit 1
    fi
    
    check_python
    check_dependencies
    
    print_info "启动 Orchestrator Service (前台模式)..."
    print_info "监听地址: ${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}"
    print_info "按 Ctrl+C 停止服务"
    echo ""
    
    $PYTHON_CMD "$PYTHON_SCRIPT"
}

# 启动服务（后台）
start_daemon() {
    if is_running; then
        print_warning "服务已在运行中 (PID: $(get_pid))"
        exit 1
    fi
    
    check_python
    check_dependencies
    
    print_info "启动 Orchestrator Service (后台模式)..."
    
    # 后台启动
    nohup $PYTHON_CMD "$PYTHON_SCRIPT" > "$LOG_FILE" 2>&1 &
    local pid=$!
    echo $pid > "$PID_FILE"
    
    # 等待启动
    sleep 2
    
    if is_running; then
        print_success "服务启动成功"
        print_info "PID: $pid"
        print_info "日志文件: $LOG_FILE"
        print_info "监听地址: ${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}"
        print_info "WebSocket 端点: ws://${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}/ws?user_id=xxx"
    else
        print_error "服务启动失败，请查看日志: $LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# 停止服务
stop_service() {
    local pid=$(get_pid)
    
    if [ -z "$pid" ]; then
        print_warning "服务未运行"
        rm -f "$PID_FILE"
        return 0
    fi
    
    print_info "正在停止服务 (PID: $pid)..."
    
    # 发送 SIGTERM
    kill -15 "$pid" 2>/dev/null
    
    # 等待进程结束
    local count=0
    while kill -0 "$pid" 2>/dev/null && [ $count -lt 10 ]; do
        sleep 1
        count=$((count + 1))
    done
    
    # 如果还在运行，强制终止
    if kill -0 "$pid" 2>/dev/null; then
        print_warning "服务未响应，强制终止..."
        kill -9 "$pid" 2>/dev/null
    fi
    
    rm -f "$PID_FILE"
    print_success "服务已停止"
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
    if is_running; then
        local pid=$(get_pid)
        print_success "服务运行中"
        echo ""
        echo "  PID:          $pid"
        echo "  监听地址:     ${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}"
        echo "  日志文件:     $LOG_FILE"
        echo ""
        
        # 显示进程信息
        if command -v ps &> /dev/null; then
            echo "进程信息:"
            ps -p "$pid" -o pid,ppid,user,%cpu,%mem,etime,cmd 2>/dev/null
        fi
        
        # 检查端口
        if command -v ss &> /dev/null; then
            echo ""
            echo "端口监听:"
            ss -tlnp 2>/dev/null | grep ":${ORCHESTRATOR_PORT}" || echo "  (未检测到端口监听)"
        elif command -v netstat &> /dev/null; then
            echo ""
            echo "端口监听:"
            netstat -tlnp 2>/dev/null | grep ":${ORCHESTRATOR_PORT}" || echo "  (未检测到端口监听)"
        fi
    else
        print_warning "服务未运行"
    fi
}

# 查看日志
show_logs() {
    if [ -f "$LOG_FILE" ]; then
        print_info "显示日志 (最后 50 行):"
        echo "----------------------------------------"
        tail -50 "$LOG_FILE"
        echo "----------------------------------------"
        print_info "实时日志: tail -f $LOG_FILE"
    else
        print_warning "日志文件不存在: $LOG_FILE"
    fi
}

# 实时日志
follow_logs() {
    if [ -f "$LOG_FILE" ]; then
        print_info "实时日志 (Ctrl+C 退出):"
        tail -f "$LOG_FILE"
    else
        print_warning "日志文件不存在: $LOG_FILE"
    fi
}

# 显示帮助
show_help() {
    echo ""
    echo "Orchestrator Service 启动脚本"
    echo ""
    echo "使用方法:"
    echo "  $0                    前台运行服务"
    echo "  $0 --daemon, -d       后台运行服务"
    echo "  $0 --stop             停止服务"
    echo "  $0 --restart          重启服务"
    echo "  $0 --status           查看服务状态"
    echo "  $0 --logs             查看日志 (最后 50 行)"
    echo "  $0 --follow, -f       实时查看日志"
    echo "  $0 --help, -h         显示帮助"
    echo ""
    echo "环境变量:"
    echo "  ORCHESTRATOR_HOST     监听地址 (默认: 0.0.0.0)"
    echo "  ORCHESTRATOR_PORT     监听端口 (默认: 8001)"
    echo "  REDIS_URL             Redis 连接地址"
    echo "  LOG_LEVEL             日志级别 (默认: INFO)"
    echo "  LLM_API_KEY           LLM API 密钥"
    echo "  LLM_API_BASE_URL      LLM API 地址"
    echo "  LLM_MODEL             LLM 模型名称"
    echo ""
    echo "示例:"
    echo "  # 前台运行"
    echo "  ./start_orchestrator.sh"
    echo ""
    echo "  # 后台运行并指定端口"
    echo "  ORCHESTRATOR_PORT=9000 ./start_orchestrator.sh --daemon"
    echo ""
    echo "  # 查看状态"
    echo "  ./start_orchestrator.sh --status"
    echo ""
}

# 主入口
case "${1:-}" in
    --daemon|-d)
        start_daemon
        ;;
    --stop)
        stop_service
        ;;
    --restart)
        restart_service
        ;;
    --status)
        show_status
        ;;
    --logs)
        show_logs
        ;;
    --follow|-f)
        follow_logs
        ;;
    --help|-h)
        show_help
        ;;
    "")
        start_foreground
        ;;
    *)
        print_error "未知参数: $1"
        show_help
        exit 1
        ;;
esac