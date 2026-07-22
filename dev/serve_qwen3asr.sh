#!/usr/bin/env bash
# Serve Qwen3-ASR-1.7B for the ensemble cross-check (python -m transcribe ensemble).
# Small enough to share GPU 0 with the resident containers.
#   dev/serve_qwen3asr.sh
set -euo pipefail
GPU="${GPU:-0}"
MODEL="Qwen/Qwen3-ASR-1.7B"
IMAGE="${IMAGE:-vllm/vllm-openai:qwen3_5-cu130-audio}"

docker run --rm --name qwen3asr --gpus "device=${GPU}" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -p 127.0.0.1:8211:8000 \
  "$IMAGE" \
  --model "$MODEL" \
  --served-model-name qwen3-asr \
  --gpu-memory-utilization 0.35 \
  --max-model-len 8192 \
  --max-num-seqs 4 \
  --trust-remote-code \
  --host 0.0.0.0 --port 8000
