"""
SSH WebSocket 安全中间件
可以在不修改原始代码的情况下为 ssh_websocket.py 添加安全防护
"""

from fastapi import Request, WebSocket
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import json
from ssh_security import (
    apply_security_checks,
    validate_ssh_connection,
    validate_command_input,
    cleanup_security_session,
    rate_limiter,
    session_security,
    security_logger,
    InputValidator,
    SecurityConfig
)


class SecurityMiddleware(BaseHTTPMiddleware):
    """HTTP请求安全中间件"""
    
    async def dispatch(self, request: Request, call_next):
        # 获取客户端IP
        client_ip = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
        if not client_ip:
            client_ip = request.client.host if request.client else "unknown"
        
        # 检查IP黑名单
        if client_ip in SecurityConfig.IP_BLACKLIST:
            security_logger.log_blocked(client_ip, "IP黑名单")
            return JSONResponse(
                status_code=403,
                content={"error": "访问被拒绝"}
            )
        
        # 检查请求速率
        if not rate_limiter.check_request(client_ip):
            return JSONResponse(
                status_code=429,
                content={"error": "请求过于频繁，请稍后再试"}
            )
        
        response = await call_next(request)
        return response


def apply_security_to_app(app):
    """
    将安全中间件应用到FastAPI应用
    
    使用方法:
    from ssh_security_middleware import apply_security_to_app
    from ssh_websocket import app
    apply_security_to_app(app)
    """
    app.add_middleware(SecurityMiddleware)
    print("[SECURITY] 安全中间件已启用")
    return app


# 安全WebSocket包装器函数
async def secure_websocket_connect(websocket: WebSocket):
    """
    安全的WebSocket连接检查
    返回: (allowed, client_ip, error_message)
    """
    allowed, error_msg, client_ip = apply_security_checks(websocket)
    if allowed:
        rate_limiter.add_conn(client_ip)
    return allowed, client_ip, error_msg


def secure_validate_connection(data: dict, client_ip: str):
    """
    安全的连接参数验证
    返回: (valid, error, sanitized_data)
    """
    valid, error, sanitized = validate_ssh_connection(data)
    if not valid:
        security_logger.log_blocked(client_ip, error)
    return valid, error, sanitized


def secure_validate_command(command: str, session_id: str):
    """
    安全的命令验证
    返回: (valid, error, command)
    """
    return validate_command_input(command, session_id)


def secure_validate_message(message: str):
    """
    验证消息大小
    返回: bool
    """
    return InputValidator.validate_msg_size(message)


def secure_session_update(session_id: str):
    """更新会话活动时间"""
    session_security.update(session_id)


def secure_check_idle(session_id: str):
    """检查会话是否空闲超时"""
    return session_security.check_idle(session_id)


def secure_cleanup(session_id: str, client_ip: str):
    """清理会话安全数据"""
    cleanup_security_session(session_id, client_ip)


def secure_register_session(session_id: str, client_ip: str):
    """注册会话"""
    session_security.register(session_id, client_ip)


def secure_log_connection(client_ip: str, hostname: str, username: str, success: bool):
    """记录连接日志"""
    security_logger.log_connection(client_ip, hostname, username, success)


# 导出配置类以便外部修改
__all__ = [
    'SecurityMiddleware',
    'apply_security_to_app',
    'secure_websocket_connect',
    'secure_validate_connection',
    'secure_validate_command',
    'secure_validate_message',
    'secure_session_update',
    'secure_check_idle',
    'secure_cleanup',
    'secure_register_session',
    'secure_log_connection',
    'SecurityConfig'
]