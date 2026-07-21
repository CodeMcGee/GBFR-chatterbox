#!/usr/bin/env bash
# Serve Qwen3-Omni-30B-A3B-Instruct AWQ-4bit for the smoke test.
# Unlike the NVFP4 build (27GB, needs a free card), this fits GPU 0's spare
# ~26GB alongside paddleocr+parakeet — no prod containers touched.
#   dev/serve_qwen3omni_awq.sh
# Then in another shell:  python dev/smoke_qwen3omni.py --base http://localhost:8210/v1
set -euo pipefail
GPU="${GPU:-0}"
MODEL="cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit"
IMAGE="${IMAGE:-vllm/vllm-openai:nightly}"

docker run --rm --name qwen3omni-smoke --gpus "device=${GPU}" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -p 127.0.0.1:8210:8000 \
  "$IMAGE" \
  --model "$MODEL" \
  --served-model-name qwen3-omni \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.72 \
  --max-model-len 8192 \
  --max-num-seqs 4 \
  --trust-remote-code \
  --host 0.0.0.0 --port 8000
