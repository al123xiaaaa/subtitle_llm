#!/bin/bash
# 启动 Hy-MT2-7B 本地翻译服务
# 用法: ./start-hy-mt2-7b.sh [port]
set -e

MODEL_DIR="/Users/xiaguangwei/.cache/hy-mt2"
MODEL_REPO="tencent/Hy-MT2-7B-GGUF"
MODEL_NAME="Hy-MT2-7B-Q4_K_M.gguf"
MODEL="$MODEL_DIR/$MODEL_NAME"
PORT="${1:-8123}"

if [ ! -f "$MODEL" ]; then
    echo "模型文件不存在，开始下载: $MODEL"
    mkdir -p "$MODEL_DIR"
    if command -v hf >/dev/null 2>&1; then
        hf download "$MODEL_REPO" "$MODEL_NAME" --local-dir "$MODEL_DIR"
    elif command -v huggingface-cli >/dev/null 2>&1; then
        huggingface-cli download "$MODEL_REPO" "$MODEL_NAME" --local-dir "$MODEL_DIR"
    else
        echo "❌ 找不到 Hugging Face CLI。请先安装: pip install -U huggingface_hub"
        echo "然后重试: ./start-hy-mt2-7b.sh"
        exit 1
    fi

    if [ ! -f "$MODEL" ]; then
        echo "❌ 下载完成后仍未找到模型文件: $MODEL"
        echo "可手动执行: hf download $MODEL_REPO $MODEL_NAME --local-dir $MODEL_DIR"
        exit 1
    fi
fi

# 检查端口是否已被占用，避免误连到旧的 1.8B 服务。
if lsof -i ":$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 $PORT 已有服务在运行，正在检查模型..."
    MODELS_JSON="$(curl -s "http://127.0.0.1:$PORT/v1/models" || true)"
    if [ -n "$MODELS_JSON" ]; then
        echo "$MODELS_JSON" | python3 -m json.tool 2>/dev/null || echo "$MODELS_JSON"
    fi
    if echo "$MODELS_JSON" | grep -Fq "$MODEL_NAME"; then
        echo "当前端口已经是 $MODEL_NAME。"
        exit 0
    fi
    echo "端口 $PORT 被其它模型占用，请停止旧服务或换一个端口。"
    exit 1
fi

# 激活 venv
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"

# 加载 .env 中的 API keys
set -a; source "$SCRIPT_DIR/.env" 2>/dev/null; set +a

echo "🚀 启动 Hy-MT2-7B 本地翻译服务 (端口: $PORT)..."
echo "   模型: $MODEL (Q4_K_M 量化)"
echo "   n_ctx: 8192 (支持较长翻译 prompt)"
echo "   如果加载失败，请升级到支持 Hy-MT2 STQ kernel 的 llama.cpp / llama-cpp-python 版本。"
echo "   按 Ctrl+C 停止"
echo ""

export HYMT2_API_KEY="local-no-key-needed"
python3 -m llama_cpp.server \
    --model "$MODEL" \
    --n_gpu_layers 0 \
    --n_ctx 8192 \
    --host 127.0.0.1 \
    --port "$PORT"
