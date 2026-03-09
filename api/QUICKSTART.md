# Hydrogen Chat API 快速启动指南

本指南将帮助你快速部署和运行 Hydrogen Chat API 服务。

## 📋 前置要求

- Python 3.8 或更高版本
- Linux/macOS 操作系统（Windows 用户请使用 WSL）
- 网络连接（用于下载依赖包）

## 🚀 快速启动

### 方法一：使用自动化脚本（推荐）

1. 进入 api 目录：
```bash
cd api
```

2. 配置环境变量（首次运行必须）：
```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入你的 API Key
nano .env  # 或使用 vim, vi 等编辑器
```

3. 赋予脚本执行权限：
```bash
chmod +x setup.sh
```

4. 运行设置脚本：
```bash
./setup.sh
```

脚本将自动完成以下操作：
- 创建 Python 虚拟环境（hydrogen_chat_api）
- 激活虚拟环境
- 安装所有依赖包
- 启动两个 API 服务

### 方法二：手动安装

如果你希望手动控制每一步，请按照以下步骤操作：

1. **配置环境变量**
```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入你的 Gemini API Key
nano .env
```

2. **创建虚拟环境**
```bash
python3 -m venv hydrogen_chat_api
```

3. **激活虚拟环境**
```bash
source hydrogen_chat_api/bin/activate
```

4. **安装依赖包**
```bash
pip install -r requirements.txt
```

5. **启动服务**

在两个不同的终端窗口中分别运行：

终端 1 - Reranker 服务（端口 9583）：
```bash
source hydrogen_chat_api/bin/activate
python Reranker_dify2vll_port9583.py
```

终端 2 - Google AI 服务（端口 6773）：
```bash
source hydrogen_chat_api/bin/activate
python Google_ai2dify_port6773.py
```

## 📦 服务说明

### 1. Reranker 服务 (端口 9583)
- **文件**: `Reranker_dify2vll_port9583.py`
- **端口**: 9583
- **功能**: 将 Dify 的 Reranker 请求转换为 vLLM 格式
- **依赖**: 需要本地运行的 vLLM 服务（端口 9582）
- **API 端点**: `POST /v1/rerank`

### 2. Google AI 服务 (端口 6773)
- **文件**: `Google_ai2dify_port6773.py`
- **端口**: 6773
- **功能**: Google Gemini API 代理服务
- **环境变量**: 需要设置 `GEMINI_API_KEY`
- **API 端点**: `POST /v1/chat/completions`

## 🔧 配置说明

### Google AI 服务配置

在运行 `Google_ai2dify_port6773.py` 之前，需要设置 Gemini API 密钥：

```bash
export GEMINI_API_KEY="your_api_key_here"
```

或者将其添加到 `~/.bashrc` 或 `~/.zshrc` 中以永久保存：

```bash
echo 'export GEMINI_API_KEY="your_api_key_here"' >> ~/.bashrc
source ~/.bashrc
```

### Reranker 服务配置

Reranker 服务需要本地运行的 vLLM 服务：
- 默认地址：`http://127.0.0.1:9582/v1/completions`
- 如需修改，请编辑 `Reranker_dify2vll_port9583.py` 中的 `VLLM_API_URL` 变量

## 🧪 测试服务

### 测试 Google AI 服务

```bash
curl -X POST http://localhost:6773/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "你好，请介绍一下自己",
    "temperature": 0.7
  }'
```

### 测试 Reranker 服务

```bash
curl -X POST http://localhost:9583/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "query": "什么是人工智能？",
    "documents": ["人工智能是计算机科学的一个分支", "今天天气很好"],
    "model": "Qwen3-Reranker-0.6B",
    "top_n": 2
  }'
```

## 🛑 停止服务

在运行服务的终端中按 `Ctrl+C` 即可停止服务。

如果服务在后台运行，可以使用：

```bash
# 查找进程
ps aux | grep python

# 终止进程（替换 <PID> 为实际进程 ID）
kill <PID>
```

## ⚠️ 常见问题

### 1. 端口已被占用

如果遇到端口占用错误，可以：
- 修改 Python 文件中的端口号
- 或终止占用端口的进程

```bash
# 查找占用端口的进程
lsof -i :9583
lsof -i :6773

# 终止进程
kill -9 <PID>
```

### 2. 虚拟环境激活失败

确保使用正确的激活命令：
- Linux/macOS: `source hydrogen_chat_api/bin/activate`
- Windows (Git Bash): `source hydrogen_chat_api/Scripts/activate`

### 3. 依赖包安装失败

尝试升级 pip 后重新安装：
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 📝 后续开发

退出虚拟环境：
```bash
deactivate
```

重新激活虚拟环境：
```bash
source hydrogen_chat_api/bin/activate
```

## 📞 获取帮助

如有问题，请检查：
1. Python 版本是否符合要求
2. 所有依赖包是否正确安装
3. 环境变量是否正确设置
4. 端口是否被其他程序占用
