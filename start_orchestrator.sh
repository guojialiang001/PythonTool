#!/bin/bash
#
# Orchestrator Service 启动脚本 (PM2 版本)
# 远程后台代理服务 - WebSocket 优先的 AI Agent 调度服务
#
# 使用方法:
#   ./start_orchestrator.sh              # 使用 PM2 启动服务
#   ./start_orchestrator.sh --stop       # 停止服务
#   ./start_orchestrator.sh --restart    # 重启服务
#   ./start_orchestrator.sh --status     # 查看状态
#   ./start_orchestrator.sh --logs       # 查看日志
#   ./start_orchestrator.sh --monit      # 打开 PM2 监控面板
#

# 脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 服务配置
SERVICE_NAME="orchestrator-service"
PYTHON_SCRIPT="orchestrator_service.py"

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

# 检查 PM2 是否安装
check_pm2() {
    if ! command -v pm2 &> /dev/null; then
        print_error "PM2 未安装"
        print_info "请先安装 PM2: npm install -g pm2"
        print_info "或者: yarn global add pm2"
        exit 1
    fi
    print_info "PM2 版本: $(pm2 -v)"
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
    
    # 获取 Python 完整路径
    PYTHON_PATH=$(which $PYTHON_CMD)
    
    # 检查 Python 版本
    PYTHON_VERSION=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    print_info "Python 版本: $PYTHON_VERSION ($PYTHON_PATH)"
}

