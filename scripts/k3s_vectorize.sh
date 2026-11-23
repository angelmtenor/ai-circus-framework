#!/usr/bin/env bash
# Index documents into Qdrant with the etl-vectorize Job. With no argument it indexes every
# document-backed scenario (~20 min on CPU); with a comma-separated list, only those: SCENARIOS
# is set on the Job's manifest before it is applied, as scripts/k3s_ml_train.sh does for
# training. The manifest itself stays free of SCENARIOS, so it keeps matching the Job that
# data-platform-manager's core/k8s_jobs.py builds.
#
# Usage: scripts/k3s_vectorize.sh [<slug>[,<slug>...]]
set -euo pipefail

SCENARIOS="${1:-}"
NS=ai-circus
JOB_YAML=k8s/jobs/etl-vectorize-job.yaml
kubectl -n "$NS" delete job etl-vectorize --ignore-not-found >/dev/null
if [[ -z "$SCENARIOS" ]]; then
    kubectl apply -f "$JOB_YAML" >/dev/null
    echo "▶ etl-vectorize (every scenario with documents, ~20 min on CPU)"
else
    [[ "$SCENARIOS" =~ ^[a-z0-9_,]+$ ]] || { echo "❌ unsafe scenario list"; exit 1; }
    kubectl set env --local -f "$JOB_YAML" "SCENARIOS=$SCENARIOS" -o yaml | kubectl apply -f - >/dev/null
    echo "▶ etl-vectorize ($SCENARIOS)"
fi
kubectl -n "$NS" wait --for=condition=complete job/etl-vectorize --timeout=1800s >/dev/null
echo "✓ documents indexed: ${SCENARIOS:-every scenario with documents}"
