#!/usr/bin/env python3
"""
三级威胁防护系统 (Three-Level Threat Protection System)

完全独立、解耦设计：
- 不依赖任何特定后端或框架
- 所有数据存储在本地 JSON 日志文件中
- 可作为中间件集成到任何 Python Web 应用
- 支持 ASGI/WSGI 中间件模式
- 支持独立 API 服务模式

防护级别：
1. 观察名单 (Watch List) - 首次可疑行为，记录并监控
2. 预警名单 (Warning List) - 多次可疑行为，发送邮件预警
3. 黑名单 (Blacklist) - 持续恶意行为，直接封禁

触发条件：
- 访问离奇路径（不在白名单中的异常路径）
- 访问敏感接口
- 路径遍历攻击
- 注入攻击尝试
- 高频异常请求

使用方式：
1. 作为独立模块导入使用
2. 作为 FastAPI/Starlette 中间件
3. 作为独立 API 服务运行

Author: Security Module
Version: 1.0.0
"""

import json
import os
import threading
import time
import logging
import smtplib
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Set
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ThreatProtection")


# ============================================================
# 枚举和数据类定义
# ============================================================

class ThreatLevel(Enum):
    """威胁级别枚举"""
    NONE = 0        # 无威胁
    WATCH = 1       # 观察名单
    WARNING = 2     # 预警名单
    BLACKLIST = 3   # 黑名单
    
    def __str__(self):
        return self.name


class ViolationType(Enum):
    """违规类型枚举"""
    ABNORMAL_PATH = "abnormal_path"           # 离奇路径访问
    PATH_TRAVERSAL = "path_traversal"         # 路径遍历攻击
    INJECTION_ATTEMPT = "injection_attempt"   # 注入攻击
    SENSITIVE_ACCESS = "sensitive_access"     # 敏感路径访问
    HIGH_FREQUENCY = "high_frequency"         # 高频请求
    SCANNER_DETECTED = "scanner_detected"     # 扫描器检测
    MALFORMED_REQUEST = "malformed_request"   # 畸形请求
    UNKNOWN = "unknown"                       # 未知类型


@dataclass
class ViolationRecord:
    """单次违规记录"""
    timestamp: str          # ISO 格式时间
    violation_type: str     # ViolationType 的值
    path: str               # 请求路径
    method: str             # HTTP 方法
    user_agent: str         # User-Agent
    detail: str             # 详细描述
    request_id: str         # 请求 ID
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ViolationRecord':
        return cls(**data)


@dataclass
class IPThreatRecord:
    """IP 威胁记录"""
    ip: str
    level: int                              # ThreatLevel 的值
    first_seen: str                         # 首次发现时间
    last_seen: str                          # 最后活动时间
    violation_count: int                    # 总违规次数
    violations: List[Dict[str, Any]]        # 违规详情列表
    email_sent_count: int                   # 已发送邮件次数
    last_email_sent: Optional[str]          # 上次发送邮件时间
    blacklist_reason: str                   # 黑名单原因
    auto_unblock_time: Optional[str]        # 自动解封时间（可选）
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPThreatRecord':
        return cls(**data)
    
    def get_level_enum(self) -> ThreatLevel:
        return ThreatLevel(self.level)


# ============================================================
# 配置类
# ============================================================

