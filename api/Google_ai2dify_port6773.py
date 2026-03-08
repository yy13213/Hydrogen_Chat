from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from google import genai
import uvicorn
import os

# 默认 API Key 配置（可选，方便测试使用）
# 如果不需要默认值，请保持为空字符串
DEFAULT_API_KEY = "AQ.Ab8RN6IS3rS-OE83s_ZhLeK4rRu8tGAhnVROpg3BwHjtTwvgYg"

# 初始化 FastAPI 应用
app = FastAPI(title="Gemini 3.1 API Proxy", description="A simple API wrapper for Gemini 3.1")

# 初始化 Gemini 客户端
# 优先使用环境变量 GEMINI_API_KEY，如果未设置则使用默认值
api_key = os.getenv("GEMINI_API_KEY", DEFAULT_API_KEY)

try:
    if api_key:
        client = genai.Client(api_key=api_key)
        if os.getenv("GEMINI_API_KEY"):
            print("✓ 使用环境变量中的 GEMINI_API_KEY")
        else:
            print("⚠ 使用默认的 API Key（环境变量未设置）")
    else:
        # 如果两者都为空，尝试让客户端自动读取
        client = genai.Client()
        print("✓ 让 Gemini 客户端自动检测 API Key")
except Exception as e:
    print(f"❌ 初始化 Gemini 客户端失败，请检查 API Key 配置。错误信息: {e}")
    print(f"提示: 可以设置环境变量 GEMINI_API_KEY 或在代码中配置 DEFAULT_API_KEY")

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