"""
SSH WebSocket 安全防护模块
提供速率限制、输入验证、IP访问控制、会话安全管理等功能
"""

from typing import Dict, List, Set
import threading
import time
import re
import ipaddress
import logging
from fastapi import WebSocket


class SecurityConfig:
    """安全配置类"""
    RATE_LIMIT_WINDOW = 60
    RATE_LIMIT_MAX_REQUESTS = 100
    RATE_LIMIT_MAX_CONNECTIONS = 10
    RATE_LIMIT_MAX_COMMANDS_PER_MINUTE = 120
    MAX_TOTAL_CONNECTIONS = 100
    CONNECTION_TIMEOUT = 30
    IDLE_TIMEOUT = 1800
    MAX_COMMAND_LENGTH = 4096
    MAX_PATH_LENGTH = 4096
    MAX_MESSAGE_SIZE = 65536
    MAX_HOSTNAME_LENGTH = 255
    MAX_USERNAME_LENGTH = 64
    MAX_PASSWORD_LENGTH = 256
    IP_WHITELIST: Set[str] = set()
    IP_BLACKLIST: Set[str] = set()
    ALLOWED_SSH_PORTS = range(1, 65536)
    DANGEROUS_COMMAND_PATTERNS = [
        r'rm\s+-rf\s+/',
        r'mkfs\.',
        r'dd\s+if=.*of=/dev/',
        r'>\s*/dev/sd[a-z]',
        r'chmod\s+-R\s+777\s+/',
        r':\(\)\{\s*:\|:\s*&\s*\};:',
    ]
    LOG_LEVEL = logging.INFO


class SecurityLogger:
    """安全日志记录器"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.logger = logging.getLogger("ssh_security")
        self.logger.setLevel(SecurityConfig.LOG_LEVEL)
        if not self.logger.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter('[%(asctime)s] [SECURITY] %(message)s'))
            self.logger.addHandler(h)
    
    def log_connection(self, ip: str, host: str, user: str, ok: bool):
        self.logger.info(f"Conn {'OK' if ok else 'FAIL'}: {ip} -> {user}@{host}")
    
    def log_rate_limit(self, id: str, type: str):
        self.logger.warning(f"RateLimit: {id} - {type}")
    
    def log_suspicious(self, ip: str, act: str):
        self.logger.warning(f"Suspicious: {ip} - {act}")
    
    def log_blocked(self, id: str, reason: str):
        self.logger.warning(f"Blocked: {id} - {reason}")
    
    def log_dangerous_cmd(self, sid: str, cmd: str):
        self.logger.warning(f"DangerousCmd: {sid} - {cmd[:80]}")


security_logger = SecurityLogger()


class RateLimiter:
    """速率限制器"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.requests: Dict[str, List[float]] = {}
        self.connections: Dict[str, int] = {}
        self.commands: Dict[str, List[float]] = {}
        self.lock = threading.Lock()
    
    def _clean(self, ts: List[float], window: int) -> List[float]:
        now = time.time()
        return [t for t in ts if now - t < window]
    
    def check_request(self, ip: str) -> bool:
        with self.lock:
            if ip not in self.requests:
                self.requests[ip] = []
            self.requests[ip] = self._clean(self.requests[ip], SecurityConfig.RATE_LIMIT_WINDOW)
            if len(self.requests[ip]) >= SecurityConfig.RATE_LIMIT_MAX_REQUESTS:
                security_logger.log_rate_limit(ip, "request")
                return False
            self.requests[ip].append(time.time())
            return True
    
    def check_conn_limit(self, ip: str) -> bool:
        with self.lock:
            if self.connections.get(ip, 0) >= SecurityConfig.RATE_LIMIT_MAX_CONNECTIONS:
                security_logger.log_rate_limit(ip, "conn_limit")
                return False
            return True
    
    def check_total_conn(self) -> bool:
        with self.lock:
            if sum(self.connections.values()) >= SecurityConfig.MAX_TOTAL_CONNECTIONS:
                security_logger.log_rate_limit("GLOBAL", "total_conn")
                return False
            return True
    
    def add_conn(self, ip: str):
        with self.lock:
            self.connections[ip] = self.connections.get(ip, 0) + 1
    
    def remove_conn(self, ip: str):
        with self.lock:
            if ip in self.connections:
                self.connections[ip] = max(0, self.connections[ip] - 1)
                if self.connections[ip] == 0:
                    del self.connections[ip]
    
    def check_cmd_rate(self, sid: str) -> bool:
        with self.lock:
            if sid not in self.commands:
                self.commands[sid] = []
            self.commands[sid] = self._clean(self.commands[sid], 60)
            if len(self.commands[sid]) >= SecurityConfig.RATE_LIMIT_MAX_COMMANDS_PER_MINUTE:
                security_logger.log_rate_limit(sid, "cmd_rate")
                return False
            self.commands[sid].append(time.time())
            return True
    
    def cleanup(self, sid: str):
        with self.lock:
            self.commands.pop(sid, None)