class ThreatProtectionConfig:
    """
    三级防护配置
    
    所有配置项都可以通过环境变量覆盖
    """
    
    def __init__(self):
        # === 存储配置 ===
        self.LOG_DIR: str = os.getenv("THREAT_LOG_DIR", "security_logs")
        self.IP_LIST_FILE: str = os.getenv("THREAT_IP_FILE", "ip_threat_list.json")
        self.OPERATION_LOG_FILE: str = os.getenv("THREAT_OP_LOG", "security_operations.jsonl")
        
        # === 级别升级阈值 ===
        self.WATCH_THRESHOLD: int = int(os.getenv("THREAT_WATCH_THRESHOLD", "1"))
        self.WARNING_THRESHOLD: int = int(os.getenv("THREAT_WARNING_THRESHOLD", "3"))
        self.BLACKLIST_THRESHOLD: int = int(os.getenv("THREAT_BLACKLIST_THRESHOLD", "5"))
        
        # === 时间窗口配置（秒） ===
        self.VIOLATION_WINDOW: int = int(os.getenv("THREAT_VIOLATION_WINDOW", "86400"))  # 24小时
        self.BLACKLIST_DURATION: int = int(os.getenv("THREAT_BLACKLIST_DURATION", "604800"))  # 7天
        self.WARNING_EMAIL_COOLDOWN: int = int(os.getenv("THREAT_EMAIL_COOLDOWN", "3600"))  # 1小时
        
        # === 邮件配置 ===
        self.EMAIL_ENABLED: bool = os.getenv("THREAT_EMAIL_ENABLED", "false").lower() == "true"
        self.SMTP_HOST: str = os.getenv("THREAT_SMTP_HOST", "smtp.gmail.com")
        self.SMTP_PORT: int = int(os.getenv("THREAT_SMTP_PORT", "587"))
        self.SMTP_USER: str = os.getenv("THREAT_SMTP_USER", "")
        self.SMTP_PASSWORD: str = os.getenv("THREAT_SMTP_PASSWORD", "")
        self.ALERT_EMAIL_TO: List[str] = [e.strip() for e in os.getenv("THREAT_ALERT_EMAIL_TO", "").split(",") if e.strip()]
        self.ALERT_EMAIL_FROM: str = os.getenv("THREAT_ALERT_EMAIL_FROM", "security@example.com")
        
        # === 路径检测配置 ===
        self.WHITELIST_PATHS: Set[str] = set()  # 白名单路径前缀
        self.ABNORMAL_PATH_PATTERNS: List[str] = [
            # 常见扫描器路径
            r'^/\.env',
            r'^/\.git',
            r'^/\.svn',
            r'^/\.htaccess',
            r'^/\.htpasswd',
            r'^/wp-admin',
            r'^/wp-content',
            r'^/wp-includes',
            r'^/wp-login',
            r'^/phpmyadmin',
            r'^/phpMyAdmin',
            r'^/admin\.php',
            r'^/administrator',
            r'^/backup',
            r'^/config\.php',
            r'^/database',
            r'^/db\.php',
            r'^/debug',
            r'^/dump',
            r'^/install',
            r'^/setup',
            r'^/shell',
            r'^/test\.php',
            r'^/upload\.php',
            r'^/xmlrpc\.php',
            r'^/cgi-bin',
            r'^/scripts',
            r'^/manager',
            r'^/console',
            r'^/solr',
            r'^/jenkins',
            r'^/actuator',
            r'^/api/v1/pods',  # K8s API
            r'^/server-status',
            r'^/server-info',
            r'^/\.aws',
            r'^/\.docker',
            r'^/etc/passwd',
            r'^/etc/shadow',
            r'^/proc/',
            r'^/var/log',
            r'^/windows/system32',
            # Cisco VPN/AnyConnect 扫描路径
            r'^/\+CSCOE\+',      # Cisco SSL VPN
            r'^/\+CSCOL\+',      # Cisco SSL VPN
            r'^/\+CSCOT\+',      # Cisco SSL VPN
            r'^/\+CSCOW\+',      # Cisco SSL VPN
            r'^/CSCOE',
            r'^/webvpn',
            r'^/dana-na',        # Juniper/Pulse VPN
            r'^/remote',
            r'^/vpn',
            r'^/sslvpn',
            # 其他常见扫描路径
            r'^/favicon\.ico$',  # 单独请求 favicon 可能是扫描
            r'^/robots\.txt$',   # 单独请求 robots.txt 可能是扫描
            r'^/sitemap\.xml$',
            r'^/crossdomain\.xml$',
            r'^/clientaccesspolicy\.xml$',
        ]
        
        # 危险模式（路径遍历、注入等）
        self.DANGEROUS_PATTERNS: List[str] = [
            r'\.\./',           # 路径遍历
            r'\.\.\\',          # Windows 路径遍历
            r'%2e%2e',          # URL 编码的 ..
            r'%252e%252e',      # 双重编码
            r'%00',             # Null 字节
            r'\x00',            # Null 字节
            r'<script',         # XSS
            r'javascript:',     # XSS
            r'onerror=',        # XSS
            r'onload=',         # XSS
            r"'.*or.*'",        # SQL 注入
            r'".*or.*"',        # SQL 注入
            r'union.*select',   # SQL 注入
            r'select.*from',    # SQL 注入
            r'insert.*into',    # SQL 注入
            r'drop.*table',     # SQL 注入
            r'exec\s*\(',       # 命令注入
            r'system\s*\(',     # 命令注入
            r'\$\{.*\}',        # 模板注入
            r'{{.*}}',          # 模板注入
        ]
        
        # 扫描器 User-Agent 特征
        self.SCANNER_USER_AGENTS: List[str] = [
            r'sqlmap',
            r'nikto',
            r'nmap',
            r'masscan',
            r'zgrab',
            r'gobuster',
            r'dirbuster',
            r'wfuzz',
            r'burp',
            r'acunetix',
            r'nessus',
            r'openvas',
            r'w3af',
            r'arachni',
            r'skipfish',
            r'whatweb',
            r'wpscan',
            r'joomscan',
        ]
        
        # 编译正则表达式
        self._compile_patterns()
    
    def _compile_patterns(self):
        """预编译正则表达式以提高性能"""
        self._abnormal_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.ABNORMAL_PATH_PATTERNS
        ]
        self._dangerous_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.DANGEROUS_PATTERNS
        ]
        self._scanner_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.SCANNER_USER_AGENTS
        ]
    
    def add_whitelist_path(self, path_prefix: str):
        """添加白名单路径前缀"""
        self.WHITELIST_PATHS.add(path_prefix)
    
    def remove_whitelist_path(self, path_prefix: str):
        """移除白名单路径前缀"""
        self.WHITELIST_PATHS.discard(path_prefix)
    
    def add_abnormal_pattern(self, pattern: str):
        """添加异常路径模式"""
        self.ABNORMAL_PATH_PATTERNS.append(pattern)
        self._abnormal_patterns.append(re.compile(pattern, re.IGNORECASE))
    
    def add_dangerous_pattern(self, pattern: str):
        """添加危险模式"""
        self.DANGEROUS_PATTERNS.append(pattern)
        self._dangerous_patterns.append(re.compile(pattern, re.IGNORECASE))


# ============================================================
# 存储管理器
# ============================================================

