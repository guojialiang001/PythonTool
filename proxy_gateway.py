#!/usr/bin/env python3
"""
OpenAI API 代理网关
支持多个后端API的代理转发，保持请求原封不动
支持多线程 + 多进程 + 异步协程 三重并发处理
详细日志记录
"""

import asyncio
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import uvicorn
from typing import Optional, AsyncGenerator
import logging
import os
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import threading
import time
import json
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | PID:%(process)d | %(threadName)-15s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="OpenAI API Proxy Gateway")

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
    '/api/minimaxm21': {'target': 'https://routerpark.com', 'rewrite': '/v1'},
    '/api/code-relay': {'target': 'https://api.code-relay.com', 'rewrite': '/v1'},
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


def get_request_id():
    global request_counter
    with request_counter_lock:
        request_counter += 1
        return f"REQ-{request_counter:06d}"


@app.on_event("startup")
async def startup_event():
    global http_client, thread_pool
    logger.info("=" * 70)
    logger.info("Starting OpenAI API Proxy Gateway")
    logger.info("=" * 70)
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), limits=HTTP_CLIENT_LIMITS, follow_redirects=True)
    logger.info(f"HTTP client pool initialized (max_connections={HTTP_CLIENT_LIMITS.max_connections})")
    thread_pool = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE, thread_name_prefix="proxy_worker")
    logger.info(f"Thread pool initialized (workers={THREAD_POOL_SIZE})")
    logger.info("-" * 70)
    logger.info("Proxy Configuration:")
    for path, config in PROXY_CONFIG.items():
        logger.info(f"   {path:20s} -> {config['target']}{config['rewrite']}")
    logger.info("-" * 70)
    logger.info(f"Process ID: {os.getpid()}")
    logger.info("=" * 70)


@app.on_event("shutdown")
async def shutdown_event():
    global http_client, thread_pool
    logger.info("Shutting down...")
    if http_client:
        await http_client.aclose()
    if thread_pool:
        thread_pool.shutdown(wait=True)
    logger.info("Goodbye!")


def find_proxy_config(path: str):
    # 1. 按长度降序排序，确保最长匹配优先
    # 2. 严格匹配路径段，防止 /api/opus 误匹配 /api/opus-backup
    for prefix in sorted(PROXY_CONFIG.keys(), key=len, reverse=True):
        if path == prefix or path.startswith(prefix + '/'):
            return prefix, PROXY_CONFIG[prefix]
    return None


def build_target_url(path: str, prefix: str, config: dict) -> str:
    return config['target'] + config['rewrite'] + path[len(prefix):]


def filter_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in EXCLUDED_HEADERS}


def extract_request_info(body: bytes) -> dict:
    info = {'is_stream': False, 'model': 'unknown', 'messages_preview': '', 'messages_count': 0, 'last_role': '', 'temperature': None, 'max_tokens': None}
    if body:
        try:
            body_json = json.loads(body)
            info['is_stream'] = body_json.get('stream', False)
            info['model'] = body_json.get('model', 'unknown')
            info['temperature'] = body_json.get('temperature')
            info['max_tokens'] = body_json.get('max_tokens')
            messages = body_json.get('messages', [])
            info['messages_count'] = len(messages)
            if messages:
                last_msg = messages[-1]
                info['last_role'] = last_msg.get('role', '')
                content = last_msg.get('content', '')
                if isinstance(content, str):
                    preview = content[:150].replace('\n', ' ')
                    info['messages_preview'] = preview + "..." if len(content) > 150 else preview
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            if item.get('type') == 'text':
                                text = item.get('text', '')
                                preview = text[:150].replace('\n', ' ')
                                info['messages_preview'] = preview + "..." if len(text) > 150 else preview
                                break
                            elif item.get('type') == 'image_url':
                                info['messages_preview'] = "[Image]"
        except:
            pass
    return info


