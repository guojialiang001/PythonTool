#!/bin/bash

# SSH WebSocket服务启动脚本
# 使用pm2管理Python进程

# 设置工作目录
cd "$(dirname "$0")"

# 检查Python是否安装
if ! command -v python &> /dev/null; then
    echo "错误: 未找到Python，请先安装Python"
    exit 1
fi

# 检查pm2是否安装
if ! command -v pm2 &> /dev/null; then
    echo "错误: 未找到pm2，请先安装pm2"
    echo "安装命令: npm install -g pm2"
    exit 1
fi

# 检查依赖是否安装
if [ ! -f "requirements.txt" ]; then
    echo "错误: 未找到requirements.txt文件"
    exit 1
fi

# 安装Python依赖
echo "正在检查Python依赖..."
pip install -r requirements.txt

# 检查并生成SSL证书（如果不存在）
if [ ! -f "key.pem" ] || [ ! -f "cert.pem" ]; then
    echo "正在生成SSL证书..."
    python generate_ssl.py
fi

# 停止已存在的服务（如果存在）
echo "正在停止已存在的SSH WebSocket服务..."
pm2 stop ssh-websocket 2>/dev/null || true

# 启动SSH WebSocket服务
echo "正在启动SSH WebSocket服务..."
pm2 start ssh_websocket.py --name "ssh-websocket" --interpreter python --cwd .

# 显示服务状态
echo "服务启动完成，当前状态:"
pm2 status

echo ""
echo "常用命令:"
echo "  pm2 logs ssh-websocket    # 查看日志"
echo "  pm2 restart ssh-websocket # 重启服务"
echo "  pm2 stop ssh-websocket    # 停止服务"
echo "  pm2 delete ssh-websocket  # 删除服务"
echo ""
echo "WebSocket服务运行在: https://localhost:8002"
echo "WebSocket端点: wss://localhost:8002/ws/ssh"
echo "注意: 由于使用自签名证书，浏览器会显示安全警告，请点击"继续前往""