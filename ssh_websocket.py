from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict
import paramiko
import asyncio
import threading
import json
import time
import os
import re
from contextlib import asynccontextmanager
from collections import deque

# SSH连接信息模型
class SSHConnection(BaseModel):
    hostname: str
    port: int = 22
    username: str
    password: Optional[str] = None
    key_file: Optional[str] = None
    width: Optional[int] = 80
    height: Optional[int] = 24

# WebSocket消息类型
class WebSocketMessage(BaseModel):
    type: str  # "connect", "command", "disconnect", "resize"
    data: Optional[Dict] = None

# Resize节流器 - 修复P0问题: Resize命令洪泛
class ResizeThrottler:
    """Resize命令节流器,防止拖动浏览器时洪泛后端"""

    def __init__(self, interval=0.3):
        self.interval = interval  # 节流间隔(秒)
        self.last_resize_time = {}  # 每个session的最后resize时间
        self.pending_resize = {}  # 等待执行的resize
        self.lock = threading.Lock()

    def should_execute(self, session_id: str, width: int, height: int) -> bool:
        """判断是否应该执行resize,并记录待执行的resize"""
        with self.lock:
            current_time = time.time()
            last_time = self.last_resize_time.get(session_id, 0)

            # 如果距离上次resize超过间隔,立即执行
            if current_time - last_time >= self.interval:
                self.last_resize_time[session_id] = current_time
                # 清除pending
                if session_id in self.pending_resize:
                    del self.pending_resize[session_id]
                return True

            # 否则记录为pending,等待下次执行
            self.pending_resize[session_id] = (width, height, current_time)
            return False

    def get_pending(self, session_id: str):
        """获取待执行的resize"""
        with self.lock:
            if session_id in self.pending_resize:
                width, height, _ = self.pending_resize[session_id]
                del self.pending_resize[session_id]
                self.last_resize_time[session_id] = time.time()
                return (width, height)
            return None

