#!/usr/bin/env bash
# Serve Qwen3-Omni-30B-A3B-Instruct-NVFP4 for the smoke test.
# Needs ONE free 32GB card (the ~29GB weights + audio encoder + kv leave little
# headroom). Free a card first, e.g. stop the Qwen35B server, then:
#   GPU=1 dev/serve_qwen3omni.sh
# Then in another shell:  python dev/smoke_qwen3omni.py
#
# Uses the vllm docker image already on this box. Qwen3-Omni needs a recent vllm;
# if 'nightly' rejects the arch, try the qwen3_5-cu130 tag.
set -euo pipefail
GPU="${GPU:-0}"
MODEL="catplusplus/Qwen3-Omni-30B-A3B-Instruct-NVFP4"
IMAGE="${IMAGE:-vllm/vllm-openai:nightly}"

docker run --rm --gpus "device=${GPU}" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -p 8000:8000 \
  "$IMAGE" \
  --model "$MODEL" \
  --served-model-name qwen3-omni \
  --quantization compressed-tensors \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95 \
  --max-model-len 8192 \
  --max-num-seqs 8 \
  --trust-remote-code \
  --host 0.0.0.0 --port 8000
