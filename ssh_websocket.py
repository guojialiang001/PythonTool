from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict
import paramiko
import asyncio
import threading
import json
import time
from contextlib import asynccontextmanager

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

# SSH会话管理器
class SSHSessionManager:
    def __init__(self):
        self.sessions: Dict[str, paramiko.SSHClient] = {}
        self.websocket_connections: Dict[str, WebSocket] = {}
        self.command_history: Dict[str, list] = {}  # 存储每个会话的命令历史
        self.cwd_cache: Dict[str, str] = {} # 存储每个会话的当前工作目录（猜测值）
        self.lock = threading.Lock()
    
    def generate_session_id(self, connection: SSHConnection) -> str:
        return f"{connection.username}@{connection.hostname}:{connection.port}"
    
    def update_cwd(self, session_id: str, command: str, ssh_client: paramiko.SSHClient = None):
        """尝试从命令中更新当前工作目录"""
        with self.lock:
            # 简单的cd命令解析
            parts = command.strip().split()
            if not parts:
                return
                
            # 处理连续命令，如 cd /tmp && ls
            # 这里只做最简单的处理，假设命令以cd开头
            if parts[0] == 'cd' and len(parts) > 1:
                path = parts[1]
                # 忽略复杂的情况，如变量等
                if '$' in path or '`' in path:
                    return
                
                # 修复：增强版 - 总是尝试获取真实的当前目录
                if ssh_client:
                    try:
                        # 执行cd命令后立即获取真实的当前目录
                        # 使用组合命令：先cd，再pwd
                        combined_command = f"cd '{path}' && pwd"
                        stdin, stdout, stderr = ssh_client.exec_command(combined_command, timeout=5)
                        real_cwd = stdout.read().decode('utf-8', errors='ignore').strip()
                        error_output = stderr.read().decode('utf-8', errors='ignore').strip()
                        
                        if real_cwd and not error_output:
                            self.cwd_cache[session_id] = real_cwd
                            print(f"CWD真实更新: {real_cwd}")
                            return
                        elif error_output:
                            print(f"cd命令执行错误: {error_output}")
                    except Exception as e:
                        print(f"获取真实CWD失败: {e}")
                
                # 如果无法获取真实路径，使用本地逻辑推算
                current = self.cwd_cache.get(session_id, '~')
                
                if path.startswith('/'):
                    # 绝对路径
                    self.cwd_cache[session_id] = path
                elif path == '~':
                    self.cwd_cache[session_id] = '~'
                elif path == '..':
                    # 本地逻辑处理上一级目录
                    if current == '~':
                        # 从主目录返回，使用回退方案
                        self.cwd_cache[session_id] = '/home'
                    elif current == '/':
                        # 已经在根目录，保持不变
                        pass
                    else:
                        # 普通路径，返回上一级
                        parent = os.path.dirname(current.rstrip('/'))
                        self.cwd_cache[session_id] = parent or '/'
                elif path == '.':
                    pass
                else:
                    # 相对路径
                    if current == '~':
                        self.cwd_cache[session_id] = f"~/{path}"
                    elif current == '/':
                         self.cwd_cache[session_id] = f"/{path}"
                    else:
                        self.cwd_cache[session_id] = f"{current}/{path}"
            
            # 调试输出
            print(f"CWD更新: {self.cwd_cache.get(session_id)}")

    def get_cwd(self, session_id: str) -> str:
        with self.lock:
            return self.cwd_cache.get(session_id, '~')

    def get_username(self, session_id: str) -> str:
        """获取当前用户名（简化版本）"""
        # 这里应该通过SSH连接获取实际用户名，暂时返回默认值
        return "root"

    def sync_current_directory(self, session_id: str, ssh_client: paramiko.SSHClient) -> str:
        """同步当前工作目录（从SSH获取真实路径）"""
        if not ssh_client:
            return self.cwd_cache.get(session_id, '~')
        
        try:
            stdin, stdout, stderr = ssh_client.exec_command("pwd", timeout=3)
            real_cwd = stdout.read().decode('utf-8', errors='ignore').strip()
            error_output = stderr.read().decode('utf-8', errors='ignore').strip()
            
            if real_cwd and not error_output:
                with self.lock:
                    self.cwd_cache[session_id] = real_cwd
                print(f"CWD同步: {real_cwd}")
                return real_cwd
            else:
                print(f"CWD同步失败: {error_output}")
                return self.cwd_cache.get(session_id, '~')
        except Exception as e:
            print(f"CWD同步异常: {e}")
            return self.cwd_cache.get(session_id, '~')

    def get_file_color_info(self, filename: str, file_type: str, is_executable: bool, is_base: bool) -> dict:
        """获取文件颜色信息（增强版）"""
        # 基础颜色映射
        color_info = {
            "color_class": "file",
            "ansi_color": "\x1b[0m",
            "css_color": "#ffffff"
        }
        
        # 隐藏文件检测
        if filename.startswith('.'):
            color_info.update({
                "color_class": "hidden",
                "ansi_color": "\x1b[90m",
                "css_color": "#808080"
            })
            return color_info
        
        # 扩展名检测
        ext = filename.split('.')[-1].lower() if '.' in filename else ""
        
        # 压缩文件
        compressed_exts = ['zip', 'tar', 'gz', 'bz2', 'xz', '7z', 'rar', 'tgz', 'tbz']
        if ext in compressed_exts:
            color_info.update({
                "color_class": "compressed",
                "ansi_color": "\x1b[91m",
                "css_color": "#ff6b6b"
            })
            return color_info
        
        # 图片文件
        image_exts = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'ico', 'webp', 'tiff']
        if ext in image_exts:
            color_info.update({
                "color_class": "image",
                "ansi_color": "\x1b[95m",
                "css_color": "#cc99ff"
            })
            return color_info
        
        # 代码文件
        code_exts = ['py', 'js', 'java', 'cpp', 'c', 'h', 'php', 'rb', 'go', 'rs', 'ts', 'jsx', 'tsx', 'vue']
        if ext in code_exts:
            color_info.update({
                "color_class": "code",
                "ansi_color": "\x1b[92m",
                "css_color": "#51cf66"
            })
            return color_info
        
        # 文档文件
        doc_exts = ['pdf', 'doc', 'docx', 'txt', 'md', 'rst', 'odt']
        if ext in doc_exts:
            color_info.update({
                "color_class": "document",
                "ansi_color": "\x1b[96m",
                "css_color": "#74c0fc"
            })
            return color_info
        
        # 基础文件类型
        if file_type == "directory":
            color_info.update({
                "color_class": "directory",
                "ansi_color": "\x1b[34;1m",
                "css_color": "#339af0"
            })
        elif is_base:
            color_info.update({
                "color_class": "base",
                "ansi_color": "\x1b[33;1m",
                "css_color": "#ffd43b"
            })
        elif is_executable:
            color_info.update({
                "color_class": "executable",
                "ansi_color": "\x1b[92m",
                "css_color": "#51cf66"
            })
        elif file_type == "symlink":
            color_info.update({
                "color_class": "symlink",
                "ansi_color": "\x1b[96m",
                "css_color": "#22d3ee"
            })
        elif file_type in ["socket", "pipe", "block", "char"]:
            color_info.update({
                "color_class": "special",
                "ansi_color": "\x1b[35m",
                "css_color": "#cc5de8"
            })
        
        return color_info

    def get_ls_file_info(self, ssh_client, filename: str, current_dir: str) -> dict:
        """获取文件详细信息（增强版，包含颜色信息）"""
        try:
            # 使用stat命令获取文件详细信息
            stat_cmd = f"stat -c '%F|%a|%A' '{filename}'"
            stdin, stdout, stderr = ssh_client.exec_command(f"cd {current_dir} && {stat_cmd}", timeout=5)
            stat_output = stdout.read().decode('utf-8', errors='ignore').strip()
            
            if not stat_output:
                # 如果stat命令失败，使用ls -ld作为备选
                ls_cmd = f"ls -ld '{filename}'"
                stdin, stdout, stderr = ssh_client.exec_command(f"cd {current_dir} && {ls_cmd}", timeout=5)
                ls_output = stdout.read().decode('utf-8', errors='ignore').strip()
                
                if ls_output:
                    # 解析ls -ld输出
                    parts = ls_output.split()
                    if len(parts) >= 9:
                        permissions = parts[0]
                        file_type = "directory" if permissions.startswith('d') else "file"
                        is_executable = permissions[3] == 'x' or permissions[6] == 'x' or permissions[9] == 'x'
                        is_base = filename.lower() in ['base', 'miniconda', 'conda', 'anaconda']
                        
                        # 获取颜色信息
                        color_info = self.get_file_color_info(filename, file_type, is_executable, is_base)
                        
                        return {
                            "name": filename,
                            "type": file_type,
                            "permissions": permissions[1:],  # 移除第一个字符（文件类型标识）
                            "is_executable": is_executable,
                            "is_base": is_base,
                            "color_info": color_info
                        }
                
                # 如果都失败，返回默认值
                color_info = self.get_file_color_info(filename, "file", False, False)
                return {
                    "name": filename,
                    "type": "file",
                    "permissions": "----------",
                    "is_executable": False,
                    "is_base": False,
                    "color_info": color_info
                }
            
            # 解析stat输出: 文件类型|八进制权限|符号权限
            file_type_str, octal_perms, symbolic_perms = stat_output.split('|')
            
            # 判断文件类型
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
            
            # 判断是否可执行（基于符号权限）
            is_executable = 'x' in symbolic_perms
            
            # 判断是否是BASE路径
            is_base = filename.lower() in ['base', 'miniconda', 'conda', 'anaconda']
            
            # 获取颜色信息
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
            print(f"获取文件信息失败 {filename}: {e}")
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
        
        # 计算最大文件名长度（考虑颜色代码空间）
        max_name_length = max(len(f['name']) for f in files)
        # 列宽（考虑颜色代码和间距）
        column_width = max_name_length + 2
        # 计算列数（预留一些边距）
        num_columns = max(1, (terminal_width - 10) // column_width)
        
        # 按列排列文件（按行优先）
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
        """处理ls命令，返回结构化数据（支持横纵排列）"""
        try:
            # 提取ls命令的参数和路径
            import re
            ls_match = re.match(r'^\s*ls\s*(.*)$', command)
            ls_args = ls_match.group(1) if ls_match else ""
            
            # 执行ls -1获取文件列表
            ls_cmd = f"ls -1 {ls_args}".strip()
            stdin, stdout, stderr = ssh_client.exec_command(f"cd {current_dir} && {ls_cmd}", timeout=5)
            ls_output = stdout.read().decode('utf-8', errors='ignore').strip()
            
            if not ls_output:
                return None
            
            # 解析文件列表
            files = [f.strip() for f in ls_output.split('\n') if f.strip()]
            
            # 获取每个文件的详细信息
            file_info_list = []
            for filename in files:
                file_info = self.get_ls_file_info(ssh_client, filename, current_dir)
                file_info_list.append(file_info)
            
            # 生成多列布局信息
            multicolumn_info = self.format_ls_multicolumn(file_info_list, terminal_width)
            
            # 获取当前提示符（模拟）
            prompt = f"(base) root@VM-0-15-ubuntu:{current_dir}# "
            
            return {
                "type": "ls_output",
                "data": {
                    "files": file_info_list,
                    "layout": multicolumn_info,
                    "prompt": prompt
                }
            }
            
        except Exception as e:
            print(f"处理ls结构化输出失败: {e}")
            return None

    def add_command_to_history(self, session_id: str, command: str):
        """添加命令到历史记录"""
        with self.lock:
            # 确保只存储纯命令，不包含提示符
            # 清理命令，移除可能的提示符（如：(base) root@VM-0-15-ubuntu:~# ls -la）
            # 查找最后一个可能的提示符结束字符（# 或 $）
            cleaned_command = command.strip()
            
            # 处理常见的Shell提示符模式
            prompt_end_chars = ['#', '$', '>']
            for char in prompt_end_chars:
                if char in cleaned_command:
                    # 只保留提示符后的内容
                    cleaned_command = cleaned_command.split(char, 1)[-1].strip()
                    break
            
            # 跳过空命令
            if not cleaned_command:
                return
                
            if session_id not in self.command_history:
                self.command_history[session_id] = []
            # 避免重复添加相同的命令
            if not self.command_history[session_id] or self.command_history[session_id][-1] != cleaned_command:
                self.command_history[session_id].append(cleaned_command)
    
    def get_history_command(self, session_id: str, direction: str, current_index: int) -> dict:
        """获取历史命令
        
        Args:
            session_id: 会话ID
            direction: "up"或"down"
            current_index: 当前历史索引
            
        Returns:
            dict: 包含历史命令和新索引的字典
        """
        with self.lock:
            history = self.command_history.get(session_id, [])
            max_index = len(history) - 1
            
            if direction == "up":
                # 向上箭头，获取上一个历史命令
                new_index = current_index - 1 if current_index > 0 else max_index
            elif direction == "down":
                # 向下箭头，获取下一个历史命令
                new_index = current_index + 1 if current_index < max_index else -1  # -1表示没有命令
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
                        timeout=30  # 增加SSH连接超时时间
                    )
                elif connection.key_file:
                    ssh.connect(
                        hostname=connection.hostname,
                        port=connection.port,
                        username=connection.username,
                        key_filename=connection.key_file,
                        timeout=30  # 增加SSH连接超时时间
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
    # 启动时初始化会话管理器
    app.state.ssh_manager = SSHSessionManager()
    yield
    # 关闭时清理所有连接
    with app.state.ssh_manager.lock:
        for ssh in app.state.ssh_manager.sessions.values():
            ssh.close()

app = FastAPI(title="SSH WebSocket工具", lifespan=lifespan)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws/ssh")
async def websocket_ssh_endpoint(websocket: WebSocket):
    """WebSocket SSH终端端点"""
    await websocket.accept()
    
    session_id = None
    ssh_client = None
    channel = None
    
    try:
        # 接收连接信息
        connection_data = await websocket.receive_text()
        print(f"接收到连接数据: {connection_data}")
        
        connection_info = json.loads(connection_data)
        
        # 验证连接信息
        if "type" not in connection_info:
            error_msg = "消息缺少type字段"
            print(f"错误: {error_msg}")
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": error_msg
            }))
            return
        
        if connection_info["type"] != "connect":
            error_msg = f"首次消息必须是连接类型，当前类型: {connection_info['type']}"
            print(f"错误: {error_msg}")
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": error_msg
            }))
            return
        
        # 验证data字段是否存在
        if "data" not in connection_info:
            error_msg = "连接信息缺少data字段"
            print(f"错误: {error_msg}")
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": error_msg
            }))
            return
        
        connection = SSHConnection(**connection_info["data"])
        session_id = app.state.ssh_manager.generate_session_id(connection)
        print(f"生成会话ID: {session_id}")
        
        # 建立SSH连接
        print(f"正在建立SSH连接: {connection.username}@{connection.hostname}:{connection.port}")
        ssh_client = app.state.ssh_manager.connect_ssh(connection)
        app.state.ssh_manager.register_websocket(session_id, websocket)
        print(f"SSH连接成功")
        
        # 创建交互式shell通道，配置终端类型和模式
        channel = ssh_client.invoke_shell(term='xterm', width=connection.width, height=connection.height)
        channel.settimeout(1.0)  # 增加通道超时时间，提高稳定性
        print(f"创建shell通道成功")
        
        # 发送连接成功消息
        # 发送 connected 类型消息（标准）
        connected_response = {
            "type": "connected",
            "session_id": session_id,
            "message": "SSH连接成功"
        }
        print(f"发送connected响应: {connected_response}")
        await websocket.send_text(json.dumps(connected_response))
        
        # 同时发送 connect 类型消息（兼容某些客户端）
        connect_response = {
            "type": "connect",
            "session_id": session_id,
            "message": "SSH连接成功"
        }
        print(f"发送connect响应: {connect_response}")
        await websocket.send_text(json.dumps(connect_response))
        
        # 启动数据接收任务
        output_paused = asyncio.Event()
        output_paused.set() # Initially, output is not paused
        output_buffer = ""  # 输出缓冲区
        last_output_time = 0  # 最后输出时间
        OUTPUT_MERGE_TIMEOUT = 0.05  # 输出合并超时时间（秒）

        async def receive_ssh_output():
            nonlocal output_buffer, last_output_time
            while True:
                try:
                    # 接收数据到缓冲区
                    if channel.recv_ready() and output_paused.is_set():
                        data = channel.recv(1024).decode('utf-8', errors='ignore')
                        if data:
                            # 将数据添加到缓冲区
                            output_buffer += data
                            last_output_time = time.time()
                            
                    # 检查是否需要发送缓冲区内容
                    current_time = time.time()
                    if output_buffer and (current_time - last_output_time > OUTPUT_MERGE_TIMEOUT):
                        # 处理缓冲区中的数据
                        data = output_buffer
                        output_buffer = ""  # 清空缓冲区
                        
                        # 过滤服务器回显的命令，避免重复显示
                        nonlocal last_sent_command
                        if last_sent_command:
                            # 处理命令回显，考虑ANSI转义序列
                            # 先处理可能包含控制字符的情况
                            import re
                            # 创建一个正则表达式，匹配命令回显，忽略中间的控制序列
                            cmd_pattern = re.escape(last_sent_command) + r'(?:\x1b\[[0-9;]*[a-zA-Z])*\r\n'
                            if re.search(cmd_pattern, data):
                                # 替换掉回显的命令和控制序列
                                data = re.sub(cmd_pattern, '', data)
                                # 重置last_sent_command，避免多次过滤
                                last_sent_command = None

                        # 过滤不必要的系统状态行（如 Memory usage / IPv4 address 提示）
                        try:
                            import re as _re
                            ansi = _re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
                            stripped = ansi.sub('', data)
                            # 逐行过滤
                            lines = data.replace('\r\n', '\n').split('\n')
                            stripped_lines = stripped.replace('\r\n', '\n').split('\n')
                            filtered = []
                            drop_prefixes = [
                                'Memory usage:',
                                'IPv4 address for ',
                                'System load:'
                            ]
                            for orig, s in zip(lines, stripped_lines):
                                s = s.strip()
                                if not s:
                                    filtered.append(orig)
                                    continue
                                if any(s.startswith(dp) for dp in drop_prefixes):
                                    # 丢弃该行
                                    continue
                                filtered.append(orig)
                            data = '\n'.join(filtered)
                        except Exception:
                            # 过滤异常时忽略，继续输出
                            pass

                        # 发送过滤后的输出
                        try:
                            import re as _clean_re
                            data_for_send = data
                            # 移除常见ANSI CSI颜色/样式控制序列
                            data_for_send = _clean_re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", data_for_send)
                            # 移除Bracketed Paste模式切换序列
                            data_for_send = _clean_re.sub(r"\x1b\[\?2004[hl]", "", data_for_send)
                            # 移除OSC (Operating System Command) 序列，如设置终端标题
                            data_for_send = _clean_re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", data_for_send)
                            # 统一换行符
                            data_for_send = data_for_send.replace("\r\n", "\n").replace("\r", "\n")
                        except Exception:
                            data_for_send = data

                        await websocket.send_text(json.dumps({
                            "type": "output",
                            "data": data_for_send
                        }))
                    await asyncio.sleep(0.01)
                except:
                    break
        
        receive_task = asyncio.create_task(receive_ssh_output())
        last_sent_command = None
        tab_last_command = ""
        tab_last_options = []
        tab_cycle_index = -1
        tab_last_is_cd = False
        
        # 处理客户端消息
        while True:
            try:
                message_data = await websocket.receive_text()
                message = json.loads(message_data)
                
                if message["type"] == "command":
                    # 执行命令
                    command = message["data"]["command"]
                    # 移除命令末尾的换行符，避免发送多余的换行导致重复提示符
                    command = command.rstrip('\r\n')
                    
                    # 检查是否为ls命令，尝试结构化输出
                    try:
                        import re as _lsre
                        simple_ls = _lsre.match(r"^\s*ls(\s|$)", command) is not None
                        has_ops = any(op in command for op in ['|', ';', '&&', '||'])
                        
                        if simple_ls and not has_ops:
                            # 获取当前工作目录
                            current_dir = app.state.ssh_manager.get_cwd(session_id)
                            
                            # 尝试结构化输出（颜色支持）
                            # 获取终端宽度（默认80列）
                            terminal_width = 80  # 默认值
                            ls_structured = app.state.ssh_manager.process_ls_structured(
                                ssh_client, command, session_id, current_dir, terminal_width
                            )
                            
                            if ls_structured and ls_structured["data"]["files"]:
                                # 发送结构化输出
                                await websocket.send_text(json.dumps(ls_structured))
                                
                                # 发送提示符（模拟命令执行完成）
                                # 修复：避免输出重叠和多余换行
                                prompt_text = ls_structured["data"]["prompt"].strip()
                                if prompt_text:
                                    # 确保只有一个换行在开头，避免重叠
                                    prompt_response = {
                                        "type": "output", 
                                        "data": "\n" + prompt_text
                                    }
                                    await websocket.send_text(json.dumps(prompt_response))
                                
                                # 跳过正常命令执行流程
                                continue
                    except Exception as e:
                        print(f"结构化ls输出失败，回退到普通模式: {e}")
                    
                    # 回退到普通ls处理（单列无颜色）
                    try:
                        import re as _lsre
                        simple_ls = _lsre.match(r"^\s*ls(\s|$)", command) is not None
                        has_ops = any(op in command for op in ['|', ';', '&&', '||'])
                        if simple_ls and not has_ops:
                            tail = command[len(command.split('ls', 1)[0]) + 2:] if 'ls' in command else ''
                            # 如果已有 -l 或 -1 或 --format=single-column，则不改写
                            has_long = _lsre.search(r"(^|\s)-[^\s]*l", tail) is not None
                            has_single = ('-1' in tail) or ('--format=single-column' in tail)
                            if not has_long and not has_single:
                                # 将前缀 ls 改为 ls -1 --color=never，保留原尾部参数和路径
                                command = _lsre.sub(r"^\s*ls", "ls -1 --color=never", command, count=1)
                    except Exception:
                        pass
                    
                    last_sent_command = command
                    channel.send(command + "\n")
                    # 将命令添加到历史记录
                    app.state.ssh_manager.add_command_to_history(session_id, command)
                    # 尝试更新CWD（传入ssh_client用于cd ..命令）
                    app.state.ssh_manager.update_cwd(session_id, command, ssh_client)
                    
                    # 修复：对于cd命令，延迟同步当前目录
                    if command.strip().startswith('cd '):
                        async def delayed_sync():
                            await asyncio.sleep(0.5)  # 等待cd命令执行完成
                            try:
                                app.state.ssh_manager.sync_current_directory(session_id, ssh_client)
                            except Exception as e:
                                print(f"延迟CWD同步失败: {e}")
                        asyncio.create_task(delayed_sync())
                elif message["type"] == "resize":
                    # 处理终端尺寸调整
                    if "data" in message and isinstance(message["data"], dict):
                        width = message["data"].get("width")
                        height = message["data"].get("height")
                        if width and height and channel:
                            channel.resize_pty(width=width, height=height)
                            print(f"终端尺寸调整为: width={width}, height={height}")

                    
                elif message["type"] == "tab_complete":
                    # 处理TAB补全请求
                    # 如果前端发送了当前上下文，我们尝试智能补全
                    context_command = ""
                    if "data" in message and isinstance(message["data"], dict) and "command" in message["data"]:
                        context_command = message["data"]["command"]
                    
                    if context_command:
                        output_paused.clear()
                        try:
                            # 获取当前猜测的CWD
                            cwd = app.state.ssh_manager.get_cwd(session_id)
                            
                            # 分析最后一个词
                            # 注意：这里需要处理引号等复杂情况，但简单起见，我们只处理空格分割
                            args = context_command.split()
                            # 如果是以空格结尾，说明是在输入新的参数，last_word为空
                            if context_command.endswith(" "):
                                last_word = ""
                            else:
                                last_word = args[-1] if args else ""
                            
                            # 决定补全类型
                            # 如果是第一个词，或者前面是管道/分号等，尝试命令补全
                            # 简单判断：如果是第一个词，补全命令
                            is_command_completion = len(args) <= 1 and not context_command.endswith(" ")
                            
                            completions = []
                            err_data = ""
                            
                            if is_command_completion:
                                # 命令补全，使用 compgen -c
                                completion_script = f"compgen -c {last_word}"
                                stdin, stdout, stderr = ssh_client.exec_command(f"bash -c '{completion_script}'", timeout=5)
                                out_data = stdout.read().decode('utf-8', errors='ignore')
                                completions = [c.strip() for c in out_data.split('\n') if c.strip()]
                            else:
                                # 文件/目录补全
                                # 采用更可靠的策略：列出当前目录所有文件，在Python端过滤
                                # 使用 ls -1F，目录会以 / 结尾，可执行文件以 * 结尾等
                                ls_cmd = "ls -1F --color=never"
                                if cwd != '~':
                                    ls_cmd = f"cd {cwd} && {ls_cmd}"
                                
                                print(f"执行补全列表获取: {ls_cmd}")
                                # 直接执行，不使用 bash -c 包装，减少转义问题
                                stdin, stdout, stderr = ssh_client.exec_command(ls_cmd, timeout=5)
                                
                                out_raw = stdout.read().decode('utf-8', errors='ignore')
                                err_data = stderr.read().decode('utf-8', errors='ignore')
                                
                                import re
                                ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
                                out_data = ansi.sub('', out_raw)
                                all_files = [c.strip() for c in out_data.split('\n') if c.strip()]
                                
                                # 在 Python 端进行过滤
                                if args[0] == 'cd':
                                    # cd 命令只补全目录（以 / 结尾的项）
                                    # 过滤出以 last_word 开头 且 以 / 结尾的项
                                    filtered = [f for f in all_files if f.startswith(last_word) and f.endswith('/')]
                                    # 去掉末尾的 /，因为前端补全通常不需要显示 /
                                    completions = [f[:-1] for f in filtered]
                                else:
                                    # 其他命令补全所有文件
                                    # 过滤出以 last_word 开头的项
                                    # 此时保留 ls -F 的标记（如 / * @ 等），还是去掉？
                                    # 为了保持一致性，我们去掉末尾的标记字符
                                    filtered = [f for f in all_files if f.startswith(last_word)]
                                    completions = []
                                    for f in filtered:
                                        if f.endswith(('/', '*', '@', '|', '=')):
                                            completions.append(f[:-1])
                                        else:
                                            completions.append(f)

                            print(f"补全结果: {len(completions)} 个候选项")
                            
                            # 如果无结果，尝试在根目录回退一次（适配用户在 / 下的情况）
                            if not completions and not is_command_completion and args and args[0] == 'cd':
                                try:
                                    ls_root = "ls -1F --color=never /"
                                    stdin, stdout, stderr = ssh_client.exec_command(ls_root, timeout=5)
                                    out_root_raw = stdout.read().decode('utf-8', errors='ignore')
                                    out_root = ansi.sub('', out_root_raw)
                                    root_files = [c.strip() for c in out_root.split('\n') if c.strip()]
                                    filtered = [f for f in root_files if f.startswith(last_word) and f.endswith('/')]
                                    completions = [f[:-1] for f in filtered]
                                    print(f"根目录回退补全: {len(completions)} 个候选项")
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
                            print(f"智能补全失败: {e}")
                            # 发送空结果，告知前端处理完毕
                            await websocket.send_text(json.dumps({
                                "type": "tab_completion_options",
                                "data": {
                                    "options": [],
                                    "base": "",
                                    "error": str(e)
                                }
                            }))
                        finally:
                            # 无论如何，恢复输出
                            await asyncio.sleep(0.1) # 等待一小段时间，让可能的垃圾输出被丢弃
                            output_paused.set()
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "tab_completion_options",
                            "data": {
                                "options": [],
                                "base": "",
                                "path_prefix": app.state.ssh_manager.get_cwd(session_id),
                                "debug_error": ""
                            }
                        }))
                    
                elif message["type"] == "tab_complete_result":
                    # 处理TAB补全结果
                    completion = message["data"]["completion"]
                    channel.send(completion)
                    
                elif message["type"] == "history_get":
                    # 处理历史命令请求
                    data = message["data"]
                    direction = data.get("direction", "up")
                    current_index = data.get("current_index", -1)
                    # 获取历史命令
                    history_result = app.state.ssh_manager.get_history_command(session_id, direction, current_index)
                    # 发送历史命令响应
                    await websocket.send_text(json.dumps({
                        "type": "history_result",
                        "data": history_result
                    }))
                    
                elif message["type"] == "resize":
                    # 调整终端大小
                    cols = message["data"].get("cols", 80)
                    rows = message["data"].get("rows", 24)
                    channel.resize_pty(width=cols, height=rows)
                    
                elif message["type"] == "disconnect":
                    # 断开连接
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
        print(f"发送错误消息: {error_msg}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": error_msg
            }))
        except Exception as send_error:
            print(f"发送错误消息失败: {send_error}")
    finally:
        # 清理资源
        if 'receive_task' in locals() and receive_task:
            receive_task.cancel()
        if channel:
            try:
                channel.close()
            except Exception as e:
                print(f"关闭SSH通道失败: {e}")
        if session_id:
            try:
                app.state.ssh_manager.disconnect_ssh(session_id)
            except Exception as e:
                print(f"断开SSH连接失败: {e}")
        # 尝试关闭WebSocket连接，但处理已关闭的情况
        try:
            await websocket.close()
        except Exception as close_error:
            # 忽略连接已关闭的错误
            if "Unexpected ASGI message" not in str(close_error):
                print(f"关闭WebSocket连接失败: {close_error}")