def mask_api_key(auth_header: str) -> str:
    if not auth_header:
        return "None"
    if auth_header.startswith("Bearer "):
        key = auth_header[7:]
        if len(key) > 12:
            return f"Bearer {key[:8]}...{key[-4:]}"
    return "***"


async def stream_response(response: httpx.Response, request_id: str, start_time: float) -> AsyncGenerator[bytes, None]:
    """流式响应生成器，正确处理httpx流"""
    chunk_count = 0
    total_bytes = 0
    
    try:
        async for chunk in response.aiter_bytes():
            chunk_count += 1
            total_bytes += len(chunk)
            yield chunk
                
    except Exception as e:
        logger.error(f"[{request_id}] STREAM ERROR: {type(e).__name__}: {str(e)}")
        raise
    finally:
        await response.aclose()
        final_elapsed = time.time() - start_time
        logger.info(f"[{request_id}] STREAM COMPLETE: {chunk_count} chunks, {total_bytes} bytes, {final_elapsed:.3f}s")
        logger.info("")


async def proxy_request(request: Request, target_url: str, request_id: str) -> Response:
    global http_client
    start_time = time.time()
    method = request.method
    original_headers = dict(request.headers)
    headers = filter_headers(original_headers)
    query_params = str(request.query_params) if request.query_params else None
    if query_params:
        target_url = f"{target_url}?{query_params}"
    body = await request.body()
    req_info = extract_request_info(body)
    client_ip = request.client.host if request.client else "unknown"
    masked_key = mask_api_key(original_headers.get('authorization', ''))
    user_agent = original_headers.get('user-agent', 'unknown')[:50]

    # 详细的用户请求日志
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"[{request_id}] INCOMING REQUEST")
    logger.info("=" * 70)
    logger.info(f"[{request_id}] Client IP    : {client_ip}")
    logger.info(f"[{request_id}] User-Agent   : {user_agent}")
    logger.info(f"[{request_id}] API Key      : {masked_key}")
    logger.info(f"[{request_id}] Method       : {method}")
    logger.info(f"[{request_id}] Path         : {request.url.path}")
    logger.info(f"[{request_id}] Target       : {target_url}")
    logger.info(f"[{request_id}] Body Size    : {len(body)} bytes")
    logger.info(f"[{request_id}] " + "-" * 50)
    logger.info(f"[{request_id}] Model        : {req_info['model']}")
    logger.info(f"[{request_id}] Stream       : {req_info['is_stream']}")
    logger.info(f"[{request_id}] Messages     : {req_info['messages_count']} message(s)")
    logger.info(f"[{request_id}] Last Role    : {req_info['last_role']}")
    if req_info['temperature'] is not None:
        logger.info(f"[{request_id}] Temperature  : {req_info['temperature']}")
    if req_info['max_tokens'] is not None:
        logger.info(f"[{request_id}] Max Tokens   : {req_info['max_tokens']}")
    logger.info(f"[{request_id}] " + "-" * 50)
    logger.info(f"[{request_id}] User Message : {req_info['messages_preview']}")
    logger.info(f"[{request_id}] " + "=" * 50)

    try:
        if req_info['is_stream']:
            logger.info(f"[{request_id}] Starting streaming connection...")
            
            # 先发起请求获取状态码
            req = http_client.build_request(method, target_url, headers=headers, content=body)
            upstream_response = await http_client.send(req, stream=True)
            
            elapsed = time.time() - start_time
            logger.info(f"[{request_id}] UPSTREAM RESPONSE (streaming)")
            logger.info(f"[{request_id}]    Status: {upstream_response.status_code}, Time to 1st byte: {elapsed:.3f}s")

            # 使用独立的流式响应生成器，并透传状态码
            return StreamingResponse(
                stream_response(upstream_response, request_id, start_time),
                status_code=upstream_response.status_code,
                media_type=upstream_response.headers.get('content-type', 'text/event-stream')
            )
        else:
            logger.info(f"[{request_id}] Sending request to upstream...")
            response = await http_client.request(method=method, url=target_url, headers=headers, content=body)
            elapsed = time.time() - start_time
            response_headers = dict(response.headers)
            for h in ['content-length', 'transfer-encoding', 'content-encoding']:
                response_headers.pop(h, None)
            logger.info(f"[{request_id}] UPSTREAM RESPONSE")
            logger.info(f"[{request_id}]    Status: {response.status_code}, Size: {len(response.content)} bytes, Time: {elapsed:.3f}s")
            logger.info(f"[{request_id}] REQUEST COMPLETE")
            logger.info("")
            return Response(content=response.content, status_code=response.status_code, headers=response_headers, media_type=response_headers.get('content-type'))

    except httpx.TimeoutException as e:
        elapsed = time.time() - start_time
        logger.error(f"[{request_id}] TIMEOUT ERROR: {target_url}, Time: {elapsed:.3f}s, Error: {str(e)}")
        raise
    except httpx.RequestError as e:
        elapsed = time.time() - start_time
        logger.error(f"[{request_id}] REQUEST ERROR: {target_url}, Time: {elapsed:.3f}s, Error: {type(e).__name__}: {str(e)}")
        raise


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_handler(request: Request, path: str):
    request_id = get_request_id()
    full_path = "/" + path
    result = find_proxy_config(full_path)
    if result is None:
        logger.warning(f"[{request_id}] NO PROXY CONFIG for path: {full_path}")
        return Response(content='{"error": "No proxy configuration found"}', status_code=404, media_type="application/json")
    prefix, config = result
    target_url = build_target_url(full_path, prefix, config)
    try:
        return await proxy_request(request, target_url, request_id)
    except httpx.TimeoutException:
        return Response(content='{"error": "Upstream server timeout"}', status_code=504, media_type="application/json")
    except httpx.RequestError as e:
        return Response(content=f'{{"error": "Proxy error: {str(e)}"}}', status_code=502, media_type="application/json")


