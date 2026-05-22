#!/bin/bash
# 启动 Hy-MT2-1.8B 本地翻译服务
# 用法: ./start-hy-mt2.sh [port]
set -e

MODEL="/Users/xiaguangwei/.cache/hy-mt2/Hy-MT2-1.8B-Q6_K.gguf"
PORT="${1:-8123}"

if [ ! -f "$MODEL" ]; then
    echo "❌ 模型文件不存在: $MODEL"
    echo "请先下载: hf download tencent/Hy-MT2-1.8B-GGUF Hy-MT2-1.8B-Q6_K.gguf --local-dir ~/.cache/hy-mt2"
    exit 1
fi

# 检查端口是否已被占用
if lsof -i ":$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "✅ 端口 $PORT 已有服务在运行"
    curl -s "http://127.0.0.1:$PORT/v1/models" | python3 -m json.tool 2>/dev/null
    exit 0
fi

# 激活 venv
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"

# 加载 .env 中的 API keys
set -a; source "$SCRIPT_DIR/.env" 2>/dev/null; set +a

echo "🚀 启动 Hy-MT2-1.8B 本地翻译服务 (端口: $PORT)..."
echo "   模型: $MODEL (Q6_K 量化, ~1.4G)"
echo "   n_ctx: 8192 (支持较长翻译 prompt)"
echo "   按 Ctrl+C 停止"
echo ""

export HYMT2_API_KEY="local-no-key-needed"
python3 -m llama_cpp.server \
    --model "$MODEL" \
    --n_gpu_layers 0 \
    --n_ctx 8192 \
    --host 127.0.0.1 \
    --port "$PORT"
