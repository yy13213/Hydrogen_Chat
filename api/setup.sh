#!/bin/bash

# Hydrogen Chat API 自动化设置脚本
# 此脚本将创建虚拟环境、安装依赖并启动服务

set -e  # 遇到错误时立即退出

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${GREEN}[信息]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[警告]${NC} $1"
}

print_error() {
    echo -e "${RED}[错误]${NC} $1"
}

# 检查 Python 版本
check_python() {
    print_info "检查 Python 版本..."
    if command -v python3 &> /dev/null; then
        PYTHON_CMD=python3
        PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
        print_info "找到 Python 版本: $PYTHON_VERSION"
    elif command -v python &> /dev/null; then
        PYTHON_CMD=python
        PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
        print_info "找到 Python 版本: $PYTHON_VERSION"
    else
        print_error "未找到 Python，请先安装 Python 3.8 或更高版本"
        exit 1
    fi
}

# 创建虚拟环境
create_venv() {
    VENV_NAME="api_venv"
    
    if [ -d "$VENV_NAME" ]; then
        print_warning "虚拟环境 '$VENV_NAME' 已存在"
        read -p "是否删除并重新创建？(y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            print_info "删除旧的虚拟环境..."
            rm -rf "$VENV_NAME"
        else
            print_info "使用现有虚拟环境"
            return 0
        fi
    fi
    
    print_info "创建虚拟环境 '$VENV_NAME'..."
    $PYTHON_CMD -m venv $VENV_NAME
    
    if [ $? -eq 0 ]; then
        print_info "虚拟环境创建成功"
    else
        print_error "虚拟环境创建失败"
        exit 1
    fi
}

# 激活虚拟环境
activate_venv() {
    VENV_NAME="api_venv"
    print_info "激活虚拟环境..."
    
    if [ -f "$VENV_NAME/bin/activate" ]; then
        source "$VENV_NAME/bin/activate"
        print_info "虚拟环境已激活"
    else
        print_error "未找到激活脚本"
        exit 1
    fi
}

# 安装依赖包
install_dependencies() {
    print_info "升级 pip..."
    pip install --upgrade pip
    
    if [ -f "requirements.txt" ]; then
        print_info "安装依赖包..."
        pip install -r requirements.txt
        
        if [ $? -eq 0 ]; then
            print_info "依赖包安装成功"
        else
            print_error "依赖包安装失败"
            exit 1
        fi
    else
        print_error "未找到 requirements.txt 文件"
        exit 1
    fi
}

# 检查环境变量
check_env_vars() {
    print_info "检查环境变量..."
    
    if [ -z "$GEMINI_API_KEY" ]; then
        print_warning "未设置 GEMINI_API_KEY 环境变量"
        print_warning "Google AI 服务 (port 6773) 需要此环境变量才能正常工作"
        read -p "是否现在设置？(y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            read -p "请输入你的 Gemini API Key: " api_key
            export GEMINI_API_KEY="$api_key"
            print_info "GEMINI_API_KEY 已设置（仅在当前会话有效）"
            print_info "要永久保存，请将以下内容添加到 ~/.bashrc 或 ~/.zshrc："
            echo "export GEMINI_API_KEY=\"$api_key\""
        fi
    else
        print_info "GEMINI_API_KEY 已设置"
    fi
}

# 检查服务文件是否存在
check_service_files() {
    print_info "检查服务文件..."
    
    if [ ! -f "Reranker_dify2vll_port9583.py" ]; then
        print_error "未找到 Reranker_dify2vll_port9583.py"
        exit 1
    fi
    
    if [ ! -f "Google_ai2dify_port6773.py" ]; then
        print_error "未找到 Google_ai2dify_port6773.py"
        exit 1
    fi
    
    print_info "所有服务文件就绪"
}

# 启动服务
start_services() {
    print_info "准备启动服务..."
    echo
    print_warning "服务将在前台运行，按 Ctrl+C 可停止"
    print_warning "如需后台运行，请使用 tmux 或 screen"
    echo
    
    read -p "选择启动模式: [1] 顺序启动（推荐用于测试） [2] 后台启动（需要 tmux） [3] 跳过启动: " -n 1 -r
    echo
    
    case $REPLY in
        1)
            print_info "将依次启动两个服务（先启动 Reranker，然后启动 Google AI）"
            print_info "启动 Reranker 服务 (端口 9583)..."
            echo "----------------------------------------"
            python Reranker_dify2vll_port9583.py &
            RERANKER_PID=$!
            sleep 2
            
            print_info "启动 Google AI 服务 (端口 6773)..."
            echo "----------------------------------------"
            python Google_ai2dify_port6773.py &
            GOOGLE_PID=$!
            
            echo
            print_info "两个服务已启动"
            print_info "Reranker PID: $RERANKER_PID"
            print_info "Google AI PID: $GOOGLE_PID"
            print_warning "按 Ctrl+C 停止所有服务"
            
            # 等待用户中断
            trap "kill $RERANKER_PID $GOOGLE_PID; print_info '服务已停止'; exit 0" INT
            wait
            ;;
        2)
            if command -v tmux &> /dev/null; then
                print_info "使用 tmux 后台启动服务..."
                
                # 启动 Reranker 服务
                tmux new-session -d -s reranker_service "source api_venv/bin/activate && python Reranker_dify2vll_port9583.py"
                print_info "Reranker 服务已在 tmux 会话 'reranker_service' 中启动"
                
                # 启动 Google AI 服务
                tmux new-session -d -s google_ai_service "source api_venv/bin/activate && python Google_ai2dify_port6773.py"
                print_info "Google AI 服务已在 tmux 会话 'google_ai_service' 中启动"
                
                echo
                print_info "查看服务输出："
                echo "  tmux attach -t reranker_service"
                echo "  tmux attach -t google_ai_service"
                print_info "停止服务："
                echo "  tmux kill-session -t reranker_service"
                echo "  tmux kill-session -t google_ai_service"
            else
                print_error "未安装 tmux，请先安装: sudo apt-get install tmux"
                exit 1
            fi
            ;;
        3)
            print_info "跳过服务启动"
            print_info "你可以手动启动服务："
            echo "  source api_venv/bin/activate"
            echo "  python Reranker_dify2vll_port9583.py"
            echo "  python Google_ai2dify_port6773.py"
            ;;
        *)
            print_warning "无效选项，跳过服务启动"
            ;;
    esac
}

# 主函数
main() {
    echo "========================================"
    echo "  Hydrogen Chat API 自动化设置脚本"
    echo "========================================"
    echo
    
    # 检查 Python
    check_python
    
    # 创建虚拟环境
    create_venv
    
    # 激活虚拟环境
    activate_venv
    
    # 安装依赖
    install_dependencies
    
    # 检查环境变量
    check_env_vars
    
    # 检查服务文件
    check_service_files
    
    echo
    print_info "✓ 环境设置完成！"
    echo
    
    # 启动服务
    start_services
}

# 运行主函数
main
