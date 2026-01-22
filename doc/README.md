# PythonTool - 多媒体处理工具集

Python 多媒体处理工具集，提供视频处理、音频提取、图片处理、SSH WebSocket 等功能。

## 安装

```bash
pip install -r requirements.txt
```

## 工具列表

### 视频处理

| 工具 | 说明 |
|------|------|
| `extract_last_frame.py` | 提取视频最后N帧 |
| `extract_audio.py` | 从视频提取音频 |

```bash
# 提取最后一帧
python extract_last_frame.py video.mp4

# 提取倒数5帧
python extract_last_frame.py video.mp4 -n 5

# 提取音频
python extract_audio.py video.mp4 -o output.mp3
```

### 图片处理

| 工具 | 说明 |
|------|------|
| `remove_watermark.py` | 去除图片水印 |
| `image_upscaler.py` | 基础图片放大 (OpenCV) |
| `upscale_image.py` | AI 图片放大 (Real-ESRGAN) |

```bash
# 去水印（自动检测）
python remove_watermark.py image.jpg

# 去水印（指定区域）
python remove_watermark.py image.jpg -r 100,100,200,50

# 基础放大
python image_upscaler.py image.jpg

# AI 放大（4倍）
python upscale_image.py image.jpg -s 4
```

### SSH WebSocket

| 工具 | 说明 |
|------|------|
| `ssh_websocket.py` | WebSocket SSH 服务器 |

```bash
# 启动服务
python ssh_websocket.py

# 或使用 uvicorn
uvicorn ssh_websocket:app --host 0.0.0.0 --port 8002 --reload
```

### 安全防护

| 工具 | 说明 |
|------|------|
| `security_threat_protection.py` | 三级威胁防护系统 |
| `ssh_security.py` | SSH 安全模块 |
| `ssh_security_middleware.py` | SSH 安全中间件 |

### 代理网关

| 工具 | 说明 |
|------|------|
| `proxy_gateway.py` | 代理网关 |
| `proxy_gateway_mcp.py` | MCP 代理网关 |
| `orchestrator_service.py` | 编排服务 |

### 其他工具

| 工具 | 说明 |
|------|------|
| `Mail.py` | 邮件发送模块 |
| `set_claude_env.py` | Claude 环境配置 |
| `generate_ssl.py` | SSL 证书生成 |

## 依赖说明

- **视频/音频**: OpenCV, MoviePy
- **图片处理**: OpenCV, Pillow, NumPy
- **AI 放大**: PyTorch, Real-ESRGAN, BasicSR
- **Web 服务**: FastAPI, Uvicorn, WebSockets
- **SSH**: Paramiko

## 注意事项

1. AI 放大首次运行会自动下载模型（约 1GB+）
2. GPU 加速需安装 CUDA 版本的 PyTorch
3. 大文件处理需要足够内存

## License

MIT
