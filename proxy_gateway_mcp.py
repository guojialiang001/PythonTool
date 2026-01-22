#!/usr/bin/env python3
"""
OpenAI API 代理网关 + Exa MCP 集成版
支持多个后端API的代理转发
集成 Exa MCP 搜索功能，带 30 分钟线程安全缓存
返回 OpenAI 兼容格式

安全特性：
- 路径遍历攻击防护
- URL 注入防护
- 白名单路径验证
- 请求路径规范化
- 敏感路径保护
- 三级威胁防护系统（观察名单 -> 预警名单 -> 黑名单）
"""

import asyncio
import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn
from typing import Optional, AsyncGenerator, Dict, Any, Tuple, List
import logging
import os
import re
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import threading
import time
import json
from datetime import datetime

# 导入三级威胁防护系统
try:
    from security_threat_protection import (
        ThreatProtectionEngine,
        ThreatProtectionConfig,
        ThreatLevel,
        ViolationType
    )
    THREAT_PROTECTION_AVAILABLE = True
except ImportError:
    THREAT_PROTECTION_AVAILABLE = False
    ThreatProtectionEngine = None
    ThreatProtectionConfig = None
    ThreatLevel = None
    ViolationType = None

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | PID:%(process)d | %(threadName)-15s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="OpenAI API Proxy Gateway with MCP + Threat Protection")

# ============================================================
# 三级威胁防护系统初始化
# ============================================================
threat_engine: Optional[ThreatProtectionEngine] = None

def init_threat_protection():
    """初始化三级威胁防护系统"""
    global threat_engine
    
    if not THREAT_PROTECTION_AVAILABLE:
        logger.warning("ThreatProtection module not available, skipping initialization")
        return
    
    try:
        config = ThreatProtectionConfig()
        
        # 配置白名单路径（这些路径不会触发威胁检测）
        # 添加所有代理配置的路径前缀
        for path_prefix in PROXY_CONFIG.keys():
            config.add_whitelist_path(path_prefix)
        
        # 添加系统路径
        config.add_whitelist_path("/health")
        config.add_whitelist_path("/stats")
        config.add_whitelist_path("/security")
        config.add_whitelist_path("/rate-limit")
        config.add_whitelist_path("/api/mcp")
        config.add_whitelist_path("/threat")  # 威胁防护管理接口
        
        threat_engine = ThreatProtectionEngine(config)
        logger.info("=" * 60)
        logger.info("Three-Level Threat Protection System ENABLED")
        logger.info("  Level 1: Watch List (1st violation)")
        logger.info("  Level 2: Warning List (3+ violations) -> Email alert")
        logger.info("  Level 3: Blacklist (5+ violations) -> Block requests")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"Failed to initialize ThreatProtection: {e}")
        threat_engine = None

# 延迟初始化（在 PROXY_CONFIG 定义后）

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# MCP 配置
MCP_CONFIG = {
    "exa": {
        "url": "https://mcp.exa.ai/mcp?exaApiKey=6d36568c-2f26-4f73-8f8e-0d5d85a258e7&tools=web_search_exa,get_code_context_exa,crawling_exa,company_research_exa,linkedin_search_exa,deep_researcher_start,deep_researcher_check",
        "headers": {}
    }
}

# 代理配置 (继承自原版)
PROXY_CONFIG = {
    '/api/chat': {'target': 'https://api.xiaomimimo.com', 'rewrite': '/v1/chat'},
    '/api/claude': {'target': 'https://api.avoapi.com', 'rewrite': '/v1'},
    '/api/mimo': {'target': 'https://api.xiaomimimo.com', 'rewrite': '/v1'},
    '/api/opus-backup': {'target': 'https://aicodelink.top', 'rewrite': '/v1'},
    '/api/opus': {'target': 'https://api.code-relay.com', 'rewrite': '/v1'},
    '/api/gemini': {'target': 'https://claude.chiddns.com', 'rewrite': '/v1'},
    '/api/deepseek': {'target': 'https://claude.chiddns.com', 'rewrite': '/v1'},
    '/api/sonnet-backup': {'target': 'https://aicodelink.top', 'rewrite': '/v1'},
    '/api/sonnet': {'target': 'https://api.code-relay.com', 'rewrite': '/v1'},
    '/api/minimax': {'target': 'https://claude.chiddns.com', 'rewrite': '/v1'},
    '/api/grok': {'target': 'https://api.avoapi.com', 'rewrite': '/v1'},
    '/api/minimaxm21': {'target': 'https://aiping.cn/api', 'rewrite': '/v1'},
    '/api/code-relay': {'target': 'https://api.code-relay.com', 'rewrite': '/v1'},
    '/api/qwen': {'target': 'https://aiping.cn/api', 'rewrite': '/v1'},
    '/api/deepseekv32': {'target': 'https://aiping.cn/api', 'rewrite': '/v1'},
    '/api/Qwen3VL32B': {'target': 'https://api.suanli.cn', 'rewrite': '/v1'},
    '/api/Qwen330BA3B': {'target': 'https://api.suanli.cn', 'rewrite': '/v1'},
    '/api/qwenCoderPlus': {'target': 'https://apis.iflow.cn', 'rewrite': '/v1'},
    '/api/qwenVLPlus': {'target': 'https://apis.iflow.cn', 'rewrite': '/v1'},
    '/api/qwenMax': {'target': 'https://apis.iflow.cn', 'rewrite': '/v1'},
    '/api/kimiK2': {'target': 'https://apis.iflow.cn', 'rewrite': '/v1'},
    '/api/doubaoSeed': {'target': 'https://api.routin.ai', 'rewrite': '/v1'},
    '/api/mistral': {'target': 'https://api.mistral.ai', 'rewrite': '/v1'}
}

# 初始化三级威胁防护系统（在 PROXY_CONFIG 定义后）
init_threat_protection()

EXCLUDED_HEADERS = {
    'host', 'content-length', 'transfer-encoding',
    'connection', 'keep-alive', 'proxy-authenticate',
    'proxy-authorization', 'te', 'trailers', 'upgrade'
}

# --- 路径安全防护配置 ---
class PathSecurityConfig:
    """路径安全配置"""
    # 危险的路径模式（正则表达式）
    DANGEROUS_PATTERNS: List[str] = [
        r'\.\./',           # 路径遍历 ../
        r'\.\.\\',          # Windows 路径遍历 ..\
        r'%2e%2e',          # URL 编码的 ..
        r'%252e%252e',      # 双重 URL 编码的 ..
        r'\.%2e',           # 混合编码
        r'%2e\.',           # 混合编码
        r'/\./',            # 隐藏目录访问
        r'\\\.\\',          # Windows 隐藏目录
        r'%00',             # Null 字节注入
        r'\x00',            # Null 字节
        r'%0d%0a',          # CRLF 注入
        r'\r\n',            # CRLF
        r'%0d',             # CR
        r'%0a',             # LF
    ]

    # 敏感路径前缀（禁止访问）
    SENSITIVE_PATHS: List[str] = [
        '/etc/',
        '/proc/',
        '/sys/',
        '/dev/',
        '/root/',
        '/home/',
        '/var/log/',
        '/var/run/',
        '/tmp/',
        '/usr/local/etc/',
        'C:\\Windows\\',
        'C:\\System32\\',
        'C:\\Program Files\\',
        '/.git/',
        '/.svn/',
        '/.env',
        '/config/',
        '/secrets/',
        '/private/',
        '/admin/',
        '/.htaccess',
        '/.htpasswd',
        '/wp-config',
        '/web.config',
    ]

    # 允许的 URL scheme
    ALLOWED_SCHEMES: List[str] = ['http', 'https']

    # 最大路径长度
    MAX_PATH_LENGTH: int = 2048

    # 最大 URL 长度
    MAX_URL_LENGTH: int = 4096


