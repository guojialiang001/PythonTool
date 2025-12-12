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
        self.lock = threading.Lock()
    
    def generate_session_id(self, connection: SSHConnection) -> str:
        return f"{connection.username}@{connection.hostname}:{connection.port}"
    
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
        
        # 创建交互式shell通道
        channel = ssh_client.invoke_shell()
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
        async def receive_ssh_output():
            while True:
                try:
                    if channel.recv_ready():
                        data = channel.recv(1024).decode('utf-8', errors='ignore')
                        if data:
                            await websocket.send_text(json.dumps({
                                "type": "output",
                                "data": data
                            }))
                    await asyncio.sleep(0.01)
                except:
                    break
        
        # 启动接收任务
        receive_task = asyncio.create_task(receive_ssh_output())
        
        # 处理客户端消息
        while True:
            try:
                message_data = await websocket.receive_text()
                message = json.loads(message_data)
                
                if message["type"] == "command":
                    # 执行命令
                    command = message["data"]["command"]
                    channel.send(command + "\n")
                    # 将命令添加到历史记录
                    app.state.ssh_manager.add_command_to_history(session_id, command)
                    
                elif message["type"] == "tab_complete":
                    # 处理TAB补全请求，发送制表符到SSH通道
                    channel.send("\t")
                    
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