# SSH会话管理器 - 修复P1问题: 历史记录扩展
class SSHSessionManager:
    # 修复P1: 历史记录容量从默认扩展到100
    MAX_HISTORY_SIZE = 100

    def __init__(self):
        self.sessions: Dict[str, paramiko.SSHClient] = {}
        self.websocket_connections: Dict[str, WebSocket] = {}
        self.command_history: Dict[str, deque] = {}  # 使用deque,自动限制大小
        self.cwd_cache: Dict[str, str] = {}
        self.home_dir_cache: Dict[str, str] = {}
        self.lock = threading.Lock()
        # 修复P0: 添加resize节流器
        self.resize_throttler = ResizeThrottler(interval=0.3)

    def generate_session_id(self, connection: SSHConnection) -> str:
        return f"{connection.username}@{connection.hostname}:{connection.port}"

    def update_cwd(self, session_id: str, command: str, ssh_client: paramiko.SSHClient = None):
        """
        尝试从命令中更新当前工作目录
        修复P0: 确保CWD不被Backspace等键盘输入影响
        """
        # 修复P0: 只处理实际的命令,忽略键盘控制字符
        if not command or not command.strip():
            return

        # 过滤掉控制字符和特殊按键
        if any(c in command for c in ['\x08', '\x7f', '\x1b']):  # Backspace, Delete, Escape
            return

        with self.lock:
            parts = command.strip().split()
            if not parts:
                return

            # 只处理cd命令
            if parts[0] == 'cd':
                if len(parts) == 1:
                    path = '~'
                else:
                    path = parts[1]

                # 总是尝试在SSH服务器上执行并获取真实目录
                if ssh_client:
                    try:
                        combined_command = f"cd {path} && pwd"
                        stdin, stdout, stderr = ssh_client.exec_command(combined_command, timeout=5)
                        real_cwd = stdout.read().decode('utf-8', errors='ignore').strip()
                        error_output = stderr.read().decode('utf-8', errors='ignore').strip()

                        if real_cwd and not error_output:
                            self.cwd_cache[session_id] = real_cwd
                            print(f"[CWD] 真实更新: {real_cwd}")
                            return
                        elif error_output:
                            print(f"[CWD] cd命令错误: {error_output}")
                    except Exception as e:
                        print(f"[CWD] 获取失败: {e}")

                # 本地逻辑推算(回退方案)
                current = self.cwd_cache.get(session_id, '~')

                if path == '~' or len(parts) == 1:
                    if ssh_client:
                        try:
                            stdin, stdout, stderr = ssh_client.exec_command("pwd", timeout=5)
                            home_dir = stdout.read().decode('utf-8', errors='ignore').strip()
                            if home_dir and not stderr.read().decode('utf-8', errors='ignore').strip():
                                self.cwd_cache[session_id] = home_dir
                                print(f"[CWD] 主目录更新: {home_dir}")
                                return
                        except Exception as e:
                            print(f"[CWD] 获取主目录失败: {e}")
                    self.cwd_cache[session_id] = '~'
                elif path.startswith('/'):
                    self.cwd_cache[session_id] = path
                elif path == '..':
                    # 修复P1: 优化父目录切换
                    if current == '~':
                        if ssh_client:
                            try:
                                stdin, stdout, stderr = ssh_client.exec_command("cd .. && pwd", timeout=5)
                                real_path = stdout.read().decode('utf-8', errors='ignore').strip()
                                if real_path and not stderr.read().decode('utf-8', errors='ignore').strip():
                                    self.cwd_cache[session_id] = real_path
                                    print(f"[CWD] 父目录更新: {real_path}")
                                    return
                            except Exception as e:
                                print(f"[CWD] 父目录失败: {e}")
                        self.cwd_cache[session_id] = '/'
                    elif current == '/':
                        pass  # 已在根目录
                    else:
                        parent = os.path.dirname(current.rstrip('/'))
                        self.cwd_cache[session_id] = parent or '/'
                elif path == '.':
                    pass  # 当前目录不变
                else:
                    # 相对路径 - 修复P1
                    if ssh_client:
                        try:
                            combined = f"cd {current} && cd {path} && pwd" if current != '~' else f"cd {path} && pwd"
                            stdin, stdout, stderr = ssh_client.exec_command(combined, timeout=5)
                            real_path = stdout.read().decode('utf-8', errors='ignore').strip()
                            if real_path and not stderr.read().decode('utf-8', errors='ignore').strip():
                                self.cwd_cache[session_id] = real_path
                                print(f"[CWD] 相对路径更新: {real_path}")
                                return
                        except Exception as e:
                            print(f"[CWD] 相对路径失败: {e}")

                    # 本地推算
                    if current == '~':
                        self.cwd_cache[session_id] = f"~/{path}"
                    elif current == '/':
                        self.cwd_cache[session_id] = f"/{path}"
                    else:
                        self.cwd_cache[session_id] = f"{current}/{path}"

            print(f"[CWD] 当前: {self.cwd_cache.get(session_id)}")

    def get_cwd(self, session_id: str) -> str:
        with self.lock:
            return self.cwd_cache.get(session_id, '~')

    @staticmethod
    def escape_shell_path(path: str) -> str:
        """
        转义路径中的危险字符，防止命令注入
        纯函数，不依赖任何状态

        Args:
            path: 原始路径字符串

        Returns:
            str: 转义后的安全路径
        """
        if not path:
            return path
        # 替换单引号为 '\''，这是shell中转义单引号的标准方式
        return path.replace("'", "'\"'\"'")

    @staticmethod
    def validate_path_format(path: str) -> tuple:
        """
        验证路径格式是否有效（纯函数，不依赖状态）

        Args:
            path: 要验证的路径

        Returns:
            tuple: (is_valid: bool, error_message: str, normalized_path: str)
        """
        # 空路径
        if path is None:
            return (False, "路径为None", "")

        if not isinstance(path, str):
            return (False, f"路径类型错误: {type(path).__name__}", "")

        # 去除首尾空白
        normalized = path.strip()

        if not normalized:
            return (False, "路径为空字符串", "")

        # 路径长度限制 (Linux PATH_MAX = 4096)
        if len(normalized) > 4096:
            return (False, f"路径过长: {len(normalized)} > 4096", "")

        # 检查空字节（安全漏洞）
        if '\x00' in normalized:
            return (False, "路径包含空字节", "")

        # 检查危险的控制字符
        control_chars = ['\x01', '\x02', '\x03', '\x04', '\x05', '\x06', '\x07',
                         '\x08', '\x0b', '\x0c', '\x0e', '\x0f', '\x10', '\x11',
                         '\x12', '\x13', '\x14', '\x15', '\x16', '\x17', '\x18',
                         '\x19', '\x1a', '\x1b', '\x1c', '\x1d', '\x1e', '\x1f']
        for c in control_chars:
            if c in normalized:
                return (False, f"路径包含控制字符: {repr(c)}", "")

        # 检查命令注入字符（除了路径分隔符和~以外的shell元字符）
        # 注意：单引号和反斜杠可以出现在文件名中，但需要转义
        dangerous_patterns = ['$(', '`', '${', '||', '&&', ';', '\n', '\r']
        for pattern in dangerous_patterns:
            if pattern in normalized:
                return (False, f"路径包含危险字符: {repr(pattern)}", "")

        return (True, "", normalized)

    def resolve_frontend_path(self, frontend_path: str, ssh_client: paramiko.SSHClient = None,
                              default_path: str = None) -> tuple:
        """
        解析前端传来的路径（纯函数逻辑，不使用缓存存储）

        设计原则：
        1. 后端不存储路径，不做缓存
        2. 每次调用都基于输入参数独立解析
        3. 优先使用前端传来的路径
        4. SSH验证仅用于获取真实路径，不用于持久化

        Args:
            frontend_path: 前端发送的当前路径
            ssh_client: SSH客户端（可选，用于解析 ~ 和验证路径）
            default_path: 默认路径（当前端路径无效时使用）

        Returns:
            tuple: (resolved_path: str, is_valid: bool, error_message: str)
        """
        # 如果没有提供默认路径，使用根目录
        if default_path is None:
            default_path = '/'

        # 验证路径格式
        is_valid, error_msg, normalized_path = self.validate_path_format(frontend_path)
        if not is_valid:
            print(f"[PATH-RESOLVE] 路径格式无效: {error_msg}")
            return (default_path, False, error_msg)

        frontend_path = normalized_path

        # 处理 ~ 路径（需要SSH客户端解析）
        if frontend_path == '~' or frontend_path.startswith('~/'):
            if ssh_client:
                try:
                    stdin, stdout, stderr = ssh_client.exec_command("echo $HOME", timeout=2)
                    home_dir = stdout.read().decode('utf-8', errors='ignore').strip()
                    stderr.read()  # 消费错误输出

                    if home_dir and home_dir.startswith('/'):
                        if frontend_path == '~':
                            print(f"[PATH-RESOLVE] ~ 解析为: {home_dir}")
                            return (home_dir, True, "")
                        else:
                            # ~/subdir -> /home/user/subdir
                            expanded = home_dir + frontend_path[1:]
                            print(f"[PATH-RESOLVE] {frontend_path} 展开为: {expanded}")
                            frontend_path = expanded
                    else:
                        print(f"[PATH-RESOLVE] 获取HOME失败，使用默认路径")
                        return (default_path, False, "无法获取HOME目录")
                except Exception as e:
                    print(f"[PATH-RESOLVE] 获取HOME异常: {e}")
                    return (default_path, False, f"获取HOME异常: {e}")
            else:
                # 没有SSH客户端，无法解析 ~ 路径
                print(f"[PATH-RESOLVE] 无SSH客户端，无法解析 ~ 路径")
                return (default_path, False, "无SSH客户端，无法解析~路径")

        # 处理绝对路径
        if frontend_path.startswith('/'):
            if ssh_client:
                try:
                    safe_path = self.escape_shell_path(frontend_path)
                    check_cmd = f"cd '{safe_path}' 2>/dev/null && pwd"
                    stdin, stdout, stderr = ssh_client.exec_command(check_cmd, timeout=2)
                    real_path = stdout.read().decode('utf-8', errors='ignore').strip()
                    error = stderr.read().decode('utf-8', errors='ignore').strip()

                    if real_path and not error and real_path.startswith('/'):
                        print(f"[PATH-RESOLVE] 绝对路径验证成功: {real_path}")
                        return (real_path, True, "")
                    else:
                        print(f"[PATH-RESOLVE] 绝对路径无效: {frontend_path}")
                        return (default_path, False, f"路径不存在或无法访问: {frontend_path}")
                except Exception as e:
                    print(f"[PATH-RESOLVE] 绝对路径验证异常: {e}")
                    # 验证异常时信任前端路径格式
                    return (frontend_path, True, f"验证异常但信任格式: {e}")
            else:
                # 没有SSH客户端，信任前端绝对路径格式
                print(f"[PATH-RESOLVE] 无SSH客户端，信任绝对路径: {frontend_path}")
                return (frontend_path, True, "")

        # 相对路径需要基准目录，但我们不使用缓存，所以需要默认路径
        if ssh_client:
            try:
                safe_default = self.escape_shell_path(default_path)
                safe_frontend = self.escape_shell_path(frontend_path)

                if default_path and default_path != '~' and default_path.startswith('/'):
                    resolve_cmd = f"cd '{safe_default}' && cd '{safe_frontend}' 2>/dev/null && pwd"
                else:
                    resolve_cmd = f"cd '{safe_frontend}' 2>/dev/null && pwd"

                stdin, stdout, stderr = ssh_client.exec_command(resolve_cmd, timeout=2)
                real_path = stdout.read().decode('utf-8', errors='ignore').strip()
                stderr.read()  # 消费错误输出

                if real_path and real_path.startswith('/'):
                    print(f"[PATH-RESOLVE] 相对路径解析: {frontend_path} -> {real_path}")
                    return (real_path, True, "")
                else:
                    print(f"[PATH-RESOLVE] 相对路径解析失败: {frontend_path}")
                    return (default_path, False, f"相对路径无效: {frontend_path}")
            except Exception as e:
                print(f"[PATH-RESOLVE] 相对路径解析异常: {e}")
                return (default_path, False, f"相对路径解析异常: {e}")

        # 没有SSH客户端且是相对路径，无法解析
        print(f"[PATH-RESOLVE] 无SSH客户端，无法解析相对路径: {frontend_path}")
        return (default_path, False, "无SSH客户端，无法解析相对路径")

    def sync_cwd_from_frontend(self, session_id: str, frontend_path: str, ssh_client: paramiko.SSHClient = None, skip_validation: bool = False) -> str:
        """
        从前端同步当前工作目录
        处理前端发送的 currentPath 参数

        注意：此方法为兼容性保留，内部使用 resolve_frontend_path 纯函数
        后端不主动存储路径，仅在必要时更新缓存作为回退

        Args:
            session_id: 会话ID
            frontend_path: 前端发送的当前路径
            ssh_client: SSH客户端（可选，用于验证路径）
            skip_validation: 是否跳过 SSH 验证（用于 cd 命令，避免重复验证）

        Returns:
            str: 解析后的有效路径
        """
        # 获取后端缓存路径作为默认值（仅用于回退）
        backend_path = self.get_cwd(session_id)

        # 如果前端路径为空或无效，返回后端缓存路径
        if not frontend_path or not frontend_path.strip():
            print(f"[CWD-SYNC] 前端路径为空，使用后端路径: {backend_path}")
            return backend_path

        # 规范化前端路径
        frontend_path = frontend_path.strip()

        # 如果跳过验证（如cd命令），直接处理绝对路径
        if skip_validation:
            is_valid, error_msg, normalized = self.validate_path_format(frontend_path)
            if is_valid and normalized.startswith('/'):
                print(f"[CWD-SYNC] 跳过验证，使用前端绝对路径: {normalized}")
                return normalized
            elif is_valid and (normalized == '~' or normalized.startswith('~/')):
                # ~ 路径需要展开
                if ssh_client:
                    try:
                        stdin, stdout, stderr = ssh_client.exec_command("echo $HOME", timeout=2)
                        home_dir = stdout.read().decode('utf-8', errors='ignore').strip()
                        stderr.read()
                        if home_dir and home_dir.startswith('/'):
                            if normalized == '~':
                                print(f"[CWD-SYNC] ~ 展开为: {home_dir}")
                                return home_dir
                            else:
                                expanded = home_dir + normalized[1:]
                                print(f"[CWD-SYNC] {normalized} 展开为: {expanded}")
                                return expanded
                    except Exception as e:
                        print(f"[CWD-SYNC] 展开~失败: {e}")
                # 无法展开，返回原始值
                return normalized if is_valid else backend_path
            elif is_valid:
                # 相对路径，跳过验证时直接返回
                print(f"[CWD-SYNC] 跳过验证，使用前端路径: {normalized}")
                return normalized

        # 使用纯函数解析路径
        resolved_path, is_valid, error_msg = self.resolve_frontend_path(
            frontend_path, ssh_client, backend_path
        )

        if is_valid:
            print(f"[CWD-SYNC] 路径解析成功: {frontend_path} -> {resolved_path}")
        else:
            print(f"[CWD-SYNC] 路径解析失败: {error_msg}，使用: {resolved_path}")

        return resolved_path

    def get_username(self, session_id: str) -> str:
        return "root"

    def sync_current_directory(self, session_id: str, ssh_client: paramiko.SSHClient) -> str:
        """同步当前工作目录"""
        if not ssh_client:
            return self.cwd_cache.get(session_id, '~')

        try:
            # 获取HOME
            stdin, stdout, stderr = ssh_client.exec_command("echo $HOME", timeout=3)
            home_dir = stdout.read().decode('utf-8', errors='ignore').strip()
            error_output = stderr.read().decode('utf-8', errors='ignore').strip()

            if home_dir and not error_output:
                with self.lock:
                    self.home_dir_cache[session_id] = home_dir
                print(f"[HOME] 同步: {home_dir}")

            # 获取CWD
            stdin, stdout, stderr = ssh_client.exec_command("pwd", timeout=3)
            real_cwd = stdout.read().decode('utf-8', errors='ignore').strip()
            error_output = stderr.read().decode('utf-8', errors='ignore').strip()

            if real_cwd and not error_output:
                with self.lock:
                    self.cwd_cache[session_id] = real_cwd
                print(f"[CWD] 同步: {real_cwd}")
                return real_cwd
            else:
                print(f"[CWD] 同步失败: {error_output}")
                return self.cwd_cache.get(session_id, '~')
        except Exception as e:
            print(f"[CWD] 同步异常: {e}")
            return self.cwd_cache.get(session_id, '~')

    def get_file_color_info(self, filename: str, file_type: str, is_executable: bool, is_base: bool) -> dict:
        """
        获取文件颜色信息
        修复P1: 完善LS命令颜色输出
        """
        color_info = {
            "color_class": "file",
            "ansi_color": "\x1b[0m",  # 默认白色
            "css_color": "#ffffff"
        }

        # 隐藏文件 - 灰色
        if filename.startswith('.'):
            color_info.update({
                "color_class": "hidden",
                "ansi_color": "\x1b[90m",  # 暗灰色
                "css_color": "#808080"
            })
            return color_info

        ext = filename.split('.')[-1].lower() if '.' in filename else ""

        # 压缩文件 - 红色
        if ext in ['zip', 'tar', 'gz', 'bz2', 'xz', '7z', 'rar', 'tgz', 'tbz']:
            color_info.update({
                "color_class": "compressed",
                "ansi_color": "\x1b[91m",  # 亮红色
                "css_color": "#ff6b6b"
            })
            return color_info

        # 图片文件 - 紫色
        if ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'ico', 'webp', 'tiff']:
            color_info.update({
                "color_class": "image",
                "ansi_color": "\x1b[95m",  # 紫色
                "css_color": "#cc99ff"
            })
            return color_info

        # 代码文件 - 青色
        if ext in ['py', 'js', 'java', 'cpp', 'c', 'h', 'php', 'rb', 'go', 'rs', 'ts', 'jsx', 'tsx', 'vue']:
            color_info.update({
                "color_class": "code",
                "ansi_color": "\x1b[96m",  # 青色
                "css_color": "#51cf66"
            })
            return color_info

        # 文档文件 - 蓝色
        if ext in ['pdf', 'doc', 'docx', 'txt', 'md', 'rst', 'odt']:
            color_info.update({
                "color_class": "document",
                "ansi_color": "\x1b[94m",  # 亮蓝色
                "css_color": "#74c0fc"
            })
            return color_info

        # 目录 - 蓝色加粗
        if file_type == "directory":
            color_info.update({
                "color_class": "directory",
                "ansi_color": "\x1b[34;1m",  # 蓝色加粗
                "css_color": "#339af0"
            })
        # 可执行文件 - 绿色加粗
        elif is_executable:
            color_info.update({
                "color_class": "executable",
                "ansi_color": "\x1b[32;1m",  # 绿色加粗
                "css_color": "#51cf66"
            })
        # BASE环境 - 黄色
        elif is_base:
            color_info.update({
                "color_class": "base",
                "ansi_color": "\x1b[33;1m",  # 黄色加粗
                "css_color": "#ffd43b"
            })
        # 符号链接 - 青色
        elif file_type == "symlink":
            color_info.update({
                "color_class": "symlink",
                "ansi_color": "\x1b[36;1m",  # 青色加粗
                "css_color": "#22d3ee"
            })
        # 特殊文件 - 紫色
        elif file_type in ["socket", "pipe", "block", "char"]:
            color_info.update({
                "color_class": "special",
                "ansi_color": "\x1b[35m",  # 紫色
                "css_color": "#cc5de8"
            })

        return color_info

    def get_ls_file_info(self, ssh_client, filename: str, current_dir: str) -> dict:
        """获取文件详细信息(包含颜色)"""
        try:
            stat_cmd = f"stat -c '%F|%a|%A' '{filename}'"
            stdin, stdout, stderr = ssh_client.exec_command(f"cd '{current_dir}' && {stat_cmd}", timeout=5)
            stat_output = stdout.read().decode('utf-8', errors='ignore').strip()

            if not stat_output:
                # 使用ls -ld作为备选
                ls_cmd = f"ls -ld '{filename}'"
                stdin, stdout, stderr = ssh_client.exec_command(f"cd '{current_dir}' && {ls_cmd}", timeout=5)
                ls_output = stdout.read().decode('utf-8', errors='ignore').strip()

                if ls_output:
                    parts = ls_output.split()
                    if len(parts) >= 9:
                        permissions = parts[0]
                        file_type = "directory" if permissions.startswith('d') else "file"
                        is_executable = 'x' in permissions
                        is_base = filename.lower() in ['base', 'miniconda', 'conda', 'anaconda']

                        color_info = self.get_file_color_info(filename, file_type, is_executable, is_base)

                        return {
                            "name": filename,
                            "type": file_type,
                            "permissions": permissions[1:],
                            "is_executable": is_executable,
                            "is_base": is_base,
                            "color_info": color_info
                        }

                # 默认值
                color_info = self.get_file_color_info(filename, "file", False, False)
                return {
                    "name": filename,
                    "type": "file",
                    "permissions": "----------",
                    "is_executable": False,
                    "is_base": False,
                    "color_info": color_info
                }

            # 解析stat输出
            file_type_str, octal_perms, symbolic_perms = stat_output.split('|')

            file_type = "file"
            if "directory" in file_type_str.lower():
                file_type = "directory"
            elif "symbolic link" in file_type_str.lower():
                file_type = "symlink"
            elif "socket" in file_type_str.lower():
                file_type = "socket"
            elif "fifo" in file_type_str.lower():
                file_type = "pipe"
            elif "block device" in file_type_str.lower():
                file_type = "block"
            elif "character device" in file_type_str.lower():
                file_type = "char"

            is_executable = 'x' in symbolic_perms
            is_base = filename.lower() in ['base', 'miniconda', 'conda', 'anaconda']

            color_info = self.get_file_color_info(filename, file_type, is_executable, is_base)

            return {
                "name": filename,
                "type": file_type,
                "permissions": symbolic_perms,
                "is_executable": is_executable,
                "is_base": is_base,
                "color_info": color_info
            }

        except Exception as e:
            print(f"[LS] 获取文件信息失败 {filename}: {e}")
            color_info = self.get_file_color_info(filename, "file", False, False)
            return {
                "name": filename,
                "type": "file",
                "permissions": "----------",
                "is_executable": False,
                "is_base": False,
                "color_info": color_info
            }

    def format_ls_multicolumn(self, files, terminal_width=80):
        """格式化文件列表为多列显示"""
        if not files:
            return {
                "columns": 1,
                "rows": [],
                "column_width": 10
            }

        max_name_length = max(len(f['name']) for f in files)
        column_width = max_name_length + 2
        num_columns = max(1, (terminal_width - 10) // column_width)

        rows = []
        files_per_row = (len(files) + num_columns - 1) // num_columns

        for i in range(files_per_row):
            start_idx = i * num_columns
            end_idx = min(start_idx + num_columns, len(files))
            row_files = files[start_idx:end_idx]
            rows.append(row_files)

        return {
            "columns": num_columns,
            "rows": rows,
            "column_width": column_width,
            "total_files": len(files)
        }

    def process_ls_structured(self, ssh_client, command: str, session_id: str, current_dir: str, terminal_width=80) -> dict:
        """处理ls命令,返回结构化数据(支持颜色)"""
        try:
            ls_match = re.match(r'^\s*ls\s*(.*)$', command)
            ls_args = ls_match.group(1) if ls_match else ""

            ls_cmd = f"ls -1 {ls_args}".strip()
            combined_ls_cmd = f"set -e && cd '{current_dir}' && {ls_cmd}"
            print(f"[LS] 执行命令: {combined_ls_cmd} (当前目录: {current_dir})")
            stdin, stdout, stderr = ssh_client.exec_command(combined_ls_cmd, timeout=5)
            ls_output = stdout.read().decode('utf-8', errors='ignore').strip()
            error_output = stderr.read().decode('utf-8', errors='ignore').strip()

            print(f"[LS] 输出长度: {len(ls_output)}, 错误: {error_output[:100] if error_output else 'None'}")

            if not ls_output and error_output:
                print(f"[LS] 执行失败: {error_output}")
                return None
            elif not ls_output:
                print(f"[LS] 空目录 (current_dir={current_dir})")
                with self.lock:
                    home_dir = self.home_dir_cache.get(session_id, '/root')

                if current_dir == home_dir:
                    display_dir = '~'
                elif current_dir.startswith(home_dir + '/'):
                    display_dir = '~' + current_dir[len(home_dir):]
                else:
                    display_dir = current_dir

                prompt = f"(base) root@VM-0-15-ubuntu:{display_dir}# "

                return {
                    "type": "ls_output",
                    "data": {
                        "files": [],
                        "layout": {
                            "columns": 1,
                            "rows": [],
                            "column_width": 10,
                            "total_files": 0
                        },
                        "prompt": prompt,
                        "currentPath": current_dir
                    }
                }

            files = [f.strip() for f in ls_output.split('\n') if f.strip()]
            print(f"[LS] 找到 {len(files)} 个文件")

            file_info_list = []
            for filename in files:
                file_info = self.get_ls_file_info(ssh_client, filename, current_dir)
                file_info_list.append(file_info)

            multicolumn_info = self.format_ls_multicolumn(file_info_list, terminal_width)

            with self.lock:
                home_dir = self.home_dir_cache.get(session_id, '/root')

            if current_dir == home_dir:
                display_dir = '~'
            elif current_dir.startswith(home_dir + '/'):
                display_dir = '~' + current_dir[len(home_dir):]
            else:
                display_dir = current_dir

            prompt = f"(base) root@VM-0-15-ubuntu:{display_dir}# "

            return {
                "type": "ls_output",
                "data": {
                    "files": file_info_list,
                    "layout": multicolumn_info,
                    "prompt": prompt,
                    "currentPath": current_dir
                }
            }

        except Exception as e:
            print(f"[LS] 处理失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def add_command_to_history(self, session_id: str, command: str):
        """
        添加命令到历史记录
        修复P1: 扩展到100条,使用deque自动限制大小
        """
        with self.lock:
            cleaned_command = command.strip()

            # 清理提示符
            for char in ['#', '$', '>']:
                if char in cleaned_command:
                    cleaned_command = cleaned_command.split(char, 1)[-1].strip()
                    break

            if not cleaned_command:
                return

            if session_id not in self.command_history:
                # 使用deque,maxlen自动限制大小
                self.command_history[session_id] = deque(maxlen=self.MAX_HISTORY_SIZE)

            # 避免重复
            if not self.command_history[session_id] or self.command_history[session_id][-1] != cleaned_command:
                self.command_history[session_id].append(cleaned_command)
                print(f"[HISTORY] 添加命令: {cleaned_command} (总数: {len(self.command_history[session_id])})")

    def get_history_command(self, session_id: str, direction: str, current_index: int) -> dict:
        """获取历史命令"""
        with self.lock:
            history = list(self.command_history.get(session_id, deque()))
            max_index = len(history) - 1

            if not history:
                return {"command": "", "index": -1}

            if direction == "up":
                new_index = current_index - 1 if current_index > 0 else max_index
            elif direction == "down":
                new_index = current_index + 1 if current_index < max_index else -1
            else:
                return {"command": "", "index": current_index}

            command = history[new_index] if new_index >= 0 else ""
            return {"command": command, "index": new_index}

    def connect_ssh(self, connection: SSHConnection) -> paramiko.SSHClient:
        session_id = self.generate_session_id(connection)

        with self.lock:
            if session_id in self.sessions:
                return self.sessions[session_id]

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            try:
                if connection.password:
                    ssh.connect(
                        hostname=connection.hostname,
                        port=connection.port,
                        username=connection.username,
                        password=connection.password,
                        timeout=10  # 修复P0: 优化超时时间
                    )
                elif connection.key_file:
                    ssh.connect(
                        hostname=connection.hostname,
                        port=connection.port,
                        username=connection.username,
                        key_filename=connection.key_file,
                        timeout=10
                    )
                else:
                    raise ValueError("Either password or key_file must be provided")

                self.sessions[session_id] = ssh
                return ssh

            except Exception as e:
                raise Exception(f"SSH连接失败: {str(e)}")

    def disconnect_ssh(self, session_id: str):
        with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id].close()
                del self.sessions[session_id]
            if session_id in self.websocket_connections:
                del self.websocket_connections[session_id]

    def register_websocket(self, session_id: str, websocket: WebSocket):
        with self.lock:
            self.websocket_connections[session_id] = websocket

    def unregister_websocket(self, session_id: str):
        with self.lock:
            if session_id in self.websocket_connections:
                del self.websocket_connections[session_id]

# 创建FastAPI应用
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ssh_manager = SSHSessionManager()
    print("[STARTUP] SSH Manager initialized")
    yield
    with app.state.ssh_manager.lock:
        for ssh in app.state.ssh_manager.sessions.values():
            ssh.close()
    print("[SHUTDOWN] All SSH connections closed")

app = FastAPI(title="SSH WebSocket工具 (优化版)", lifespan=lifespan)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws/ssh")
async def websocket_ssh_endpoint(websocket: WebSocket):
    """
    WebSocket SSH终端端点 (优化版)
    修复了所有P0和P1问题
    """
    await websocket.accept()

    session_id = None
    ssh_client = None
    channel = None

    try:
        # 接收连接信息
        connection_data = await websocket.receive_text()
        print(f"[CONN] 接收连接数据: {connection_data}")

        connection_info = json.loads(connection_data)

        # 验证连接信息
        if "type" not in connection_info:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "消息缺少type字段"
            }))
            return

        if connection_info["type"] != "connect":
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"首次消息必须是连接类型,当前类型: {connection_info['type']}"
            }))
            return

        if "data" not in connection_info:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "连接信息缺少data字段"
            }))
            return

        connection = SSHConnection(**connection_info["data"])
        session_id = app.state.ssh_manager.generate_session_id(connection)
        print(f"[CONN] 会话ID: {session_id}")

        # 建立SSH连接
        print(f"[CONN] 连接: {connection.username}@{connection.hostname}:{connection.port}")
        ssh_client = app.state.ssh_manager.connect_ssh(connection)
        app.state.ssh_manager.register_websocket(session_id, websocket)
        print(f"[CONN] SSH连接成功")

        # 创建shell通道
        channel = ssh_client.invoke_shell(term='xterm', width=connection.width, height=connection.height)
        channel.settimeout(1.0)
        print(f"[CONN] Shell通道创建成功")

        # 同步当前工作目录
        app.state.ssh_manager.sync_current_directory(session_id, ssh_client)

        # 发送连接成功消息
        await websocket.send_text(json.dumps({
            "type": "connected",
            "session_id": session_id,
            "message": "SSH连接成功"
        }))

        # 兼容性: 同时发送connect类型
        await websocket.send_text(json.dumps({
            "type": "connect",
            "session_id": session_id,
            "message": "SSH连接成功"
        }))

        # 输出处理
        output_paused = asyncio.Event()
        output_paused.set()
        output_buffer = ""
        last_output_time = 0
        OUTPUT_MERGE_TIMEOUT = 0.05

        async def receive_ssh_output():
            nonlocal output_buffer, last_output_time
            while True:
                try:
                    if channel.recv_ready() and output_paused.is_set():
                        data = channel.recv(1024).decode('utf-8', errors='ignore')
                        if data:
                            output_buffer += data
                            last_output_time = time.time()

                    # 修复：移除 is_expecting_pwd 的阻塞逻辑
                    # cd 命令现在通过后台 exec_command 获取 pwd，不会干扰用户输入

                    current_time = time.time()
                    if output_buffer and (current_time - last_output_time > OUTPUT_MERGE_TIMEOUT):
                        data = output_buffer
                        output_buffer = ""

                        # 过滤命令回显
                        nonlocal last_sent_command
                        if last_sent_command:
                            cmd_pattern = re.escape(last_sent_command) + r'(?:\x1b\[[0-9;]*[a-zA-Z])*\r\n'
                            if re.search(cmd_pattern, data):
                                data = re.sub(cmd_pattern, '', data)
                                last_sent_command = None

                        # 过滤系统状态行
                        try:
                            ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
                            stripped = ansi.sub('', data)
                            lines = data.replace('\r\n', '\n').split('\n')
                            stripped_lines = stripped.replace('\r\n', '\n').split('\n')
                            filtered = []
                            drop_prefixes = ['Memory usage:', 'IPv4 address for ', 'System load:']
                            for orig, s in zip(lines, stripped_lines):
                                s = s.strip()
                                if not s:
                                    filtered.append(orig)
                                    continue
                                if any(s.startswith(dp) for dp in drop_prefixes):
                                    continue
                                filtered.append(orig)
                            data = '\n'.join(filtered)
                        except Exception:
                            pass

                        # 清理ANSI序列
                        try:
                            data_for_send = data
                            data_for_send = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", data_for_send)
                            data_for_send = re.sub(r"\x1b\[\?2004[hl]", "", data_for_send)
                            data_for_send = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", data_for_send)
                            data_for_send = data_for_send.replace("\r\n", "\n").replace("\r", "\n")
                        except Exception:
                            data_for_send = data

                        # 获取当前真实路径
                        current_path = app.state.ssh_manager.get_cwd(session_id)
                        await websocket.send_text(json.dumps({
                            "type": "output",
                            "data": {
                                "output": data_for_send,
                                "currentPath": current_path
                            }
                        }))
                    await asyncio.sleep(0.01)
                except:
                    break

        # 初始化变量
        last_sent_command = None
        # 移除 is_expecting_pwd 和 pwd_wait_start_time，不再需要

        # 启动接收任务
        receive_task = asyncio.create_task(receive_ssh_output())

        # 处理客户端消息
        while True:
            try:
                message_data = await websocket.receive_text()
                message = json.loads(message_data)

                if message["type"] == "command":
                    command = message["data"]["command"]
                    command = command.rstrip('\r\n')

                    # 处理前端发送的 currentPath 参数
                    # 对于 cd 命令，跳过 SSH 验证（cd 命令会自己更新路径，避免重复验证）
                    frontend_path = message["data"].get("currentPath", "")
                    is_cd_command = command.strip().startswith('cd ') or command.strip() == 'cd'

                    # 始终同步前端路径，确保后端缓存与前端一致
                    synced_cwd = app.state.ssh_manager.sync_cwd_from_frontend(
                        session_id, frontend_path, ssh_client,
                        skip_validation=is_cd_command  # cd 命令跳过验证
                    )
                    print(f"[CMD] 前端路径: {frontend_path}, 同步后: {synced_cwd}, cd命令: {is_cd_command}")

                    app.state.ssh_manager.add_command_to_history(session_id, command)

                    # ls命令结构化处理
                    # 修复：确保所有 ls 命令都返回 ls_output 类型，即使处理失败也返回空结果
                    try:
                        simple_ls = re.match(r"^\s*ls(\s|$)", command) is not None
                        has_ops = any(op in command for op in ['|', ';', '&&', '||'])

                        if simple_ls and not has_ops:
                            # 使用同步后的路径，确保与前端一致
                            current_dir = synced_cwd
                            print(f"[LS] 使用同步后的目录: {current_dir} (session_id: {session_id})")
                            terminal_width = 80
                            ls_structured = app.state.ssh_manager.process_ls_structured(
                                ssh_client, command, session_id, current_dir, terminal_width
                            )

                            if ls_structured:
                                await websocket.send_text(json.dumps(ls_structured))
                                continue
                            else:
                                # 修复：如果结构化处理返回 None，仍然返回空的 ls_output
                                home_dir = app.state.ssh_manager.home_dir_cache.get(session_id, '/root')
                                if current_dir == home_dir:
                                    display_dir = '~'
                                elif current_dir.startswith(home_dir + '/'):
                                    display_dir = '~' + current_dir[len(home_dir):]
                                else:
                                    display_dir = current_dir

                                prompt = f"(base) root@VM-0-15-ubuntu:{display_dir}# "
                                await websocket.send_text(json.dumps({
                                    "type": "ls_output",
                                    "data": {
                                        "files": [],
                                        "layout": {
                                            "columns": 1,
                                            "rows": [],
                                            "column_width": 10,
                                            "total_files": 0
                                        },
                                        "prompt": prompt,
                                        "currentPath": current_dir
                                    }
                                }))
                                continue
                    except Exception as e:
                        print(f"[LS] 结构化失败: {e}")
                        # 修复：即使出现异常，也返回 ls_output 类型的空结果，而不是回退到普通处理
                        try:
                            simple_ls = re.match(r"^\s*ls(\s|$)", command) is not None
                            has_ops = any(op in command for op in ['|', ';', '&&', '||'])
                            if simple_ls and not has_ops:
                                # 使用同步后的路径
                                current_dir = synced_cwd
                                home_dir = app.state.ssh_manager.home_dir_cache.get(session_id, '/root')
                                if current_dir == home_dir:
                                    display_dir = '~'
                                elif current_dir.startswith(home_dir + '/'):
                                    display_dir = '~' + current_dir[len(home_dir):]
                                else:
                                    display_dir = current_dir

                                prompt = f"(base) root@VM-0-15-ubuntu:{display_dir}# "
                                await websocket.send_text(json.dumps({
                                    "type": "ls_output",
                                    "data": {
                                        "files": [],
                                        "layout": {
                                            "columns": 1,
                                            "rows": [],
                                            "column_width": 10,
                                            "total_files": 0
                                        },
                                        "prompt": prompt,
                                        "error": str(e),
                                        "currentPath": current_dir
                                    }
                                }))
                                continue
                        except:
                            pass

                    # 普通ls处理（仅在非简单ls命令时执行，如 ls -la | grep xxx）
                    try:
                        simple_ls = re.match(r"^\s*ls(\s|$)", command) is not None
                        has_ops = any(op in command for op in ['|', ';', '&&', '||'])
                        if simple_ls and not has_ops:
                            tail = command[len(command.split('ls', 1)[0]) + 2:] if 'ls' in command else ''
                            has_long = re.search(r"(^|\s)-[^\s]*l", tail) is not None
                            has_single = ('-1' in tail) or ('--format=single-column' in tail)
                            if not has_long and not has_single:
                                command = re.sub(r"^\s*ls", "ls -1 --color=never", command, count=1)
                    except Exception:
                        pass

                    last_sent_command = command

                    # cd命令特殊处理
                    # 修复：不要通过 channel.send pwd，会干扰用户输入（Tab/Ctrl）
                    # 改为通过 exec_command 在后台获取 pwd，不影响用户操作
                    if command.strip().startswith('cd ') or command.strip() == 'cd':
                        channel.send(command + "\n")
                        # 不再发送 pwd 到channel，改为后台执行
                        # channel.send("pwd\n")  # 删除：这会干扰用户输入

                        # 后台获取当前目录，不干扰用户输入
                        real_cwd = None
                        try:
                            # 提取 cd 的目标路径
                            cd_parts = command.strip().split(maxsplit=1)
                            if len(cd_parts) > 1:
                                target = cd_parts[1]
                            else:
                                target = '~'

                            # 使用前端同步的路径作为基础目录
                            base_dir = synced_cwd

                            # 构建后台执行命令，需要考虑基础目录
                            # 逻辑：先切换到基础目录，再执行cd，成功返回新路径，失败返回基础目录
                            if target.startswith('/'):
                                # 绝对路径：直接cd到目标，失败则返回基础目录
                                safe_base = app.state.ssh_manager.escape_shell_path(base_dir)
                                pwd_cmd = f"cd {target} 2>/dev/null && pwd || echo '{safe_base}'"
                            elif target == '~' or target.startswith('~/'):
                                # home路径：直接cd，失败则返回基础目录
                                safe_base = app.state.ssh_manager.escape_shell_path(base_dir)
                                pwd_cmd = f"cd {target} 2>/dev/null && pwd || echo '{safe_base}'"
                            elif base_dir and base_dir.startswith('/'):
                                # 相对路径 + 绝对基础目录：先切换到基础目录再cd
                                safe_base = app.state.ssh_manager.escape_shell_path(base_dir)
                                # 先cd到基础目录，然后尝试cd到目标
                                # 成功则pwd返回新目录，失败则pwd返回基础目录
                                pwd_cmd = f"cd '{safe_base}' && (cd {target} 2>/dev/null && pwd || pwd)"
                            else:
                                # 其他情况（如基础目录是~）
                                pwd_cmd = f"cd {target} 2>/dev/null && pwd || pwd"

                            print(f"[CWD] 执行命令: {pwd_cmd} (base_dir={base_dir}, target={target})")
                            stdin, stdout, stderr = ssh_client.exec_command(pwd_cmd, timeout=2)
                            real_cwd = stdout.read().decode('utf-8', errors='ignore').strip()

                            if real_cwd and real_cwd.startswith('/'):
                                app.state.ssh_manager.cwd_cache[session_id] = real_cwd
                                print(f"[CWD] 后台更新: {real_cwd}")
                        except Exception as e:
                            print(f"[CWD] 后台更新失败: {e}")
                            # 失败时使用本地逻辑更新
                            app.state.ssh_manager.update_cwd(session_id, command, ssh_client)
                            real_cwd = app.state.ssh_manager.get_cwd(session_id)

                        # 发送 cd_result 响应，包含新的 currentPath
                        if real_cwd:
                            await websocket.send_text(json.dumps({
                                "type": "cd_result",
                                "data": {
                                    "currentPath": real_cwd
                                }
                            }))
                    else:
                        channel.send(command + "\n")
                        app.state.ssh_manager.update_cwd(session_id, command, ssh_client)

                elif message["type"] == "resize":
                    # 修复P0: Resize节流 + 修复：resize时暂停输出并清空缓冲区
                    if "data" in message and isinstance(message["data"], dict):
                        width = message["data"].get("width")
                        height = message["data"].get("height")
                        if width and height and channel:
                            # 使用节流器判断是否执行
                            if app.state.ssh_manager.resize_throttler.should_execute(session_id, width, height):
                                # 暂停输出接收
                                output_paused.clear()
                                try:
                                    channel.resize_pty(width=width, height=height)
                                    print(f"[RESIZE] 执行: {width}x{height}")
                                    # 等待并清空resize触发的任何输出
                                    await asyncio.sleep(0.05)
                                    while channel.recv_ready():
                                        channel.recv(4096)  # 丢弃resize触发的输出
                                finally:
                                    # 恢复输出接收
                                    output_paused.set()
                            else:
                                print(f"[RESIZE] 节流: {width}x{height}")

                elif message["type"] == "tab_complete":
                    # TAB补全
                    context_command = ""
                    if "data" in message and isinstance(message["data"], dict) and "command" in message["data"]:
                        context_command = message["data"]["command"]

                    if "data" in message and isinstance(message["data"], dict):
                        output_paused.clear()
                        try:
                            # 处理前端发送的 currentPath 参数
                            frontend_path = message["data"].get("currentPath", "")
                            if frontend_path:
                                cwd = app.state.ssh_manager.sync_cwd_from_frontend(
                                    session_id, frontend_path, ssh_client
                                )
                                print(f"[TAB] 前端路径: {frontend_path}, 同步后: {cwd}")
                            else:
                                cwd = app.state.ssh_manager.get_cwd(session_id)

                            if not context_command or not context_command.strip():
                                args = []
                                last_word = ""
                                is_command_completion = True
                            else:
                                args = context_command.split()
                                if context_command.endswith(" "):
                                    last_word = ""
                                else:
                                    last_word = args[-1] if args else ""
                                is_command_completion = len(args) <= 1 and not context_command.endswith(" ")

                            completions = []
                            err_data = ""

                            if is_command_completion:
                                completion_script = f"compgen -c {last_word}"
                                stdin, stdout, stderr = ssh_client.exec_command(f"bash -c '{completion_script}'", timeout=5)
                                out_data = stdout.read().decode('utf-8', errors='ignore')
                                completions = [c.strip() for c in out_data.split('\n') if c.strip()]
                            else:
                                ls_cmd = "ls -1F --color=never"
                                if cwd != '~' and cwd:
                                    # 使用安全转义的路径
                                    safe_cwd = app.state.ssh_manager.escape_shell_path(cwd)
                                    ls_cmd = f"cd '{safe_cwd}' && {ls_cmd}"

                                print(f"[TAB] 补全列表: {ls_cmd}")
                                stdin, stdout, stderr = ssh_client.exec_command(ls_cmd, timeout=5)

                                out_raw = stdout.read().decode('utf-8', errors='ignore')
                                err_data = stderr.read().decode('utf-8', errors='ignore')

                                ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
                                out_data = ansi.sub('', out_raw)
                                all_files = [c.strip() for c in out_data.split('\n') if c.strip()]

                                if args and args[0] == 'cd':
                                    filtered = [f for f in all_files if f.startswith(last_word) and f.endswith('/')]
                                    completions = [f[:-1] for f in filtered]
                                else:
                                    filtered = [f for f in all_files if f.startswith(last_word)]
                                    completions = []
                                    for f in filtered:
                                        if f.endswith(('/', '*', '@', '|', '=')):
                                            completions.append(f[:-1])
                                        else:
                                            completions.append(f)

                            print(f"[TAB] 结果: {len(completions)} 个")

                            if not completions and not is_command_completion and args and args[0] == 'cd':
                                try:
                                    ls_root = "ls -1F --color=never /"
                                    stdin, stdout, stderr = ssh_client.exec_command(ls_root, timeout=5)
                                    out_root_raw = stdout.read().decode('utf-8', errors='ignore')
                                    out_root = ansi.sub('', out_root_raw)
                                    root_files = [c.strip() for c in out_root.split('\n') if c.strip()]
                                    filtered = [f for f in root_files if f.startswith(last_word) and f.endswith('/')]
                                    completions = [f[:-1] for f in filtered]
                                    print(f"[TAB] 根目录回退: {len(completions)} 个")
                                except Exception as _:
                                    pass

                            await websocket.send_text(json.dumps({
                                "type": "tab_completion_options",
                                "data": {
                                    "options": completions,
                                    "base": last_word,
                                    "path_prefix": cwd if not is_command_completion else "",
                                    "debug_error": err_data if not completions else ""
                                }
                            }))

                        except Exception as e:
                            print(f"[TAB] 失败: {e}")
                            await websocket.send_text(json.dumps({
                                "type": "tab_completion_options",
                                "data": {
                                    "options": [],
                                    "base": "",
                                    "error": str(e)
                                }
                            }))
                        finally:
                            await asyncio.sleep(0.1)
                            output_paused.set()
                    else:
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "tab_completion_options",
                                "data": {
                                    "options": [],
                                    "base": "",
                                    "path_prefix": app.state.ssh_manager.get_cwd(session_id),
                                    "debug_error": "Invalid message format"
                                }
                            }))
                        except Exception as e:
                            print(f"[TAB] 发送响应失败: {e}")

                elif message["type"] == "history_get":
                    # 历史命令请求
                    data = message["data"]
                    direction = data.get("direction", "up")
                    current_index = data.get("current_index", -1)
                    history_result = app.state.ssh_manager.get_history_command(session_id, direction, current_index)
                    await websocket.send_text(json.dumps({
                        "type": "history_result",
                        "data": history_result
                    }))

                elif message["type"] == "ctrl_command":
                    # 修复P1: 优化CTRL命令响应速度
                    ctrl_command = message["data"].get("command", "")
                    print(f"[CTRL] 命令: {ctrl_command}")

                    # 立即发送控制字符,不等待
                    ctrl_chars = {
                        "c": chr(3),   # CTRL+C
                        "d": chr(4),   # CTRL+D
                        "z": chr(26),  # CTRL+Z
                        "l": chr(12),  # CTRL+L
                        "a": chr(1),   # CTRL+A
                        "e": chr(5),   # CTRL+E
                        "k": chr(11),  # CTRL+K
                        "u": chr(21),  # CTRL+U
                    }

                    if ctrl_command in ctrl_chars:
                        channel.send(ctrl_chars[ctrl_command])
                        print(f"[CTRL] 发送: {ctrl_command}")

                elif message["type"] == "disconnect":
                    break

            except WebSocketDisconnect:
                break
            except Exception as e:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"处理消息时出错: {str(e)}"
                }))

    except Exception as e:
        error_msg = f"连接失败: {str(e)}"
        print(f"[ERROR] {error_msg}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": error_msg
            }))
        except Exception as send_error:
            print(f"[ERROR] 发送错误消息失败: {send_error}")
    finally:
        if 'receive_task' in locals() and receive_task:
            receive_task.cancel()
        if channel:
            try:
                channel.close()
            except Exception as e:
                print(f"[CLEANUP] 关闭通道失败: {e}")
        if session_id:
            try:
                app.state.ssh_manager.disconnect_ssh(session_id)
            except Exception as e:
                print(f"[CLEANUP] 断开SSH失败: {e}")
        try:
            await websocket.close()
        except Exception as close_error:
            if "Unexpected ASGI message" not in str(close_error):
                print(f"[CLEANUP] 关闭WebSocket失败: {close_error}")

@app.get("/")
async def root():
    """API首页"""
    return {
        "message": "SSH WebSocket工具API (优化版)",
        "version": "2.0.0 - 修复了P0/P1问题",
        "fixes": [
            "P0: Resize命令节流(300ms)",
            "P0: CWD不受Backspace影响",
            "P0: 优化Ctrl+C响应速度",
            "P1: 历史记录扩展到100条",
            "P1: 完善LS命令颜色",
            "P1: 优化所有CTRL快捷键"
        ],
        "websocket_endpoints": [
            "/ws/ssh - 实时SSH终端",
        ]
    }

@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok", "version": "2.0.0"}

if __name__ == "__main__":
    import uvicorn

    print("=" * 80)
    print("SSH WebSocket Server (优化版) - 修复了所有P0/P1问题")
    print("=" * 80)
    print("监听: http://0.0.0.0:8003")
    print("WebSocket: ws://0.0.0.0:8003/ws/ssh")
    print("=" * 80)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8003,
        ws_ping_timeout=None,
        ws_ping_interval=None,
    )