class PathSecurityValidator:
    """
    路径安全验证器

    提供多层安全防护：
    1. 路径长度检查
    2. 危险模式检测
    3. 敏感路径保护
    4. URL 规范化和验证
    5. 路径遍历防护
    """

    def __init__(self, config: PathSecurityConfig = None):
        self.config = config or PathSecurityConfig()
        # 预编译正则表达式以提高性能
        self._dangerous_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.config.DANGEROUS_PATTERNS
        ]
        logger.info("PathSecurityValidator initialized with enhanced protection")

    def validate_path(self, path: str, request_id: str = "") -> Tuple[bool, str]:
        """
        验证请求路径的安全性

        Args:
            path: 请求路径
            request_id: 请求 ID（用于日志）

        Returns:
            (is_valid, error_message) - 如果有效返回 (True, "")，否则返回 (False, 错误信息)
        """
        if not path:
            return False, "Empty path is not allowed"

        # 1. 检查路径长度
        if len(path) > self.config.MAX_PATH_LENGTH:
            logger.warning(f"[{request_id}] SECURITY: Path too long ({len(path)} > {self.config.MAX_PATH_LENGTH})")
            return False, f"Path exceeds maximum length of {self.config.MAX_PATH_LENGTH}"

        # 2. URL 解码并检查（防止编码绕过）
        try:
            decoded_path = unquote(unquote(path))  # 双重解码以防止双重编码攻击
        except Exception as e:
            logger.warning(f"[{request_id}] SECURITY: Path decode error: {str(e)}")
            return False, "Invalid path encoding"

        # 3. 检查危险模式
        for pattern in self._dangerous_patterns:
            if pattern.search(path) or pattern.search(decoded_path):
                logger.warning(f"[{request_id}] SECURITY: Dangerous pattern detected in path: {path[:100]}")
                return False, "Path contains dangerous patterns"

        # 4. 检查敏感路径
        path_lower = decoded_path.lower()
        for sensitive in self.config.SENSITIVE_PATHS:
            if path_lower.startswith(sensitive.lower()) or sensitive.lower() in path_lower:
                logger.warning(f"[{request_id}] SECURITY: Sensitive path access attempt: {path[:100]}")
                return False, "Access to sensitive path is forbidden"

        # 5. 规范化路径并检查路径遍历
        normalized = self._normalize_path(decoded_path)
        if normalized != decoded_path.replace('\\', '/'):
            # 路径规范化后发生变化，可能存在路径遍历
            if '..' in path or '..' in decoded_path:
                logger.warning(f"[{request_id}] SECURITY: Path traversal attempt detected: {path[:100]}")
                return False, "Path traversal is not allowed"

        # 6. 检查是否包含 null 字节
        if '\x00' in path or '\x00' in decoded_path:
            logger.warning(f"[{request_id}] SECURITY: Null byte injection attempt: {path[:100]}")
            return False, "Null byte in path is not allowed"

        return True, ""

    def validate_url(self, url: str, request_id: str = "") -> Tuple[bool, str]:
        """
        验证目标 URL 的安全性

        Args:
            url: 目标 URL
            request_id: 请求 ID

        Returns:
            (is_valid, error_message)
        """
        if not url:
            return False, "Empty URL is not allowed"

        # 1. 检查 URL 长度
        if len(url) > self.config.MAX_URL_LENGTH:
            logger.warning(f"[{request_id}] SECURITY: URL too long ({len(url)} > {self.config.MAX_URL_LENGTH})")
            return False, f"URL exceeds maximum length of {self.config.MAX_URL_LENGTH}"

        # 2. 解析 URL
        try:
            parsed = urlparse(url)
        except Exception as e:
            logger.warning(f"[{request_id}] SECURITY: URL parse error: {str(e)}")
            return False, "Invalid URL format"

        # 3. 检查 scheme
        if parsed.scheme.lower() not in self.config.ALLOWED_SCHEMES:
            logger.warning(f"[{request_id}] SECURITY: Invalid URL scheme: {parsed.scheme}")
            return False, f"URL scheme '{parsed.scheme}' is not allowed"

        # 4. 检查是否有主机名
        if not parsed.netloc:
            logger.warning(f"[{request_id}] SECURITY: URL missing host: {url[:100]}")
            return False, "URL must have a valid host"

        # 5. 检查是否是内网地址（防止 SSRF）
        is_internal, reason = self._check_internal_address(parsed.netloc, request_id)
        if is_internal:
            logger.warning(f"[{request_id}] SECURITY: Internal address access attempt: {parsed.netloc}")
            return False, reason

        # 6. 验证路径部分
        if parsed.path:
            path_valid, path_error = self.validate_path(parsed.path, request_id)
            if not path_valid:
                return False, f"URL path validation failed: {path_error}"

        return True, ""

    def _normalize_path(self, path: str) -> str:
        """
        规范化路径，移除冗余的 . 和 ..
        """
        # 将反斜杠转换为正斜杠
        path = path.replace('\\', '/')

        # 分割路径
        parts = path.split('/')
        normalized = []

        for part in parts:
            if part == '..':
                if normalized and normalized[-1] != '':
                    normalized.pop()
            elif part != '.' and part != '':
                normalized.append(part)
            elif part == '' and not normalized:
                normalized.append('')

        result = '/'.join(normalized)
        if path.startswith('/') and not result.startswith('/'):
            result = '/' + result

        return result

    def _check_internal_address(self, host: str, request_id: str = "") -> Tuple[bool, str]:
        """
        检查是否是内网地址（防止 SSRF 攻击）

        注意：这里只做基本检查，生产环境应该使用更严格的检查
        """
        host_lower = host.lower()

        # 移除端口号
        if ':' in host_lower:
            host_lower = host_lower.split(':')[0]

        # 检查 localhost
        if host_lower in ['localhost', '127.0.0.1', '::1', '0.0.0.0']:
            return True, "Access to localhost is forbidden"

        # 检查内网 IP 范围
        internal_patterns = [
            r'^10\.',                    # 10.0.0.0/8
            r'^172\.(1[6-9]|2[0-9]|3[0-1])\.',  # 172.16.0.0/12
            r'^192\.168\.',              # 192.168.0.0/16
            r'^169\.254\.',              # 链路本地地址
            r'^fc00:',                   # IPv6 私有地址
            r'^fe80:',                   # IPv6 链路本地
        ]

        for pattern in internal_patterns:
            if re.match(pattern, host_lower, re.IGNORECASE):
                return True, "Access to internal network is forbidden"

        # 检查是否是 metadata 服务（云环境）
        metadata_hosts = [
            '169.254.169.254',           # AWS/GCP/Azure metadata
            'metadata.google.internal',   # GCP
            'metadata.azure.com',         # Azure
        ]

        if host_lower in metadata_hosts:
            return True, "Access to cloud metadata service is forbidden"

        return False, ""

    def sanitize_path(self, path: str) -> str:
        """
        清理和规范化路径

        Returns:
            清理后的安全路径
        """
        # URL 解码
        try:
            path = unquote(path)
        except:
            pass

        # 移除 null 字节
        path = path.replace('\x00', '')

        # 规范化
        path = self._normalize_path(path)

        # 确保以 / 开头
        if not path.startswith('/'):
            path = '/' + path

        return path


# 创建全局安全验证器实例
path_security = PathSecurityValidator()


