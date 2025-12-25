#!/usr/bin/env python3
"""
OpenAI API 代理网关 + Exa MCP 集成版
支持多个后端API的代理转发
集成 Exa MCP 搜索功能，带 30 分钟线程安全缓存
返回 OpenAI 兼容格式
"""

import asyncio
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn
from typing import Optional, AsyncGenerator, Dict, Any, Tuple
import logging
import os
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import threading
import time
import json
from datetime import datetime

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | PID:%(process)d | %(threadName)-15s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="OpenAI API Proxy Gateway with MCP")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# MCP 配置
MCP_CONFIG = {
    "exa": {
        "url": "https://mcp.exa.ai/mcp?exaApiKey=6d36568c-2f26-4f73-8f8e-0d5d85a258e7&tools=web_search_exa,get_code_context_exa,crawling_exa,company_research_exa,linkedin_search_exa,deep_researcher_start,deep_researcher_check",
        "headers": {}
    }
}

# 代理配置 (继承自原版)
PROXY_CONFIG = {
    '/api/chat': {'target': 'https://api.xiaomimimo.com', 'rewrite': '/v1/chat'},
    '/api/claude': {'target': 'https://api.avoapi.com', 'rewrite': '/v1'},
    '/api/mimo': {'target': 'https://api.xiaomimimo.com', 'rewrite': '/v1'},
    '/api/opus-backup': {'target': 'https://api.code-relay.com', 'rewrite': '/v1'},
    '/api/opus': {'target': 'https://aiai.li', 'rewrite': '/v1'},
    '/api/gemini': {'target': 'https://claude.chiddns.com', 'rewrite': '/v1'},
    '/api/deepseek': {'target': 'https://aicodelink.top', 'rewrite': '/v1'},
    '/api/sonnet-backup': {'target': 'https://cifang.xyz', 'rewrite': '/v1'},
    '/api/sonnet': {'target': 'https://aiai.li', 'rewrite': '/v1'},
    '/api/minimax': {'target': 'https://aicodelink.top', 'rewrite': '/v1'},
    '/api/grok': {'target': 'https://api.avoapi.com', 'rewrite': '/v1'},
    '/api/minimaxm21': {'target': 'https://aiping.cn/api', 'rewrite': '/v1'},
    '/api/code-relay': {'target': 'https://api.code-relay.com', 'rewrite': '/v1'},
    '/api/qwen': {'target': 'https://aiping.cn/api', 'rewrite': '/v1'},
    '/api/deepseekv32': {'target': 'https://aiping.cn/api', 'rewrite': '/v1'}
}

EXCLUDED_HEADERS = {
    'host', 'content-length', 'transfer-encoding', 
    'connection', 'keep-alive', 'proxy-authenticate',
    'proxy-authorization', 'te', 'trailers', 'upgrade'
}

HTTP_CLIENT_LIMITS = httpx.Limits(max_keepalive_connections=100, max_connections=200, keepalive_expiry=30.0)
http_client: Optional[httpx.AsyncClient] = None
thread_pool: Optional[ThreadPoolExecutor] = None
THREAD_POOL_SIZE = min(20, (os.cpu_count() or 1) * 4)
request_counter = 0
request_counter_lock = threading.Lock()

