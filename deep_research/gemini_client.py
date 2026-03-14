"""
gemini_client.py — 统一 Gemini 客户端
使用最新 google-genai SDK（from google import genai）
所有模块通过此处获取 client 和 MODEL 常量
"""

import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "placeholder")
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "http://localhost:6773")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(base_url=GEMINI_BASE_URL),
)