# --- 安全统计类 ---
class SecurityStats:
    """
    安全事件统计和记录

    功能：
    1. 统计各类安全事件数量
    2. 记录最近的安全事件详情
    3. 线程安全的计数器
    """

    def __init__(self, max_events: int = 1000):
        self.lock = threading.Lock()
        self.max_events = max_events

        # 统计计数器
        self.blocked_count = 0
        self.path_traversal_count = 0
        self.ssrf_attempt_count = 0
        self.invalid_path_count = 0
        self.sensitive_path_count = 0
        self.dangerous_pattern_count = 0
        self.rate_limit_count = 0  # 速率限制计数

        # 最近的安全事件列表
        self._recent_events: List[Dict[str, Any]] = []

    def record_event(self, event_type: str, request_id: str, path: str,
                     detail: str = "", client_ip: str = ""):
        """
        记录安全事件

        Args:
            event_type: 事件类型 (path_traversal, ssrf, invalid_path, sensitive_path, dangerous_pattern, rate_limit)
            request_id: 请求 ID
            path: 请求路径
            detail: 详细信息
            client_ip: 客户端 IP
        """
        with self.lock:
            # 更新计数器
            self.blocked_count += 1

            if event_type == "path_traversal":
                self.path_traversal_count += 1
            elif event_type == "ssrf":
                self.ssrf_attempt_count += 1
            elif event_type == "invalid_path":
                self.invalid_path_count += 1
            elif event_type == "sensitive_path":
                self.sensitive_path_count += 1
            elif event_type == "dangerous_pattern":
                self.dangerous_pattern_count += 1
            elif event_type == "rate_limit":
                self.rate_limit_count += 1

            # 记录事件详情
            event = {
                "timestamp": datetime.now().isoformat(),
                "type": event_type,
                "request_id": request_id,
                "path": path[:200] if path else "",  # 截断过长的路径
                "detail": detail[:500] if detail else "",  # 截断过长的详情
                "client_ip": client_ip
            }

            self._recent_events.append(event)

            # 保持列表大小在限制内
            if len(self._recent_events) > self.max_events:
                self._recent_events = self._recent_events[-self.max_events:]

            # 记录到日志
            logger.warning(f"[SECURITY EVENT] Type: {event_type} | Request: {request_id} | "
                          f"Path: {path[:100]} | Detail: {detail[:100]}")

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        获取最近的安全事件

        Args:
            limit: 返回的最大事件数量

        Returns:
            最近的安全事件列表（按时间倒序）
        """
        with self.lock:
            return list(reversed(self._recent_events[-limit:]))

    def get_summary(self) -> Dict[str, Any]:
        """
        获取安全统计摘要
        """
        with self.lock:
            return {
                "total_blocked": self.blocked_count,
                "path_traversal": self.path_traversal_count,
                "ssrf_attempts": self.ssrf_attempt_count,
                "invalid_paths": self.invalid_path_count,
                "sensitive_paths": self.sensitive_path_count,
                "dangerous_patterns": self.dangerous_pattern_count,
                "recent_events_count": len(self._recent_events)
            }


# 创建全局安全统计实例
security_stats = SecurityStats()


# --- 速率限制和自动封禁类 ---
class RateLimiterConfig:
    """
    速率限制配置

    设计原则：只封禁明显的恶意攻击，正常用户几乎不可能触发

    正常使用场景分析：
    - 前端默认 12 并发 + 2 个总结模型 = 14 并发
    - 每个对话请求约 1-5 秒完成
    - 正常用户每分钟最多 100-200 请求
    - 即使疯狂点击，人类也很难超过每秒 10 个请求

    攻击场景：
    - 脚本攻击通常每秒数百甚至数千请求
    - 设置阈值为每秒 200 请求，这是人类绝对不可能达到的速率
    """
    # 时间窗口（秒）
    WINDOW_SIZE: int = 60

    # 每个时间窗口内允许的最大请求数
    # 设置为 2000，即每分钟 2000 请求，正常用户不可能达到
    MAX_REQUESTS_PER_WINDOW: int = 2000

    # 触发封禁的阈值（每秒请求数）
    # 设置为 200 rps，这是人类绝对不可能达到的速率
    # 即使用脚本，200 rps 也是明显的攻击行为
    BAN_THRESHOLD_RPS: int = 200

    # 封禁持续时间（秒）
    BAN_DURATION: int = 600  # 10 分钟

    # 永久封禁阈值（被临时封禁的次数）
    PERMANENT_BAN_THRESHOLD: int = 3

    # 清理过期记录的间隔（秒）
    CLEANUP_INTERVAL: int = 60

    # 突发请求容忍度（1秒内的请求峰值）
    # 设置为 100，即 1 秒内 100 个请求才触发警告
    BURST_LIMIT: int = 100


class RateLimiter:
    """
    速率限制器 - 带自动封禁功能

    功能：
    1. 滑动窗口速率限制
    2. 超速自动临时封禁
    3. 多次违规永久封禁
    4. 线程安全
    5. 自动清理过期记录

    工作原理：
    - 每个 IP 维护一个请求时间戳列表
    - 计算滑动窗口内的请求数
    - 如果请求速率超过阈值，自动封禁
    - 多次被封禁后，永久封禁
    """

    def __init__(self, config: RateLimiterConfig = None):
        self.config = config or RateLimiterConfig()
        self.lock = threading.Lock()

        # IP -> 请求时间戳列表
        self._requests: Dict[str, List[float]] = {}

        # IP -> 封禁信息 {banned_until: float, ban_count: int, permanent: bool}
        self._bans: Dict[str, Dict[str, Any]] = {}

        # 统计信息
        self._stats = {
            "total_requests": 0,
            "rate_limited": 0,
            "temp_bans": 0,
            "permanent_bans": 0
        }

        # 上次清理时间
        self._last_cleanup = time.time()

        logger.info(f"RateLimiter initialized: {self.config.MAX_REQUESTS_PER_WINDOW} req/{self.config.WINDOW_SIZE}s, "
                   f"ban threshold: {self.config.BAN_THRESHOLD_RPS} rps")

    def is_allowed(self, client_ip: str, request_id: str = "") -> Tuple[bool, str]:
        """
        检查请求是否被允许

        Args:
            client_ip: 客户端 IP
            request_id: 请求 ID（用于日志）

        Returns:
            (is_allowed, reason) - 如果允许返回 (True, "")，否则返回 (False, 原因)
        """
        current_time = time.time()

        with self.lock:
            self._stats["total_requests"] += 1

            # 定期清理过期记录
            if current_time - self._last_cleanup > self.config.CLEANUP_INTERVAL:
                self._cleanup_expired(current_time)
                self._last_cleanup = current_time

            # 1. 检查是否被封禁
            if client_ip in self._bans:
                ban_info = self._bans[client_ip]

                # 永久封禁
                if ban_info.get("permanent", False):
                    logger.warning(f"[{request_id}] RATE LIMIT: Permanently banned IP: {client_ip}")
                    return False, "IP is permanently banned due to repeated violations"

                # 临时封禁
                if current_time < ban_info.get("banned_until", 0):
                    remaining = int(ban_info["banned_until"] - current_time)
                    logger.warning(f"[{request_id}] RATE LIMIT: Temporarily banned IP: {client_ip}, {remaining}s remaining")
                    return False, f"IP is temporarily banned. Try again in {remaining} seconds"
                else:
                    # 封禁已过期，但保留 ban_count
                    pass

            # 2. 获取或创建请求记录
            if client_ip not in self._requests:
                self._requests[client_ip] = []

            requests = self._requests[client_ip]

            # 3. 清理窗口外的旧请求
            window_start = current_time - self.config.WINDOW_SIZE
            requests[:] = [t for t in requests if t > window_start]

            # 4. 计算当前速率
            request_count = len(requests)

            # 5. 检查是否超过速率限制
            if request_count >= self.config.MAX_REQUESTS_PER_WINDOW:
                self._stats["rate_limited"] += 1

                # 计算每秒请求数
                if request_count > 0:
                    time_span = current_time - requests[0] if requests else 1
                    rps = request_count / max(time_span, 1)

                    # 如果速率极高，触发封禁
                    if rps >= self.config.BAN_THRESHOLD_RPS:
                        self._ban_ip(client_ip, current_time, request_id)
                        return False, f"IP banned due to excessive request rate ({rps:.1f} rps)"

                logger.warning(f"[{request_id}] RATE LIMIT: {client_ip} exceeded limit: {request_count}/{self.config.MAX_REQUESTS_PER_WINDOW}")
                return False, f"Rate limit exceeded. Maximum {self.config.MAX_REQUESTS_PER_WINDOW} requests per {self.config.WINDOW_SIZE} seconds"

            # 6. 记录本次请求
            requests.append(current_time)

            return True, ""

    def _ban_ip(self, client_ip: str, current_time: float, request_id: str = ""):
        """
        封禁 IP

        内部方法，必须在持有锁的情况下调用
        """
        if client_ip not in self._bans:
            self._bans[client_ip] = {"ban_count": 0, "permanent": False}

        ban_info = self._bans[client_ip]
        ban_info["ban_count"] = ban_info.get("ban_count", 0) + 1

        # 检查是否达到永久封禁阈值
        if ban_info["ban_count"] >= self.config.PERMANENT_BAN_THRESHOLD:
            ban_info["permanent"] = True
            self._stats["permanent_bans"] += 1
            logger.error(f"[{request_id}] RATE LIMIT: IP {client_ip} PERMANENTLY BANNED after {ban_info['ban_count']} violations")
        else:
            # 临时封禁，时间随违规次数增加
            ban_duration = self.config.BAN_DURATION * ban_info["ban_count"]
            ban_info["banned_until"] = current_time + ban_duration
            self._stats["temp_bans"] += 1
            logger.error(f"[{request_id}] RATE LIMIT: IP {client_ip} TEMPORARILY BANNED for {ban_duration}s "
                        f"(violation #{ban_info['ban_count']})")

    def _cleanup_expired(self, current_time: float):
        """
        清理过期的请求记录

        内部方法，必须在持有锁的情况下调用
        """
        window_start = current_time - self.config.WINDOW_SIZE

        # 清理过期的请求记录
        expired_ips = []
        for ip, requests in self._requests.items():
            requests[:] = [t for t in requests if t > window_start]
            if not requests:
                expired_ips.append(ip)

        for ip in expired_ips:
            del self._requests[ip]

        if expired_ips:
            logger.debug(f"RateLimiter cleanup: removed {len(expired_ips)} inactive IPs")

    def unban_ip(self, client_ip: str) -> bool:
        """
        手动解除 IP 封禁

        Args:
            client_ip: 要解封的 IP

        Returns:
            是否成功解封
        """
        with self.lock:
            if client_ip in self._bans:
                del self._bans[client_ip]
                logger.info(f"IP {client_ip} has been unbanned")
                return True
            return False

    def get_ban_info(self, client_ip: str) -> Optional[Dict[str, Any]]:
        """
        获取 IP 的封禁信息
        """
        with self.lock:
            if client_ip in self._bans:
                return self._bans[client_ip].copy()
            return None

    def get_stats(self) -> Dict[str, Any]:
        """
        获取速率限制统计信息
        """
        with self.lock:
            return {
                **self._stats,
                "active_ips": len(self._requests),
                "banned_ips": len([b for b in self._bans.values() if b.get("permanent") or time.time() < b.get("banned_until", 0)]),
                "permanent_banned_ips": len([b for b in self._bans.values() if b.get("permanent")])
            }

    def get_banned_ips(self) -> List[Dict[str, Any]]:
        """
        获取所有被封禁的 IP 列表
        """
        current_time = time.time()
        with self.lock:
            result = []
            for ip, info in self._bans.items():
                if info.get("permanent") or current_time < info.get("banned_until", 0):
                    result.append({
                        "ip": ip,
                        "permanent": info.get("permanent", False),
                        "ban_count": info.get("ban_count", 0),
                        "banned_until": datetime.fromtimestamp(info.get("banned_until", 0)).isoformat() if not info.get("permanent") else "permanent"
                    })
            return result


# 创建全局速率限制器实例
rate_limiter = RateLimiter()


# Header 校验配置
REQUIRED_HEADERS = {
    'origin': 'https://www.toproject.cloud',
    'priority': 'u=1, i',
    'referer': 'https://www.toproject.cloud/'
}

HTTP_CLIENT_LIMITS = httpx.Limits(max_keepalive_connections=60, max_connections=120, keepalive_expiry=30.0)
http_client: Optional[httpx.AsyncClient] = None
thread_pool: Optional[ThreadPoolExecutor] = None
# 对于2G2核服务器处理18并发的优化配置：
# - 使用20个线程（略大于并发数，留有余量）
# - 这是异步I/O密集型任务，线程主要等待网络响应
# - 2核CPU可以支持20个I/O等待线程而不会过度负载
# - 内存占用约：20线程 * 10MB = 200MB，仍在安全范围内
THREAD_POOL_SIZE = 20
request_counter = 0
request_counter_lock = threading.Lock()

# --- 缓存管理类（线程安全 + 请求去重） ---
class MCPCache:
    """
    线程安全的缓存管理器，支持：
    1. 30分钟过期策略
    2. 精确字符串匹配
    3. 请求去重（防止并发时重复调用 MCP）
    
    并发安全机制：
    - 使用 threading.Lock 保护缓存字典的读写
    - 使用 pending 字典 + asyncio.Event 实现请求去重
    - 当多个并发请求查询同一个 query 时，只有第一个会真正调用 MCP，
      其他请求会等待第一个完成后直接获取缓存结果
    """
    def __init__(self, expiry_seconds: int = 1800):
        self.cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self.lock = threading.Lock()
        self.expiry_seconds = expiry_seconds
        # 用于跟踪正在进行中的请求，防止重复调用
        self.pending: Dict[str, asyncio.Event] = {}
        self.pending_lock = asyncio.Lock()

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        """
        获取缓存（线程安全）
        返回 None 表示缓存未命中或已过期
        """
        with self.lock:
            if query in self.cache:
                data, timestamp = self.cache[query]
                if time.time() - timestamp < self.expiry_seconds:
                    logger.info(f"Cache HIT for query: {query[:50]}...")
                    return data
                else:
                    logger.info(f"Cache EXPIRED for query: {query[:50]}...")
                    del self.cache[query]
            return None

    def set(self, query: str, data: Dict[str, Any]):
        """
        设置缓存（线程安全）
        """
        with self.lock:
            self.cache[query] = (data, time.time())
            logger.info(f"Cache SET for query: {query[:50]}...")

    async def acquire_or_wait(self, query: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        尝试获取执行权或等待其他请求完成
        
        返回:
            (True, None) - 获得执行权，需要调用 MCP
            (False, data) - 其他请求已完成，直接返回缓存数据
        
        并发场景示例：
        - 请求A: acquire_or_wait("test") -> (True, None)，获得执行权
        - 请求B: acquire_or_wait("test") -> 等待...
        - 请求C: acquire_or_wait("test") -> 等待...
        - 请求A: 完成 MCP 调用，调用 release("test")
        - 请求B, C: 被唤醒，返回 (False, cached_data)
        """
        # 先检查缓存
        cached = self.get(query)
        if cached is not None:
            return (False, cached)
        
        async with self.pending_lock:
            # 双重检查：在获取 pending_lock 后再次检查缓存
            cached = self.get(query)
            if cached is not None:
                return (False, cached)
            
            # 检查是否有其他请求正在处理这个 query
            if query in self.pending:
                event = self.pending[query]
                logger.info(f"Request waiting for pending query: {query[:50]}...")
            else:
                # 创建新的 Event，标记这个 query 正在处理中
                event = asyncio.Event()
                self.pending[query] = event
                logger.info(f"Request acquired lock for query: {query[:50]}...")
                return (True, None)  # 获得执行权
        
        # 等待其他请求完成
        await event.wait()
        
        # 等待完成后，从缓存获取结果
        cached = self.get(query)
        if cached is not None:
            logger.info(f"Request got result after waiting: {query[:50]}...")
            return (False, cached)
        else:
            # 极端情况：等待的请求失败了，需要重新尝试
            logger.warning(f"Waited but no cache found, retrying: {query[:50]}...")
            return await self.acquire_or_wait(query)

    async def release(self, query: str):
        """
        释放执行权，唤醒等待的请求
        """
        async with self.pending_lock:
            if query in self.pending:
                event = self.pending.pop(query)
                event.set()  # 唤醒所有等待的请求
                logger.info(f"Released lock and notified waiters for: {query[:50]}...")