rate_limiter = RateLimiter()


class InputValidator:
    """输入验证器"""
    
    @staticmethod
    def validate_hostname(h: str) -> tuple:
        if not h:
            return (False, "空主机名", "")
        if len(h) > SecurityConfig.MAX_HOSTNAME_LENGTH:
            return (False, "主机名过长", "")
        h = h.strip()
        try:
            ipaddress.ip_address(h)
            return (True, "", h)
        except ValueError:
            pass
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9\-\.]*[a-zA-Z0-9]$', h) and len(h) > 1:
            if len(h) == 1 and h.isalnum():
                return (True, "", h)
            return (False, "主机名格式无效", "")
        return (True, "", h)
    
    @staticmethod
    def validate_port(p) -> tuple:
        try:
            p = int(p)
        except (ValueError, TypeError):
            return (False, "端口无效", 22)
        if p < 1 or p > 65535:
            return (False, "端口范围无效", 22)
        return (True, "", p)
    
    @staticmethod
    def validate_username(u: str) -> tuple:
        if not u:
            return (False, "空用户名", "")
        if len(u) > SecurityConfig.MAX_USERNAME_LENGTH:
            return (False, "用户名过长", "")
        u = u.strip()
        if not re.match(r'^[a-zA-Z0-9_\-]+$', u):
            return (False, "用户名含非法字符", "")
        return (True, "", u)
    
    @staticmethod
    def validate_password(p: str) -> tuple:
        if p is None:
            return (True, "", None)
        if len(p) > SecurityConfig.MAX_PASSWORD_LENGTH:
            return (False, "密码过长", "")
        if '\x00' in p:
            return (False, "密码含非法字符", "")
        return (True, "", p)
    
    @staticmethod
    def validate_command(c: str) -> tuple:
        if not c:
            return (True, "", "", False)
        if len(c) > SecurityConfig.MAX_COMMAND_LENGTH:
            return (False, "命令过长", "", False)
        if '\x00' in c:
            return (False, "命令含非法字符", "", False)
        dangerous = False
        for p in SecurityConfig.DANGEROUS_COMMAND_PATTERNS:
            if re.search(p, c, re.IGNORECASE):
                dangerous = True
                break
        return (True, "", c, dangerous)
    
    @staticmethod
    def validate_path(p: str) -> tuple:
        if not p:
            return (True, "", "")
        if len(p) > SecurityConfig.MAX_PATH_LENGTH:
            return (False, "路径过长", "")
        if '\x00' in p:
            return (False, "路径含非法字符", "")
        return (True, "", p.strip())
    
    @staticmethod
    def validate_msg_size(m: str) -> bool:
        if not m:
            return True
        return len(m.encode('utf-8')) <= SecurityConfig.MAX_MESSAGE_SIZE


