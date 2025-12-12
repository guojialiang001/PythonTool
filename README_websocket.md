# SSH WebSocket工具

基于WebSocket的实时SSH连接工具，支持交互式终端和实时命令执行。

## 功能特性

- 🌐 **WebSocket实时通信** - 双向实时数据传输
- 💻 **交互式终端** - 完整的终端模拟器功能
- ⚡ **实时命令执行** - 实时输出流式传输
- 🔄 **终端大小调整** - 支持动态调整终端尺寸
- 🛡️ **连接管理** - 自动重连和错误处理

## 安装依赖

```bash
pip install -r requirements.txt
```

## 启动服务

```bash
# 启动WebSocket版本
python ssh_websocket.py

# 或使用uvicorn
uvicorn ssh_websocket:app --host 0.0.0.0 --port 8002 --reload
```

服务启动后，访问 http://localhost:8002 查看API文档。

## WebSocket端点

### 1. 实时SSH终端 (`/ws/ssh`)

提供完整的交互式终端功能，支持：
- 实时命令输入和输出
- 终端大小调整
- 会话保持
- 实时错误处理

### 2. 单次命令执行 (`/ws/ssh/execute`)

执行单次命令并实时返回输出，适合：
- 批量命令执行
- 脚本运行
- 监控任务

## 使用示例

### JavaScript客户端示例

```html
<!DOCTYPE html>
<html>
<head>
    <title>SSH WebSocket终端</title>
    <style>
        #terminal {
            background: #000;
            color: #fff;
            font-family: monospace;
            padding: 10px;
            height: 400px;
            overflow-y: auto;
        }
        #input {
            width: 100%;
            background: #000;
            color: #fff;
            border: none;
            outline: none;
            font-family: monospace;
        }
    </style>
</head>
<body>
    <h2>SSH WebSocket终端</h2>
    <div id="terminal"></div>
    <input type="text" id="input" placeholder="输入命令...">
    
    <script>
        class SSHTerminal {
            constructor() {
                this.ws = null;
                this.terminal = document.getElementById('terminal');
                this.input = document.getElementById('input');
                this.sessionId = null;
                
                this.connect();
                this.setupEventListeners();
            }
            
            connect() {
                this.ws = new WebSocket('ws://localhost:8002/ws/ssh');
                
                this.ws.onopen = () => {
                    this.log('正在连接SSH服务器...');
                    
                    // 发送连接信息
                    this.ws.send(JSON.stringify({
                        type: 'connect',
                        data: {
                            hostname: '192.168.1.100',
                            port: 22,
                            username: 'root',
                            password: 'your_password'
                        }
                    }));
                };
                
                this.ws.onmessage = (event) => {
                    const message = JSON.parse(event.data);
                    
                    switch (message.type) {
                        case 'connected':
                            this.sessionId = message.session_id;
                            this.log('SSH连接成功！');
                            break;
                        case 'output':
                            this.appendOutput(message.data);
                            break;
                        case 'error':
                            this.log('错误: ' + message.message);
                            break;
                        case 'completed':
                            this.log(`命令执行完成，退出码: ${message.exit_code}`);
                            break;
                    }
                };
                
                this.ws.onclose = () => {
                    this.log('连接已断开');
                };
            }
            
            setupEventListeners() {
                this.input.addEventListener('keypress', (e) => {
                    if (e.key === 'Enter') {
                        const command = this.input.value;
                        this.input.value = '';
                        
                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send(JSON.stringify({
                                type: 'command',
                                data: { command: command }
                            }));
                        }
                    }
                });
                
                // 调整终端大小
                window.addEventListener('resize', () => {
                    this.resizeTerminal();
                });
            }
            
            resizeTerminal() {
                const cols = Math.floor(this.terminal.offsetWidth / 8);
                const rows = Math.floor(this.terminal.offsetHeight / 16);
                
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({
                        type: 'resize',
                        data: { cols: cols, rows: rows }
                    }));
                }
            }
            
            log(message) {
                this.terminal.innerHTML += `<div>${message}</div>`;
                this.terminal.scrollTop = this.terminal.scrollHeight;
            }
            
            appendOutput(data) {
                this.terminal.innerHTML += data.replace(/\n/g, '<br>');
                this.terminal.scrollTop = this.terminal.scrollHeight;
            }
        }
        
        // 初始化终端
        new SSHTerminal();
    </script>
</body>
</html>
```

### Python客户端示例

```python
import asyncio
import websockets
import json

async def ssh_websocket_example():
    # 连接信息
    connection_info = {
        "hostname": "192.168.1.100",
        "port": 22,
        "username": "root",
        "password": "your_password"
    }
    
    # 连接到WebSocket
    async with websockets.connect('ws://localhost:8002/ws/ssh') as websocket:
        # 发送连接请求
        await websocket.send(json.dumps({
            "type": "connect",
            "data": connection_info
        }))
        
        # 接收连接确认
        response = await websocket.recv()
        print("连接响应:", response)
        
        # 发送命令
        commands = ["ls -la", "pwd", "whoami"]
        
        for command in commands:
            await websocket.send(json.dumps({
                "type": "command",
                "data": {"command": command}
            }))
            
            # 接收输出
            while True:
                response = await websocket.recv()
                message = json.loads(response)
                
                if message["type"] == "output":
                    print("输出:", message["data"], end="")
                elif message["type"] == "error":
                    print("错误:", message["message"])
                    break
                elif message["type"] == "completed":
                    print(f"命令执行完成，退出码: {message.get('exit_code', 'N/A')}")
                    break
                
                await asyncio.sleep(0.1)

# 运行示例
asyncio.run(ssh_websocket_example())
```

### 单次命令执行示例

```python
import asyncio
import websockets
import json

async def execute_single_command():
    async with websockets.connect('ws://localhost:8002/ws/ssh/execute') as websocket:
        # 发送执行请求
        await websocket.send(json.dumps({
            "type": "execute",
            "data": {
                "connection": {
                    "hostname": "192.168.1.100",
                    "port": 22,
                    "username": "root",
                    "password": "your_password"
                },
                "command": "ls -la /home",
                "timeout": 30
            }
        }))
        
        # 实时接收输出
        while True:
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                message = json.loads(response)
                
                if message["type"] == "output":
                    print(message["data"], end="")
                elif message["type"] == "error":
                    print("错误:", message["message"])
                elif message["type"] == "completed":
                    print(f"\n命令执行完成")
                    break
                    
            except asyncio.TimeoutError:
                continue

asyncio.run(execute_single_command())
```

## 消息格式

### 客户端到服务器

```json
{
    "type": "connect|command|resize|disconnect",
    "data": {}
}
```

### 服务器到客户端

```json
{
    "type": "connected|output|error|completed",
    "data": {},
    "message": "",
    "session_id": "",
    "exit_code": 0
}
```

## 性能优化

- 使用异步I/O处理并发连接
- 连接池管理减少SSH连接开销
- 缓冲区优化减少网络传输
- 心跳机制保持连接活跃

## 安全注意事项

- 使用WSS (WebSocket Secure) 在生产环境
- 实现身份验证和授权
- 限制并发连接数
- 监控和日志记录

## 故障排除

### 常见问题

1. **连接失败**：检查SSH服务器配置和网络连接
2. **认证失败**：验证用户名、密码或密钥文件
3. **输出乱码**：确保终端编码设置正确
4. **连接超时**：调整超时设置或检查网络状况

### 调试模式

启用详细日志：
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

这个WebSocket版本的SSH工具提供了真正的实时交互体验，非常适合Web终端、远程管理和自动化运维场景。