mcp_cache = MCPCache(expiry_seconds=1800)

# --- 工具函数 ---
def get_request_id():
    global request_counter
    with request_counter_lock:
        request_counter += 1
        return f"REQ-{request_counter:06d}"

@app.on_event("startup")
async def startup_event():
    global http_client, thread_pool
    logger.info("=" * 70)
    logger.info("Starting OpenAI API Proxy Gateway with MCP")
    logger.info("=" * 70)
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0), 
        limits=HTTP_CLIENT_LIMITS, 
        follow_redirects=True
    )
    thread_pool = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE, thread_name_prefix="proxy_worker")
    logger.info(f"HTTP client and Thread pool ({THREAD_POOL_SIZE} workers) initialized")
    logger.info("=" * 70)

@app.on_event("shutdown")
async def shutdown_event():
    global http_client, thread_pool
    if http_client:
        await http_client.aclose()
    if thread_pool:
        thread_pool.shutdown(wait=True)
    logger.info("Shutdown complete")

def find_proxy_config(path: str, request_id: str = "") -> Optional[Tuple[str, dict]]:
    """
    查找匹配的代理配置

    增强安全性：
    1. 严格匹配白名单路径
    2. 防止路径混淆攻击
    """
    # 规范化路径
    normalized_path = path_security.sanitize_path(path)

    for prefix in sorted(PROXY_CONFIG.keys(), key=len, reverse=True):
        if normalized_path == prefix or normalized_path.startswith(prefix + '/'):
            logger.debug(f"[{request_id}] Proxy config matched: {prefix} for path: {normalized_path}")
            return prefix, PROXY_CONFIG[prefix]

    logger.debug(f"[{request_id}] No proxy config found for path: {normalized_path}")
    return None


