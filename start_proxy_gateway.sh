#!/bin/bash
#
# OpenAI API Proxy Gateway - PM2 启动脚本
# 使用PM2管理proxy_gateway.py进程
#

# 脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 应用名称
APP_NAME="proxy-gateway"

# Python路径（可根据需要修改）
PYTHON_PATH="${PYTHON_PATH:-python3}"

# 默认配置
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-4}"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查PM2是否安装
check_pm2() {
    if ! command -v pm2 &> /dev/null; then
        log_error "PM2 未安装！请先安装PM2："
        echo "  npm install -g pm2"
        exit 1
    fi
    log_info "PM2 版本: $(pm2 --version)"
}

# 检查Python依赖
check_dependencies() {
    log_info "检查Python依赖..."
    
    # 检查基础依赖
    $PYTHON_PATH -c "import fastapi, uvicorn, httpx" 2>/dev/null
    if [ $? -ne 0 ]; then
        log_warning "缺少基础Python依赖，正在安装..."
        $PYTHON_PATH -m pip install fastapi uvicorn "httpx[http2]"
    fi
    
    # 检查h2依赖（httpx的http2支持）
    $PYTHON_PATH -c "import h2" 2>/dev/null
    if [ $? -ne 0 ]; then
        log_warning "缺少h2依赖（HTTP/2支持），正在安装..."
        $PYTHON_PATH -m pip install "httpx[http2]"
    fi
    
    log_success "Python依赖检查完成"
}

# 启动服务
start() {
    log_info "正在启动 $APP_NAME..."
    
    # 检查是否已经运行
    pm2 describe $APP_NAME > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        log_warning "$APP_NAME 已经在运行中"
        pm2 show $APP_NAME
        return 0
    fi
    
    # 使用PM2启动Python应用
    pm2 start "$SCRIPT_DIR/proxy_gateway.py" \
        --name "$APP_NAME" \
        --interpreter "$PYTHON_PATH" \
        -- \
        --host "$HOST" \
        --port "$PORT" \
        --workers "$WORKERS"
    
    if [ $? -eq 0 ]; then
        log_success "$APP_NAME 启动成功！"
        echo ""
        pm2 show $APP_NAME
        echo ""
        log_info "服务地址: http://$HOST:$PORT"
        log_info "查看日志: pm2 logs $APP_NAME"
    else
        log_error "启动失败！"
        exit 1
    fi
}

# 停止服务
stop() {
    log_info "正在停止 $APP_NAME..."
    pm2 stop $APP_NAME 2>/dev/null
    if [ $? -eq 0 ]; then
        log_success "$APP_NAME 已停止"
    else
        log_warning "$APP_NAME 未在运行"
    fi
}

# 重启服务
restart() {
    log_info "正在重启 $APP_NAME..."
    pm2 restart $APP_NAME 2>/dev/null
    if [ $? -eq 0 ]; then
        log_success "$APP_NAME 重启成功"
        pm2 show $APP_NAME
    else
        log_warning "$APP_NAME 未在运行，正在启动..."
        start
    fi
}

# 删除服务
delete() {
    log_info "正在删除 $APP_NAME..."
    pm2 delete $APP_NAME 2>/dev/null
    if [ $? -eq 0 ]; then
        log_success "$APP_NAME 已删除"
    else
        log_warning "$APP_NAME 不存在"
    fi
}

# 查看状态
status() {
    pm2 describe $APP_NAME > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        pm2 show $APP_NAME
    else
        log_warning "$APP_NAME 未在运行"
    fi
}

# 查看日志
logs() {
    pm2 logs $APP_NAME --lines 100
}

# 实时日志
logs_live() {
    pm2 logs $APP_NAME
}

# 保存PM2配置（开机自启）
save() {
    log_info "保存PM2进程列表..."
    pm2 save
    log_success "进程列表已保存"
    
    log_info "设置开机自启..."
    pm2 startup
    log_info "请按照上方提示执行相应命令以启用开机自启"
}

# 显示帮助
show_help() {
    echo ""
    echo "=========================================="
    echo "  OpenAI API Proxy Gateway - PM2 管理脚本"
    echo "=========================================="
    echo ""
    echo "用法: $0 <命令>"
    echo ""
    echo "命令:"
    echo "  start       启动服务"
    echo "  stop        停止服务"
    echo "  restart     重启服务"
    echo "  delete      删除服务（从PM2中移除）"
    echo "  status      查看服务状态"
    echo "  logs        查看最近100行日志"
    echo "  logs-live   实时查看日志"
    echo "  save        保存PM2配置并设置开机自启"
    echo ""
    echo "环境变量:"
    echo "  HOST        监听地址 (默认: 0.0.0.0)"
    echo "  PORT        监听端口 (默认: 8000)"
    echo "  WORKERS     工作进程数 (默认: 4)"
    echo "  PYTHON_PATH Python路径 (默认: python3)"
    echo ""
    echo "示例:"
    echo "  $0 start                    # 使用默认配置启动"
    echo "  PORT=9000 $0 start          # 使用9000端口启动"
    echo "  $0 logs                     # 查看日志"
    echo "  $0 restart                  # 重启服务"
    echo ""
}

# 主入口
main() {
    case "$1" in
        start)
            check_pm2
            check_dependencies
            start
            ;;
        stop)
            check_pm2
            stop
            ;;
        restart)
            check_pm2
            restart
            ;;
        delete)
            check_pm2
            delete
            ;;
        status)
            check_pm2
            status
            ;;
        logs)
            check_pm2
            logs
            ;;
        logs-live)
            check_pm2
            logs_live
            ;;
        save)
            check_pm2
            save
            ;;
        *)
            show_help
            exit 0
            ;;
    esac
}

# 执行
main "$@"