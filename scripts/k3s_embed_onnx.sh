#!/usr/bin/env bash
# Install (or remove) the ONNX export of the `local-embed` model on llm-gateway's model volume, then
# restart the gateway so it loads it. Usage: scripts/k3s_embed_onnx.sh int8|fp32|off
#
# int8: ~350 MB, 14x faster than torch on CPU, but its vectors differ slightly from the original model's
#       (cosine ~0.994) — documents already in Qdrant should be re-vectorized afterwards (etl-vectorize).
# fp32: bit-for-bit the original vectors (cosine 1.0), but ~1.4 GB resident — more than torch needs, and
#       over the pod's memory limit; only for a machine that raised it.
# off:  remove the export; the gateway falls back to in-process torch.
set -euo pipefail

variant="${1:-}"
namespace="${K3S_NAMESPACE:-ai-circus}"
src="${EMBED_ONNX_SRC:-$HOME/.cache/ai-circus/onnx/voyage-4-nano}"
dest="/models/hf/onnx/voyage-4-nano"

case "$variant" in
	int8) file="model.int8.onnx" ;;
	fp32) file="model.onnx" ;;
	off)
		kubectl -n "$namespace" exec deploy/llm-gateway -- rm -rf "$dest"
		kubectl -n "$namespace" rollout restart deployment/llm-gateway
		echo "✓ ONNX export removed — llm-gateway restarts on torch"
		exit 0
		;;
	*) echo "usage: $0 int8|fp32|off" >&2; exit 2 ;;
esac

if [ ! -f "$src/$file" ]; then
	echo "→ no export in $src — running scripts/export_local_embed_onnx.py (downloads torch once; needs ~4 GB free RAM)"
	uv run "$(dirname "$0")/export_local_embed_onnx.py" "$src"
fi

kubectl -n "$namespace" exec deploy/llm-gateway -- sh -c "rm -rf $dest && mkdir -p $dest"
for pair in "$file:model.onnx" "tokenizer.json:tokenizer.json"; do
	from="${pair%%:*}"
	to="${pair##*:}"
	kubectl -n "$namespace" exec -i deploy/llm-gateway -- sh -c "cat > $dest/$to" < "$src/$from"
done
kubectl -n "$namespace" rollout restart deployment/llm-gateway
kubectl -n "$namespace" rollout status deployment/llm-gateway --timeout=300s
echo "✓ local-embed now runs on ONNX Runtime ($variant)"