def validate_required_headers(headers: dict, request_id: str) -> tuple[bool, str]:
    """
    校验请求头是否包含必需的值
    返回: (是否通过, 错误信息)
    """
    for header_name, expected_value in REQUIRED_HEADERS.items():
        actual_value = headers.get(header_name, '')
        if actual_value != expected_value:
            logger.warning(f"[{request_id}] Header validation failed: {header_name}")
            logger.warning(f"[{request_id}]    Expected: '{expected_value}'")
            logger.warning(f"[{request_id}]    Actual  : '{actual_value}'")
            return False, f"Invalid or missing header: {header_name}"

    logger.info(f"[{request_id}] Header validation passed")
    return True, ""

def build_target_url(path: str, prefix: str, config: dict) -> str:
    return config['target'] + config['rewrite'] + path[len(prefix):]

def build_target_url(path: str, prefix: str, config: dict, request_id: str = "") -> Tuple[str, bool, str]:
    """
    构建目标 URL

    Returns:
        (url, is_valid, error_message)
    """
    # 构建基础 URL
    remaining_path = path[len(prefix):]
    target_url = config['target'] + config['rewrite'] + remaining_path

    # 验证目标 URL 安全性
    is_valid, error = path_security.validate_url(target_url, request_id)

    return target_url, is_valid, error

def filter_headers(headers: dict) -> dict:
    filtered = {k: v for k, v in headers.items() if k.lower() not in EXCLUDED_HEADERS}
    filtered.pop('accept-encoding', None)
    return filtered

def mask_api_key(auth_header: str) -> str:
    if not auth_header or not auth_header.startswith("Bearer "):
        return "***"
    key = auth_header[7:]
    return f"Bearer {key[:8]}...{key[-4:]}" if len(key) > 12 else "***"

# --- MCP 核心逻辑 ---
async def call_exa_mcp(query: str, request_id: str) -> Dict[str, Any]:
    """调用 Exa MCP web_search_exa 工具"""
    url = MCP_CONFIG["exa"]["url"]
    
    # MCP JSON-RPC 请求格式
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {
                "query": query,
                "num_results": 5
            }
        }
    }
    
    logger.info(f"[{request_id}] Calling Exa MCP for query: {query}")
    try:
        # 增加必要的头信息，防止 406 错误
        # 根据 Exa MCP 报错：Client must accept both application/json and text/event-stream
        mcp_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }
        response = await http_client.post(url, json=payload, headers=mcp_headers, timeout=60.0)
        response.raise_for_status()
        
        # 调试日志：打印原始响应
        raw_text = response.text
        logger.info(f"[{request_id}] MCP Raw Response: {raw_text[:500]}...")
        
        try:
            result = response.json()
        except Exception as je:
            logger.error(f"[{request_id}] JSON Parse Error: {str(je)}, Raw: {raw_text}")
            # 如果是 SSE 格式，尝试提取 JSON 部分
            if "data:" in raw_text:
                for line in raw_text.splitlines():
                    if line.startswith("data:"):
                        try:
                            result = json.loads(line[5:].strip())
                            break
                        except: continue
            else:
                raise
        
        # 提取结果内容
        if "result" in result and "content" in result["result"]:
            # 通常 content 是一个列表，包含 text 字段
            contents = result["result"]["content"]
            search_text = ""
            for item in contents:
                if item.get("type") == "text":
                    search_text += item.get("text", "") + "\n\n"
            return {"raw": result, "text": search_text.strip()}
        else:
            logger.error(f"[{request_id}] Unexpected MCP response format: {result}")
            return {"raw": result, "text": "No results found or error in MCP response."}
            
    except Exception as e:
        logger.error(f"[{request_id}] MCP Call Error: {str(e)}")
        raise


async def get_mcp_context(query: str, request_id: str) -> Optional[str]:
    """
    获取 MCP 搜索结果作为上下文（带缓存和请求去重）
    
    返回:
        搜索结果文本，如果失败则返回 None
    """
    acquired = False
    try:
        # 尝试获取执行权或等待其他请求完成
        acquired, cached_result = await mcp_cache.acquire_or_wait(query)
        
        if not acquired:
            # 缓存命中或等待其他请求完成后获得结果
            logger.info(f"[{request_id}] MCP context from cache for: {query[:50]}")
            if cached_result and "text" in cached_result:
                mcp_text = cached_result["text"]
                logger.info(f"[{request_id}] " + "=" * 50)
                logger.info(f"[{request_id}] MCP CACHED RESULT (length: {len(mcp_text)} chars):")
                logger.info(f"[{request_id}] {mcp_text[:500]}...")
                logger.info(f"[{request_id}] " + "=" * 50)
                return mcp_text
            return None
        
        # 获得执行权，调用 MCP
        logger.info(f"[{request_id}] Fetching MCP context for: {query[:50]}")
        mcp_res = await call_exa_mcp(query, request_id)
        
        # 打印 MCP 返回结果
        mcp_text = mcp_res["text"]
        logger.info(f"[{request_id}] " + "=" * 50)
        logger.info(f"[{request_id}] MCP SEARCH RESULT (length: {len(mcp_text)} chars):")
        logger.info(f"[{request_id}] {mcp_text[:1000]}...")
        logger.info(f"[{request_id}] " + "=" * 50)
        
        # 存入缓存（存储 text 字段）
        mcp_cache.set(query, {"text": mcp_text})
        
        return mcp_text
        
    except Exception as e:
        logger.error(f"[{request_id}] MCP Context Error: {str(e)}")
        return None
    
    finally:
        if acquired and query:
            await mcp_cache.release(query)


def extract_user_query(body_json: dict) -> Optional[str]:
    """
    从请求体中提取用户的查询内容
    支持 OpenAI 格式的 messages 数组
    """
    messages = body_json.get("messages", [])
    if not messages:
        return None
    
    # 获取最后一条用户消息
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # 处理多模态消息
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return item.get("text", "")
    return None


def inject_mcp_context(body_json: dict, mcp_context: str, request_id: str = "") -> dict:
    """
    将 MCP 搜索结果注入到请求体中
    作为 system 消息添加到 messages 开头
    """
    messages = body_json.get("messages", [])
    
    # 构建 MCP 上下文消息
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mcp_system_content = f"""[MCP Search Context - Retrieved at {current_time}]
The following is real-time search information that may help answer the user's question:

{mcp_context}

[End of Search Context]
Please use this information to provide an accurate and up-to-date response."""

    mcp_system_message = {
        "role": "system",
        "content": mcp_system_content
    }
    
    # 打印注入的提示词
    logger.info(f"[{request_id}] " + "-" * 50)
    logger.info(f"[{request_id}] INJECTED MCP SYSTEM PROMPT:")
    logger.info(f"[{request_id}] {mcp_system_content[:800]}...")
    logger.info(f"[{request_id}] " + "-" * 50)
    
    # 检查是否已有 system 消息
    new_messages = []
    has_system = False
    for msg in messages:
        if msg.get("role") == "system":
            has_system = True
            # 将 MCP 上下文追加到现有 system 消息
            original_content = msg.get("content", "")
            msg = msg.copy()
            msg["content"] = f"{original_content}\n\n{mcp_system_content}"
            logger.info(f"[{request_id}] MCP context appended to existing system message")
        new_messages.append(msg)
    
    if not has_system:
        # 在开头插入 MCP system 消息
        new_messages.insert(0, mcp_system_message)
        logger.info(f"[{request_id}] MCP context inserted as new system message")
    
    body_json = body_json.copy()
    body_json["messages"] = new_messages
    return body_json

def format_to_openai(query: str, search_result: str) -> Dict[str, Any]:
    """将搜索结果包装成 OpenAI chat.completions 格式"""
    content = f"Based on the search results for '{query}':\n\n{search_result}"
    
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "exa-search-mcp",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": len(query) // 4,
            "completion_tokens": len(content) // 4,
            "total_tokens": (len(query) + len(content)) // 4
        }
    }

# --- 路由处理 ---