class IPAccessControl:
    """IP访问控制"""
    
    @staticmethod
    def check_allowed(ip: str) -> tuple:
        if ip in SecurityConfig.IP_BLACKLIST:
            return (False, "IP被封禁")
        if SecurityConfig.IP_WHITELIST and ip not in SecurityConfig.IP_WHITELIST:
            return (False, "IP不在白名单")
        return (True, "")
    
    @staticmethod
    def get_client_ip(ws: WebSocket) -> str:
        for k, v in ws.headers.items():
            if k.lower() == 'x-forwarded-for':
                return v.split(',')[0].strip()
        return ws.client.host if ws.client else "unknown"


class SessionSecurity:
    """会话安全管理"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.ips: Dict[str, str] = {}
        self.activity: Dict[str, float] = {}
        self.lock = threading.Lock()
    
    def register(self, sid: str, ip: str):
        with self.lock:
            self.ips[sid] = ip
            self.activity[sid] = time.time()
    
    def update(self, sid: str):
        with self.lock:
            self.activity[sid] = time.time()
    
    def check_idle(self, sid: str) -> bool:
        with self.lock:
            last = self.activity.get(sid, 0)
            return (time.time() - last) > SecurityConfig.IDLE_TIMEOUT
    
    def cleanup(self, sid: str):
        with self.lock:
            self.ips.pop(sid, None)
            self.activity.pop(sid, None)


session_security = SessionSecurity()


def apply_security_checks(websocket: WebSocket, session_id: str = None) -> tuple:
    """应用所有安全检查，返回: (allowed, error_message, client_ip)"""
    client_ip = IPAccessControl.get_client_ip(websocket)
    
    allowed, msg = IPAccessControl.check_allowed(client_ip)
    if not allowed:
        security_logger.log_blocked(client_ip, msg)
        return (False, msg, client_ip)
    
    if not rate_limiter.check_request(client_ip):
        return (False, "请求过于频繁", client_ip)
    
    if not rate_limiter.check_conn_limit(client_ip):
        return (False, "连接数超限", client_ip)
    
    if not rate_limiter.check_total_conn():
        return (False, "服务器连接数已满", client_ip)
    
    return (True, "", client_ip)


def validate_ssh_connection(data: dict) -> tuple:
    """验证SSH连接参数，返回: (valid, error, sanitized_data)"""
    result = {}
    
    ok, err, val = InputValidator.validate_hostname(data.get('hostname', ''))
    if not ok:
        return (False, f"主机名验证失败: {err}", {})
    result['hostname'] = val
    
    ok, err, val = InputValidator.validate_port(data.get('port', 22))
    if not ok:
        return (False, f"端口验证失败: {err}", {})
    result['port'] = val
    
    ok, err, val = InputValidator.validate_username(data.get('username', ''))
    if not ok:
        return (False, f"用户名验证失败: {err}", {})
    result['username'] = val
    
    ok, err, val = InputValidator.validate_password(data.get('password'))
    if not ok:
        return (False, f"密码验证失败: {err}", {})
    result['password'] = val
    
    result['key_file'] = data.get('key_file')
    result['width'] = data.get('width', 80)
    result['height'] = data.get('height', 24)
    
    return (True, "", result)


def validate_command_input(command: str, session_id: str) -> tuple:
    """验证命令输入，返回: (valid, error, command)"""
    ok, err, cmd, dangerous = InputValidator.validate_command(command)
    if not ok:
        security_logger.log_blocked(session_id, err)
        return (False, err, "")
    
    if dangerous:
        security_logger.log_dangerous_cmd(session_id, cmd)
    
    if not rate_limiter.check_cmd_rate(session_id):
        return (False, "命令发送过于频繁", "")
    
    return (True, "", cmd)


def cleanup_security_session(session_id: str, client_ip: str):
    """清理会话安全数据"""
    rate_limiter.cleanup(session_id)
    rate_limiter.remove_conn(client_ip)
    session_security.cleanup(session_id)
