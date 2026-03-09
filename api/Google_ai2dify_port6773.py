from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from google import genai
import uvicorn
import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

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

# 原有的简化请求格式
class GenerateRequest(BaseModel):
    model: str = "gemini-3-pro-preview"
    prompt: str
    temperature: float = 0.7

# Gemini 原生 API 请求格式
class GeminiNativeRequest(BaseModel):
    contents: List[Dict[str, Any]]
    generationConfig: Optional[Dict[str, Any]] = None
    safetySettings: Optional[List[Dict[str, Any]]] = None
    systemInstruction: Optional[Dict[str, Any]] = None

@app.post("/v1/chat/completions")
async def generate_text(request: GenerateRequest):
    """
    接收用户的 prompt 和指定模型，调用对应的 Gemini 模型（简化格式）
    """
    try:
        response = client.models.generate_content(
            model=request.model,
            contents=request.prompt,
            config=genai.types.GenerateContentConfig(
                temperature=request.temperature,
            )
        )
        
        return {
            "status": "success",
            "model": request.model,
            "response": response.text
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"调用 Gemini API 失败: {str(e)}")


@app.post("/v1/v1beta/models/{model_name}:generateContent")
@app.post("/v1beta/models/{model_name}:generateContent")
async def gemini_native_generate(
    model_name: str,
    request: GeminiNativeRequest,
    x_goog_api_key: Optional[str] = Header(None)
):
    """
    支持 Gemini 原生 API 格式的端点
    路径示例：/v1/v1beta/models/gemini-2.5-flash:generateContent
    """
    try:
        # 如果请求头中提供了 API key，使用该 key 创建新的客户端
        if x_goog_api_key:
            temp_client = genai.Client(api_key=x_goog_api_key)
            print(f"🔑 使用请求头中的 API Key")
        else:
            temp_client = client
            print(f"🔑 使用默认配置的 API Key")
        
        # 构建配置参数
        config_params = {}
        if request.generationConfig:
            gen_config = request.generationConfig
            if "temperature" in gen_config:
                config_params["temperature"] = gen_config["temperature"]
            if "maxOutputTokens" in gen_config:
                config_params["max_output_tokens"] = gen_config["maxOutputTokens"]
            if "topP" in gen_config:
                config_params["top_p"] = gen_config["topP"]
            if "topK" in gen_config:
                config_params["top_k"] = gen_config["topK"]
        
        # 添加 safety_settings 到配置中
        if request.safetySettings:
            config_params["safety_settings"] = request.safetySettings
        
        # 添加 system_instruction 到配置中
        if request.systemInstruction:
            config_params["system_instruction"] = request.systemInstruction
        
        # 调用 Gemini API
        response = temp_client.models.generate_content(
            model=model_name,
            contents=request.contents,
            config=genai.types.GenerateContentConfig(**config_params) if config_params else None
        )
        
        # 返回符合 Gemini 原生格式的响应
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": response.text}],
                        "role": "model"
                    },
                    "finishReason": "STOP",
                    "index": 0
                }
            ],
            "usageMetadata": {
                "promptTokenCount": getattr(response, "prompt_token_count", 0),
                "candidatesTokenCount": getattr(response, "candidates_token_count", 0),
                "totalTokenCount": getattr(response, "total_token_count", 0)
            }
        }
        
    except Exception as e:
        print(f"❌ 调用失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"调用 Gemini API 失败: {str(e)}")

if __name__ == "__main__":
    # 从环境变量读取端口配置，默认为 6773
    port = int(os.getenv("GOOGLE_AI_PORT", 6773))
    print(f"启动服务，监听端口: {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
