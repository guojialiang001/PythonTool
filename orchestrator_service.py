#!/usr/bin/env python3
"""
远程后台代理服务 (Orchestrator Service)
独立部署的服务，负责总体调度 AI 智能体和沙箱

功能：
1. WebSocket 对话 - 主要通信方式，支持流式响应和事件推送
2. Agent 管理 - 每个会话一个 Agent 实例，实现多窗口隔离
3. 任务分析 - 自动分析任务复杂度，决定是否需要沙箱
4. 事件透明化 - 所有执行过程都通过 WebSocket 推送给前端
5. 多用户窗口隔离 - 每个浏览器窗口独立会话，互不影响

部署方式：
    python orchestrator_service.py

环境变量：
    ORCHESTRATOR_HOST - 服务监听地址 (默认: 0.0.0.0)
    ORCHESTRATOR_PORT - 服务监听端口 (默认: 8001)
    REDIS_URL - Redis 连接地址 (默认: redis://localhost:6379/0)
    LLM_API_KEY - LLM API 密钥
    LLM_API_BASE_URL - LLM API 地址
    LLM_MODEL - LLM 模型名称

服务访问地址：
    域名: https://sandbox.toproject.cloud
    实际IP: 8.136.32.51:8001
    
本服务作为代理后端，前端通过域名访问，实际请求转发到内部 IP
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Set, List
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, APIRouter, Query
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
    
    # 服务器配置
    SERVER_DOMAIN = os.getenv("SERVER_DOMAIN", "sandbox.toproject.cloud")  # 对外域名
    SERVER_IP = os.getenv("SERVER_IP", "8.136.32.51")  # 实际 IP
    
    # API 前缀配置
    API_PREFIX = os.getenv("API_PREFIX", "/orchestrator")
    
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
    
    # 会话超时配置（秒）
    SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT", "3600"))  # 默认1小时


# 配置日志
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("orchestrator")


# ============================================================================
# 会话管理（多窗口隔离）
# ============================================================================

@dataclass
class UserSession:
    """
    用户会话信息
    
    每个浏览器窗口/标签页都有独立的 session_id，
    即使是同一个 user_id，不同窗口也是独立的会话
    """
    session_id: str  # 唯一会话ID（每个窗口独立）
    user_id: str     # 用户ID（可以相同）
    connection_id: str  # WebSocket 连接ID
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_active: datetime = field(default_factory=datetime.utcnow)
    conversation_id: Optional[str] = None  # 当前对话ID
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据


# ============================================================================
# WebSocket 连接管理
# ============================================================================

@dataclass
class WebSocketConnection:
    """WebSocket 连接信息"""
    websocket: WebSocket
    user_id: str
    connection_id: str
    session_id: str  # 会话ID，用于多窗口隔离
    connected_at: datetime = field(default_factory=datetime.utcnow)
    subscriptions: Set[str] = field(default_factory=set)


class WebSocketManager:
    """
    WebSocket 连接管理器
    
    支持多用户多窗口：
    - 每个 WebSocket 连接有唯一的 connection_id
    - 每个浏览器窗口有唯一的 session_id
    - 同一用户可以有多个窗口，每个窗口独立
    """
    
    def __init__(self):
        self._connections: Dict[str, WebSocketConnection] = {}  # connection_id -> connection
        self._user_connections: Dict[str, Set[str]] = {}  # user_id -> connection_ids
        self._session_connections: Dict[str, str] = {}  # session_id -> connection_id
        self._sessions: Dict[str, UserSession] = {}  # session_id -> session
    
    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        session_id: str,
        connection_id: str = None
    ) -> WebSocketConnection:
        """
        建立连接
        
        Args:
            websocket: WebSocket 对象
            user_id: 用户ID
            session_id: 会话ID（每个窗口独立）
            connection_id: 连接ID（可选，自动生成）
        """
        await websocket.accept()
        
        connection_id = connection_id or str(uuid.uuid4())
        
        # 如果该 session 已有连接，先断开旧连接
        if session_id in self._session_connections:
            old_connection_id = self._session_connections[session_id]
            await self.disconnect(old_connection_id)
        
        connection = WebSocketConnection(
            websocket=websocket,
            user_id=user_id,
            connection_id=connection_id,
            session_id=session_id
        )
        
        self._connections[connection_id] = connection
        self._session_connections[session_id] = connection_id
        
        if user_id not in self._user_connections:
            self._user_connections[user_id] = set()
        self._user_connections[user_id].add(connection_id)
        
        # 创建或更新会话
        if session_id not in self._sessions:
            self._sessions[session_id] = UserSession(
                session_id=session_id,
                user_id=user_id,
                connection_id=connection_id
            )
        else:
            self._sessions[session_id].connection_id = connection_id
            self._sessions[session_id].last_active = datetime.utcnow()
        
        logger.info(f"WebSocket 连接建立: connection={connection_id}, session={session_id}, user={user_id}")
        return connection
    
    async def disconnect(self, connection_id: str):
        """断开连接"""
        connection = self._connections.pop(connection_id, None)
        if connection:
            user_id = connection.user_id
            session_id = connection.session_id
            
            # 清理用户连接映射
            if user_id in self._user_connections:
                self._user_connections[user_id].discard(connection_id)
                if not self._user_connections[user_id]:
                    del self._user_connections[user_id]
            
            # 清理会话连接映射
            if session_id in self._session_connections:
                if self._session_connections[session_id] == connection_id:
                    del self._session_connections[session_id]
            
            logger.info(f"WebSocket 连接断开: connection={connection_id}, session={session_id}")
    
    async def send_to_connection(self, connection_id: str, message: Dict[str, Any]):
        """发送消息到指定连接"""
        connection = self._connections.get(connection_id)
        if connection:
            try:
                await connection.websocket.send_json(message)
                # 更新会话活跃时间
                session = self._sessions.get(connection.session_id)
                if session:
                    session.last_active = datetime.utcnow()
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
                await self.disconnect(connection_id)
    
    async def send_to_session(self, session_id: str, message: Dict[str, Any]):
        """发送消息到指定会话"""
        connection_id = self._session_connections.get(session_id)
        if connection_id:
            await self.send_to_connection(connection_id, message)
    
    async def send_to_user(self, user_id: str, message: Dict[str, Any]):
        """发送消息到用户的所有连接（所有窗口）"""
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
    
    def get_session(self, session_id: str) -> Optional[UserSession]:
        """获取会话"""
        return self._sessions.get(session_id)
    
    def get_connection_by_session(self, session_id: str) -> Optional[WebSocketConnection]:
        """通过会话ID获取连接"""
        connection_id = self._session_connections.get(session_id)
        if connection_id:
            return self._connections.get(connection_id)
        return None
    
    def get_user_connections(self, user_id: str) -> Set[str]:
        """获取用户的所有连接"""
        return self._user_connections.get(user_id, set())
    
    def get_user_sessions(self, user_id: str) -> List[UserSession]:
        """获取用户的所有会话"""
        sessions = []
        for session in self._sessions.values():
            if session.user_id == user_id:
                sessions.append(session)
        return sessions
    
    @property
    def connection_count(self) -> int:
        """连接数量"""
        return len(self._connections)
    
    @property
    def session_count(self) -> int:
        """会话数量"""
        return len(self._sessions)
    
    def cleanup_expired_sessions(self, timeout_seconds: int = 3600):
        """清理过期会话"""
        now = datetime.utcnow()
        expired_sessions = []
        
        for session_id, session in self._sessions.items():
            if (now - session.last_active).total_seconds() > timeout_seconds:
                expired_sessions.append(session_id)
        
        for session_id in expired_sessions:
            session = self._sessions.pop(session_id, None)
            if session:
                logger.info(f"清理过期会话: {session_id}")
        
        return len(expired_sessions)


# ============================================================================
# Pydantic 模型
# ============================================================================

class StatsResponse(BaseModel):
    """统计响应"""
    active_connections: int
    active_sessions: int
    active_users: int
    active_agents: int


# ============================================================================
# 会话级 Agent 管理器
# ============================================================================

class SessionAgentManager:
    """
    会话级 Agent 管理器
    
    每个会话（session_id）有独立的 Agent 实例，
    实现多窗口完全隔离
    """
    
    def __init__(self):
        self._agents: Dict[str, Any] = {}  # session_id -> agent
        self._lock = asyncio.Lock()
    
    def get_agent(self, session_id: str):
        """
        获取会话的 Agent
        
        如果不存在则创建新的 Agent
        """
        if session_id not in self._agents:
            # 延迟导入，避免循环依赖
            try:
                from app.agent import create_agent
                self._agents[session_id] = create_agent(session_id)
            except ImportError:
                # 如果没有 app.agent 模块，创建一个模拟 Agent
                self._agents[session_id] = MockAgent(session_id)
            logger.info(f"为会话 {session_id} 创建新 Agent")
        return self._agents[session_id]
    
    def remove_agent(self, session_id: str):
        """移除会话的 Agent"""
        agent = self._agents.pop(session_id, None)
        if agent:
            logger.info(f"移除会话 {session_id} 的 Agent")
        return agent
    
    async def clear_all(self):
        """清理所有 Agent"""
        for session_id in list(self._agents.keys()):
            agent = self._agents.pop(session_id, None)
            if agent and hasattr(agent, 'cleanup'):
                try:
                    await agent.cleanup()
                except Exception as e:
                    logger.error(f"清理 Agent {session_id} 失败: {e}")
        logger.info("已清理所有 Agent")
    
    @property
    def agent_count(self) -> int:
        return len(self._agents)


class MockAgent:
    """模拟 Agent（用于测试）"""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.memory = []
    
    async def process_message(self, message: str, conversation_id: str):
        """处理消息（模拟）"""
        yield {"type": "thinking", "step_type": "analyzing", "content": "正在分析..."}
        yield {"type": "token", "content": f"收到消息: {message}"}
        yield {"type": "token", "content": f"\n会话ID: {self.session_id}"}
        yield {"type": "token", "content": f"\n对话ID: {conversation_id}"}
    
    def get_sandbox_info(self):
        return None
    
    def has_sandbox(self):
        return False
    
    def clear_memory(self):
        self.memory = []


# ============================================================================
# Orchestrator 服务
# ============================================================================

class OrchestratorService:
    """
    Orchestrator 服务
    
    核心职责：
    1. WebSocket 通信 - 主要通信方式
    2. Agent 管理 - 每个会话一个 Agent，实现多窗口隔离
    3. 事件转发 - 将 Agent 事件转发给前端
    
    多窗口隔离机制：
    - 每个浏览器窗口通过 session_id 标识
    - 每个 session_id 有独立的 Agent 实例
    - 同一用户的不同窗口互不影响
    """
    
    def __init__(self):
        self._initialized = False
        
        # WebSocket 管理器
        self.ws_manager = WebSocketManager()
        
        # 会话级 Agent 管理器
        self.session_agent_manager = SessionAgentManager()
        
        # Redis 客户端（可选）
        self.redis: Optional[aioredis.Redis] = None
        
        # 清理任务
        self._cleanup_task: Optional[asyncio.Task] = None
    
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
        
        # 启动定期清理任务
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        
        self._initialized = True
        logger.info("Orchestrator 服务初始化完成")
    
    async def _periodic_cleanup(self):
        """定期清理过期会话"""
        while True:
            try:
                await asyncio.sleep(300)  # 每5分钟检查一次
                expired_count = self.ws_manager.cleanup_expired_sessions(Config.SESSION_TIMEOUT)
                if expired_count > 0:
                    logger.info(f"清理了 {expired_count} 个过期会话")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理任务错误: {e}")
    
    async def shutdown(self):
        """关闭服务"""
        logger.info("正在关闭 Orchestrator 服务...")
        
        # 取消清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # 清理所有 Agent
        await self.session_agent_manager.clear_all()
        
        # 关闭 Redis
        if self.redis:
            await self.redis.close()
        
        self._initialized = False
        logger.info("Orchestrator 服务已关闭")
    
    async def handle_websocket(self, websocket: WebSocket, user_id: str, session_id: str):
        """
        处理 WebSocket 连接
        
        Args:
            websocket: WebSocket 对象
            user_id: 用户ID
            session_id: 会话ID（每个窗口独立）
        
        这是主要的通信入口，所有前端交互都通过这里。
        每个窗口通过 session_id 实现完全隔离。
        """
        connection_id = str(uuid.uuid4())
        connection = await self.ws_manager.connect(websocket, user_id, session_id, connection_id)
        
        # 发送连接成功消息
        await self.ws_manager.send_to_connection(connection_id, {
            "type": "connected",
            "connection_id": connection_id,
            "session_id": session_id,
            "user_id": user_id,
            "server_domain": Config.SERVER_DOMAIN,
            "server_ip": Config.SERVER_IP,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        try:
            while True:
                # 接收消息
                data = await websocket.receive_json()
                await self._handle_message(connection, data)
                
        except WebSocketDisconnect:
            logger.info(f"WebSocket 断开: connection={connection_id}, session={session_id}")
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
            
            elif message_type == "get_session_info":
                await self._handle_get_session_info(connection, request_id)
            
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
        1. 获取会话的 Agent（每个会话独立）
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
        
        # 更新会话的对话ID
        session = self.ws_manager.get_session(connection.session_id)
        if session:
            session.conversation_id = conversation_id
        
        # 发送确认
        await self._send_response(connection, "chat_started", {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "session_id": connection.session_id
        }, request_id)
        
        # 获取会话的 Agent（每个会话独立，实现多窗口隔离）
        agent = self.session_agent_manager.get_agent(connection.session_id)
        
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
                    "session_id": connection.session_id,
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
                    event_message["sandbox_session_id"] = event.get("session_id")
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
                "session_id": connection.session_id,
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
        agent = self.session_agent_manager.get_agent(connection.session_id)
        sandbox_info = agent.get_sandbox_info()
        
        await self._send_response(connection, "sandbox_info", {
            "has_sandbox": agent.has_sandbox(),
            "sandbox_info": sandbox_info,
            "session_id": connection.session_id
        }, request_id)
    
    async def _handle_clear_memory(
        self,
        connection: WebSocketConnection,
        request_id: Optional[str]
    ):
        """清空对话记忆"""
        agent = self.session_agent_manager.get_agent(connection.session_id)
        agent.clear_memory()
        
        await self._send_response(connection, "memory_cleared", {
            "success": True,
            "session_id": connection.session_id
        }, request_id)
    
    async def _handle_get_stats(
        self,
        connection: WebSocketConnection,
        request_id: Optional[str]
    ):
        """获取统计信息"""
        stats = self.get_stats()
        await self._send_response(connection, "stats", stats.model_dump(), request_id)
    
    async def _handle_get_session_info(
        self,
        connection: WebSocketConnection,
        request_id: Optional[str]
    ):
        """获取当前会话信息"""
        session = self.ws_manager.get_session(connection.session_id)
        
        session_info = {
            "session_id": connection.session_id,
            "user_id": connection.user_id,
            "connection_id": connection.connection_id,
            "connected_at": connection.connected_at.isoformat()
        }
        
        if session:
            session_info.update({
                "created_at": session.created_at.isoformat(),
                "last_active": session.last_active.isoformat(),
                "conversation_id": session.conversation_id
            })
        
        await self._send_response(connection, "session_info", session_info, request_id)
    
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
            "session_id": connection.session_id,
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
            active_sessions=self.ws_manager.session_count,
            active_users=len(self.ws_manager._user_connections),
            active_agents=self.session_agent_manager.agent_count
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
    description="远程后台代理服务 - WebSocket 为主的 AI Agent 调度服务（多窗口隔离）",
    version="2.1.0",
    lifespan=lifespan,
    docs_url=f"{Config.API_PREFIX}/docs",
    redoc_url=f"{Config.API_PREFIX}/redoc",
    openapi_url=f"{Config.API_PREFIX}/openapi.json"
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

# 创建 API 路由器（带前缀）
api_router = APIRouter(prefix=Config.API_PREFIX, tags=["orchestrator"])


# ----------------------------------------------------------------------------
# HTTP 路由
# ----------------------------------------------------------------------------

@api_router.get("/health")
async def health_check():
    """
    健康检查
    
    路径: GET {API_PREFIX}/health
    示例: GET /orchestrator/health
    """
    return {
        "status": "healthy",
        "service": "orchestrator",
        "version": "2.1.0",
        "server_domain": Config.SERVER_DOMAIN,
        "server_ip": Config.SERVER_IP,
        "api_prefix": Config.API_PREFIX
    }


@api_router.get("/stats", response_model=StatsResponse)
async def get_stats(orch: OrchestratorService = Depends(get_orchestrator)):
    """
    获取统计信息
    
    路径: GET {API_PREFIX}/stats
    示例: GET /orchestrator/stats
    """
    return orch.get_stats()


@api_router.get("/info")
async def get_info():
    """
    获取服务信息和所有可用路由
    
    路径: GET {API_PREFIX}/info
    示例: GET /orchestrator/info
    """
    return {
        "service": "Orchestrator Service",
        "version": "2.1.0",
        "description": "远程后台代理服务 - WebSocket 为主的 AI Agent 调度服务（多窗口隔离）",
        "server_domain": Config.SERVER_DOMAIN,
        "server_ip": Config.SERVER_IP,
        "api_prefix": Config.API_PREFIX,
        "access_urls": {
            "http": f"https://{Config.SERVER_DOMAIN}{Config.API_PREFIX}",
            "websocket": f"wss://{Config.SERVER_DOMAIN}{Config.API_PREFIX}/ws",
            "internal_http": f"http://{Config.SERVER_IP}:{Config.PORT}{Config.API_PREFIX}",
            "internal_ws": f"ws://{Config.SERVER_IP}:{Config.PORT}{Config.API_PREFIX}/ws"
        },
        "features": [
            "多用户支持",
            "多窗口隔离 - 每个浏览器窗口独立会话",
            "WebSocket 实时通信",
            "流式响应",
            "Agent 自动管理"
        ],
        "routes": {
            "http": [
                {
                    "method": "GET",
                    "path": f"{Config.API_PREFIX}/health",
                    "description": "健康检查"
                },
                {
                    "method": "GET",
                    "path": f"{Config.API_PREFIX}/stats",
                    "description": "获取统计信息"
                },
                {
                    "method": "GET",
                    "path": f"{Config.API_PREFIX}/info",
                    "description": "获取服务信息和所有可用路由"
                },
                {
                    "method": "GET",
                    "path": f"{Config.API_PREFIX}/docs",
                    "description": "Swagger UI 文档"
                },
                {
                    "method": "GET",
                    "path": f"{Config.API_PREFIX}/redoc",
                    "description": "ReDoc 文档"
                }
            ],
            "websocket": [
                {
                    "path": f"{Config.API_PREFIX}/ws",
                    "params": "?user_id=xxx&session_id=xxx",
                    "description": "主 WebSocket 端点（多窗口隔离）",
                    "note": "session_id 用于区分不同窗口，每个窗口应使用唯一的 session_id",
                    "message_types": {
                        "client_to_server": [
                            {"type": "ping", "description": "心跳检测"},
                            {"type": "chat", "description": "发送对话消息", "payload": {"message": "string", "conversation_id": "string (optional)", "include_thinking": "boolean (optional)"}},
                            {"type": "get_sandbox_info", "description": "获取沙箱信息"},
                            {"type": "clear_memory", "description": "清空对话记忆"},
                            {"type": "get_stats", "description": "获取统计信息"},
                            {"type": "get_session_info", "description": "获取当前会话信息"}
                        ],
                        "server_to_client": [
                            {"type": "connected", "description": "连接成功（包含 session_id）"},
                            {"type": "pong", "description": "心跳响应"},
                            {"type": "chat_started", "description": "对话开始"},
                            {"type": "thinking", "description": "思考过程"},
                            {"type": "token", "description": "文本增量（流式）"},
                            {"type": "task_analysis", "description": "任务分析结果"},
                            {"type": "sandbox_ready", "description": "沙箱就绪（含 VNC 信息）"},
                            {"type": "flow_node", "description": "流程节点状态"},
                            {"type": "plan_start", "description": "计划开始"},
                            {"type": "plan_complete", "description": "计划完成"},
                            {"type": "plan_revision", "description": "计划修订中"},
                            {"type": "plan_revised", "description": "计划已修订"},
                            {"type": "step_start", "description": "步骤开始"},
                            {"type": "step_success", "description": "步骤成功"},
                            {"type": "step_failed", "description": "步骤失败"},
                            {"type": "step_retry", "description": "步骤重试"},
                            {"type": "tool_call", "description": "工具调用"},
                            {"type": "tool_result", "description": "工具结果"},
                            {"type": "llm_call", "description": "LLM 调用"},
                            {"type": "variable_set", "description": "变量设置"},
                            {"type": "retry", "description": "重试事件"},
                            {"type": "chat_complete", "description": "对话完成"},
                            {"type": "sandbox_info", "description": "沙箱信息"},
                            {"type": "memory_cleared", "description": "记忆已清空"},
                            {"type": "session_info", "description": "会话信息"},
                            {"type": "stats", "description": "统计信息"},
                            {"type": "error", "description": "错误"}
                        ]
                    }
                },
                {
                    "path": f"{Config.API_PREFIX}/ws/chat",
                    "params": "?user_id=xxx&session_id=xxx",
                    "description": "对话 WebSocket 端点（兼容旧版，功能同 /ws）"
                }
            ]
        },
        "config": {
            "host": Config.HOST,
            "port": Config.PORT,
            "server_domain": Config.SERVER_DOMAIN,
            "server_ip": Config.SERVER_IP,
            "llm_model": Config.LLM_MODEL,
            "session_timeout": Config.SESSION_TIMEOUT
        }
    }


# ----------------------------------------------------------------------------
# WebSocket 路由
# ----------------------------------------------------------------------------

@api_router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str = Query(..., description="用户ID"),
    session_id: str = Query(None, description="会话ID（每个窗口独立，不传则自动生成）")
):
    """
    主 WebSocket 端点（多窗口隔离）
    
    外部访问: wss://sandbox.toproject.cloud/orchestrator/ws?user_id=xxx&session_id=xxx
    内部访问: ws://8.136.32.51:8001/orchestrator/ws?user_id=xxx&session_id=xxx
    
    参数：
    - user_id: 用户ID（必需）
    - session_id: 会话ID（可选，不传则自动生成）
    
    多窗口隔离说明：
    - 每个浏览器窗口应使用不同的 session_id
    - 同一用户的不同窗口完全独立，互不影响
    - 每个窗口有独立的 Agent 实例和对话历史
    
    消息格式：
    ```json
    {
        "type": "消息类型",
        "payload": {...},
        "request_id": "可选的请求ID"
    }
    ```
    """
    if not user_id:
        await websocket.close(code=4001, reason="缺少 user_id 参数")
        return
    
    # 如果没有提供 session_id，自动生成一个
    if not session_id:
        session_id = str(uuid.uuid4())
    
    orch = get_orchestrator()
    await orch.handle_websocket(websocket, user_id, session_id)


@api_router.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    user_id: str = Query(..., description="用户ID"),
    session_id: str = Query(None, description="会话ID（每个窗口独立，不传则自动生成）")
):
    """
    对话 WebSocket 端点（兼容旧版）
    
    外部访问: wss://sandbox.toproject.cloud/orchestrator/ws/chat?user_id=xxx&session_id=xxx
    内部访问: ws://8.136.32.51:8001/orchestrator/ws/chat?user_id=xxx&session_id=xxx
    
    与 /ws 功能相同，保留用于向后兼容
    """
    if not user_id:
        await websocket.close(code=4001, reason="缺少 user_id 参数")
        return
    
    if not session_id:
        session_id = str(uuid.uuid4())
    
    orch = get_orchestrator()
    await orch.handle_websocket(websocket, user_id, session_id)


# 注册路由器到应用
app.include_router(api_router)


# ============================================================================
# 主入口
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    print(f"""
╔════════════════════════════════════════════════════════════════════════════╗
║        Orchestrator Service v2.1 - 多窗口隔离版 (代理后端)                 ║
╠════════════════════════════════════════════════════════════════════════════╣
║  功能:                                                                      ║
║    - WebSocket 通信: 主要通信方式，支持流式响应                             ║
║    - 多窗口隔离: 每个浏览器窗口独立会话，互不影响                           ║
║    - Agent 管理: 每个会话一个 Agent，完全隔离                               ║
║    - 任务分析: 自动分析任务复杂度，决定是否需要沙箱                         ║
║    - 事件透明化: 所有执行过程通过 WebSocket 推送                            ║
╠════════════════════════════════════════════════════════════════════════════╣
║  服务地址:                                                                  ║
║    - 外部域名: https://{Config.SERVER_DOMAIN}
║    - 内部 IP:  http://{Config.SERVER_IP}:{Config.PORT}
║    - API 前缀: {Config.API_PREFIX}
╠════════════════════════════════════════════════════════════════════════════╣
║  外部访问 (通过域名):                                                       ║
║    - HTTP:  https://{Config.SERVER_DOMAIN}{Config.API_PREFIX}/health
║    - HTTP:  https://{Config.SERVER_DOMAIN}{Config.API_PREFIX}/info
║    - WS:    wss://{Config.SERVER_DOMAIN}{Config.API_PREFIX}/ws?user_id=xxx&session_id=xxx
╠════════════════════════════════════════════════════════════════════════════╣
║  内部访问 (直连 IP):                                                        ║
║    - HTTP:  http://{Config.SERVER_IP}:{Config.PORT}{Config.API_PREFIX}/health
║    - HTTP:  http://{Config.SERVER_IP}:{Config.PORT}{Config.API_PREFIX}/docs
║    - WS:    ws://{Config.SERVER_IP}:{Config.PORT}{Config.API_PREFIX}/ws?user_id=xxx&session_id=xxx
╠════════════════════════════════════════════════════════════════════════════╣
║  多窗口使用说明:                                                            ║
║    - 每个浏览器窗口使用不同的 session_id                                    ║
║    - 同一用户的不同窗口完全独立                                             ║
║    - session_id 可选，不传则自动生成                                        ║
╠════════════════════════════════════════════════════════════════════════════╣
║  配置:                                                                      ║
║    - 监听地址: {Config.HOST}:{Config.PORT}
║    - LLM 模型: {Config.LLM_MODEL}
║    - 会话超时: {Config.SESSION_TIMEOUT}秒
╚════════════════════════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "orchestrator_service:app",
        host=Config.HOST,
        port=Config.PORT,
        reload=False,
        log_level=Config.LOG_LEVEL.lower()
    )