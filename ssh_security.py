"""
Minimal security helpers for ssh_websocket.py.

The original project referenced a richer security layer (rate limiting, input
validation, session idle timeouts, logging, etc.). Those modules were missing in
this repository, which prevents the SSH WebSocket backend from starting.

This file provides a small, dependency-free implementation that keeps the same
public API expected by ssh_websocket.py.

Notes:
- This is NOT meant to be a complete security solution.
- It does implement basic message size limits, simple per-IP connection counts,
  and session idle timeouts to avoid runaway resource usage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

try:
    # Only used for typing; ssh_websocket.py passes a FastAPI WebSocket instance.
    from fastapi import WebSocket
except Exception:  # pragma: no cover
    WebSocket = Any  # type: ignore


# ---- Basic validators -------------------------------------------------------


class InputValidator:
    """Small helpers to validate incoming WebSocket payloads."""

    # Keep reasonably small; terminal payloads should be frequent but tiny.
    MAX_MESSAGE_BYTES = 256 * 1024

    @staticmethod
    def validate_msg_size(msg: Any, max_bytes: int | None = None) -> bool:
        if msg is None:
            return False
        if max_bytes is None:
            max_bytes = InputValidator.MAX_MESSAGE_BYTES
        try:
            if isinstance(msg, (bytes, bytearray)):
                size = len(msg)
            else:
                size = len(str(msg).encode("utf-8", errors="ignore"))
            return size <= max_bytes
        except Exception:
            return False


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def validate_ssh_connection(data: Any) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate and sanitize SSH connection parameters sent from the frontend.

    Supported auth fields:
    - password
    - key_file / keyFile (server-side path, use with caution)
    - key_content / keyContent (private key content pasted/uploaded from browser)
    - passphrase (optional)

    Supported jump host:
    - jump: { hostname, port, username, password|key_*, passphrase }
      (If jump.enabled === false, it is ignored.)
    """

    def _sanitize_one(conn: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        hostname = str(conn.get("hostname", "")).strip()
        username = str(conn.get("username", "")).strip()
        port = _as_int(conn.get("port", 22), 22)

        # Frontend sometimes sends cols/rows as well as width/height.
        width = conn.get("width", conn.get("cols", 80))
        height = conn.get("height", conn.get("rows", 24))
        width_i = _as_int(width, 80)
        height_i = _as_int(height, 24)

        password = conn.get("password")
        key_file = conn.get("key_file") or conn.get("keyFile")
        key_content = conn.get("key_content") or conn.get("keyContent")
        passphrase = conn.get("passphrase")

        if not hostname:
            return False, "hostname 不能为空", {}
        if not username:
            return False, "username 不能为空", {}
        if port <= 0 or port > 65535:
            return False, "port 范围错误", {}

        # Basic hard limits to avoid abuse.
        if len(hostname) > 255 or len(username) > 128:
            return False, "连接参数过长", {}

        # Auth must be provided.
        if not password and not key_file and not key_content:
            return False, "必须提供 password 或 key", {}

        # Avoid massive private keys in WS payloads.
        if isinstance(key_content, str) and len(key_content) > 256 * 1024:
            return False, "key_content 过大", {}

        sanitized: Dict[str, Any] = {
            "hostname": hostname,
            "port": port,
            "username": username,
            "password": password,
            "key_file": key_file,
            "key_content": key_content,
            "passphrase": passphrase,
            "width": max(20, min(width_i, 500)),
            "height": max(5, min(height_i, 300)),
        }
        return True, "", sanitized

    if not isinstance(data, dict):
        return False, "连接参数格式错误", {}

    ok, err, sanitized = _sanitize_one(data)
    if not ok:
        return ok, err, sanitized

    jump = data.get("jump")
    if isinstance(jump, dict):
        if jump.get("enabled") is False:
            # ignore
            pass
        else:
            ok2, err2, jump_sanitized = _sanitize_one(jump)
            if not ok2:
                return False, f"jump: {err2}", {}
            sanitized["jump"] = jump_sanitized

    return True, "", sanitized


def validate_command_input(command: Any, session_id: str) -> Tuple[bool, str, str]:
    """
    Command validation for legacy 'command' messages.

    In a full PTY passthrough terminal, validating shell commands is generally
    not feasible. We keep this permissive and only enforce basic size limits.
    """
    if command is None:
        return False, "命令为空", ""
    cmd = str(command)
    if len(cmd) > 64 * 1024:
        return False, "命令过长", ""
    # Disallow NUL bytes; they can confuse downstream tooling.
    if "\x00" in cmd:
        return False, "命令包含非法字符", ""
    return True, "", cmd


# ---- Rate limiting & session tracking --------------------------------------


class _RateLimiter:
    def __init__(self, max_conns_per_ip: int = 10):
        self.max_conns_per_ip = max_conns_per_ip
        self._counts: Dict[str, int] = {}

    def can_add(self, ip: str) -> bool:
        return self._counts.get(ip, 0) < self.max_conns_per_ip

    def add_conn(self, ip: str) -> None:
        self._counts[ip] = self._counts.get(ip, 0) + 1

    def remove_conn(self, ip: str) -> None:
        if ip in self._counts:
            self._counts[ip] -= 1
            if self._counts[ip] <= 0:
                del self._counts[ip]


@dataclass
class _SessionInfo:
    ip: str
    created_at: float
    last_active: float


class _SessionSecurity:
    def __init__(self, idle_timeout_seconds: int = 60 * 30):
        self.idle_timeout_seconds = idle_timeout_seconds
        self._sessions: Dict[str, _SessionInfo] = {}

    def register(self, session_id: str, ip: str) -> None:
        now = time.time()
        self._sessions[session_id] = _SessionInfo(ip=ip, created_at=now, last_active=now)

    def unregister(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def update(self, session_id: str) -> None:
        info = self._sessions.get(session_id)
        if info:
            info.last_active = time.time()

    def check_idle(self, session_id: str) -> bool:
        info = self._sessions.get(session_id)
        if not info:
            return False
        return (time.time() - info.last_active) > self.idle_timeout_seconds


class _SecurityLogger:
    def log_blocked(self, ip: Optional[str], reason: str) -> None:
        print(f"[SECURITY] blocked ip={ip or 'unknown'} reason={reason}")

    def log_connection(self, ip: Optional[str], host: str, user: str, success: bool) -> None:
        print(f"[SECURITY] connect ip={ip or 'unknown'} host={host} user={user} success={success}")


rate_limiter = _RateLimiter()
session_security = _SessionSecurity()
security_logger = _SecurityLogger()


def apply_security_checks(websocket: WebSocket) -> Tuple[bool, str, Optional[str]]:
    """
    Basic pre-flight check used by ssh_websocket.py.

    Returns: (allowed, error_message, client_ip)
    """
    client_ip: Optional[str] = None
    try:
        if getattr(websocket, "client", None):
            client_ip = websocket.client.host  # type: ignore[attr-defined]
    except Exception:
        client_ip = None

    ip = client_ip or "unknown"
    if not rate_limiter.can_add(ip):
        return False, "连接过多，请稍后再试", client_ip

    return True, "", client_ip


def cleanup_security_session(session_id: str, client_ip: Optional[str]) -> None:
    try:
        session_security.unregister(session_id)
    except Exception:
        pass
    if client_ip:
        try:
            rate_limiter.remove_conn(client_ip)
        except Exception:
            pass
