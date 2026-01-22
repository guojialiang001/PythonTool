#!/bin/bash
#
# Doc Preview Service - PM2 start script
# Manage preview_service.py with PM2
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="preview-service"
PYTHON_PATH="${PYTHON_PATH:-python3}"

# Default config
PREVIEW_HOST="${PREVIEW_HOST:-0.0.0.0}"
PREVIEW_PORT="${PREVIEW_PORT:-8004}"

export PREVIEW_HOST
export PREVIEW_PORT
if [ -n "${PREVIEW_TMP_DIR:-}" ]; then export PREVIEW_TMP_DIR; fi
if [ -n "${PREVIEW_MAX_SIZE_MB:-}" ]; then export PREVIEW_MAX_SIZE_MB; fi
if [ -n "${PREVIEW_TIMEOUT_SEC:-}" ]; then export PREVIEW_TIMEOUT_SEC; fi
if [ -n "${PREVIEW_TTL_HOURS:-}" ]; then export PREVIEW_TTL_HOURS; fi
if [ -n "${MAX_CONCURRENT_CONVERSIONS:-}" ]; then export MAX_CONCURRENT_CONVERSIONS; fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

check_pm2() {
    if ! command -v pm2 &> /dev/null; then
        log_error "PM2 is not installed."
        echo "  npm install -g pm2"
        exit 1
    fi
    log_info "PM2 version: $(pm2 --version)"
}

check_dependencies() {
    log_info "Checking Python dependencies..."
    $PYTHON_PATH -c "import fastapi, uvicorn, aiofiles" 2>/dev/null
    if [ $? -ne 0 ]; then
        log_warning "Missing dependencies, installing..."
        if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
            $PYTHON_PATH -m pip install -r "$SCRIPT_DIR/requirements.txt"
        else
            $PYTHON_PATH -m pip install fastapi uvicorn aiofiles filetype
        fi
    fi
    log_success "Dependencies OK."
}

start() {
    log_info "Starting $APP_NAME..."
    pm2 describe $APP_NAME > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        log_warning "$APP_NAME is already running"
        pm2 show $APP_NAME
        return 0
    fi

    pm2 start "$SCRIPT_DIR/preview_service.py" \
        --name "$APP_NAME" \
        --interpreter "$PYTHON_PATH"

    if [ $? -eq 0 ]; then
        log_success "$APP_NAME started successfully."
        echo ""
        pm2 show $APP_NAME
        echo ""
        log_info "Address: http://$PREVIEW_HOST:$PREVIEW_PORT"
        log_info "Upload:  POST /api/preview/upload"
        log_info "Status:  GET  /api/preview/{file_id}"
        log_info "Content: GET  /api/preview/{file_id}/content"
        log_info "Logs: pm2 logs $APP_NAME"
    else
        log_error "Failed to start $APP_NAME."
        exit 1
    fi
}

stop() {
    log_info "Stopping $APP_NAME..."
    pm2 stop $APP_NAME 2>/dev/null
    if [ $? -eq 0 ]; then
        log_success "$APP_NAME stopped."
    else
        log_warning "$APP_NAME is not running."
    fi
}

restart() {
    log_info "Restarting $APP_NAME..."
    pm2 restart $APP_NAME 2>/dev/null
    if [ $? -eq 0 ]; then
        log_success "$APP_NAME restarted."
        pm2 show $APP_NAME
    else
        log_warning "$APP_NAME is not running, starting..."
        start
    fi
}

delete() {
    log_info "Deleting $APP_NAME..."
    pm2 delete $APP_NAME 2>/dev/null
    if [ $? -eq 0 ]; then
        log_success "$APP_NAME deleted."
    else
        log_warning "$APP_NAME does not exist."
    fi
}

status() {
    pm2 describe $APP_NAME > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        pm2 show $APP_NAME
    else
        log_warning "$APP_NAME is not running."
    fi
}

logs() {
    pm2 logs $APP_NAME --lines 100
}

logs_live() {
    pm2 logs $APP_NAME
}

save() {
    log_info "Saving PM2 process list..."
    pm2 save
    log_success "Process list saved."
    log_info "Setting up startup scripts..."
    pm2 startup
    log_info "Follow the above instructions to enable startup."
}

show_help() {
    echo ""
    echo "=========================================="
    echo "  Doc Preview Service - PM2 Manager"
    echo "=========================================="
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start       Start service"
    echo "  stop        Stop service"
    echo "  restart     Restart service"
    echo "  delete      Remove service from PM2"
    echo "  status      Show status"
    echo "  logs        Show last 100 log lines"
    echo "  logs-live   Follow logs"
    echo "  save        Save PM2 list + setup startup"
    echo ""
    echo "Environment:"
    echo "  PREVIEW_HOST (default: 0.0.0.0)"
    echo "  PREVIEW_PORT (default: 8004)"
    echo "  PREVIEW_TMP_DIR"
    echo "  PREVIEW_MAX_SIZE_MB"
    echo "  PREVIEW_TIMEOUT_SEC"
    echo "  PREVIEW_TTL_HOURS"
    echo "  MAX_CONCURRENT_CONVERSIONS"
    echo "  PYTHON_PATH (default: python3)"
    echo ""
    echo "Examples:"
    echo "  $0 start"
    echo "  PREVIEW_PORT=9003 $0 start"
    echo "  $0 logs"
}

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

main "$@"