# 检查依赖
check_dependencies() {
    print_info "检查 Python 依赖..."
    
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

# 生成 PM2 ecosystem 配置文件
generate_ecosystem_config() {
    cat > ecosystem.config.js << EOF
module.exports = {
  apps: [{
    name: '${SERVICE_NAME}',
    script: '${PYTHON_PATH}',
    args: '${PYTHON_SCRIPT}',
    cwd: '${SCRIPT_DIR}',
    interpreter: 'none',
    
    // 环境变量
    env: {
      ORCHESTRATOR_HOST: '${ORCHESTRATOR_HOST}',
      ORCHESTRATOR_PORT: '${ORCHESTRATOR_PORT}',
      REDIS_URL: '${REDIS_URL}',
      LOG_LEVEL: '${LOG_LEVEL}',
      LLM_API_KEY: process.env.LLM_API_KEY || '',
      LLM_API_BASE_URL: process.env.LLM_API_BASE_URL || '',
      LLM_MODEL: process.env.LLM_MODEL || '',
      LLM_MAX_TOKENS: process.env.LLM_MAX_TOKENS || '4096',
      LLM_TEMPERATURE: process.env.LLM_TEMPERATURE || '0.7'
    },
    
    // 实例配置
    instances: 1,
    exec_mode: 'fork',
    
    // 自动重启配置
    autorestart: true,
    watch: false,
    max_memory_restart: '1G',
    
    // 重启策略
    exp_backoff_restart_delay: 100,
    max_restarts: 10,
    min_uptime: '10s',
    
    // 日志配置
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    error_file: '${SCRIPT_DIR}/logs/orchestrator-error.log',
    out_file: '${SCRIPT_DIR}/logs/orchestrator-out.log',
    merge_logs: true,
    
    // 优雅关闭
    kill_timeout: 5000,
    listen_timeout: 3000,
    
    // 健康检查 (可选)
    // health_check: {
    //   url: 'http://localhost:${ORCHESTRATOR_PORT}/health',
    //   interval: 30000
    // }
  }]
};
EOF
    print_info "已生成 PM2 配置文件: ecosystem.config.js"
}

# 创建日志目录
create_log_dir() {
    if [ ! -d "${SCRIPT_DIR}/logs" ]; then
        mkdir -p "${SCRIPT_DIR}/logs"
        print_info "已创建日志目录: ${SCRIPT_DIR}/logs"
    fi
}

# 检查服务是否运行
is_running() {
    pm2 describe "$SERVICE_NAME" &> /dev/null
    return $?
}

# 启动服务
start_service() {
    check_pm2
    check_python
    check_dependencies
    create_log_dir
    generate_ecosystem_config
    
    if is_running; then
        print_warning "服务已在运行中"
        pm2 describe "$SERVICE_NAME"
        exit 0
    fi
    
    print_info "使用 PM2 启动 Orchestrator Service..."
    
    pm2 start ecosystem.config.js
    
    # 等待启动
    sleep 2
    
    if is_running; then
        print_success "服务启动成功"
        echo ""
        pm2 describe "$SERVICE_NAME"
        echo ""
        print_info "监听地址: ${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}"
        print_info "WebSocket 端点: ws://${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}/ws?user_id=xxx"
        print_info "健康检查: http://${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT}/health"
        echo ""
        print_info "常用命令:"
        echo "  查看日志:   pm2 logs ${SERVICE_NAME}"
        echo "  查看状态:   pm2 status"
        echo "  监控面板:   pm2 monit"
        echo "  重启服务:   pm2 restart ${SERVICE_NAME}"
        echo "  停止服务:   pm2 stop ${SERVICE_NAME}"
        
        # 保存 PM2 进程列表（开机自启）
        print_info ""
        print_info "如需开机自启，请执行:"
        echo "  pm2 save"
        echo "  pm2 startup"
    else
        print_error "服务启动失败"
        pm2 logs "$SERVICE_NAME" --lines 20
        exit 1
    fi
}

# 停止服务
stop_service() {
    check_pm2
    
    if ! is_running; then
        print_warning "服务未运行"
        return 0
    fi
    
    print_info "正在停止服务..."
    pm2 stop "$SERVICE_NAME"
    pm2 delete "$SERVICE_NAME"
    print_success "服务已停止"
}

# 重启服务
restart_service() {
    check_pm2
    
    if ! is_running; then
        print_warning "服务未运行，正在启动..."
        start_service
        return
    fi
    
    print_info "正在重启服务..."
    pm2 restart "$SERVICE_NAME"
    print_success "服务已重启"
    pm2 describe "$SERVICE_NAME"
}

# 重载服务（零停机）
reload_service() {
    check_pm2
    
    if ! is_running; then
        print_warning "服务未运行，正在启动..."
        start_service
        return
    fi
    
    print_info "正在重载服务 (零停机)..."
    pm2 reload "$SERVICE_NAME"
    print_success "服务已重载"
}

# 查看状态
show_status() {
    check_pm2
    
    if is_running; then
        print_success "服务运行中"
        echo ""
        pm2 describe "$SERVICE_NAME"
        echo ""
        
        # 检查端口
        if command -v ss &> /dev/null; then
            echo "端口监听:"
            ss -tlnp 2>/dev/null | grep ":${ORCHESTRATOR_PORT}" || echo "  (未检测到端口监听)"
        elif command -v netstat &> /dev/null; then
            echo "端口监听:"
            netstat -tlnp 2>/dev/null | grep ":${ORCHESTRATOR_PORT}" || echo "  (未检测到端口监听)"
        fi
    else
        print_warning "服务未运行"
    fi
}

# 查看日志
show_logs() {
    check_pm2
    
    print_info "显示日志 (最后 50 行):"
    pm2 logs "$SERVICE_NAME" --lines 50 --nostream
}

# 实时日志
follow_logs() {
    check_pm2
    
    print_info "实时日志 (Ctrl+C 退出):"
    pm2 logs "$SERVICE_NAME"
}

# 打开监控面板
show_monit() {
    check_pm2
    pm2 monit
}

# 显示所有 PM2 进程
show_list() {
    check_pm2
    pm2 list
}

# 显示帮助
show_help() {
    echo ""
    echo "Orchestrator Service 启动脚本 (PM2 版本)"
    echo ""
    echo "使用方法:"
    echo "  $0                    使用 PM2 启动服务"
    echo "  $0 --stop             停止服务"
    echo "  $0 --restart          重启服务"
    echo "  $0 --reload           重载服务 (零停机)"
    echo "  $0 --status           查看服务状态"
    echo "  $0 --logs             查看日志 (最后 50 行)"
    echo "  $0 --follow, -f       实时查看日志"
    echo "  $0 --monit            打开 PM2 监控面板"
    echo "  $0 --list             显示所有 PM2 进程"
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
    echo "PM2 常用命令:"
    echo "  pm2 status            查看所有进程状态"
    echo "  pm2 logs              查看所有日志"
    echo "  pm2 monit             打开监控面板"
    echo "  pm2 save              保存进程列表"
    echo "  pm2 startup           设置开机自启"
    echo ""
    echo "示例:"
    echo "  # 启动服务"
    echo "  ./start_orchestrator.sh"
    echo ""
    echo "  # 指定端口启动"
    echo "  ORCHESTRATOR_PORT=9000 ./start_orchestrator.sh"
    echo ""
    echo "  # 查看实时日志"
    echo "  ./start_orchestrator.sh --follow"
    echo ""
    echo "  # 设置开机自启"
    echo "  pm2 save && pm2 startup"
    echo ""
}

# 主入口
case "${1:-}" in
    --stop)
        stop_service
        ;;
    --restart)
        restart_service
        ;;
    --reload)
        reload_service
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
    --monit)
        show_monit
        ;;
    --list)
        show_list
        ;;
    --help|-h)
        show_help
        ;;
    "")
        start_service
        ;;
    *)
        print_error "未知参数: $1"
        show_help
        exit 1
        ;;
esac