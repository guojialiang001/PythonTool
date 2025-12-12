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
        self.lock = threading.Lock()
    
    def generate_session_id(self, connection: SSHConnection) -> str:
        return f"{connection.username}@{connection.hostname}:{connection.port}"
    
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
                        timeout=10
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
        connection_info = json.loads(connection_data)
        
        # 验证连接信息
        if "type" not in connection_info or connection_info["type"] != "connect":
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "首次消息必须是连接类型"
            }))
            return
        
        # 验证data字段是否存在
        if "data" not in connection_info:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "连接信息缺少data字段"
            }))
            return
        
        connection = SSHConnection(**connection_info["data"])
        session_id = app.state.ssh_manager.generate_session_id(connection)
        
        # 建立SSH连接
        ssh_client = app.state.ssh_manager.connect_ssh(connection)
        app.state.ssh_manager.register_websocket(session_id, websocket)
        
        # 创建交互式shell通道
        channel = ssh_client.invoke_shell()
        channel.settimeout(0.1)
        
        await websocket.send_text(json.dumps({
            "type": "connected",
            "session_id": session_id,
            "message": "SSH连接成功"
        }))
        
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
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"连接失败: {str(e)}"
        }))
    finally:
        # 清理资源
        if 'receive_task' in locals() and receive_task:
            receive_task.cancel()
        if channel:
            channel.close()
        if session_id:
            app.state.ssh_manager.disconnect_ssh(session_id)
        await websocket.close()

@app.websocket("/ws/ssh/execute")
async def websocket_command_endpoint(websocket: WebSocket):
    """WebSocket命令执行端点（单次命令）"""
    await websocket.accept()
    
    try:
        # 接收命令信息
        command_data = await websocket.receive_text()
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
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"执行命令时出错: {str(e)}"
        }))
    finally:
        await websocket.close()

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
    
    # SSL证书配置
    ssl_config = {
        "ssl_keyfile": "key.pem",  # SSL私钥文件路径
        "ssl_certfile": "cert.pem",  # SSL证书文件路径
        "ssl_keyfile_password": None,  # SSL私钥密码
    }
    
    # 检查是否配置了SSL证书
    use_ssl = True
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8002, 
        ws_ping_timeout=None, 
        ws_ping_interval=None,
        **ssl_config if use_ssl else {}
    )