@app.websocket("/ws/ssh/execute")
async def websocket_command_endpoint(websocket: WebSocket):
    """
    WebSocket命令执行端点（单次命令）
    """
    await websocket.accept()
    print("WebSocket连接已接受")
    
    try:
        # 接收命令信息
        command_data = await websocket.receive_text()
        print(f"接收到命令数据: {command_data}")
        command_info = json.loads(command_data)
        
        if "type" not in command_info or command_info["type"] != "execute":
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "消息类型必须是execute"
            }))
            return
        
        connection = SSHConnection(**command_info["data"]["connection"])
        command = command_info["data"]["command"]
        timeout = command_info["data"].get("timeout", 30)
        
        # 建立SSH连接
        ssh_manager: SSHSessionManager = app.state.ssh_manager
        ssh_client = ssh_manager.connect_ssh(connection)
        
        # 执行命令
        stdin, stdout, stderr = ssh_client.exec_command(command, timeout=timeout)
        
        # 实时发送输出
        async def stream_output():
            while True:
                if stdout.channel.recv_ready():
                    data = stdout.channel.recv(1024).decode('utf-8', errors='ignore')
                    if data:
                        await websocket.send_text(json.dumps({
                            "type": "output",
                            "data": data
                        }))
                
                if stdout.channel.recv_stderr_ready():
                    data = stdout.channel.recv_stderr(1024).decode('utf-8', errors='ignore')
                    if data:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "data": data
                        }))
                
                if stdout.channel.exit_status_ready():
                    exit_code = stdout.channel.recv_exit_status()
                    await websocket.send_text(json.dumps({
                        "type": "completed",
                        "exit_code": exit_code
                    }))
                    break
                
                await asyncio.sleep(0.01)
        
        await stream_output()
        
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"执行命令时出错: {str(e)}"
            }))
        except Exception as send_error:
            print(f"发送错误消息失败: {send_error}")
    finally:
        # 尝试关闭WebSocket连接，但处理已关闭的情况
        try:
            await websocket.close()
        except Exception as close_error:
            # 忽略连接已关闭的错误
            if "Unexpected ASGI message" not in str(close_error):
                print(f"关闭WebSocket连接失败: {close_error}")

@app.get("/")
async def root():
    """API首页"""
    return {
        "message": "SSH WebSocket工具API",
        "version": "1.0.0",
        "websocket_endpoints": [
            "/ws/ssh - 实时SSH终端",
            "/ws/ssh/execute - 单次命令执行"
        ]
    }

if __name__ == "__main__":
    import uvicorn
    
    # 禁用SSL证书
    use_ssl = False
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8003, 
        ws_ping_timeout=None, 
        ws_ping_interval=None,
        # 不使用SSL证书
    )
