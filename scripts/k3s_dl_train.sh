#!/usr/bin/env bash
# Train ONE deep_learning scenario as an in-cluster Job (`make k3s-dl-train SCENARIO=…`):
# renders k8s/jobs/dl-training-job.yaml and, when a node advertises nvidia.com/gpu, adds
# `runtimeClassName: nvidia` + one GPU — the same Job data-platform-manager's admin
# "Train in cluster" button builds (core/k8s_jobs.py). Without a GPU the Job uses the
# scenario's reduced CPU budget.
#
# Usage: scripts/k3s_dl_train.sh <deep_learning slug> [timeout seconds]
set -euo pipefail

SCENARIO="${1:?usage: k3s_dl_train.sh <deep_learning slug> [timeout]}"
TIMEOUT="${2:-3600}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOB="dl-training-${SCENARIO//_/-}"

gpus=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}' 2>/dev/null \
    | awk '{s+=$1} END {print s+0}')
render=(-e "s/__JOB_NAME__/$JOB/" -e "s/__SCENARIOS__/$SCENARIO/g")
if [ "$gpus" -gt 0 ]; then
    render+=(-e 's/^\(      restartPolicy: Never\)$/\1\n      runtimeClassName: nvidia/')
    # 6Gi, not 4Gi: CUDA torch keeps ~1.5-2 GB of runtime in host RAM, on top of the
    # model, during the ONNX export — a 4Gi GPU Job gets OOM-killed right after training.
    render+=(-e 's#limits: {cpu: "4", memory: 4Gi}#limits: {cpu: "4", memory: 6Gi, nvidia.com/gpu: 1}#')
    device="GPU"
else
    device="CPU (reduced budget)"
fi

kubectl -n ai-circus delete job "$JOB" --ignore-not-found
sed "${render[@]}" "$REPO_ROOT/k8s/jobs/dl-training-job.yaml" | kubectl apply -f -
echo "⏳ $JOB training on $device (logs: kubectl -n ai-circus logs -f job/$JOB)"
# Poll for success OR failure — `kubectl wait --for=condition=complete` alone never
# returns for a failed Job (e.g. OOMKilled) until the full timeout.
deadline=$((SECONDS + TIMEOUT))
while [ $SECONDS -lt $deadline ]; do
    status=$(kubectl -n ai-circus get job "$JOB" -o jsonpath='{.status.succeeded}/{.status.failed}')
    case "$status" in
        1/*) echo "✓ $JOB complete"; exit 0 ;;
        */[1-9]*)
            echo "❌ $JOB failed — last log lines:" >&2
            kubectl -n ai-circus logs "job/$JOB" --tail=15 >&2 || true
            kubectl -n ai-circus get pods -l "job-name=$JOB" \
                -o jsonpath='{range .items[*]}{.status.containerStatuses[0].state.terminated.reason}{"\n"}{end}' >&2
            exit 1 ;;
    esac
    sleep 10
done
echo "❌ $JOB still running after ${TIMEOUT}s" >&2
exit 1
