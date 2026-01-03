#!/usr/bin/env python3
"""
远程后台代理服务 (Orchestrator Service)
独立部署的服务，负责总体调度 AI 智能体和沙箱

功能：
1. WebSocket 对话 - 主要通信方式，支持流式响应和事件推送
2. Agent 管理 - 每个用户一个 Agent 实例，沙箱由 Agent 自动管理
3. 任务分析 - 自动分析任务复杂度，决定是否需要沙箱
4. 事件透明化 - 所有执行过程都通过 WebSocket 推送给前端

部署方式：
    python orchestrator_service.py

环境变量：
    ORCHESTRATOR_HOST - 服务监听地址 (默认: 0.0.0.0)
    ORCHESTRATOR_PORT - 服务监听端口 (默认: 8001)
    REDIS_URL - Redis 连接地址 (默认: redis://localhost:6379/0)
    LLM_API_KEY - LLM API 密钥
    LLM_API_BASE_URL - LLM API 地址
    LLM_MODEL - LLM 模型名称
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Set
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Redis (可选，用于分布式部署)
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    aioredis = None


# ============================================================================
# 配置
# ============================================================================

class Config:
    """服务配置"""
    # 服务配置
    HOST = os.getenv("ORCHESTRATOR_HOST", "0.0.0.0")
    PORT = int(os.getenv("ORCHESTRATOR_PORT", "8001"))
    
    # Redis 配置
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # LLM 配置
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4")
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    
    # 日志配置
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


# 配置日志
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("orchestrator")


# ============================================================================
# WebSocket 连接管理
# ============================================================================

@dataclass
class WebSocketConnection:
    """WebSocket 连接信息"""
    websocket: WebSocket
    user_id: str
    connection_id: str
    connected_at: datetime = field(default_factory=datetime.utcnow)
    subscriptions: Set[str] = field(default_factory=set)


class WebSocketManager:
    """WebSocket 连接管理器"""
    
    def __init__(self):
        self._connections: Dict[str, WebSocketConnection] = {}
        self._user_connections: Dict[str, Set[str]] = {}  # user_id -> connection_ids
    
    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        connection_id: str = None
    ) -> WebSocketConnection:
        """建立连接"""
        await websocket.accept()
        
        connection_id = connection_id or str(uuid.uuid4())
        connection = WebSocketConnection(
            websocket=websocket,
            user_id=user_id,
            connection_id=connection_id
        )
        
        self._connections[connection_id] = connection
        
        if user_id not in self._user_connections:
            self._user_connections[user_id] = set()
        self._user_connections[user_id].add(connection_id)
        
        logger.info(f"WebSocket 连接建立: {connection_id}, 用户: {user_id}")
        return connection
    
    async def disconnect(self, connection_id: str):
        """断开连接"""
        connection = self._connections.pop(connection_id, None)
        if connection:
            user_id = connection.user_id
            if user_id in self._user_connections:
                self._user_connections[user_id].discard(connection_id)
                if not self._user_connections[user_id]:
                    del self._user_connections[user_id]
            
            logger.info(f"WebSocket 连接断开: {connection_id}")
    
    async def send_to_connection(self, connection_id: str, message: Dict[str, Any]):
        """发送消息到指定连接"""
        connection = self._connections.get(connection_id)
        if connection:
            try:
                await connection.websocket.send_json(message)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
                await self.disconnect(connection_id)
    
    async def send_to_user(self, user_id: str, message: Dict[str, Any]):
        """发送消息到用户的所有连接"""
        connection_ids = self._user_connections.get(user_id, set())
        for connection_id in list(connection_ids):
            await self.send_to_connection(connection_id, message)
    
    async def broadcast(self, message: Dict[str, Any]):
        """广播消息到所有连接"""
        for connection_id in list(self._connections.keys()):
            await self.send_to_connection(connection_id, message)
    
    def get_connection(self, connection_id: str) -> Optional[WebSocketConnection]:
        """获取连接"""
        return self._connections.get(connection_id)
    
    def get_user_connections(self, user_id: str) -> Set[str]:
        """获取用户的所有连接"""
        return self._user_connections.get(user_id, set())
    
    @property
    def connection_count(self) -> int:
        """连接数量"""
        return len(self._connections)


# ============================================================================
# Pydantic 模型
# ============================================================================

class StatsResponse(BaseModel):
    """统计响应"""
    active_connections: int
    active_users: int
    active_agents: int


# ============================================================================
# Orchestrator 服务
# ============================================================================

class OrchestratorService:
    """
    Orchestrator 服务
    
    核心职责：
    1. WebSocket 通信 - 主要通信方式
    2. Agent 管理 - 每个用户一个 Agent，沙箱由 Agent 自动管理
    3. 事件转发 - 将 Agent 事件转发给前端
    """
    
    def __init__(self):
        self._initialized = False
        
        # WebSocket 管理器
        self.ws_manager = WebSocketManager()
        
        # Redis 客户端（可选）
        self.redis: Optional[aioredis.Redis] = None
        
        # Agent 管理器（延迟导入）
        self._agent_manager = None
    
    @property
    def agent_manager(self):
        """获取 Agent 管理器"""
        if self._agent_manager is None:
            from app.agent import agent_manager
            self._agent_manager = agent_manager
        return self._agent_manager
    
    async def initialize(self):
        """初始化服务"""
        if self._initialized:
            return
        
        logger.info("正在初始化 Orchestrator 服务...")
        
        # 连接 Redis（如果可用）
        if REDIS_AVAILABLE:
            try:
                self.redis = await aioredis.from_url(Config.REDIS_URL)
                await self.redis.ping()
                logger.info("Redis 连接成功")
            except Exception as e:
                logger.warning(f"Redis 连接失败，使用内存存储: {e}")
                self.redis = None
        
        self._initialized = True
        logger.info("Orchestrator 服务初始化完成")
    
    async def shutdown(self):
        """关闭服务"""
        logger.info("正在关闭 Orchestrator 服务...")
        
        # 清理所有 Agent（包括销毁沙箱）
        await self.agent_manager.clear_all()
        
        # 关闭 Redis
        if self.redis:
            await self.redis.close()
        
        self._initialized = False
        logger.info("Orchestrator 服务已关闭")
    
    async def handle_websocket(self, websocket: WebSocket, user_id: str):
        """
        处理 WebSocket 连接
        
        这是主要的通信入口，所有前端交互都通过这里
        """
        connection_id = str(uuid.uuid4())
        connection = await self.ws_manager.connect(websocket, user_id, connection_id)
        
        # 发送连接成功消息
        await self.ws_manager.send_to_connection(connection_id, {
            "type": "connected",
            "connection_id": connection_id,
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        try:
            while True:
                # 接收消息
                data = await websocket.receive_json()
                await self._handle_message(connection, data)
                
        except WebSocketDisconnect:
            logger.info(f"WebSocket 断开: {connection_id}")
        except Exception as e:
            logger.error(f"WebSocket 错误: {e}")
        finally:
            await self.ws_manager.disconnect(connection_id)
    
    async def _handle_message(self, connection: WebSocketConnection, data: Dict[str, Any]):
        """处理 WebSocket 消息"""
        message_type = data.get("type")
        payload = data.get("payload", {})
        request_id = data.get("request_id")
        
        try:
            if message_type == "ping":
                await self._send_response(connection, "pong", {}, request_id)
            
            elif message_type == "chat":
                await self._handle_chat(connection, payload, request_id)
            
            elif message_type == "get_sandbox_info":
                await self._handle_get_sandbox_info(connection, request_id)
            
            elif message_type == "clear_memory":
                await self._handle_clear_memory(connection, request_id)
            
            elif message_type == "get_stats":
                await self._handle_get_stats(connection, request_id)
            
            else:
                await self._send_error(connection, f"未知消息类型: {message_type}", request_id)
                
        except Exception as e:
            logger.error(f"处理消息错误: {e}")
            await self._send_error(connection, str(e), request_id)
    
    async def _handle_chat(
        self,
        connection: WebSocketConnection,
        payload: Dict[str, Any],
        request_id: Optional[str]
    ):
        """
        处理对话请求
        
        这是核心功能：
        1. 获取用户的 Agent（每个用户一个）
        2. Agent 自动分析任务，决定是否需要沙箱
        3. 如果需要沙箱，Agent 自动创建
        4. 所有事件通过 WebSocket 推送给前端
        """
        user_message = payload.get("message", "").strip()
        conversation_id = payload.get("conversation_id")
        include_thinking = payload.get("include_thinking", True)
        
        if not user_message:
            await self._send_error(connection, "消息不能为空", request_id)
            return
        
        # 生成消息 ID
        message_id = str(uuid.uuid4())
        conversation_id = conversation_id or str(uuid.uuid4())
        
        # 发送确认
        await self._send_response(connection, "chat_started", {
            "conversation_id": conversation_id,
            "message_id": message_id
        }, request_id)
        
        # 获取用户的 Agent（每个用户一个，沙箱由 Agent 自动管理）
        agent = self.agent_manager.get_agent(connection.user_id)
        
        # 处理消息并转发所有事件
        full_response = ""
        
        try:
            async for event in agent.process_message(
                message=user_message,
                conversation_id=conversation_id
            ):
                event_type = event.get("type")
                
                # 构建事件消息
                event_message = {
                    "type": event_type,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "request_id": request_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                # 根据事件类型添加数据
                if event_type == "thinking" and include_thinking:
                    event_message["step_type"] = event.get("step_type")
                    event_message["content"] = event.get("content")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "token":
                    content = event.get("content", "")
                    full_response += content
                    event_message["content"] = content
                    event_message["delta"] = content
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "task_analysis":
                    event_message["analysis"] = event.get("analysis")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "sandbox_ready":
                    # 沙箱就绪事件 - 前端可以用来显示 VNC
                    event_message["session_id"] = event.get("session_id")
                    event_message["vnc_url"] = event.get("vnc_url")
                    event_message["vnc_password"] = event.get("vnc_password")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "flow_node":
                    event_message["node"] = event.get("node")
                    event_message["status"] = event.get("status")
                    event_message["message"] = event.get("message")
                    event_message["data"] = event.get("data")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type in ["plan_start", "plan_complete", "plan_revision", "plan_revised"]:
                    event_message["message"] = event.get("message")
                    event_message["data"] = event.get("data")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type in ["step_start", "step_success", "step_failed", "step_retry"]:
                    event_message["message"] = event.get("message")
                    event_message["data"] = event.get("data")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "tool_call":
                    event_message["tool"] = event.get("tool")
                    event_message["arguments"] = event.get("arguments")
                    event_message["data"] = event.get("data")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "tool_result":
                    event_message["tool"] = event.get("tool")
                    event_message["result"] = event.get("result")
                    event_message["data"] = event.get("data")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "llm_call":
                    event_message["purpose"] = event.get("purpose")
                    event_message["message"] = event.get("message")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "variable_set":
                    event_message["data"] = event.get("data")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "retry":
                    event_message["attempt"] = event.get("attempt")
                    event_message["max_retries"] = event.get("max_retries")
                    event_message["error"] = event.get("error")
                    event_message["delay"] = event.get("delay")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                
                elif event_type == "error":
                    event_message["error"] = event.get("message") or event.get("error")
                    await self.ws_manager.send_to_connection(connection.connection_id, event_message)
                    return
            
            # 发送完成消息
            await self.ws_manager.send_to_connection(connection.connection_id, {
                "type": "chat_complete",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "request_id": request_id,
                "content": full_response,
                "timestamp": datetime.utcnow().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Agent 处理错误: {e}")
            await self._send_error(connection, str(e), request_id)
    
    async def _handle_get_sandbox_info(
        self,
        connection: WebSocketConnection,
        request_id: Optional[str]
    ):
        """获取当前沙箱信息"""
        agent = self.agent_manager.get_agent(connection.user_id)
        sandbox_info = agent.get_sandbox_info()
        
        await self._send_response(connection, "sandbox_info", {
            "has_sandbox": agent.has_sandbox(),
            "sandbox_info": sandbox_info
        }, request_id)
    
    async def _handle_clear_memory(
        self,
        connection: WebSocketConnection,
        request_id: Optional[str]
    ):
        """清空对话记忆"""
        agent = self.agent_manager.get_agent(connection.user_id)
        agent.clear_memory()
        
        await self._send_response(connection, "memory_cleared", {
            "success": True
        }, request_id)
    
    async def _handle_get_stats(
        self,
        connection: WebSocketConnection,
        request_id: Optional[str]
    ):
        """获取统计信息"""
        stats = self.get_stats()
        await self._send_response(connection, "stats", stats.model_dump(), request_id)
    
    async def _send_response(
        self,
        connection: WebSocketConnection,
        response_type: str,
        data: Dict[str, Any],
        request_id: Optional[str]
    ):
        """发送响应"""
        message = {
            "type": response_type,
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat(),
            **data
        }
        await self.ws_manager.send_to_connection(connection.connection_id, message)
    
    async def _send_error(
        self,
        connection: WebSocketConnection,
        error: str,
        request_id: Optional[str]
    ):
        """发送错误"""
        await self._send_response(connection, "error", {"error": error}, request_id)
    
    def get_stats(self) -> StatsResponse:
        """获取统计信息"""
        return StatsResponse(
            active_connections=self.ws_manager.connection_count,
            active_users=len(self.ws_manager._user_connections),
            active_agents=len(self.agent_manager._agents)
        )


# ============================================================================
# FastAPI 应用
# ============================================================================

# 全局 Orchestrator 实例
orchestrator: Optional[OrchestratorService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global orchestrator
    orchestrator = OrchestratorService()
    await orchestrator.initialize()
    yield
    await orchestrator.shutdown()


# 创建 FastAPI 应用
app = FastAPI(
    title="Orchestrator Service",
    description="远程后台代理服务 - WebSocket 为主的 AI Agent 调度服务",
    version="2.0.0",
    lifespan=lifespan
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_orchestrator() -> OrchestratorService:
    """获取 Orchestrator 实例"""
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    return orchestrator


# ============================================================================
# API 路由
# ============================================================================

@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "orchestrator",
        "version": "2.0.0"
    }


@app.get("/stats", response_model=StatsResponse)
async def get_stats(orch: OrchestratorService = Depends(get_orchestrator)):
    """获取统计信息"""
    return orch.get_stats()


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str = None
):
    """
    主 WebSocket 端点
    
    这是前端的主要通信入口。
    
    连接参数：
    - user_id: 用户ID（必需）
    
    消息格式：
    ```json
    {
        "type": "消息类型",
        "payload": {...},
        "request_id": "可选的请求ID"
    }
    ```
    
    支持的消息类型：
    - ping: 心跳检测
    - chat: 发送对话消息
    - get_sandbox_info: 获取沙箱信息
    - clear_memory: 清空对话记忆
    - get_stats: 获取统计信息
    
    响应事件类型：
    - connected: 连接成功
    - pong: 心跳响应
    - chat_started: 对话开始
    - thinking: 思考过程
    - token: 文本增量
    - task_analysis: 任务分析结果
    - sandbox_ready: 沙箱就绪（包含 VNC 连接信息）
    - flow_node: 流程节点状态
    - plan_start/plan_complete: 计划开始/完成
    - step_start/step_success/step_failed: 步骤状态
    - tool_call/tool_result: 工具调用和结果
    - llm_call: LLM 调用
    - chat_complete: 对话完成
    - error: 错误
    """
    if not user_id:
        await websocket.close(code=4001, reason="缺少 user_id 参数")
        return
    
    orch = get_orchestrator()
    await orch.handle_websocket(websocket, user_id)


@app.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    user_id: str = None
):
    """
    对话 WebSocket 端点（兼容旧版）
    
    与 /ws 功能相同，保留用于向后兼容
    """
    if not user_id:
        await websocket.close(code=4001, reason="缺少 user_id 参数")
        return
    
    orch = get_orchestrator()
    await orch.handle_websocket(websocket, user_id)


# ============================================================================
# 主入口
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║        Orchestrator Service v2.0 - WebSocket 优先            ║
╠══════════════════════════════════════════════════════════════╣
║  功能:                                                        ║
║    - WebSocket 通信: 主要通信方式，支持流式响应               ║
║    - Agent 管理: 每个用户一个 Agent，沙箱自动管理             ║
║    - 任务分析: 自动分析任务复杂度，决定是否需要沙箱           ║
║    - 事件透明化: 所有执行过程通过 WebSocket 推送              ║
╠══════════════════════════════════════════════════════════════╣
║  WebSocket 端点:                                              ║
║    - ws://host:port/ws?user_id=xxx                           ║
║    - ws://host:port/ws/chat?user_id=xxx (兼容)               ║
╠══════════════════════════════════════════════════════════════╣
║  配置:                                                        ║
║    - 监听地址: {Config.HOST}:{Config.PORT}
║    - LLM 模型: {Config.LLM_MODEL}
╚══════════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "orchestrator_service:app",
        host=Config.HOST,
        port=Config.PORT,
        reload=False,
        log_level=Config.LOG_LEVEL.lower()
    )