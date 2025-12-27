#!/bin/bash
# ============================================
# 三级威胁防护系统启动脚本
# ============================================
# 
# 使用方式:
#   ./start_threat_protection.sh              # 直接启动（前台运行）
#   ./start_threat_protection.sh start        # PM2 启动（后台运行）
#   ./start_threat_protection.sh stop         # PM2 停止
#   ./start_threat_protection.sh restart      # PM2 重启
#   ./start_threat_protection.sh status       # 查看状态
#   ./start_threat_protection.sh logs         # 查看日志
#   ./start_threat_protection.sh test         # 运行测试
#
# 环境变量:
#   THREAT_PORT=8765                          # 指定端口（默认 8765）
#

# 配置
APP_NAME="threat-protection"
PORT=${THREAT_PORT:-8765}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/security_threat_protection.py"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_banner() {
    echo -e "${BLUE}"
    echo "=========================================="
    echo "  三级威胁防护系统"
    echo "  Three-Level Threat Protection System"
    echo "=========================================="
    echo -e "${NC}"
}

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 Python 是否可用
check_python() {
    if ! command -v python &> /dev/null; then
        if command -v python3 &> /dev/null; then
            PYTHON_CMD="python3"
        else
            print_error "Python 未安装，请先安装 Python 3.8+"
            exit 1
        fi
    else
        PYTHON_CMD="python"
    fi
}

# 检查 PM2 是否可用
check_pm2() {
    if ! command -v pm2 &> /dev/null; then
        print_warn "PM2 未安装，将使用前台模式运行"
        print_info "安装 PM2: npm install -g pm2"
        return 1
    fi
    return 0
}

# 直接启动（前台运行）
start_foreground() {
    print_banner
    print_info "端口: $PORT"
    print_info "API 文档: http://localhost:$PORT/docs"
    print_info "按 Ctrl+C 停止服务"
    echo "=========================================="
    $PYTHON_CMD "$PYTHON_SCRIPT" --mode api --port $PORT
}

# PM2 启动（后台运行）
start_pm2() {
    if ! check_pm2; then
        print_warn "回退到前台模式..."
        start_foreground
        return
    fi
    
    print_banner
    print_info "使用 PM2 启动服务..."
    
    # 检查是否已经在运行
    if pm2 list | grep -q "$APP_NAME"; then
        print_warn "服务已在运行，正在重启..."
        pm2 restart "$APP_NAME"
    else
        pm2 start "$PYTHON_SCRIPT" \
            --name "$APP_NAME" \
            --interpreter "$PYTHON_CMD" \
            -- --mode api --port $PORT
    fi
    
    print_info "服务已启动"
    print_info "端口: $PORT"
    print_info "API 文档: http://localhost:$PORT/docs"
    print_info "查看日志: pm2 logs $APP_NAME"
    print_info "查看状态: pm2 status $APP_NAME"
}

# PM2 停止
stop_pm2() {
    if ! check_pm2; then
        print_error "PM2 未安装"
        exit 1
    fi
    
    print_info "停止服务..."
    pm2 stop "$APP_NAME" 2>/dev/null || print_warn "服务未在运行"
    print_info "服务已停止"
}

# PM2 重启
restart_pm2() {
    if ! check_pm2; then
        print_error "PM2 未安装"
        exit 1
    fi
    
    print_info "重启服务..."
    pm2 restart "$APP_NAME" 2>/dev/null || start_pm2
    print_info "服务已重启"
}

# 查看状态
show_status() {
    print_banner
    
    # 检查 PM2 状态
    if check_pm2; then
        print_info "PM2 服务状态:"
        pm2 status "$APP_NAME" 2>/dev/null || print_warn "服务未在 PM2 中运行"
    fi
    
    # 检查端口占用
    print_info "端口 $PORT 状态:"
    if command -v lsof &> /dev/null; then
        lsof -i :$PORT 2>/dev/null || print_info "端口 $PORT 未被占用"
    elif command -v netstat &> /dev/null; then
        netstat -tlnp 2>/dev/null | grep ":$PORT " || print_info "端口 $PORT 未被占用"
    fi
    
    # 检查日志文件
    print_info "日志文件:"
    if [ -d "$SCRIPT_DIR/security_logs" ]; then
        ls -la "$SCRIPT_DIR/security_logs/"
    else
        print_warn "日志目录不存在"
    fi
}

# 查看日志
show_logs() {
    if check_pm2; then
        pm2 logs "$APP_NAME" --lines 50
    else
        print_info "本地日志文件:"
        if [ -f "$SCRIPT_DIR/security_logs/security_operations.jsonl" ]; then
            tail -50 "$SCRIPT_DIR/security_logs/security_operations.jsonl"
        else
            print_warn "日志文件不存在"
        fi
    fi
}

# 运行测试
run_test() {
    print_banner
    print_info "运行测试..."
    $PYTHON_CMD "$PYTHON_SCRIPT" --mode test
}

# 显示帮助
show_help() {
    print_banner
    echo "使用方式:"
    echo "  $0              直接启动（前台运行）"
    echo "  $0 start        PM2 启动（后台运行）"
    echo "  $0 stop         PM2 停止"
    echo "  $0 restart      PM2 重启"
    echo "  $0 status       查看状态"
    echo "  $0 logs         查看日志"
    echo "  $0 test         运行测试"
    echo "  $0 help         显示帮助"
    echo ""
    echo "环境变量:"
    echo "  THREAT_PORT     指定端口（默认 8765）"
    echo ""
    echo "示例:"
    echo "  THREAT_PORT=9000 $0 start    # 使用端口 9000 启动"
}

# 主函数
main() {
    check_python
    
    case "${1:-}" in
        start)
            start_pm2
            ;;
        stop)
            stop_pm2
            ;;
        restart)
            restart_pm2
            ;;
        status)
            show_status
            ;;
        logs)
            show_logs
            ;;
        test)
            run_test
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            # 默认前台启动
            start_foreground
            ;;
    esac
}

main "$@"