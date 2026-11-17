#!/bin/bash
# 在 DSW 上把训练好的 adapter 合并并转成 Q4_K_M 的 GGUF，再传回仓库。
# 用法：bash export_gguf.sh <输出目录> <仓库> <名字>
# 例如：bash export_gguf.sh /mnt/workspace/out_v5 xiaobang123/xiangke-lora-job-v5 v5
set -e
OUT="$1"; REPO="$2"; NAME="$3"
BASE=/mnt/workspace/models/Spark-X2.5-1.7B
WORK=/mnt/workspace/export_$NAME
LLAMA=/mnt/workspace/llama.cpp
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$MODELSCOPE_API_TOKEN" ]; then echo "ERROR: export MODELSCOPE_API_TOKEN first"; exit 1; fi
if [ ! -f "$OUT/adapter/adapter_model.safetensors" ]; then echo "ERROR: $OUT/adapter 还没有 adapter"; exit 1; fi
mkdir -p "$WORK"

echo "== 1/4 合并 adapter =="
python "$HERE/merge_lora.py" "$BASE" "$OUT/adapter" "$WORK/merged"

echo "== 2/4 准备 llama.cpp =="
if [ ! -d "$LLAMA" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA" || git clone --depth 1 https://gitee.com/mirrors/llama.cpp "$LLAMA"
fi
python -m pip install -q gguf sentencepiece protobuf "numpy<3" 2>/dev/null || true
if [ ! -x "$LLAMA/build/bin/llama-quantize" ]; then
  cmake -S "$LLAMA" -B "$LLAMA/build" -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=OFF >/dev/null
  cmake --build "$LLAMA/build" --target llama-quantize -j 8 >/dev/null
fi

echo "== 3/4 转成 GGUF =="
python "$LLAMA/convert_hf_to_gguf.py" "$WORK/merged" --outtype f16 --outfile "$WORK/f16.gguf"
"$LLAMA/build/bin/llama-quantize" "$WORK/f16.gguf" "$WORK/xiangke-$NAME-Q4_K_M.gguf" Q4_K_M >/dev/null
ls -l "$WORK/xiangke-$NAME-Q4_K_M.gguf"; sha256sum "$WORK/xiangke-$NAME-Q4_K_M.gguf"

echo "== 4/4 上传到仓库 =="
modelscope upload "$REPO" "$WORK/xiangke-$NAME-Q4_K_M.gguf" "gguf/xiangke-$NAME-Q4_K_M.gguf" --repo-type model --commit-message "gguf $NAME"
echo "== EXPORT DONE $NAME =="