class StorageManager:
    """
    本地文件存储管理器
    
    负责：
    - IP 名单的持久化存储
    - 操作日志的记录
    - 数据的加载和保存
    """
    
    def __init__(self, config: ThreatProtectionConfig):
        self.config = config
        self.lock = threading.RLock()
        
        # 确保日志目录存在
        self.log_dir = Path(config.LOG_DIR)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.ip_list_path = self.log_dir / config.IP_LIST_FILE
        self.operation_log_path = self.log_dir / config.OPERATION_LOG_FILE
        
        # 内存中的 IP 记录缓存
        self._ip_records: Dict[str, IPThreatRecord] = {}
        
        # 加载已有数据
        self._load_ip_records()
        
        logger.info(f"StorageManager initialized, log_dir={self.log_dir}")
    
    def _load_ip_records(self):
        """从文件加载 IP 记录"""
        with self.lock:
            if self.ip_list_path.exists():
                try:
                    with open(self.ip_list_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        for ip, record_data in data.items():
                            self._ip_records[ip] = IPThreatRecord.from_dict(record_data)
                    logger.info(f"Loaded {len(self._ip_records)} IP records from {self.ip_list_path}")
                except Exception as e:
                    logger.error(f"Failed to load IP records: {e}")
                    self._ip_records = {}
    
    def _save_ip_records(self):
        """保存 IP 记录到文件"""
        with self.lock:
            try:
                data = {ip: record.to_dict() for ip, record in self._ip_records.items()}
                # 先写入临时文件，再原子性重命名
                temp_path = self.ip_list_path.with_suffix('.tmp')
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                temp_path.replace(self.ip_list_path)
            except Exception as e:
                logger.error(f"Failed to save IP records: {e}")
    
    def get_ip_record(self, ip: str) -> Optional[IPThreatRecord]:
        """获取 IP 记录"""
        with self.lock:
            return self._ip_records.get(ip)
    
    def set_ip_record(self, record: IPThreatRecord):
        """设置 IP 记录"""
        with self.lock:
            self._ip_records[record.ip] = record
            self._save_ip_records()
    
    def delete_ip_record(self, ip: str) -> bool:
        """删除 IP 记录"""
        with self.lock:
            if ip in self._ip_records:
                del self._ip_records[ip]
                self._save_ip_records()
                return True
            return False
    
    def get_all_records(self) -> Dict[str, IPThreatRecord]:
        """获取所有 IP 记录"""
        with self.lock:
            return dict(self._ip_records)
    
    def get_records_by_level(self, level: ThreatLevel) -> List[IPThreatRecord]:
        """按威胁级别获取记录"""
        with self.lock:
            return [r for r in self._ip_records.values() if r.level == level.value]
    
    def log_operation(self, operation: str, ip: str, detail: Dict[str, Any]):
        """记录操作日志（追加写入 JSONL 格式）"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "ip": ip,
            "detail": detail
        }
        
        try:
            with open(self.operation_log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"Failed to write operation log: {e}")
    
    def get_operation_logs(self, limit: int = 100, ip_filter: str = None) -> List[Dict[str, Any]]:
        """读取操作日志"""
        logs = []
        try:
            if self.operation_log_path.exists():
                with open(self.operation_log_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            entry = json.loads(line)
                            if ip_filter is None or entry.get("ip") == ip_filter:
                                logs.append(entry)
                # 返回最新的记录
                return logs[-limit:][::-1]
        except Exception as e:
            logger.error(f"Failed to read operation logs: {e}")
        return logs


# ============================================================
# 邮件通知器
# ============================================================

# 尝试导入 Mail.py 中的邮件发送函数
try:
    from Mail import mail as send_mail_via_qq
    MAIL_MODULE_AVAILABLE = True
    logger.info("Mail module imported successfully, using QQ mail for notifications")
except ImportError:
    MAIL_MODULE_AVAILABLE = False
    send_mail_via_qq = None
    logger.info("Mail module not available, will use built-in SMTP if configured")


class EmailNotifier:
    """
    邮件通知器
    
    负责发送安全预警邮件
    优先使用 Mail.py 中的 QQ 邮箱发送功能
    """
    
    def __init__(self, config: ThreatProtectionConfig):
        self.config = config
        self._last_email_time: Dict[str, float] = {}  # IP -> 上次发送时间
        self.lock = threading.Lock()
        
        # 如果 Mail 模块可用，自动启用邮件功能
        if MAIL_MODULE_AVAILABLE:
            self.config.EMAIL_ENABLED = True
            logger.info("Email notifications enabled via Mail.py (QQ Mail)")
    
    def can_send_email(self, ip: str) -> bool:
        """检查是否可以发送邮件（冷却时间检查）"""
        # 如果 Mail 模块可用，始终允许发送（但仍有冷却时间）
        if not self.config.EMAIL_ENABLED and not MAIL_MODULE_AVAILABLE:
            return False
        
        with self.lock:
            last_time = self._last_email_time.get(ip, 0)
            return time.time() - last_time >= self.config.WARNING_EMAIL_COOLDOWN
    
    def _send_via_mail_module(self, subject: str, content: str) -> bool:
        """使用 Mail.py 模块发送邮件"""
        if not MAIL_MODULE_AVAILABLE or send_mail_via_qq is None:
            return False
        
        try:
            result = send_mail_via_qq(
                content=content,
                from_name="三级威胁防护系统",
                to_name="安全管理员",
                subject=subject
            )
            return result
        except Exception as e:
            logger.error(f"Failed to send email via Mail module: {e}")
            return False
    
    def _send_via_smtp(self, subject: str, body: str) -> bool:
        """使用内置 SMTP 发送邮件（备用方案）"""
        if not self.config.ALERT_EMAIL_TO:
            logger.warning("No alert email recipients configured")
            return False
        
        try:
            msg = MIMEMultipart()
            msg['From'] = self.config.ALERT_EMAIL_FROM
            msg['To'] = ', '.join(self.config.ALERT_EMAIL_TO)
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            with smtplib.SMTP(self.config.SMTP_HOST, self.config.SMTP_PORT) as server:
                server.starttls()
                if self.config.SMTP_USER and self.config.SMTP_PASSWORD:
                    server.login(self.config.SMTP_USER, self.config.SMTP_PASSWORD)
                server.send_message(msg)
            
            return True
        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")
            return False
    
    def send_warning_email(self, record: IPThreatRecord) -> bool:
        """发送预警邮件"""
        if not self.can_send_email(record.ip):
            if not self.config.EMAIL_ENABLED and not MAIL_MODULE_AVAILABLE:
                logger.info(f"Email disabled, skipping warning email for {record.ip}")
            else:
                logger.info(f"Email cooldown active for {record.ip}")
            return False
        
        subject = f"[安全预警] IP {record.ip} 已升级到预警名单"
        
        # 构建邮件内容（使用纯 ASCII 兼容字符，避免编码问题）
        violations_text = "\n".join([
            f"  - [{v.get('timestamp', 'N/A')}] {v.get('violation_type', 'unknown')}: {v.get('path', 'N/A')}"
            for v in record.violations[-10:]  # 最近 10 条
        ])
        
        body = f"""========================================
        安全预警通知
========================================

IP 地址: {record.ip}
当前级别: {ThreatLevel(record.level).name}
违规次数: {record.violation_count}
首次发现: {record.first_seen}
最后活动: {record.last_seen}

最近违规记录:
{violations_text}

请及时关注并采取必要措施。

----------------------------------------
此邮件由三级威胁防护系统自动发送
发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        # 优先使用 Mail.py 模块
        success = False
        if MAIL_MODULE_AVAILABLE:
            success = self._send_via_mail_module(subject, body)
            if success:
                logger.info(f"Warning email sent via Mail.py for IP {record.ip}")
        
        # 如果 Mail.py 失败，尝试内置 SMTP
        if not success and self.config.EMAIL_ENABLED:
            success = self._send_via_smtp(subject, body)
            if success:
                logger.info(f"Warning email sent via SMTP for IP {record.ip}")
        
        if success:
            with self.lock:
                self._last_email_time[record.ip] = time.time()
        
        return success
    
    def send_blacklist_email(self, record: IPThreatRecord) -> bool:
        """发送黑名单通知邮件"""
        # 黑名单邮件不受冷却时间限制
        if not self.config.EMAIL_ENABLED and not MAIL_MODULE_AVAILABLE:
            return False
        
        subject = f"[严重警告] IP {record.ip} 已被加入黑名单"
        
        violations_text = "\n".join([
            f"  - [{v.get('timestamp', 'N/A')}] {v.get('violation_type', 'unknown')}: {v.get('path', 'N/A')}"
            for v in record.violations[-20:]
        ])
        
        body = f"""****************************************
        严重安全警告
****************************************

IP 地址 {record.ip} 已被加入黑名单!

当前级别: 黑名单 (BLACKLIST)
违规次数: {record.violation_count}
首次发现: {record.first_seen}
最后活动: {record.last_seen}
封禁原因: {record.blacklist_reason}

违规记录:
{violations_text}

该 IP 的所有请求将被直接拒绝。

----------------------------------------
此邮件由三级威胁防护系统自动发送
发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        # 优先使用 Mail.py 模块
        success = False
        if MAIL_MODULE_AVAILABLE:
            success = self._send_via_mail_module(subject, body)
            if success:
                logger.info(f"Blacklist email sent via Mail.py for IP {record.ip}")
        
        # 如果 Mail.py 失败，尝试内置 SMTP
        if not success and self.config.EMAIL_ENABLED:
            success = self._send_via_smtp(subject, body)
            if success:
                logger.info(f"Blacklist email sent via SMTP for IP {record.ip}")
        
        return success


# ============================================================
# 威胁检测器
# ============================================================

class ThreatDetector:
    """
    威胁检测器
    
    负责分析请求并检测潜在威胁
    """
    
    def __init__(self, config: ThreatProtectionConfig):
        self.config = config
    
    def detect_threat(self, path: str, method: str = "GET", 
                      user_agent: str = "", body: str = "") -> Tuple[bool, ViolationType, str]:
        """
        检测请求是否存在威胁
        
        Returns:
            (is_threat, violation_type, detail)
        """
        # 检查白名单
        for whitelist_prefix in self.config.WHITELIST_PATHS:
            if path.startswith(whitelist_prefix):
                return False, ViolationType.UNKNOWN, ""
        
        # 1. 检查扫描器 User-Agent
        if user_agent:
            for pattern in self.config._scanner_patterns:
                if pattern.search(user_agent):
                    return True, ViolationType.SCANNER_DETECTED, f"Scanner detected: {user_agent[:100]}"
        
        # 2. 检查危险模式（路径遍历、注入等）
        check_content = path + (body or "")
        for pattern in self.config._dangerous_patterns:
            if pattern.search(check_content):
                if '..' in check_content or '%2e' in check_content.lower():
                    return True, ViolationType.PATH_TRAVERSAL, f"Path traversal detected: {pattern.pattern}"
                return True, ViolationType.INJECTION_ATTEMPT, f"Dangerous pattern detected: {pattern.pattern}"
        
        # 3. 检查异常路径
        for pattern in self.config._abnormal_patterns:
            if pattern.search(path):
                return True, ViolationType.ABNORMAL_PATH, f"Abnormal path detected: {path[:100]}"
        
        return False, ViolationType.UNKNOWN, ""
    
    def is_abnormal_path(self, path: str) -> bool:
        """检查是否是异常路径"""
        for pattern in self.config._abnormal_patterns:
            if pattern.search(path):
                return True
        return False
    
    def is_dangerous_pattern(self, content: str) -> bool:
        """检查是否包含危险模式"""
        for pattern in self.config._dangerous_patterns:
            if pattern.search(content):
                return True
        return False


# ============================================================
# 核心防护引擎
# ============================================================

class ThreatProtectionEngine:
    """
    三级威胁防护引擎
    
    核心功能：
    1. 请求检查 - 判断请求是否应该被阻止
    2. 违规记录 - 记录违规行为
    3. 级别升级 - 自动升级威胁级别
    4. 邮件通知 - 发送预警邮件
    5. 黑名单管理 - 管理被封禁的 IP
    """
    
    def __init__(self, config: ThreatProtectionConfig = None):
        self.config = config or ThreatProtectionConfig()
        self.storage = StorageManager(self.config)
        self.detector = ThreatDetector(self.config)
        self.notifier = EmailNotifier(self.config)
        self.lock = threading.RLock()
        
        # 统计信息
        self._stats = {
            "total_checks": 0,
            "blocked_requests": 0,
            "watch_list_additions": 0,
            "warning_upgrades": 0,
            "blacklist_additions": 0,
            "emails_sent": 0
        }
        
        logger.info("ThreatProtectionEngine initialized")
    
    def check_request(self, ip: str, path: str, method: str = "GET",
                      user_agent: str = "", body: str = "",
                      request_id: str = "") -> Tuple[bool, str, ThreatLevel]:
        """
        检查请求是否应该被允许
        
        Args:
            ip: 客户端 IP
            path: 请求路径
            method: HTTP 方法
            user_agent: User-Agent
            body: 请求体
            request_id: 请求 ID
        
        Returns:
            (is_allowed, reason, threat_level)
            - is_allowed: True 表示允许，False 表示阻止
            - reason: 阻止原因（如果被阻止）
            - threat_level: 当前威胁级别
        """
        with self.lock:
            self._stats["total_checks"] += 1
        
        # 1. 检查是否在黑名单中
        record = self.storage.get_ip_record(ip)
        if record and record.level == ThreatLevel.BLACKLIST.value:
            # 检查是否已过自动解封时间
            if record.auto_unblock_time:
                unblock_time = datetime.fromisoformat(record.auto_unblock_time)
                if datetime.now() >= unblock_time:
                    # 自动解封
                    self._downgrade_level(ip, ThreatLevel.WARNING, "Auto unblock after blacklist duration")
                    record = self.storage.get_ip_record(ip)
                else:
                    with self.lock:
                        self._stats["blocked_requests"] += 1
                    logger.warning(f"[{request_id}] BLOCKED: IP {ip} is blacklisted until {record.auto_unblock_time}")
                    return False, f"IP is blacklisted until {record.auto_unblock_time}", ThreatLevel.BLACKLIST
            else:
                with self.lock:
                    self._stats["blocked_requests"] += 1
                logger.warning(f"[{request_id}] BLOCKED: IP {ip} is permanently blacklisted")
                return False, "IP is permanently blacklisted", ThreatLevel.BLACKLIST
        
        # 2. 检测威胁
        is_threat, violation_type, detail = self.detector.detect_threat(path, method, user_agent, body)
        
        if is_threat:
            # 记录违规
            self._record_violation(ip, path, method, user_agent, violation_type, detail, request_id)
            
            # 获取更新后的记录
            record = self.storage.get_ip_record(ip)
            current_level = ThreatLevel(record.level) if record else ThreatLevel.NONE
            
            # 如果已经是黑名单，阻止请求
            if current_level == ThreatLevel.BLACKLIST:
                with self.lock:
                    self._stats["blocked_requests"] += 1
                return False, f"IP blacklisted: {detail}", ThreatLevel.BLACKLIST
            
            # 返回允许但记录威胁级别
            logger.info(f"[{request_id}] THREAT DETECTED: IP {ip}, level={current_level.name}, type={violation_type.value}")
            return True, "", current_level
        
        # 3. 正常请求
        current_level = ThreatLevel(record.level) if record else ThreatLevel.NONE
        return True, "", current_level
    
    def _record_violation(self, ip: str, path: str, method: str, user_agent: str,
                          violation_type: ViolationType, detail: str, request_id: str):
        """记录违规行为并可能升级威胁级别"""
        now = datetime.now()
        now_str = now.isoformat()
        
        # 创建违规记录
        violation = ViolationRecord(
            timestamp=now_str,
            violation_type=violation_type.value,
            path=path[:500],  # 截断过长的路径
            method=method,
            user_agent=user_agent[:200],  # 截断过长的 UA
            detail=detail[:500],
            request_id=request_id
        )
        
        # 获取或创建 IP 记录
        record = self.storage.get_ip_record(ip)
        
        if record is None:
            # 新 IP，创建记录并加入观察名单
            record = IPThreatRecord(
                ip=ip,
                level=ThreatLevel.WATCH.value,
                first_seen=now_str,
                last_seen=now_str,
                violation_count=1,
                violations=[violation.to_dict()],
                email_sent_count=0,
                last_email_sent=None,
                blacklist_reason="",
                auto_unblock_time=None
            )
            self.storage.set_ip_record(record)
            self.storage.log_operation("ADD_TO_WATCH", ip, {
                "reason": detail,
                "violation_type": violation_type.value,
                "path": path[:200]
            })
            with self.lock:
                self._stats["watch_list_additions"] += 1
            logger.info(f"[{request_id}] IP {ip} added to WATCH list (first violation)")
        else:
            # 更新现有记录
            record.last_seen = now_str
            record.violation_count += 1
            record.violations.append(violation.to_dict())
            
            # 只保留最近 100 条违规记录
            if len(record.violations) > 100:
                record.violations = record.violations[-100:]
            
            # 检查是否需要升级级别
            old_level = ThreatLevel(record.level)
            new_level = self._calculate_new_level(record)
            
            if new_level.value > old_level.value:
                record.level = new_level.value
                self._handle_level_upgrade(record, old_level, new_level, detail, request_id)
            
            self.storage.set_ip_record(record)
    
    def _calculate_new_level(self, record: IPThreatRecord) -> ThreatLevel:
        """根据违规次数计算新的威胁级别"""
        # 计算时间窗口内的有效违规次数
        now = datetime.now()
        window_start = now - timedelta(seconds=self.config.VIOLATION_WINDOW)
        
        recent_violations = [
            v for v in record.violations
            if datetime.fromisoformat(v.get("timestamp", "1970-01-01")) > window_start
        ]
        
        recent_count = len(recent_violations)
        
        if recent_count >= self.config.BLACKLIST_THRESHOLD:
            return ThreatLevel.BLACKLIST
        elif recent_count >= self.config.WARNING_THRESHOLD:
            return ThreatLevel.WARNING
        elif recent_count >= self.config.WATCH_THRESHOLD:
            return ThreatLevel.WATCH
        else:
            return ThreatLevel.NONE
    
    def _handle_level_upgrade(self, record: IPThreatRecord, old_level: ThreatLevel,
                               new_level: ThreatLevel, detail: str, request_id: str):
        """处理级别升级"""
        ip = record.ip
        
        self.storage.log_operation("LEVEL_UPGRADE", ip, {
            "old_level": old_level.name,
            "new_level": new_level.name,
            "violation_count": record.violation_count,
            "reason": detail
        })
        
        logger.warning(f"[{request_id}] IP {ip} upgraded from {old_level.name} to {new_level.name}")
        
        if new_level == ThreatLevel.WARNING:
            # 升级到预警名单，发送邮件
            with self.lock:
                self._stats["warning_upgrades"] += 1
            
            if self.notifier.send_warning_email(record):
                record.email_sent_count += 1
                record.last_email_sent = datetime.now().isoformat()
                with self.lock:
                    self._stats["emails_sent"] += 1
        
        elif new_level == ThreatLevel.BLACKLIST:
            # 升级到黑名单
            record.blacklist_reason = detail
            # 设置自动解封时间
            unblock_time = datetime.now() + timedelta(seconds=self.config.BLACKLIST_DURATION)
            record.auto_unblock_time = unblock_time.isoformat()
            
            with self.lock:
                self._stats["blacklist_additions"] += 1
            
            if self.notifier.send_blacklist_email(record):
                record.email_sent_count += 1
                record.last_email_sent = datetime.now().isoformat()
                with self.lock:
                    self._stats["emails_sent"] += 1
    
    def _downgrade_level(self, ip: str, new_level: ThreatLevel, reason: str):
        """降级威胁级别"""
        record = self.storage.get_ip_record(ip)
        if record:
            old_level = ThreatLevel(record.level)
            record.level = new_level.value
            record.auto_unblock_time = None
            self.storage.set_ip_record(record)
            
            self.storage.log_operation("LEVEL_DOWNGRADE", ip, {
                "old_level": old_level.name,
                "new_level": new_level.name,
                "reason": reason
            })
            
            logger.info(f"IP {ip} downgraded from {old_level.name} to {new_level.name}: {reason}")
    
    # === 管理接口 ===
    
    def record_violation(self, ip: str, path: str, method: str = "GET",
                         user_agent: str = "", violation_type: ViolationType = ViolationType.UNKNOWN,
                         detail: str = "", request_id: str = "") -> Tuple[bool, ThreatLevel]:
        """
        公开的违规记录方法，供外部调用
        
        当外部检测到违规行为（如 header validation 失败）时，
        可以调用此方法记录违规并可能升级威胁级别。
        
        Args:
            ip: 客户端 IP
            path: 请求路径
            method: HTTP 方法
            user_agent: User-Agent
            violation_type: 违规类型
            detail: 详细描述
            request_id: 请求 ID
        
        Returns:
            (is_blocked, threat_level)
            - is_blocked: True 表示 IP 已被加入黑名单
            - threat_level: 当前威胁级别
        """
        # 记录违规
        self._record_violation(ip, path, method, user_agent, violation_type, detail, request_id)
        
        # 获取更新后的记录
        record = self.storage.get_ip_record(ip)
        current_level = ThreatLevel(record.level) if record else ThreatLevel.NONE
        
        is_blocked = current_level == ThreatLevel.BLACKLIST
        
        logger.info(f"[{request_id}] VIOLATION RECORDED: IP {ip}, level={current_level.name}, type={violation_type.value}")
        
        return is_blocked, current_level
    
    def manual_blacklist(self, ip: str, reason: str, permanent: bool = False) -> bool:
        """手动将 IP 加入黑名单"""
        now_str = datetime.now().isoformat()
        
        record = self.storage.get_ip_record(ip)
        if record is None:
            record = IPThreatRecord(
                ip=ip,
                level=ThreatLevel.BLACKLIST.value,
                first_seen=now_str,
                last_seen=now_str,
                violation_count=0,
                violations=[],
                email_sent_count=0,
                last_email_sent=None,
                blacklist_reason=f"Manual: {reason}",
                auto_unblock_time=None if permanent else (
                    datetime.now() + timedelta(seconds=self.config.BLACKLIST_DURATION)
                ).isoformat()
            )
        else:
            record.level = ThreatLevel.BLACKLIST.value
            record.blacklist_reason = f"Manual: {reason}"
            record.last_seen = now_str
            if not permanent:
                record.auto_unblock_time = (
                    datetime.now() + timedelta(seconds=self.config.BLACKLIST_DURATION)
                ).isoformat()
            else:
                record.auto_unblock_time = None
        
        self.storage.set_ip_record(record)
        self.storage.log_operation("MANUAL_BLACKLIST", ip, {
            "reason": reason,
            "permanent": permanent
        })
        
        with self.lock:
            self._stats["blacklist_additions"] += 1
        
        logger.warning(f"IP {ip} manually blacklisted: {reason} (permanent={permanent})")
        return True
    
    def manual_unblock(self, ip: str, reason: str = "Manual unblock") -> bool:
        """手动解除 IP 封禁"""
        record = self.storage.get_ip_record(ip)
        if record is None:
            return False
        
        old_level = ThreatLevel(record.level)
        
        # 完全移除记录或降级到 NONE
        self.storage.delete_ip_record(ip)
        
        self.storage.log_operation("MANUAL_UNBLOCK", ip, {
            "old_level": old_level.name,
            "reason": reason
        })
        
        logger.info(f"IP {ip} manually unblocked: {reason}")
        return True
    
    def get_ip_status(self, ip: str) -> Optional[Dict[str, Any]]:
        """获取 IP 状态"""
        record = self.storage.get_ip_record(ip)
        if record is None:
            return None
        
        return {
            "ip": record.ip,
            "level": ThreatLevel(record.level).name,
            "first_seen": record.first_seen,
            "last_seen": record.last_seen,
            "violation_count": record.violation_count,
            "recent_violations": record.violations[-10:],
            "email_sent_count": record.email_sent_count,
            "blacklist_reason": record.blacklist_reason,
            "auto_unblock_time": record.auto_unblock_time
        }
    
    def get_all_ips_by_level(self, level: ThreatLevel) -> List[Dict[str, Any]]:
        """获取指定级别的所有 IP"""
        records = self.storage.get_records_by_level(level)
        return [
            {
                "ip": r.ip,
                "level": ThreatLevel(r.level).name,
                "violation_count": r.violation_count,
                "last_seen": r.last_seen,
                "blacklist_reason": r.blacklist_reason if level == ThreatLevel.BLACKLIST else None
            }
            for r in records
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self.lock:
            stats = dict(self._stats)
        
        # 添加各级别的 IP 数量
        all_records = self.storage.get_all_records()
        level_counts = {level.name: 0 for level in ThreatLevel}
        for record in all_records.values():
            level_name = ThreatLevel(record.level).name
            level_counts[level_name] += 1
        
        stats["level_counts"] = level_counts
        stats["total_tracked_ips"] = len(all_records)
        
        return stats
    
    def get_recent_operations(self, limit: int = 50, ip_filter: str = None) -> List[Dict[str, Any]]:
        """获取最近的操作日志"""
        return self.storage.get_operation_logs(limit=limit, ip_filter=ip_filter)
    
    def cleanup_expired_records(self) -> int:
        """清理过期的记录"""
        now = datetime.now()
        cleaned = 0
        
        all_records = self.storage.get_all_records()
        for ip, record in all_records.items():
            # 检查是否是过期的黑名单
            if record.level == ThreatLevel.BLACKLIST.value and record.auto_unblock_time:
                unblock_time = datetime.fromisoformat(record.auto_unblock_time)
                if now >= unblock_time:
                    self._downgrade_level(ip, ThreatLevel.WARNING, "Auto cleanup: blacklist expired")
                    cleaned += 1
            
            # 检查是否是长期无活动的观察名单
            elif record.level == ThreatLevel.WATCH.value:
                last_seen = datetime.fromisoformat(record.last_seen)
                if (now - last_seen).total_seconds() > self.config.VIOLATION_WINDOW * 7:  # 7 倍窗口期
                    self.storage.delete_ip_record(ip)
                    self.storage.log_operation("AUTO_CLEANUP", ip, {
                        "reason": "Long inactive watch list entry"
                    })
                    cleaned += 1
        
        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} expired records")
        
        return cleaned


# ============================================================
# ASGI 中间件（可选，用于 FastAPI/Starlette）
# ============================================================

class ThreatProtectionMiddleware:
    """
    ASGI 中间件
    
    可以直接集成到 FastAPI 或 Starlette 应用中
    
    使用示例:
        from fastapi import FastAPI
        from security_threat_protection import ThreatProtectionMiddleware, ThreatProtectionConfig
        
        app = FastAPI()
        config = ThreatProtectionConfig()
        config.add_whitelist_path("/api/")  # 添加白名单
        app.add_middleware(ThreatProtectionMiddleware, config=config)
    """
    
    def __init__(self, app, config: ThreatProtectionConfig = None):
        self.app = app
        self.engine = ThreatProtectionEngine(config)
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # 提取请求信息
        path = scope.get("path", "/")
        method = scope.get("method", "GET")
        
        # 获取客户端 IP
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        
        # 检查 X-Forwarded-For 头
        headers = dict(scope.get("headers", []))
        forwarded_for = headers.get(b"x-forwarded-for", b"").decode()
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        
        # 获取 User-Agent
        user_agent = headers.get(b"user-agent", b"").decode()
        
        # 生成请求 ID
        request_id = f"REQ-{int(time.time() * 1000)}"
        
        # 检查请求
        is_allowed, reason, threat_level = self.engine.check_request(
            ip=client_ip,
            path=path,
            method=method,
            user_agent=user_agent,
            request_id=request_id
        )
        
        if not is_allowed:
            # 返回 403 响应
            response_body = json.dumps({
                "error": "Forbidden",
                "detail": reason,
                "request_id": request_id
            }).encode()
            
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"x-threat-level", threat_level.name.encode()],
                    [b"x-request-id", request_id.encode()]
                ]
            })
            await send({
                "type": "http.response.body",
                "body": response_body
            })
            return
        
        # 继续处理请求
        await self.app(scope, receive, send)


# ============================================================
# 独立 API 服务（可选）
# ============================================================

def create_standalone_api():
    """
    创建独立的 FastAPI 应用
    
    可以作为独立服务运行，提供 REST API 管理接口
    
    运行方式:
        python security_threat_protection.py --port 8080
    """
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.responses import JSONResponse
    except ImportError:
        logger.error("FastAPI not installed. Run: pip install fastapi uvicorn")
        return None
    
    app = FastAPI(
        title="三级威胁防护系统 API",
        description="独立的安全防护服务，提供 IP 威胁管理功能",
        version="1.0.0"
    )
    
    # 创建引擎实例
    engine = ThreatProtectionEngine()
    
    @app.get("/")
    async def root():
        return {
            "service": "Three-Level Threat Protection System",
            "version": "1.0.0",
            "status": "running"
        }
    
    @app.get("/stats")
    async def get_stats():
        """获取统计信息"""
        return engine.get_stats()
    
    @app.get("/ip/{ip}")
    async def get_ip_status(ip: str):
        """获取 IP 状态"""
        status = engine.get_ip_status(ip)
        if status is None:
            return {"ip": ip, "status": "not_tracked"}
        return status
    
    @app.post("/check")
    async def check_request(
        ip: str,
        path: str,
        method: str = "GET",
        user_agent: str = ""
    ):
        """检查请求"""
        is_allowed, reason, level = engine.check_request(
            ip=ip,
            path=path,
            method=method,
            user_agent=user_agent
        )
        return {
            "ip": ip,
            "is_allowed": is_allowed,
            "reason": reason,
            "threat_level": level.name
        }
    
    @app.get("/list/{level}")
    async def list_ips_by_level(level: str):
        """获取指定级别的 IP 列表"""
        try:
            threat_level = ThreatLevel[level.upper()]
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid level: {level}")
        
        return engine.get_all_ips_by_level(threat_level)
    
    @app.post("/blacklist/{ip}")
    async def blacklist_ip(
        ip: str,
        reason: str = Query(..., description="封禁原因"),
        permanent: bool = Query(False, description="是否永久封禁")
    ):
        """手动封禁 IP"""
        success = engine.manual_blacklist(ip, reason, permanent)
        return {"success": success, "ip": ip, "action": "blacklisted"}
    
    @app.post("/unblock/{ip}")
    async def unblock_ip(ip: str, reason: str = Query("Manual unblock")):
        """手动解封 IP"""
        success = engine.manual_unblock(ip, reason)
        if not success:
            raise HTTPException(status_code=404, detail=f"IP {ip} not found")
        return {"success": success, "ip": ip, "action": "unblocked"}
    
    @app.get("/logs")
    async def get_logs(
        limit: int = Query(50, ge=1, le=500),
        ip: str = Query(None, description="按 IP 过滤")
    ):
        """获取操作日志"""
        return engine.get_recent_operations(limit=limit, ip_filter=ip)
    
    @app.post("/cleanup")
    async def cleanup():
        """清理过期记录"""
        cleaned = engine.cleanup_expired_records()
        return {"cleaned": cleaned}
    
    return app


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="三级威胁防护系统")
    parser.add_argument("--mode", choices=["api", "test"], default="api",
                        help="运行模式: api=启动 API 服务, test=运行测试")
    parser.add_argument("--host", default="0.0.0.0", help="API 服务监听地址")
    parser.add_argument("--port", type=int, default=8080, help="API 服务端口")
    
    args = parser.parse_args()
    
    if args.mode == "api":
        app = create_standalone_api()
        if app:
            try:
                import uvicorn
                logger.info(f"Starting Threat Protection API on {args.host}:{args.port}")
                uvicorn.run(app, host=args.host, port=args.port)
            except ImportError:
                logger.error("uvicorn not installed. Run: pip install uvicorn")
    
    elif args.mode == "test":
        # 运行简单测试
        logger.info("Running tests...")
        
        config = ThreatProtectionConfig()
        config.add_whitelist_path("/api/")
        
        engine = ThreatProtectionEngine(config)
        
        # 测试正常请求
        result = engine.check_request("192.168.1.1", "/api/users", "GET")
        logger.info(f"Normal request: {result}")
        
        # 测试异常路径
        result = engine.check_request("192.168.1.2", "/.env", "GET")
        logger.info(f"Abnormal path: {result}")
        
        # 测试路径遍历
        result = engine.check_request("192.168.1.3", "/../../etc/passwd", "GET")
        logger.info(f"Path traversal: {result}")
        
        # 多次违规测试
        for i in range(6):
            result = engine.check_request("192.168.1.4", f"/wp-admin/test{i}", "GET")
            logger.info(f"Violation {i+1}: {result}")
        
        # 打印统计
        logger.info(f"Stats: {engine.get_stats()}")
        
        # 打印 IP 状态
        for ip in ["192.168.1.2", "192.168.1.3", "192.168.1.4"]:
            status = engine.get_ip_status(ip)
            logger.info(f"IP {ip} status: {status}")