@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"service": "OpenAI API Proxy Gateway", "endpoints": list(PROXY_CONFIG.keys()), "status": "running"}


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat(), "process_id": os.getpid()}


@app.get("/stats")
async def stats():
    logger.info("Stats endpoint accessed")
    return {"request_count": request_counter, "process_id": os.getpid(), "thread_pool_size": THREAD_POOL_SIZE, "proxy_routes": list(PROXY_CONFIG.keys())}


def run_server(host: str, port: int, ssl_keyfile: str = None, ssl_certfile: str = None, workers: int = None):
    if workers is None:
        workers = multiprocessing.cpu_count() * 2 + 1
    ssl_config = {}
    if ssl_keyfile and ssl_certfile:
        ssl_config = {"ssl_keyfile": ssl_keyfile, "ssl_certfile": ssl_certfile}
    print("\n" + "=" * 70)
    print("OpenAI API Proxy Gateway")
    print("=" * 70)
    print(f"Host: {host}, Port: {port}, Workers: {workers}, Threads/worker: {THREAD_POOL_SIZE}")
    print("-" * 70)
    for path, config in PROXY_CONFIG.items():
        print(f"   {path:20s} -> {config['target']}{config['rewrite']}")
    print("=" * 70 + "\n")
    os.environ["WORKERS"] = str(workers)
    loop_type = "asyncio"
    if os.name != 'nt':
        try:
            import uvloop
            loop_type = "uvloop"
        except ImportError:
            pass
    uvicorn.run("proxy_gateway:app", host=host, port=port, workers=workers, loop=loop_type, access_log=True, **ssl_config)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OpenAI API Proxy Gateway")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--workers", type=int, default=None, help="Number of worker processes")
    parser.add_argument("--ssl-keyfile", default=None, help="SSL key file path")
    parser.add_argument("--ssl-certfile", default=None, help="SSL certificate file path")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, workers=args.workers, ssl_keyfile=args.ssl_keyfile, ssl_certfile=args.ssl_certfile)