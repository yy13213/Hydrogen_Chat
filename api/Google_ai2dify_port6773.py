import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import uvicorn
import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# 初始化 FastAPI 应用
app = FastAPI(title="Gemini API Proxy", description="纯粹的 Gemini 官方 SDK 透传代理")

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

    # 4. 发起请求并透传结果
    try:
        if is_stream:
            # 【流式转发】像管道一样，Google 发来一个字，就给前端吐一个字
            async def stream_generator():
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        method=request.method,
                        url=target_url,
                        headers=headers,
                        content=body,
                        timeout=120.0  # 流式请求超时时间设长一点
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
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                    timeout=60.0
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

    except Exception as e:
        print(f"❌ 代理转发失败: {str(e)}")
        return Response(content=f'{{"error": "{str(e)}"}}', status_code=500, media_type="application/json")


if __name__ == "__main__":
    # 从环境变量读取端口配置，默认为 6773
    port = int(os.getenv("GOOGLE_AI_PORT", 6773))
    print("="*50)
    print(f"🚀 代理服务器已启动，完全兼容 Google 官方 SDK")
    print(f"📡 正在监听端口: {port}...")
    print("💡 提示: 按 Ctrl+C 可以停止服务")
    print("="*50)
    uvicorn.run(app, host="0.0.0.0", port=port)