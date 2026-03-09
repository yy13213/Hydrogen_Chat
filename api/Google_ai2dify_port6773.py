from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google import genai
import uvicorn
import os

# 默认 API Key 配置
# ⚠️ 强烈建议：生产环境中请清空此处，仅使用环境变量 GEMINI_API_KEY 传入
DEFAULT_API_KEY = ""

# 初始化 FastAPI 应用
app = FastAPI(title="Gemini API Proxy", description="A simple API wrapper for Gemini")

# 初始化 Gemini 客户端
api_key = os.getenv("GEMINI_API_KEY", DEFAULT_API_KEY)

try:
    if api_key:
        client = genai.Client(api_key=api_key)
        if os.getenv("GEMINI_API_KEY"):
            print("✓ 使用环境变量中的 GEMINI_API_KEY")
        else:
            print("⚠ 使用默认的 API Key（环境变量未设置）")
    else:
        # 如果两者都为空，尝试让客户端自动读取环境默认凭证
        client = genai.Client()
        print("✓ 让 Gemini 客户端自动检测 API Key")
except Exception as e:
    print(f"❌ 初始化 Gemini 客户端失败，请检查 API Key 配置。错误信息: {e}")
    print(f"提示: 可以设置环境变量 GEMINI_API_KEY 或在代码中配置 DEFAULT_API_KEY")

# 修改 1：在请求体的数据校验模型中增加 model 字段
class GenerateRequest(BaseModel):
    model: str = "gemini-2.5-flash" # 设置一个默认模型，用户不传时默认使用此模型
    prompt: str
    temperature: float = 0.7

@app.post("/v1/chat/completions")
async def generate_text(request: GenerateRequest):
    """
    接收用户的 prompt 和指定模型，调用对应的 Gemini 模型
    """
    try:
        # 修改 2：使用 request.model 动态传入模型名称
        response = client.models.generate_content(
            model=request.model,
            contents=request.prompt,
            config=genai.types.GenerateContentConfig(
                temperature=request.temperature,
            )
        )
        
        # 返回标准化的 JSON 响应
        return {
            "status": "success",
            "model": request.model, # 返回实际使用的模型名称
            "response": response.text
        }
        
    except Exception as e:
        # 如果调用失败（例如网络问题、Quota 超限或模型名称错误），返回 500 错误
        raise HTTPException(status_code=500, detail=f"调用 Gemini API 失败: {str(e)}")

if __name__ == "__main__":
    # 统一了 print 提示的端口和实际运行的端口
    print("启动服务，监听端口: 6773...")
    uvicorn.run(app, host="0.0.0.0", port=6773)
