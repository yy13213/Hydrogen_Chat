from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from google import genai
import uvicorn
import os

# 初始化 FastAPI 应用
app = FastAPI(title="Gemini 3.1 API Proxy", description="A simple API wrapper for Gemini 3.1")

# 初始化 Gemini 客户端
# 注意：客户端会自动读取环境变量中的 GEMINI_API_KEY
try:
    client = genai.Client()
except Exception as e:
    print(f"初始化 Gemini 客户端失败，请检查是否已设置 GEMINI_API_KEY 环境变量。错误信息: {e}")

# 定义请求体的数据校验模型
class GenerateRequest(BaseModel):
    prompt: str
    temperature: float = 0.7

@app.post("/v1/chat/completions")
async def generate_text(request: GenerateRequest):
    """
    接收用户的 prompt 并调用 Gemini 3.1 模型
    """
    try:
        # 调用模型。请根据你实际申请到的 API 权限调整模型名称，例如 gemini-3.1-pro 或 gemini-3.1-flash
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=request.prompt,
            config=genai.types.GenerateContentConfig(
                temperature=request.temperature,
            )
        )
        
        # 返回标准化的 JSON 响应
        return {
            "status": "success",
            "model": "gemini-3-flash-preview",
            "response": response.text
        }
        
    except Exception as e:
        # 如果调用失败（例如网络问题或 Quota 超限），返回 500 错误
        raise HTTPException(status_code=500, detail=f"调用 Gemini API 失败: {str(e)}")

if __name__ == "__main__":
    # 将 host 设置为 0.0.0.0 以允许局域网或外部网络访问
    # 绑定到指定的 6771 端口
    print("启动服务，监听端口: 6771...")
    uvicorn.run(app, host="0.0.0.0", port=6773)