@app.post("/api/mcp/exa")
async def mcp_exa_handler(request: Request):
    """
    MCP Exa 搜索接口
    
    并发安全机制：
    1. 使用 acquire_or_wait 获取执行权或等待其他请求完成
    2. 只有获得执行权的请求才会真正调用 MCP API
    3. 其他并发请求会等待第一个请求完成后直接获取缓存结果
    4. 使用 try-finally 确保无论成功失败都会释放锁
    """
    request_id = get_request_id()
    query = ""
    acquired = False
    
    try:
        body = await request.json()
        # 兼容 OpenAI 格式提取 query，或者直接传 query
        if "messages" in body:
            messages = body["messages"]
            if messages:
                query = messages[-1].get("content", "")
        else:
            query = body.get("query", "")

        if not query:
            return JSONResponse(status_code=400, content={"error": "Query is required"})

        # 1. 尝试获取执行权或等待其他请求完成
        #    - 如果缓存命中，直接返回 (False, cached_data)
        #    - 如果获得执行权，返回 (True, None)
        #    - 如果有其他请求正在处理，会等待其完成后返回缓存
        acquired, cached_result = await mcp_cache.acquire_or_wait(query)
        
        if not acquired:
            # 缓存命中或等待其他请求完成后获得结果
            logger.info(f"[{request_id}] Returning cached/waited result for: {query[:50]}")
            return cached_result

        # 2. 获得执行权，调用 MCP
        logger.info(f"[{request_id}] Acquired execution right, calling MCP for: {query[:50]}")
        mcp_res = await call_exa_mcp(query, request_id)
        
        # 3. 格式化为 OpenAI 格式
        openai_response = format_to_openai(query, mcp_res["text"])
        
        # 4. 存入缓存
        mcp_cache.set(query, openai_response)
        
        return openai_response

    except Exception as e:
        logger.error(f"[{request_id}] MCP Handler Error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    
    finally:
        # 5. 无论成功失败，都要释放执行权，唤醒等待的请求
        if acquired and query:
            await mcp_cache.release(query)

# --- 原有代理逻辑 ---

async def stream_response(response: httpx.Response, request_id: str, start_time: float) -> AsyncGenerator[bytes, None]:
    """流式响应生成器 - 直接透传原始字节流"""
    chunk_count = 0
    total_bytes = 0
    last_log_time = start_time
    last_chunk_time = start_time
    
    try:
        async for chunk in response.aiter_bytes():
            chunk_count += 1
            chunk_size = len(chunk)
            total_bytes += chunk_size
            current_time = time.time()
            elapsed = current_time - last_chunk_time
            
            # 每个 chunk 都记录日志
            if chunk_count <= 5 or chunk_count % 10 == 0 or elapsed > 0.5:
                logger.info(f"[{request_id}] STREAM chunk#{chunk_count}: {chunk_size} bytes, elapsed: {elapsed:.3f}s, total: {total_bytes}")
                if chunk_count <= 3:
                    logger.info(f"[{request_id}] STREAM chunk#{chunk_count} preview: {chunk[:100]}")
            
            last_chunk_time = current_time
            
            # 直接yield原始字节，不做任何处理
            yield chunk
    except Exception as e:
        logger.error(f"[{request_id}] STREAM: Error during streaming: {str(e)}")
        raise
    finally:
        await response.aclose()
        logger.info(f"[{request_id}] STREAM COMPLETE: {chunk_count} chunks, {total_bytes} bytes, {time.time() - start_time:.3f}s")

def get_client_ip(request: Request) -> str:
    """
    获取客户端真实 IP 地址

    支持代理头：X-Forwarded-For, X-Real-IP
    """
    # 检查 X-Forwarded-For 头（可能包含多个 IP，取第一个）
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # 格式: client, proxy1, proxy2
        return forwarded_for.split(",")[0].strip()

    # 检查 X-Real-IP 头
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    # 使用直接连接的客户端 IP
    if request.client:
        return request.client.host

    return "unknown"


def determine_security_event_type(error_message: str) -> str:
    """
    根据错误信息确定安全事件类型
    """
    error_lower = error_message.lower()

    if "traversal" in error_lower or ".." in error_lower:
        return "path_traversal"
    elif "internal" in error_lower or "localhost" in error_lower or "ssrf" in error_lower or "metadata" in error_lower:
        return "ssrf"
    elif "sensitive" in error_lower:
        return "sensitive_path"
    elif "dangerous" in error_lower or "pattern" in error_lower:
        return "dangerous_pattern"
    else:
        return "invalid_path"


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_handler(request: Request, path: str):
    """
    代理处理器 - 带增强路径安全防护、速率限制和三级威胁防护

    安全措施：
    1. 三级威胁防护 - 观察名单 -> 预警名单 -> 黑名单
    2. 速率限制 - 防止 DDoS 和暴力攻击
    3. 路径验证 - 检查危险模式和敏感路径
    4. URL 验证 - 防止 SSRF 和 URL 注入
    5. 白名单匹配 - 只允许配置的代理路径
    6. 请求日志 - 记录所有安全相关事件
    7. 安全事件统计 - 记录和追踪安全事件
    8. 自动封禁 - 对恶意 IP 自动封禁
    """
    request_id = get_request_id()
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    # 排除已定义的特定路由（这些路由不受速率限制）
    # 注意：这些路由通常由 FastAPI 优先匹配到特定的 handler，
    # 但如果请求方法不匹配（例如对 /health 发送 POST 请求），则会回退到此通配符路由。
    excluded_paths = ["api/mcp/exa", "health", "stats", "security/stats", "rate-limit/stats", "rate-limit/banned", "threat/stats", "threat/ip", ""]
    if path in excluded_paths:
        return JSONResponse(status_code=404, content={"error": "Not Found", "request_id": request_id})

    full_path = "/" + path
    
    # === 安全检查 -1: 三级威胁防护检查（最高优先级） ===
    if threat_engine is not None:
        threat_allowed, threat_reason, threat_level = threat_engine.check_request(
            ip=client_ip,
            path=full_path,
            method=request.method,
            user_agent=user_agent,
            request_id=request_id
        )
        
        if not threat_allowed:
            # IP 在黑名单中，直接拒绝
            logger.warning(f"[{request_id}] THREAT BLOCKED: {threat_reason} | IP: {client_ip} | Path: {full_path[:100]}")
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Access denied",
                    "detail": threat_reason,
                    "threat_level": threat_level.name if threat_level else "UNKNOWN",
                    "request_id": request_id
                },
                headers={
                    "X-Threat-Level": threat_level.name if threat_level else "UNKNOWN"
                }
            )
        
        # 如果检测到威胁但未达到黑名单级别，记录但允许继续
        if threat_level and threat_level != ThreatLevel.NONE:
            logger.info(f"[{request_id}] THREAT DETECTED (allowed): level={threat_level.name} | IP: {client_ip}")
    
    # 校验必需的 headers
    headers_dict = {k.lower(): v for k, v in request.headers.items()}
    logger.info(f"[{request_id}] Request origin header: '{headers_dict.get('origin', '')}'")
    is_valid, error_msg = validate_required_headers(headers_dict, request_id)
    if not is_valid:
        logger.warning(f"[{request_id}] ACCESS DENIED for path: {full_path}")
        
        # 记录到三级威胁防护系统
        if threat_engine is not None and ViolationType is not None:
            is_blocked, current_level = threat_engine.record_violation(
                ip=client_ip,
                path=full_path,
                method=request.method,
                user_agent=user_agent,
                violation_type=ViolationType.ABNORMAL_PATH,
                detail=f"Header validation failed: {error_msg}",
                request_id=request_id
            )
            logger.warning(f"[{request_id}] THREAT RECORDED: IP {client_ip} | Level: {current_level.name} | Blocked: {is_blocked}")
            
            # 如果已被加入黑名单，返回更严重的错误
            if is_blocked:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Access denied",
                        "detail": "IP is blacklisted due to repeated violations",
                        "threat_level": current_level.name,
                        "request_id": request_id
                    },
                    headers={
                        "X-Threat-Level": current_level.name
                    }
                )
        
        return JSONResponse(
            status_code=403,
            content={"error": f"Access denied: {error_msg}"}
        )

    # 查找代理配置
    result = find_proxy_config(full_path)
    # === 安全检查 0: 速率限制检查 ===
    rate_allowed, rate_error = rate_limiter.is_allowed(client_ip, request_id)
    if not rate_allowed:
        # 记录速率限制事件
        security_stats.record_event(
            event_type="rate_limit",
            request_id=request_id,
            path=full_path,
            detail=rate_error,
            client_ip=client_ip
        )

        logger.warning(f"[{request_id}] RATE LIMITED: {rate_error} | Path: {full_path[:100]} | IP: {client_ip}")
        return JSONResponse(
            status_code=429,  # Too Many Requests
            content={
                "error": "Too many requests",
                "detail": rate_error,
                "request_id": request_id
            },
            headers={
                "Retry-After": "60",  # 建议 60 秒后重试
                "X-RateLimit-Limit": str(RateLimiterConfig.MAX_REQUESTS_PER_WINDOW),
                "X-RateLimit-Window": str(RateLimiterConfig.WINDOW_SIZE)
            }
        )

    # === 安全检查 1: 验证请求路径 ===
    path_valid, path_error = path_security.validate_path(full_path, request_id)
    if not path_valid:
        # 记录安全事件
        event_type = determine_security_event_type(path_error)
        security_stats.record_event(
            event_type=event_type,
            request_id=request_id,
            path=full_path,
            detail=path_error,
            client_ip=client_ip
        )
        
        # 记录到三级威胁防护系统
        if threat_engine is not None and ViolationType is not None:
            # 根据错误类型选择违规类型
            violation_type = ViolationType.ABNORMAL_PATH
            if "traversal" in path_error.lower():
                violation_type = ViolationType.PATH_TRAVERSAL
            elif "sensitive" in path_error.lower():
                violation_type = ViolationType.SENSITIVE_ACCESS
            elif "dangerous" in path_error.lower() or "pattern" in path_error.lower():
                violation_type = ViolationType.INJECTION_ATTEMPT
            
            is_blocked, current_level = threat_engine.record_violation(
                ip=client_ip,
                path=full_path,
                method=request.method,
                user_agent=user_agent,
                violation_type=violation_type,
                detail=f"Path validation failed: {path_error}",
                request_id=request_id
            )
            logger.warning(f"[{request_id}] THREAT RECORDED: IP {client_ip} | Level: {current_level.name} | Type: {violation_type.value}")

        logger.warning(f"[{request_id}] SECURITY BLOCKED: {path_error} | Path: {full_path[:100]} | IP: {client_ip}")
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid request path",
                "detail": path_error,
                "request_id": request_id
            }
        )

    # === 安全检查 2: 查找代理配置（白名单验证） ===
    result = find_proxy_config(full_path, request_id)
    if result is None:
        # 记录到三级威胁防护系统（访问未配置的路径可能是扫描行为）
        if threat_engine is not None and ViolationType is not None:
            is_blocked, current_level = threat_engine.record_violation(
                ip=client_ip,
                path=full_path,
                method=request.method,
                user_agent=user_agent,
                violation_type=ViolationType.ABNORMAL_PATH,
                detail=f"Access to unconfigured path: {full_path[:200]}",
                request_id=request_id
            )
            logger.warning(f"[{request_id}] THREAT RECORDED: IP {client_ip} | Level: {current_level.name} | Path not in whitelist")
            
            # 如果已被加入黑名单，返回 403
            if is_blocked:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Access denied",
                        "detail": "IP is blacklisted due to repeated violations",
                        "threat_level": current_level.name,
                        "request_id": request_id
                    },
                    headers={
                        "X-Threat-Level": current_level.name
                    }
                )
        
        logger.info(f"[{request_id}] No proxy config for path: {full_path[:100]} | IP: {client_ip}")
        return JSONResponse(
            status_code=404,
            content={
                "error": "No proxy configuration found",
                "request_id": request_id
            }
        )
    
    prefix, config = result

    # === 安全检查 3: 构建并验证目标 URL ===
    target_url, url_valid, url_error = build_target_url(full_path, prefix, config, request_id)
    if not url_valid:
        # 记录安全事件
        event_type = determine_security_event_type(url_error)
        security_stats.record_event(
            event_type=event_type,
            request_id=request_id,
            path=full_path,
            detail=f"Target URL validation failed: {url_error}",
            client_ip=client_ip
        )
        
        # 记录到三级威胁防护系统
        if threat_engine is not None and ViolationType is not None:
            # 根据错误类型选择违规类型
            violation_type = ViolationType.ABNORMAL_PATH
            if "internal" in url_error.lower() or "localhost" in url_error.lower() or "metadata" in url_error.lower():
                violation_type = ViolationType.INJECTION_ATTEMPT  # SSRF 尝试
            elif "traversal" in url_error.lower():
                violation_type = ViolationType.PATH_TRAVERSAL
            
            is_blocked, current_level = threat_engine.record_violation(
                ip=client_ip,
                path=full_path,
                method=request.method,
                user_agent=user_agent,
                violation_type=violation_type,
                detail=f"URL validation failed: {url_error}",
                request_id=request_id
            )
            logger.warning(f"[{request_id}] THREAT RECORDED: IP {client_ip} | Level: {current_level.name} | Type: {violation_type.value}")

        logger.warning(f"[{request_id}] SECURITY BLOCKED: {url_error} | Target: {target_url[:100]} | IP: {client_ip}")
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid target URL",
                "detail": url_error,
                "request_id": request_id
            }
        )
    
    start_time = time.time()
    method = request.method
    headers = filter_headers(dict(request.headers))
    
    # 处理查询参数
    if request.query_params:
        target_url = f"{target_url}?{request.query_params}"
    
    body = await request.body()
    
    logger.info(f"[{request_id}] PROXY: {method} {full_path} -> {target_url}")

    try:
        # 检查是否是聊天请求，需要注入 MCP 上下文
        body_json = None
        is_chat_request = False
        is_stream = False
        
        if body and method == "POST":
            try:
                body_json = json.loads(body)
                is_stream = body_json.get('stream', False)
                # 检查是否是聊天完成请求（包含 messages 字段）
                if "messages" in body_json and full_path.endswith("/chat/completions"):
                    is_chat_request = True
            except:
                pass
        
        # 如果是聊天请求，检查是否需要联网搜索（web_search: true）
        if is_chat_request and body_json:
            # 检查 web_search 参数
            web_search_enabled = body_json.get('web_search', False)
            
            if web_search_enabled:
                user_query = extract_user_query(body_json)
                if user_query:
                    logger.info(f"[{request_id}] " + "=" * 60)
                    logger.info(f"[{request_id}] MCP INTEGRATION START (web_search=true)")
                    logger.info(f"[{request_id}] " + "=" * 60)
                    logger.info(f"[{request_id}] User Query: {user_query}")
                    logger.info(f"[{request_id}] Fetching MCP context...")
                    
                    mcp_context = await get_mcp_context(user_query, request_id)
                    
                    if mcp_context:
                        logger.info(f"[{request_id}] MCP: Context retrieved ({len(mcp_context)} chars), injecting into request")
                        body_json = inject_mcp_context(body_json, mcp_context, request_id)
                        # 移除 web_search 参数，避免传递给后端 API（后端可能不支持此参数）
                        body_json.pop('web_search', None)
                        # 重新序列化请求体
                        body = json.dumps(body_json).encode('utf-8')
                        logger.info(f"[{request_id}] MCP: Request body updated, new size: {len(body)} bytes")
                    else:
                        logger.warning(f"[{request_id}] MCP: No context retrieved, proceeding without MCP")
                        # 移除 web_search 参数
                        body_json.pop('web_search', None)
                        body = json.dumps(body_json).encode('utf-8')
                    
                    logger.info(f"[{request_id}] " + "=" * 60)
                    logger.info(f"[{request_id}] MCP INTEGRATION END")
                    logger.info(f"[{request_id}] " + "=" * 60)
            else:
                logger.info(f"[{request_id}] web_search=false, skipping MCP integration")
                # 移除 web_search 参数（如果存在）
                if 'web_search' in body_json:
                    body_json.pop('web_search', None)
                    body = json.dumps(body_json).encode('utf-8')

        if is_stream:
            logger.info(f"[{request_id}] STREAM: Starting stream request to {target_url}")
            
            # 清理请求头，移除可能影响后端流式响应的头
            stream_headers = {k: v for k, v in headers.items()
                            if k.lower() not in ['x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host', 'x-real-ip']}
            
            logger.info(f"[{request_id}] STREAM: Request headers: {dict(stream_headers)}")
            
            req = http_client.build_request(method, target_url, headers=stream_headers, content=body)
            upstream_response = await http_client.send(req, stream=True)
            
            logger.info(f"[{request_id}] STREAM: Upstream response status: {upstream_response.status_code}")
            logger.info(f"[{request_id}] STREAM: Upstream response headers: {dict(upstream_response.headers)}")
            
            # 原始 SSE 响应头
            original_headers = dict(upstream_response.headers)
            
            response_headers = {
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
                'Content-Type': 'text/event-stream',  # 强制使用 SSE content-type
            }
            
            # 透传后端的关键响应头
            for key in ['x-request-id', 'x-ratelimit-limit', 'x-ratelimit-remaining', 'x-model-id']:
                if key.lower() in [h.lower() for h in upstream_response.headers]:
                    # 找到实际的头名
                    for h in upstream_response.headers:
                        if h.lower() == key.lower():
                            response_headers[h] = upstream_response.headers[h]
                            break
            
            logger.info(f"[{request_id}] STREAM: Response headers: {response_headers}")
            
            return StreamingResponse(
                stream_response(upstream_response, request_id, start_time),
                status_code=upstream_response.status_code,
                media_type='text/event-stream',  # 强制使用 SSE
                headers=response_headers
            )
        else:
            response = await http_client.request(method=method, url=target_url, headers=headers, content=body)
            response_headers = dict(response.headers)
            for h in ['content-length', 'transfer-encoding', 'content-encoding']:
                response_headers.pop(h, None)
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response_headers.get('content-type')
            )

    except Exception as e:
        logger.error(f"[{request_id}] Proxy Error: {str(e)}")
        return JSONResponse(status_code=502, content={"error": f"Proxy error: {str(e)}"})

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/stats")
async def stats():
    return {
        "request_count": request_counter,
        "cache_size": len(mcp_cache.cache),
        "process_id": os.getpid(),
        "security": {
            "blocked_requests": security_stats.blocked_count,
            "path_traversal_attempts": security_stats.path_traversal_count,
            "ssrf_attempts": security_stats.ssrf_attempt_count,
            "invalid_paths": security_stats.invalid_path_count,
            "rate_limited": security_stats.rate_limit_count
        },
        "rate_limit": rate_limiter.get_stats()
    }

