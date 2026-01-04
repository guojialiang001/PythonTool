#!/usr/bin/env python3
"""
Orchestrator Proxy Service - 代理转发服务

将请求转发到后端服务器 8.136.32.51:8000

只中转以下两个请求:
1. HTTP POST /endpoint/chat/conversations/start
   → http://8.136.32.51:8000/endpoint/chat/conversations/start
   (开始新对话，获取 Token)

2. WebSocket /endpoint/ws/chat?token=<jwt_token>
   → ws://8.136.32.51:8000/endpoint/ws/chat?token=<jwt_token>
   (对话 WebSocket)

部署方式:
    python orchestrator_service.py

环境变量:
    PROXY_HOST - 代理服务监听地址 (默认: 0.0.0.0)
    PROXY_PORT - 代理服务监听端口 (默认: 8001)
    BACKEND_HOST - 后端服务器地址 (默认: 8.136.32.51)
    BACKEND_PORT - 后端服务器端口 (默认: 8000)
    ENDPOINT_PREFIX - 本地端点前缀 (默认: /endpoint)
    BACKEND_API_PREFIX - 后端 API 前缀 (默认: /endpoint)
    LOG_LEVEL - 日志级别 (默认: INFO)
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException, APIRouter
from fastapi.middleware.cors import CORSMiddleware

# HTTP 客户端
import httpx

# WebSocket 客户端
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    websockets = None


# ============================================================================
# 配置
# ============================================================================

class Config:
    """服务配置"""
    # 代理服务配置
    PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
    PROXY_PORT = int(os.getenv("PROXY_PORT", "8001"))
    
    # 后端服务器配置
    BACKEND_HOST = os.getenv("BACKEND_HOST", "8.136.32.51")
    BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
    
    # 路径前缀配置
    ENDPOINT_PREFIX = os.getenv("ENDPOINT_PREFIX", "/endpoint")  # 本地端点前缀
    BACKEND_API_PREFIX = os.getenv("BACKEND_API_PREFIX", "/endpoint")  # 后端 API 前缀
    
    # 后端 URL
    BACKEND_HTTP_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
    BACKEND_WS_URL = f"ws://{BACKEND_HOST}:{BACKEND_PORT}"
    
    # 日志配置
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    # 超时配置（秒）
    HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
    WS_TIMEOUT = int(os.getenv("WS_TIMEOUT", "60"))


# 配置日志
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("orchestrator-proxy")


# ============================================================================
# HTTP 代理客户端
# ============================================================================

class HTTPProxyClient:
    """HTTP 代理客户端"""
    
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
    
    async def initialize(self):
        """初始化 HTTP 客户端"""
        self._client = httpx.AsyncClient(
            base_url=Config.BACKEND_HTTP_URL,
            timeout=httpx.Timeout(Config.HTTP_TIMEOUT),
            follow_redirects=True
        )
        logger.info(f"HTTP 代理客户端已初始化，后端: {Config.BACKEND_HTTP_URL}")
    
    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client:
            await self._client.aclose()
            logger.info("HTTP 代理客户端已关闭")
    
    async def forward_request(
        self,
        method: str,
        path: str,
        headers: dict = None,
        body: bytes = None,
        params: dict = None
    ) -> httpx.Response:
        """
        转发 HTTP 请求到后端
        
        Args:
            method: HTTP 方法
            path: 请求路径
            headers: 请求头
            body: 请求体
            params: 查询参数
        
        Returns:
            后端响应
        """
        if not self._client:
            raise RuntimeError("HTTP 客户端未初始化")
        
        # 过滤掉一些不应该转发的头
        forward_headers = {}
        if headers:
            skip_headers = {'host', 'content-length', 'transfer-encoding', 'connection'}
            forward_headers = {
                k: v for k, v in headers.items() 
                if k.lower() not in skip_headers
            }
        
        logger.info(f"=" * 60)
        logger.info(f"[HTTP 请求] 转发到后端")
        logger.info(f"  方法: {method}")
        logger.info(f"  URL: {Config.BACKEND_HTTP_URL}{path}")
        logger.info(f"  请求头: {forward_headers}")
        if body:
            try:
                body_preview = body.decode('utf-8')[:500] if len(body) > 500 else body.decode('utf-8')
                logger.info(f"  请求体: {body_preview}")
            except:
                logger.info(f"  请求体: [二进制数据, 长度: {len(body)}]")
        if params:
            logger.info(f"  查询参数: {params}")
        
        try:
            response = await self._client.request(
                method=method,
                url=path,
                headers=forward_headers,
                content=body,
                params=params
            )
            
            logger.info(f"[HTTP 响应] 后端返回")
            logger.info(f"  状态码: {response.status_code}")
            logger.info(f"  响应头: {dict(response.headers)}")
            try:
                response_preview = response.text[:1000] if len(response.text) > 1000 else response.text
                logger.info(f"  响应体: {response_preview}")
            except:
                logger.info(f"  响应体: [无法解析, 长度: {len(response.content)}]")
            logger.info(f"=" * 60)
            return response
            
        except httpx.TimeoutException as e:
            logger.error(f"[HTTP 错误] 请求超时")
            logger.error(f"  路径: {path}")
            logger.error(f"  超时时间: {Config.HTTP_TIMEOUT}秒")
            logger.error(f"  错误详情: {e}")
            logger.info(f"=" * 60)
            raise HTTPException(status_code=504, detail="后端服务超时")
        except httpx.ConnectError as e:
            logger.error(f"[HTTP 错误] 无法连接后端")
            logger.error(f"  后端地址: {Config.BACKEND_HTTP_URL}")
            logger.error(f"  错误详情: {e}")
            logger.info(f"=" * 60)
            raise HTTPException(status_code=502, detail="无法连接后端服务")
        except Exception as e:
            logger.error(f"[HTTP 错误] 转发请求失败")
            logger.error(f"  路径: {path}")
            logger.error(f"  错误类型: {type(e).__name__}")
            logger.error(f"  错误详情: {e}")
            logger.info(f"=" * 60)
            raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# WebSocket 代理
# ============================================================================

class WebSocketProxy:
    """WebSocket 代理"""
    
    def __init__(self):
        self._active_connections = 0
    
    async def proxy_websocket(
        self,
        client_ws: WebSocket,
        backend_path: str,
        query_string: str = ""
    ):
        """
        代理 WebSocket 连接
        
        Args:
            client_ws: 客户端 WebSocket
            backend_path: 后端路径
            query_string: 查询字符串
        """
        if not WEBSOCKETS_AVAILABLE:
            await client_ws.close(code=1011, reason="WebSocket 代理不可用，请安装 websockets 库")
            return
        
        # 构建后端 WebSocket URL
        backend_url = f"{Config.BACKEND_WS_URL}{backend_path}"
        if query_string:
            backend_url = f"{backend_url}?{query_string}"
        
        logger.info(f"=" * 60)
        logger.info(f"[WebSocket 连接] 建立代理连接")
        logger.info(f"  后端 URL: {backend_url}")
        logger.info(f"  查询参数: {query_string}")
        
        # 接受客户端连接
        await client_ws.accept()
        self._active_connections += 1
        
        backend_ws = None
        
        try:
            # 连接到后端 WebSocket
            backend_ws = await websockets.connect(
                backend_url,
                ping_interval=20,
                ping_timeout=Config.WS_TIMEOUT,
                close_timeout=10
            )
            
            logger.info(f"[WebSocket 连接] 成功连接到后端")
            logger.info(f"  后端 URL: {backend_url}")
            
            # 创建双向转发任务
            client_to_backend = asyncio.create_task(
                self._forward_client_to_backend(client_ws, backend_ws)
            )
            backend_to_client = asyncio.create_task(
                self._forward_backend_to_client(backend_ws, client_ws)
            )
            
            # 等待任一方向完成（通常是断开连接）
            done, pending = await asyncio.wait(
                [client_to_backend, backend_to_client],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # 取消未完成的任务
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
        except websockets.exceptions.InvalidStatusCode as e:
            logger.error(f"[WebSocket 错误] 后端连接被拒绝")
            logger.error(f"  状态码: {e.status_code}")
            logger.error(f"  后端 URL: {backend_url}")
            try:
                await client_ws.close(code=1002, reason=f"后端拒绝连接: {e.status_code}")
            except:
                pass
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"[WebSocket 关闭] 后端连接关闭")
            logger.info(f"  关闭码: {e.code}")
            logger.info(f"  原因: {e.reason}")
        except ConnectionRefusedError as e:
            logger.error(f"[WebSocket 错误] 无法连接到后端")
            logger.error(f"  后端 URL: {backend_url}")
            logger.error(f"  错误详情: {e}")
            try:
                await client_ws.close(code=1011, reason="无法连接后端服务")
            except:
                pass
        except Exception as e:
            logger.error(f"[WebSocket 错误] 代理错误")
            logger.error(f"  错误类型: {type(e).__name__}")
            logger.error(f"  错误详情: {e}")
            try:
                await client_ws.close(code=1011, reason=str(e))
            except:
                pass
        finally:
            self._active_connections -= 1
            if backend_ws:
                try:
                    await backend_ws.close()
                except:
                    pass
            logger.info(f"[WebSocket 关闭] 代理连接已关闭")
            logger.info(f"  当前活跃连接: {self._active_connections}")
            logger.info(f"=" * 60)
    
    async def _forward_client_to_backend(
        self,
        client_ws: WebSocket,
        backend_ws
    ):
        """转发客户端消息到后端"""
        try:
            while True:
                # 接收客户端消息
                data = await client_ws.receive()
                
                if data["type"] == "websocket.receive":
                    if "text" in data:
                        await backend_ws.send(data["text"])
                        logger.info(f"[WS 消息] 客户端 -> 后端")
                        logger.info(f"  内容: {data['text'][:200]}{'...' if len(data['text']) > 200 else ''}")
                    elif "bytes" in data:
                        await backend_ws.send(data["bytes"])
                        logger.info(f"[WS 消息] 客户端 -> 后端: [二进制数据, 长度: {len(data['bytes'])}]")
                elif data["type"] == "websocket.disconnect":
                    logger.info("[WS 断开] 客户端主动断开连接")
                    break
                    
        except WebSocketDisconnect:
            logger.info("[WS 断开] 客户端 WebSocket 断开")
        except Exception as e:
            logger.error(f"[WS 错误] 客户端到后端转发错误: {type(e).__name__}: {e}")
    
    async def _forward_backend_to_client(
        self,
        backend_ws,
        client_ws: WebSocket
    ):
        """转发后端消息到客户端"""
        try:
            async for message in backend_ws:
                if isinstance(message, str):
                    await client_ws.send_text(message)
                    logger.info(f"[WS 消息] 后端 -> 客户端")
                    logger.info(f"  内容: {message[:200]}{'...' if len(message) > 200 else ''}")
                elif isinstance(message, bytes):
                    await client_ws.send_bytes(message)
                    logger.info(f"[WS 消息] 后端 -> 客户端: [二进制数据, 长度: {len(message)}]")
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"[WS 关闭] 后端 WebSocket 关闭: 码={e.code}")
        except Exception as e:
            logger.error(f"[WS 错误] 后端到客户端转发错误: {type(e).__name__}: {e}")
    
    @property
    def active_connections(self) -> int:
        return self._active_connections


# ============================================================================
# FastAPI 应用
# ============================================================================

# 全局实例
http_proxy: Optional[HTTPProxyClient] = None
ws_proxy: Optional[WebSocketProxy] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global http_proxy, ws_proxy
    
    # 初始化
    http_proxy = HTTPProxyClient()
    await http_proxy.initialize()
    
    ws_proxy = WebSocketProxy()
    
    logger.info("代理服务已启动")
    
    yield
    
    # 清理
    await http_proxy.close()
    logger.info("代理服务已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="Orchestrator Proxy Service",
    description="代理转发服务 - 只中转指定的两个请求到后端",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# 请求日志中间件
# ============================================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录所有 HTTP 请求"""
    logger.info(f"=" * 60)
    logger.info(f"[收到请求]")
    logger.info(f"  方法: {request.method}")
    logger.info(f"  路径: {request.url.path}")
    logger.info(f"  查询参数: {request.query_params}")
    logger.info(f"  客户端: {request.client.host if request.client else 'unknown'}")
    logger.info(f"  请求头: {dict(request.headers)}")
    
    response = await call_next(request)
    
    logger.info(f"[响应]")
    logger.info(f"  状态码: {response.status_code}")
    logger.info(f"=" * 60)
    
    return response


