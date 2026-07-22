#!/usr/bin/env bash
# Serve Qwen3-Omni-30B-A3B-Instruct-NVFP4 for the smoke test.
# Needs ONE free 32GB card (the ~29GB weights + audio encoder + kv leave little
# headroom). Free a card first, e.g. stop the Qwen35B server, then:
#   GPU=1 dev/serve_qwen3omni.sh
# Then in another shell:  python dev/smoke_qwen3omni.py
#
# The NVFP4 checkpoint is broken out-of-the-box and needs the patched HF
# snapshot + the v0.19-nvfp4-audio image - full recipe in EXPERIMENTS.md E9.
# It also benchmarks SLOWER than the AWQ build on the 5090s; prefer
# serve_qwen3omni_awq.sh unless comparing quantizations.
set -euo pipefail
GPU="${GPU:-0}"
MODEL="catplusplus/Qwen3-Omni-30B-A3B-Instruct-NVFP4"
IMAGE="${IMAGE:-vllm/vllm-openai:v0.19-nvfp4-audio}"

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