@app.get("/security/stats")
async def security_statistics():
    """
    获取详细的安全统计信息
    """
    return {
        "timestamp": datetime.now().isoformat(),
        "statistics": {
            "total_blocked": security_stats.blocked_count,
            "by_type": {
                "path_traversal": security_stats.path_traversal_count,
                "ssrf_attempts": security_stats.ssrf_attempt_count,
                "invalid_paths": security_stats.invalid_path_count,
                "sensitive_paths": security_stats.sensitive_path_count,
                "dangerous_patterns": security_stats.dangerous_pattern_count,
                "rate_limited": security_stats.rate_limit_count
            }
        },
        "rate_limit": rate_limiter.get_stats(),
        "recent_events": security_stats.get_recent_events(limit=20),
        "config": {
            "max_path_length": PathSecurityConfig.MAX_PATH_LENGTH,
            "max_url_length": PathSecurityConfig.MAX_URL_LENGTH,
            "allowed_schemes": PathSecurityConfig.ALLOWED_SCHEMES
        }
    }

@app.get("/rate-limit/stats")
async def rate_limit_statistics():
    """
    获取速率限制统计信息
    """
    return {
        "timestamp": datetime.now().isoformat(),
        "statistics": rate_limiter.get_stats(),
        "config": {
            "window_size": RateLimiterConfig.WINDOW_SIZE,
            "max_requests_per_window": RateLimiterConfig.MAX_REQUESTS_PER_WINDOW,
            "ban_threshold_rps": RateLimiterConfig.BAN_THRESHOLD_RPS,
            "ban_duration": RateLimiterConfig.BAN_DURATION,
            "permanent_ban_threshold": RateLimiterConfig.PERMANENT_BAN_THRESHOLD
        }
    }


