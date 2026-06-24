#!/usr/bin/env bash
# 安装 FunASR Python SDK（ASR 依赖）。
#
# ADR 0003: 从 llama.cpp 二进制回切到 funasr Python SDK。
# SDK 方案通过 language 参数约束语言（杜绝跨语言幻觉）、punc_model 恢复标点、
# sentence_info 提供带时间戳分段，全面优于 llama.cpp 二进制路径。
# 代价是重新引入 PyTorch；CPU 可跑（SenseVoice 17 倍实时）。
#
# 用法：  bash scripts/setup-funasr.sh
# 模型在首次转写时自动从 HuggingFace 下载（约 1GB），无需手动获取。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# 优先用项目 venv
PYTHON="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi
echo "Python: $PYTHON"

echo "[1/2] 安装 funasr（含 PyTorch CPU 版）"
"$PYTHON" -m pip install funasr

echo "[2/2] 自检"
"$PYTHON" -c "
import funasr
print(f'  ✓ funasr {funasr.__version__}')
from funasr import AutoModel
print('  ✓ AutoModel 可导入')
import torch
print(f'  ✓ torch {torch.__version__} (CPU: {not torch.cuda.is_available()})')
"

echo ""
echo "完成。首次转写时会自动下载模型（约 1GB）到 HuggingFace 缓存目录。"
echo "如需重装：bash scripts/setup-funasr.sh"
