# SSH连接工具 - FastAPI实现

一个基于FastAPI的SSH连接管理工具，提供RESTful API接口来管理SSH连接、执行命令和文件传输。

## 功能特性

- 🔐 安全的SSH连接管理
- ⚡ 异步执行SSH命令
- 📁 文件上传和下载
- 🔄 连接池管理
- 📊 实时执行结果返回
- 🛡️ 错误处理和超时控制

## 安装依赖

```bash
pip install -r requirements.txt
```

## 启动服务

```bash
# 直接运行
python ssh_tool.py

# 或使用uvicorn
uvicorn ssh_tool:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后，访问 http://localhost:8000 查看API文档。

## API接口

### 1. 建立SSH连接

**POST** `/ssh/connect`

请求体：
```json
{
    "hostname": "192.168.1.100",
    "port": 22,
    "username": "root",
    "password": "your_password"
}
```

或使用密钥文件：
```json
{
    "hostname": "192.168.1.100", 
    "port": 22,
    "username": "root",
    "key_file": "/path/to/private_key"
}
```

### 2. 执行SSH命令

**POST** `/ssh/execute`

请求体：
```json
{
    "connection": {
        "hostname": "192.168.1.100",
        "port": 22,
        "username": "root",
        "password": "your_password"
    },
    "command": "ls -la /home",
    "timeout": 30
}
```

响应：
```json
{
    "success": true,
    "output": "命令输出内容",
    "error": "错误信息",
    "exit_code": 0,
    "execution_time": 0.123
}
```

### 3. 文件传输

**POST** `/ssh/file/transfer`

上传文件：
```json
{
    "connection": {
        "hostname": "192.168.1.100",
        "port": 22,
        "username": "root",
        "password": "your_password"
    },
    "local_path": "/local/file.txt",
    "remote_path": "/remote/file.txt",
    "direction": "upload"
}
```

下载文件：
```json
{
    "connection": {
        "hostname": "192.168.1.100",
        "port": 22,
        "username": "root",
        "password": "your_password"
    },
    "local_path": "/local/download.txt",
    "remote_path": "/remote/file.txt",
    "direction": "download"
}
```

### 4. 断开连接

**POST** `/ssh/disconnect`

请求体：
```json
{
    "hostname": "192.168.1.100",
    "port": 22,
    "username": "root"
}
```

### 5. 获取连接列表

**GET** `/ssh/connections`

响应：
```json
{
    "connections": [
        {"connection": "root@192.168.1.100:22"}
    ],
    "count": 1
}
```

## 使用示例

### Python客户端示例

```python
import requests
import json

# 服务器地址
BASE_URL = "http://localhost:8000"

# 1. 建立连接
connection_info = {
    "hostname": "192.168.1.100",
    "port": 22,
    "username": "root",
    "password": "your_password"
}

response = requests.post(f"{BASE_URL}/ssh/connect", json=connection_info)
print("连接结果:", response.json())

# 2. 执行命令
command_request = {
    "connection": connection_info,
    "command": "ls -la /home",
    "timeout": 30
}

response = requests.post(f"{BASE_URL}/ssh/execute", json=command_request)
result = response.json()
print("命令执行结果:")
print(f"成功: {result['success']}")
print(f"输出: {result['output']}")
print(f"错误: {result['error']}")
print(f"执行时间: {result['execution_time']}秒")

# 3. 文件上传
upload_request = {
    "connection": connection_info,
    "local_path": "/local/test.txt",
    "remote_path": "/remote/test.txt",
    "direction": "upload"
}

response = requests.post(f"{BASE_URL}/ssh/file/transfer", json=upload_request)
print("文件上传结果:", response.json())

# 4. 断开连接
response = requests.post(f"{BASE_URL}/ssh/disconnect", json=connection_info)
print("断开连接结果:", response.json())
```

### cURL示例

```bash
# 建立连接
curl -X POST "http://localhost:8000/ssh/connect" \
     -H "Content-Type: application/json" \
     -d '{
           "hostname": "192.168.1.100",
           "port": 22,
           "username": "root",
           "password": "your_password"
         }'

# 执行命令
curl -X POST "http://localhost:8000/ssh/execute" \
     -H "Content-Type: application/json" \
     -d '{
           "connection": {
             "hostname": "192.168.1.100",
             "port": 22,
             "username": "root",
             "password": "your_password"
           },
           "command": "ls -la",
           "timeout": 30
         }'

# 获取连接列表
curl -X GET "http://localhost:8000/ssh/connections"
```

## 配置说明

### 环境变量

可以设置以下环境变量来配置服务：

```bash
export SSH_TOOL_HOST=0.0.0.0
export SSH_TOOL_PORT=8000
export SSH_TOOL_RELOAD=true
```

### 安全注意事项

1. **密码安全**: 建议使用SSH密钥认证而非密码
2. **网络隔离**: 确保服务只在可信网络内运行
3. **超时设置**: 合理设置命令执行超时时间
4. **连接管理**: 及时断开不再使用的连接

## 错误处理

工具会返回详细的错误信息，常见错误包括：

- `400`: 连接参数错误或连接失败
- `500`: 服务器内部错误
- 命令执行超时
- 文件传输失败

## 性能优化

- 使用连接池减少重复连接开销
- 异步处理提高并发性能
- 合理的超时设置避免资源占用

## 许可证

MIT License