@app.get("/rate-limit/banned")
async def get_banned_ips():
    """
    获取被封禁的 IP 列表
    """
    return {
        "timestamp": datetime.now().isoformat(),
        "banned_ips": rate_limiter.get_banned_ips()
    }


@app.post("/rate-limit/unban/{ip}")
async def unban_ip(ip: str):
    """
    手动解封 IP

    Args:
        ip: 要解封的 IP 地址
    """
    success = rate_limiter.unban_ip(ip)
    if success:
        return {"status": "success", "message": f"IP {ip} has been unbanned"}
    else:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"IP {ip} is not in the ban list"}
        )


# ============================================================
# 三级威胁防护管理接口
# ============================================================

@app.get("/threat/stats")
async def threat_stats():
    """获取三级威胁防护统计信息"""
    if threat_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Threat protection system not available"}
        )
    
    return {
        "timestamp": datetime.now().isoformat(),
        "enabled": True,
        "statistics": threat_engine.get_stats(),
        "config": {
            "watch_threshold": threat_engine.config.WATCH_THRESHOLD,
            "warning_threshold": threat_engine.config.WARNING_THRESHOLD,
            "blacklist_threshold": threat_engine.config.BLACKLIST_THRESHOLD,
            "blacklist_duration_days": threat_engine.config.BLACKLIST_DURATION // 86400,
            "email_enabled": threat_engine.config.EMAIL_ENABLED
        }
    }


@app.get("/threat/ip/{ip}")
async def threat_ip_status(ip: str):
    """查询 IP 的威胁状态"""
    if threat_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Threat protection system not available"}
        )
    
    status = threat_engine.get_ip_status(ip)
    if status is None:
        return {"ip": ip, "status": "not_tracked", "threat_level": "NONE"}
    return status


@app.get("/threat/list/{level}")
async def threat_list_by_level(level: str):
    """获取指定威胁级别的 IP 列表"""
    if threat_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Threat protection system not available"}
        )
    
    try:
        threat_level = ThreatLevel[level.upper()]
    except (KeyError, AttributeError):
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid level: {level}. Valid levels: WATCH, WARNING, BLACKLIST"}
        )
    
    return {
        "level": level.upper(),
        "ips": threat_engine.get_all_ips_by_level(threat_level)
    }


@app.post("/threat/blacklist/{ip}")
async def threat_blacklist_ip(ip: str, reason: str = "Manual blacklist", permanent: bool = False):
    """手动将 IP 加入黑名单"""
    if threat_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Threat protection system not available"}
        )
    
    success = threat_engine.manual_blacklist(ip, reason, permanent)
    return {
        "success": success,
        "ip": ip,
        "action": "blacklisted",
        "permanent": permanent,
        "reason": reason
    }


@app.post("/threat/unblock/{ip}")
async def threat_unblock_ip(ip: str, reason: str = "Manual unblock"):
    """手动解除 IP 封禁"""
    if threat_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Threat protection system not available"}
        )
    
    success = threat_engine.manual_unblock(ip, reason)
    if not success:
        return JSONResponse(
            status_code=404,
            content={"error": f"IP {ip} not found in threat list"}
        )
    return {
        "success": success,
        "ip": ip,
        "action": "unblocked",
        "reason": reason
    }


@app.get("/threat/logs")
async def threat_logs(limit: int = 50, ip: str = None):
    """获取威胁防护操作日志"""
    if threat_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Threat protection system not available"}
        )
    
    return {
        "logs": threat_engine.get_recent_operations(limit=limit, ip_filter=ip)
    }


@app.post("/threat/cleanup")
async def threat_cleanup():
    """清理过期的威胁记录"""
    if threat_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Threat protection system not available"}
        )
    
    cleaned = threat_engine.cleanup_expired_records()
    return {
        "cleaned": cleaned,
        "message": f"Cleaned {cleaned} expired records"
    }


@app.get("/")
async def root():
    return {
        "service": "OpenAI API Proxy Gateway with MCP + Threat Protection",
        "status": "running",
        "security": "enhanced",
        "features": [
            "Three-level threat protection (Watch -> Warning -> Blacklist)",
            "Path traversal protection",
            "SSRF prevention",
            "URL injection protection",
            "Sensitive path blocking",
            "Request validation",
            "Rate limiting with auto-ban",
            "DDoS protection",
            "Email alerts for warnings"
        ],
        "threat_protection": {
            "enabled": threat_engine is not None,
            "levels": ["WATCH (1st violation)", "WARNING (3+ violations)", "BLACKLIST (5+ violations)"]
        },
        "rate_limit": {
            "max_requests_per_minute": RateLimiterConfig.MAX_REQUESTS_PER_WINDOW,
            "ban_threshold_rps": RateLimiterConfig.BAN_THRESHOLD_RPS
        }
    }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OpenAI API Proxy Gateway with MCP")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes")
    args = parser.parse_args()
    
    uvicorn.run("proxy_gateway_mcp:app", host=args.host, port=args.port, workers=args.workers, access_log=True)
