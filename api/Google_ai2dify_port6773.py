import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import uvicorn
import os
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# ==================== 并发优化配置 ====================
# 连接池配置
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", 500))  # 最大连接数
MAX_KEEPALIVE_CONNECTIONS = int(os.getenv("MAX_KEEPALIVE_CONNECTIONS", 100))  # 保持活动的连接数
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", 10.0))  # 连接超时
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", 120.0))  # 读取超时
POOL_TIMEOUT = float(os.getenv("POOL_TIMEOUT", 10.0))  # 连接池超时

# 并发限制（防止过载）
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", 1000))  # 最大并发请求数
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# 全局 HTTP 客户端（连接池复用）
http_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时创建连接池，关闭时释放资源"""
    global http_client
    
    # 启动时：创建全局 HTTP 客户端（带连接池）
    limits = httpx.Limits(
        max_connections=MAX_CONNECTIONS,
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=30.0  # 保持连接 30 秒
    )
    
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT,
        read=READ_TIMEOUT,
        write=30.0,
        pool=POOL_TIMEOUT
    )
    
    http_client = httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        http2=True,  # 启用 HTTP/2 支持
        follow_redirects=True
    )
    
    print("="*60)
    print("🚀 高并发代理服务器启动成功")
    print(f"📊 并发配置:")
    print(f"   - 最大连接数: {MAX_CONNECTIONS}")
    print(f"   - 保活连接数: {MAX_KEEPALIVE_CONNECTIONS}")
    print(f"   - 最大并发请求: {MAX_CONCURRENT_REQUESTS}")
    print(f"   - 连接超时: {CONNECT_TIMEOUT}s")
    print(f"   - 读取超时: {READ_TIMEOUT}s")
    print(f"   - HTTP/2: 已启用")
    print("="*60)
    
    yield  # 应用运行中
    
    # 关闭时：释放连接池资源
    await http_client.aclose()
    print("\n✓ HTTP 客户端连接池已关闭")

# 初始化 FastAPI 应用（使用生命周期管理）
app = FastAPI(
    title="Gemini API Proxy", 
    description="高并发 Gemini 官方 SDK 透传代理",
    lifespan=lifespan
)

# 默认 API Key 配置（推荐写在 .env 文件中）
DEFAULT_API_KEY = os.getenv("GEMINI_API_KEY", "")

if DEFAULT_API_KEY:
    print("✓ 已加载默认环境变量 GEMINI_API_KEY")
else:
    print("⚠ 未配置默认 GEMINI_API_KEY，将完全依赖客户端请求头传入")

# 使用 {path:path} 通配符拦截所有请求（包括 /v1/..., /v1beta/...）
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def reverse_proxy(path: str, request: Request):
    """
    将收到的所有请求原封不动地转发给 Google 官方 API，并将结果原路返回。
    """
    # === 🌟 核心修复：路径清洗 ===
    # 1. 修复 Dify/前端常见的路径拼接错误（消除多余的 v1/）
    if path.startswith("v1/v1beta/"):
        path = path.replace("v1/v1beta/", "v1beta/", 1)
        
    # 2. 隐藏福利：如果你在客户端选了 OpenAI 格式，自动映射到 Google 官方的 OpenAI 兼容端点
    if path.endswith("chat/completions"):
        path = "v1beta/openai/chat/completions"

    # 3. 拼接真实的 Google API 地址
    target_url = f"https://generativelanguage.googleapis.com/{path}"
    
    # 获取并拼接查询参数 (例如 ?alt=sse)
    query_params = request.url.query
    if query_params:
        target_url += f"?{query_params}"
        
    # ... 后面的代码保持不变 ...

    # 2. 处理请求头和请求体
    body = await request.body()
    headers = dict(request.headers)
    
    # ⚠️ 必须移除的请求头
    # 移除 host，否则 Google 发现 host 是你本地服务器的 IP 会拒绝请求
    headers.pop("host", None)
    # 移除 content-length，交由 httpx 自动重新计算，防止因编码问题导致长度不匹配报错
    headers.pop("content-length", None)
    
    # 自动注入 API Key：如果客户端没传，且服务器配置了，则帮忙补上
    header_keys_lower = [k.lower() for k in headers.keys()]
    if DEFAULT_API_KEY and "x-goog-api-key" not in header_keys_lower:
        headers["x-goog-api-key"] = DEFAULT_API_KEY

    # 3. 判断是否需要流式输出 (根据 URL 特征判断)
    is_stream = "alt=sse" in query_params or "streamGenerateContent" in path

    # 4. 发起请求并透传结果（使用并发控制）
    try:
        # 使用信号量限制并发，防止过载
        async with semaphore:
            if is_stream:
                # 【流式转发】像管道一样，Google 发来一个字，就给前端吐一个字
                async def stream_generator():
                    # 使用全局连接池客户端（复用连接）
                    async with http_client.stream(
                        method=request.method,
                        url=target_url,
                        headers=headers,
                        content=body,
                        timeout=READ_TIMEOUT  # 使用配置的超时时间
                    ) as response:
                        # 检查上游是否报错（如 403 404）
                        if response.status_code != 200:
                            print(f"⚠️ 上游返回错误状态码: {response.status_code}")
                            
                        # 按字节流转发
                        async for chunk in response.aiter_bytes():
                            yield chunk

                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream"
                )
                
            else:
                # 【非流式转发】等待 Google 处理完，一次性拿回所有数据发给前端
                # 使用全局连接池客户端（复用连接）
                response = await http_client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body
                )
                
                # 透传完整的状态码、响应头（清理掉一些可能导致浏览器解析问题的头）和响应体
                resp_headers = dict(response.headers)
                resp_headers.pop("content-encoding", None)
                resp_headers.pop("transfer-encoding", None)
                
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=resp_headers
                )

    except asyncio.TimeoutError:
        print(f"⏱️ 请求超时: {target_url}")
        return Response(
            content='{"error": "Request timeout"}', 
            status_code=504, 
            media_type="application/json"
        )
    except Exception as e:
        print(f"❌ 代理转发失败: {str(e)}")
        return Response(
            content=f'{{"error": "{str(e)}"}}', 
            status_code=500, 
            media_type="application/json"
        )


if __name__ == "__main__":
    # 从环境变量读取配置
    port = int(os.getenv("GOOGLE_AI_PORT", 6773))
    workers = int(os.getenv("WORKERS", 4))  # 工作进程数，建议设置为 CPU 核心数
    
    print("\n" + "="*60)
    print(f"🚀 高并发代理服务器启动中...")
    print(f"📡 监听端口: {port}")
    print(f"👷 工作进程数: {workers} (多进程模式)")
    print(f"💡 提示: 按 Ctrl+C 可以停止服务")
    print("="*60 + "\n")
    
    # 生产环境配置
    uvicorn.run(
        "Google_ai2dify_port6773:app",  # 使用字符串导入，支持多进程
        host="0.0.0.0",
        port=port,
        workers=workers,  # 多进程
        log_level="info",
        access_log=True,
        # 性能优化选项
        loop="uvloop",  # 使用更快的事件循环（需安装 uvloop）
        http="httptools",  # 使用更快的 HTTP 解析器
        limit_concurrency=MAX_CONCURRENT_REQUESTS,  # 并发限制
        backlog=2048,  # 挂起连接队列大小
        timeout_keep_alive=75  # Keep-Alive 超时
    )