# Hydrogen Chat API 更新日志

## 最新更新 (2026-03-08)

### 🎉 新增功能

#### 1. 环境变量配置支持
- ✅ 添加 `.env` 文件支持
- ✅ 创建 `.env.example` 模板文件
- ✅ 自动从 `.env` 加载配置
- ✅ 支持配置端口和 API Key

**相关文件：**
- `api/.env` - 环境变量配置文件（不提交到 Git）
- `api/.env.example` - 环境变量模板文件
- `.gitignore` - 已添加 `.env` 忽略规则

**环境变量：**
```bash
GEMINI_API_KEY=           # Google Gemini API Key
VLLM_API_URL=             # vLLM Reranker 服务地址
GOOGLE_AI_PORT=6773       # Google AI 服务端口
RERANKER_PORT=9583        # Reranker 服务端口
```

#### 2. Gemini 原生 API 格式支持

**新增端点：**

##### 非流式端点
```
POST /v1beta/models/{model_name}:generateContent
POST /v1/v1beta/models/{model_name}:generateContent
```

**功能特性：**
- ✅ 完整支持 Gemini 原生请求格式
- ✅ 支持 `contents` 数组格式
- ✅ 支持请求头 API Key 认证 (`x-goog-api-key`)
- ✅ 支持安全设置 (`safetySettings`)
- ✅ 支持系统指令 (`systemInstruction`)
- ✅ 支持工具调用 (`tools`)

##### 流式端点
```
POST /v1beta/models/{model_name}:streamGenerateContent
POST /v1/v1beta/models/{model_name}:streamGenerateContent
```

**流式响应特性：**
- ✅ 支持 SSE (Server-Sent Events) 格式
- ✅ 支持查询参数 `?alt=sse`
- ✅ 实时流式输出文本
- ✅ 自动处理流结束标记

#### 3. 高级配置支持

**generationConfig 高级参数：**
- ✅ `temperature` - 温度参数
- ✅ `maxOutputTokens` - 最大输出 Token 数
- ✅ `topP` - Top-P 采样
- ✅ `topK` - Top-K 采样
- ✅ `thinkingConfig` - 思考模式配置
  - `include_thoughts` - 是否包含思考过程
  - `thinking_level` - 思考级别 (LOW/MEDIUM/HIGH)
- ✅ `mediaResolution` - 媒体分辨率设置

#### 4. 默认 API Key 配置

**代码内配置：**
```python
# 在 Google_ai2dify_port6773.py 中
DEFAULT_API_KEY = ""  # 可填入测试用的 API Key
```

**优先级：**
1. 请求头中的 `x-goog-api-key`
2. 环境变量 `GEMINI_API_KEY`
3. 代码中的 `DEFAULT_API_KEY`

### 🔧 改进优化

1. **代码结构优化**
   - 提取配置构建函数 `build_config_params()`
   - 统一客户端选择逻辑
   - 改进错误处理和日志输出

2. **依赖管理**
   - 添加 `python-dotenv` 依赖
   - 更新 `requirements.txt`

3. **文档更新**
   - 更新 `QUICKSTART.md` 添加新功能说明
   - 添加流式 API 测试示例
   - 添加环境变量配置说明

### 📦 文件清单

**新增文件：**
- `api/.env` - 环境变量配置
- `api/.env.example` - 环境变量模板
- `api/CHANGELOG.md` - 更新日志（本文件）

**修改文件：**
- `api/Google_ai2dify_port6773.py` - 主要功能更新
- `api/Reranker_dify2vll_port9583.py` - 添加环境变量支持
- `api/requirements.txt` - 添加依赖
- `api/QUICKSTART.md` - 文档更新
- `.gitignore` - 添加忽略规则

### 🚀 使用示例

#### 简化格式（原有）
```bash
curl -X POST http://localhost:6773/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "你好",
    "temperature": 0.7,
    "model": "gemini-3-flash-preview"
  }'
```

#### Gemini 原生格式（非流式）
```bash
curl -X POST http://localhost:6773/v1beta/models/gemini-3-flash-preview:generateContent \
  -H "Content-Type: application/json" \
  -H "x-goog-api-key: YOUR_API_KEY" \
  -d '{
    "contents": [{"parts": [{"text": "你好"}], "role": "user"}],
    "generationConfig": {"temperature": 0.7}
  }'
```

#### Gemini 原生格式（流式 SSE）
```bash
curl -N -X POST "http://localhost:6773/v1beta/models/gemini-3-flash-preview:streamGenerateContent?alt=sse" \
  -H "Content-Type: application/json" \
  -H "x-goog-api-key: YOUR_API_KEY" \
  -d '{
    "contents": [{"parts": [{"text": "讲一个故事"}], "role": "user"}],
    "generationConfig": {
      "temperature": 0.7,
      "maxOutputTokens": 2048,
      "thinkingConfig": {
        "include_thoughts": false,
        "thinking_level": "HIGH"
      }
    }
  }'
```

### ⚠️ 重要提示

1. **安全性**
   - `.env` 文件包含敏感信息，已自动添加到 `.gitignore`
   - 生产环境建议只使用环境变量，不要在代码中配置 `DEFAULT_API_KEY`
   - 请求头 API Key 优先级最高，适合多租户场景

2. **兼容性**
   - 保持向后兼容，原有的 `/v1/chat/completions` 端点完全可用
   - 新功能可选使用，不影响现有代码

3. **性能**
   - 流式响应适合长文本生成
   - 使用 SSE 格式可获得更好的实时体验

### 🔮 未来计划

- [ ] 添加更多模型支持
- [ ] 实现请求缓存
- [ ] 添加速率限制
- [ ] 支持批量请求
- [ ] 添加监控和日志记录

---

**维护者**: Hydrogen Chat Team  
**更新日期**: 2026-03-08