# --- 缓存管理类（线程安全 + 请求去重） ---
class MCPCache:
    """
    线程安全的缓存管理器，支持：
    1. 30分钟过期策略
    2. 精确字符串匹配
    3. 请求去重（防止并发时重复调用 MCP）
    
    并发安全机制：
    - 使用 threading.Lock 保护缓存字典的读写
    - 使用 pending 字典 + asyncio.Event 实现请求去重
    - 当多个并发请求查询同一个 query 时，只有第一个会真正调用 MCP，
      其他请求会等待第一个完成后直接获取缓存结果
    """
    def __init__(self, expiry_seconds: int = 1800):
        self.cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self.lock = threading.Lock()
        self.expiry_seconds = expiry_seconds
        # 用于跟踪正在进行中的请求，防止重复调用
        self.pending: Dict[str, asyncio.Event] = {}
        self.pending_lock = asyncio.Lock()

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        """
        获取缓存（线程安全）
        返回 None 表示缓存未命中或已过期
        """
        with self.lock:
            if query in self.cache:
                data, timestamp = self.cache[query]
                if time.time() - timestamp < self.expiry_seconds:
                    logger.info(f"Cache HIT for query: {query[:50]}...")
                    return data
                else:
                    logger.info(f"Cache EXPIRED for query: {query[:50]}...")
                    del self.cache[query]
            return None

    def set(self, query: str, data: Dict[str, Any]):
        """
        设置缓存（线程安全）
        """
        with self.lock:
            self.cache[query] = (data, time.time())
            logger.info(f"Cache SET for query: {query[:50]}...")

    async def acquire_or_wait(self, query: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        尝试获取执行权或等待其他请求完成
        
        返回:
            (True, None) - 获得执行权，需要调用 MCP
            (False, data) - 其他请求已完成，直接返回缓存数据
        
        并发场景示例：
        - 请求A: acquire_or_wait("test") -> (True, None)，获得执行权
        - 请求B: acquire_or_wait("test") -> 等待...
        - 请求C: acquire_or_wait("test") -> 等待...
        - 请求A: 完成 MCP 调用，调用 release("test")
        - 请求B, C: 被唤醒，返回 (False, cached_data)
        """
        # 先检查缓存
        cached = self.get(query)
        if cached is not None:
            return (False, cached)
        
        async with self.pending_lock:
            # 双重检查：在获取 pending_lock 后再次检查缓存
            cached = self.get(query)
            if cached is not None:
                return (False, cached)
            
            # 检查是否有其他请求正在处理这个 query
            if query in self.pending:
                event = self.pending[query]
                logger.info(f"Request waiting for pending query: {query[:50]}...")
            else:
                # 创建新的 Event，标记这个 query 正在处理中
                event = asyncio.Event()
                self.pending[query] = event
                logger.info(f"Request acquired lock for query: {query[:50]}...")
                return (True, None)  # 获得执行权
        
        # 等待其他请求完成
        await event.wait()
        
        # 等待完成后，从缓存获取结果
        cached = self.get(query)
        if cached is not None:
            logger.info(f"Request got result after waiting: {query[:50]}...")
            return (False, cached)
        else:
            # 极端情况：等待的请求失败了，需要重新尝试
            logger.warning(f"Waited but no cache found, retrying: {query[:50]}...")
            return await self.acquire_or_wait(query)

    async def release(self, query: str):
        """
        释放执行权，唤醒等待的请求
        """
        async with self.pending_lock:
            if query in self.pending:
                event = self.pending.pop(query)
                event.set()  # 唤醒所有等待的请求
                logger.info(f"Released lock and notified waiters for: {query[:50]}...")

mcp_cache = MCPCache(expiry_seconds=1800)

# --- 工具函数 ---
def get_request_id():
    global request_counter
    with request_counter_lock:
        request_counter += 1
        return f"REQ-{request_counter:06d}"

@app.on_event("startup")
async def startup_event():
    global http_client, thread_pool
    logger.info("=" * 70)
    logger.info("Starting OpenAI API Proxy Gateway with MCP")
    logger.info("=" * 70)
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0), 
        limits=HTTP_CLIENT_LIMITS, 
        follow_redirects=True
    )
    thread_pool = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE, thread_name_prefix="proxy_worker")
    logger.info(f"HTTP client and Thread pool ({THREAD_POOL_SIZE} workers) initialized")
    logger.info("=" * 70)

@app.on_event("shutdown")
async def shutdown_event():
    global http_client, thread_pool
    if http_client:
        await http_client.aclose()
    if thread_pool:
        thread_pool.shutdown(wait=True)
    logger.info("Shutdown complete")

def find_proxy_config(path: str):
    for prefix in sorted(PROXY_CONFIG.keys(), key=len, reverse=True):
        if path == prefix or path.startswith(prefix + '/'):
            return prefix, PROXY_CONFIG[prefix]
    return None

def build_target_url(path: str, prefix: str, config: dict) -> str:
    return config['target'] + config['rewrite'] + path[len(prefix):]

def filter_headers(headers: dict) -> dict:
    filtered = {k: v for k, v in headers.items() if k.lower() not in EXCLUDED_HEADERS}
    filtered.pop('accept-encoding', None)
    return filtered

def mask_api_key(auth_header: str) -> str:
    if not auth_header or not auth_header.startswith("Bearer "):
        return "***"
    key = auth_header[7:]
    return f"Bearer {key[:8]}...{key[-4:]}" if len(key) > 12 else "***"

# --- MCP 核心逻辑 ---
async def call_exa_mcp(query: str, request_id: str) -> Dict[str, Any]:
    """调用 Exa MCP web_search_exa 工具"""
    url = MCP_CONFIG["exa"]["url"]
    
    # MCP JSON-RPC 请求格式
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {
                "query": query,
                "num_results": 5
            }
        }
    }
    
    logger.info(f"[{request_id}] Calling Exa MCP for query: {query}")
    try:
        # 增加必要的头信息，防止 406 错误
        # 根据 Exa MCP 报错：Client must accept both application/json and text/event-stream
        mcp_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }
        response = await http_client.post(url, json=payload, headers=mcp_headers, timeout=60.0)
        response.raise_for_status()
        
        # 调试日志：打印原始响应
        raw_text = response.text
        logger.info(f"[{request_id}] MCP Raw Response: {raw_text[:500]}...")
        
        try:
            result = response.json()
        except Exception as je:
            logger.error(f"[{request_id}] JSON Parse Error: {str(je)}, Raw: {raw_text}")
            # 如果是 SSE 格式，尝试提取 JSON 部分
            if "data:" in raw_text:
                for line in raw_text.splitlines():
                    if line.startswith("data:"):
                        try:
                            result = json.loads(line[5:].strip())
                            break
                        except: continue
            else:
                raise
        
        # 提取结果内容
        if "result" in result and "content" in result["result"]:
            # 通常 content 是一个列表，包含 text 字段
            contents = result["result"]["content"]
            search_text = ""
            for item in contents:
                if item.get("type") == "text":
                    search_text += item.get("text", "") + "\n\n"
            return {"raw": result, "text": search_text.strip()}
        else:
            logger.error(f"[{request_id}] Unexpected MCP response format: {result}")
            return {"raw": result, "text": "No results found or error in MCP response."}
            
    except Exception as e:
        logger.error(f"[{request_id}] MCP Call Error: {str(e)}")
        raise


async def get_mcp_context(query: str, request_id: str) -> Optional[str]:
    """
    获取 MCP 搜索结果作为上下文（带缓存和请求去重）
    
    返回:
        搜索结果文本，如果失败则返回 None
    """
    acquired = False
    try:
        # 尝试获取执行权或等待其他请求完成
        acquired, cached_result = await mcp_cache.acquire_or_wait(query)
        
        if not acquired:
            # 缓存命中或等待其他请求完成后获得结果
            logger.info(f"[{request_id}] MCP context from cache for: {query[:50]}")
            if cached_result and "text" in cached_result:
                mcp_text = cached_result["text"]
                logger.info(f"[{request_id}] " + "=" * 50)
                logger.info(f"[{request_id}] MCP CACHED RESULT (length: {len(mcp_text)} chars):")
                logger.info(f"[{request_id}] {mcp_text[:500]}...")
                logger.info(f"[{request_id}] " + "=" * 50)
                return mcp_text
            return None
        
        # 获得执行权，调用 MCP
        logger.info(f"[{request_id}] Fetching MCP context for: {query[:50]}")
        mcp_res = await call_exa_mcp(query, request_id)
        
        # 打印 MCP 返回结果
        mcp_text = mcp_res["text"]
        logger.info(f"[{request_id}] " + "=" * 50)
        logger.info(f"[{request_id}] MCP SEARCH RESULT (length: {len(mcp_text)} chars):")
        logger.info(f"[{request_id}] {mcp_text[:1000]}...")
        logger.info(f"[{request_id}] " + "=" * 50)
        
        # 存入缓存（存储 text 字段）
        mcp_cache.set(query, {"text": mcp_text})
        
        return mcp_text
        
    except Exception as e:
        logger.error(f"[{request_id}] MCP Context Error: {str(e)}")
        return None
    
    finally:
        if acquired and query:
            await mcp_cache.release(query)


def extract_user_query(body_json: dict) -> Optional[str]:
    """
    从请求体中提取用户的查询内容
    支持 OpenAI 格式的 messages 数组
    """
    messages = body_json.get("messages", [])
    if not messages:
        return None
    
    # 获取最后一条用户消息
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # 处理多模态消息
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return item.get("text", "")
    return None


def inject_mcp_context(body_json: dict, mcp_context: str, request_id: str = "") -> dict:
    """
    将 MCP 搜索结果注入到请求体中
    作为 system 消息添加到 messages 开头
    """
    messages = body_json.get("messages", [])
    
    # 构建 MCP 上下文消息
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mcp_system_content = f"""[MCP Search Context - Retrieved at {current_time}]
The following is real-time search information that may help answer the user's question:

{mcp_context}

[End of Search Context]
Please use this information to provide an accurate and up-to-date response."""

    mcp_system_message = {
        "role": "system",
        "content": mcp_system_content
    }
    
    # 打印注入的提示词
    logger.info(f"[{request_id}] " + "-" * 50)
    logger.info(f"[{request_id}] INJECTED MCP SYSTEM PROMPT:")
    logger.info(f"[{request_id}] {mcp_system_content[:800]}...")
    logger.info(f"[{request_id}] " + "-" * 50)
    
    # 检查是否已有 system 消息
    new_messages = []
    has_system = False
    for msg in messages:
        if msg.get("role") == "system":
            has_system = True
            # 将 MCP 上下文追加到现有 system 消息
            original_content = msg.get("content", "")
            msg = msg.copy()
            msg["content"] = f"{original_content}\n\n{mcp_system_content}"
            logger.info(f"[{request_id}] MCP context appended to existing system message")
        new_messages.append(msg)
    
    if not has_system:
        # 在开头插入 MCP system 消息
        new_messages.insert(0, mcp_system_message)
        logger.info(f"[{request_id}] MCP context inserted as new system message")
    
    body_json = body_json.copy()
    body_json["messages"] = new_messages
    return body_json

def format_to_openai(query: str, search_result: str) -> Dict[str, Any]:
    """将搜索结果包装成 OpenAI chat.completions 格式"""
    content = f"Based on the search results for '{query}':\n\n{search_result}"
    
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "exa-search-mcp",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": len(query) // 4,
            "completion_tokens": len(content) // 4,
            "total_tokens": (len(query) + len(content)) // 4
        }
    }

# --- 路由处理 ---

@app.post("/api/mcp/exa")
async def mcp_exa_handler(request: Request):
    """
    MCP Exa 搜索接口
    
    并发安全机制：
    1. 使用 acquire_or_wait 获取执行权或等待其他请求完成
    2. 只有获得执行权的请求才会真正调用 MCP API
    3. 其他并发请求会等待第一个请求完成后直接获取缓存结果
    4. 使用 try-finally 确保无论成功失败都会释放锁
    """
    request_id = get_request_id()
    query = ""
    acquired = False
    
    try:
        body = await request.json()
        # 兼容 OpenAI 格式提取 query，或者直接传 query
        if "messages" in body:
            messages = body["messages"]
            if messages:
                query = messages[-1].get("content", "")
        else:
            query = body.get("query", "")

        if not query:
            return JSONResponse(status_code=400, content={"error": "Query is required"})

        # 1. 尝试获取执行权或等待其他请求完成
        #    - 如果缓存命中，直接返回 (False, cached_data)
        #    - 如果获得执行权，返回 (True, None)
        #    - 如果有其他请求正在处理，会等待其完成后返回缓存
        acquired, cached_result = await mcp_cache.acquire_or_wait(query)
        
        if not acquired:
            # 缓存命中或等待其他请求完成后获得结果
            logger.info(f"[{request_id}] Returning cached/waited result for: {query[:50]}")
            return cached_result

        # 2. 获得执行权，调用 MCP
        logger.info(f"[{request_id}] Acquired execution right, calling MCP for: {query[:50]}")
        mcp_res = await call_exa_mcp(query, request_id)
        
        # 3. 格式化为 OpenAI 格式
        openai_response = format_to_openai(query, mcp_res["text"])
        
        # 4. 存入缓存
        mcp_cache.set(query, openai_response)
        
        return openai_response

    except Exception as e:
        logger.error(f"[{request_id}] MCP Handler Error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    
    finally:
        # 5. 无论成功失败，都要释放执行权，唤醒等待的请求
        if acquired and query:
            await mcp_cache.release(query)

# --- 原有代理逻辑 ---

async def stream_response(response: httpx.Response, request_id: str, start_time: float) -> AsyncGenerator[bytes, None]:
    chunk_count = 0
    total_bytes = 0
    try:
        async for chunk in response.aiter_bytes():
            chunk_count += 1
            total_bytes += len(chunk)
            yield chunk
    finally:
        await response.aclose()
        logger.info(f"[{request_id}] STREAM COMPLETE: {chunk_count} chunks, {total_bytes} bytes, {time.time() - start_time:.3f}s")

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_handler(request: Request, path: str):
    # 排除已定义的特定路由
    if path in ["api/mcp/exa", "health", "stats", ""]:
        # FastAPI 会自动处理这些路由，但由于通配符路由的存在，这里做个保险
        pass

    request_id = get_request_id()
    full_path = "/" + path
    
    # 查找代理配置
    result = find_proxy_config(full_path)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "No proxy configuration found"})
    
    prefix, config = result
    target_url = build_target_url(full_path, prefix, config)
    
    start_time = time.time()
    method = request.method
    headers = filter_headers(dict(request.headers))
    
    # 处理查询参数
    if request.query_params:
        target_url = f"{target_url}?{request.query_params}"
    
    body = await request.body()
    
    logger.info(f"[{request_id}] PROXY: {method} {full_path} -> {target_url}")

    try:
        # 检查是否是聊天请求，需要注入 MCP 上下文
        body_json = None
        is_chat_request = False
        is_stream = False
        
        if body and method == "POST":
            try:
                body_json = json.loads(body)
                is_stream = body_json.get('stream', False)
                # 检查是否是聊天完成请求（包含 messages 字段）
                if "messages" in body_json and full_path.endswith("/chat/completions"):
                    is_chat_request = True
            except:
                pass
        
        # 如果是聊天请求，先调用 MCP 获取上下文
        if is_chat_request and body_json:
            user_query = extract_user_query(body_json)
            if user_query:
                logger.info(f"[{request_id}] " + "=" * 60)
                logger.info(f"[{request_id}] MCP INTEGRATION START")
                logger.info(f"[{request_id}] " + "=" * 60)
                logger.info(f"[{request_id}] User Query: {user_query}")
                logger.info(f"[{request_id}] Fetching MCP context...")
                
                mcp_context = await get_mcp_context(user_query, request_id)
                
                if mcp_context:
                    logger.info(f"[{request_id}] MCP: Context retrieved ({len(mcp_context)} chars), injecting into request")
                    body_json = inject_mcp_context(body_json, mcp_context, request_id)
                    # 重新序列化请求体
                    body = json.dumps(body_json).encode('utf-8')
                    logger.info(f"[{request_id}] MCP: Request body updated, new size: {len(body)} bytes")
                else:
                    logger.warning(f"[{request_id}] MCP: No context retrieved, proceeding without MCP")
                
                logger.info(f"[{request_id}] " + "=" * 60)
                logger.info(f"[{request_id}] MCP INTEGRATION END")
                logger.info(f"[{request_id}] " + "=" * 60)

        if is_stream:
            req = http_client.build_request(method, target_url, headers=headers, content=body)
            upstream_response = await http_client.send(req, stream=True)
            
            response_headers = {
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            }
            for key in ['x-request-id', 'x-ratelimit-limit', 'x-ratelimit-remaining']:
                if key in upstream_response.headers:
                    response_headers[key] = upstream_response.headers[key]
            
            return StreamingResponse(
                stream_response(upstream_response, request_id, start_time),
                status_code=upstream_response.status_code,
                media_type=upstream_response.headers.get('content-type', 'text/event-stream'),
                headers=response_headers
            )
        else:
            response = await http_client.request(method=method, url=target_url, headers=headers, content=body)
            response_headers = dict(response.headers)
            for h in ['content-length', 'transfer-encoding', 'content-encoding']:
                response_headers.pop(h, None)
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response_headers.get('content-type')
            )

    except Exception as e:
        logger.error(f"[{request_id}] Proxy Error: {str(e)}")
        return JSONResponse(status_code=502, content={"error": f"Proxy error: {str(e)}"})

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/stats")
async def stats():
    return {
        "request_count": request_counter, 
        "cache_size": len(mcp_cache.cache),
        "process_id": os.getpid()
    }

@app.get("/")
async def root():
    return {"service": "OpenAI API Proxy Gateway with MCP", "status": "running"}

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OpenAI API Proxy Gateway with MCP")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes")
    args = parser.parse_args()
    
    uvicorn.run("proxy_gateway_mcp:app", host=args.host, port=args.port, workers=args.workers, access_log=True)