# ============================================================================
# 基础路由
# ============================================================================

@app.get("/")
async def root():
    """根路径 - 服务信息"""
    return {
        "service": "Orchestrator Proxy Service",
        "version": "1.0.0",
        "description": "代理转发服务 - 只中转指定的两个请求",
        "backend": f"{Config.BACKEND_HOST}:{Config.BACKEND_PORT}",
        "config": {
            "endpoint_prefix": Config.ENDPOINT_PREFIX,
            "backend_api_prefix": Config.BACKEND_API_PREFIX
        },
        "routes": [
            {
                "id": 1,
                "type": "HTTP",
                "method": "POST",
                "local_path": f"{Config.ENDPOINT_PREFIX}/chat/conversations/start",
                "backend_path": f"{Config.BACKEND_API_PREFIX}/chat/conversations/start",
                "backend_url": f"{Config.BACKEND_HTTP_URL}{Config.BACKEND_API_PREFIX}/chat/conversations/start",
                "description": "开始新对话，获取 Token",
                "auth_required": False
            },
            {
                "id": 2,
                "type": "WebSocket",
                "local_path": f"{Config.ENDPOINT_PREFIX}/ws/chat",
                "params": "?token=<jwt_token>",
                "backend_path": f"{Config.BACKEND_API_PREFIX}/ws/chat",
                "backend_url": f"{Config.BACKEND_WS_URL}{Config.BACKEND_API_PREFIX}/ws/chat?token=<jwt_token>",
                "description": "对话 WebSocket",
                "auth_required": True
            }
        ],
        "status": {
            "active_ws_connections": ws_proxy.active_connections if ws_proxy else 0
        }
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    # 检查后端连接
    backend_healthy = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{Config.BACKEND_HTTP_URL}/health")
            backend_healthy = response.status_code == 200
    except:
        pass
    
    return {
        "status": "healthy",
        "service": "orchestrator-proxy",
        "backend": {
            "url": f"{Config.BACKEND_HOST}:{Config.BACKEND_PORT}",
            "healthy": backend_healthy
        },
        "active_ws_connections": ws_proxy.active_connections if ws_proxy else 0,
        "timestamp": datetime.utcnow().isoformat()
    }


# ============================================================================
# 代理路由 - 使用 APIRouter 统一前缀
# ============================================================================

# 创建带前缀的路由器
proxy_router = APIRouter(prefix=Config.ENDPOINT_PREFIX, tags=["proxy"])


# ----------------------------------------------------------------------------
# 路由: /endpoint/ - 禁用（返回 403）
# ----------------------------------------------------------------------------

@proxy_router.api_route("/", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def endpoint_disabled():
    """
    端点根路径已禁用
    
    本地路径: {ENDPOINT_PREFIX}/
    """
    logger.warning(f"[禁止访问] 尝试访问 {Config.ENDPOINT_PREFIX}/")
    raise HTTPException(status_code=403, detail="此端点已禁用")


# ----------------------------------------------------------------------------
# 路由 #1: HTTP POST - 开始新对话，获取 Token
# ----------------------------------------------------------------------------

@proxy_router.post("/chat/conversations/start")
async def proxy_start_conversation(request: Request):
    """
    代理路由 #1: 开始新对话，获取 Token
    
    本地路径: POST {ENDPOINT_PREFIX}/chat/conversations/start
    后端路径: POST {BACKEND_API_PREFIX}/chat/conversations/start
    
    示例:
        本地: POST /endpoint/chat/conversations/start
        后端: POST http://8.136.32.51:8000/endpoint/chat/conversations/start
    
    认证: 否
    """
    if not http_proxy:
        raise HTTPException(status_code=503, detail="代理服务未初始化")
    
    # 获取请求数据
    body = await request.body()
    headers = dict(request.headers)
    
    # 构建后端路径
    backend_path = f"{Config.BACKEND_API_PREFIX}/chat/conversations/start"
    
    logger.info(f"[路由#1] POST {Config.ENDPOINT_PREFIX}/chat/conversations/start -> {backend_path}")
    
    # 转发请求
    response = await http_proxy.forward_request(
        method="POST",
        path=backend_path,
        headers=headers,
        body=body
    )
    
    # 返回后端响应
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.headers.get("content-type")
    )


# ----------------------------------------------------------------------------
# 路由 #2: WebSocket - 对话 WebSocket
# ----------------------------------------------------------------------------

@proxy_router.websocket("/ws/chat")
async def proxy_ws_chat(websocket: WebSocket):
    """
    代理路由 #2: 对话 WebSocket
    
    本地路径: WS {ENDPOINT_PREFIX}/ws/chat?token=<jwt_token>
    后端路径: WS {BACKEND_API_PREFIX}/ws/chat?token=<jwt_token>
    
    示例:
        本地: ws://localhost:8001/endpoint/ws/chat?token=xxx
        后端: ws://8.136.32.51:8000/endpoint/ws/chat?token=xxx
    
    认证: 是 (通过 token 参数)
    """
    if not ws_proxy:
        await websocket.close(code=1011, reason="代理服务未初始化")
        return
    
    # 获取查询参数
    query_string = str(websocket.query_params)
    
    # 构建后端 WebSocket 路径
    backend_ws_path = f"{Config.BACKEND_API_PREFIX}/ws/chat"
    
    logger.info(f"[路由#2] WS {Config.ENDPOINT_PREFIX}/ws/chat -> {backend_ws_path} (params: {query_string})")
    
    # 代理 WebSocket 连接
    await ws_proxy.proxy_websocket(
        client_ws=websocket,
        backend_path=backend_ws_path,
        query_string=query_string
    )


# 注册路由器
app.include_router(proxy_router)


# ============================================================================
# 主入口
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    print(f"""
╔════════════════════════════════════════════════════════════════════════════╗
║        Orchestrator Proxy Service v1.0 - 代理转发服务                      ║
╠════════════════════════════════════════════════════════════════════════════╣
║  说明: 只中转以下两个指定请求                                               ║
╠════════════════════════════════════════════════════════════════════════════╣
║  配置:                                                                      ║
║    - 监听地址: {Config.PROXY_HOST}:{Config.PROXY_PORT}
║    - 后端地址: {Config.BACKEND_HOST}:{Config.BACKEND_PORT}
║    - 本地前缀: {Config.ENDPOINT_PREFIX}
║    - 后端前缀: {Config.BACKEND_API_PREFIX}
╠════════════════════════════════════════════════════════════════════════════╣
║  转发规则:                                                                  ║
║                                                                             ║
║  #1 HTTP POST (开始新对话，获取 Token) [无需认证]:                          ║
║     本地: POST {Config.ENDPOINT_PREFIX}/chat/conversations/start
║     后端: POST {Config.BACKEND_HTTP_URL}{Config.BACKEND_API_PREFIX}/chat/conversations/start
║                                                                             ║
║  #2 WebSocket (对话 WebSocket) [需要认证]:                                  ║
║     本地: WS {Config.ENDPOINT_PREFIX}/ws/chat?token=<jwt_token>
║     后端: WS {Config.BACKEND_WS_URL}{Config.BACKEND_API_PREFIX}/ws/chat?token=<jwt_token>
╠════════════════════════════════════════════════════════════════════════════╣
║  测试:                                                                      ║
║    - 健康检查: curl http://localhost:{Config.PROXY_PORT}/health
║    - 服务信息: curl http://localhost:{Config.PROXY_PORT}/
║    - API 文档: http://localhost:{Config.PROXY_PORT}/docs
╚════════════════════════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "orchestrator_service:app",
        host=Config.PROXY_HOST,
        port=Config.PROXY_PORT,
        reload=False,
        log_level=Config.LOG_LEVEL.lower()
    )