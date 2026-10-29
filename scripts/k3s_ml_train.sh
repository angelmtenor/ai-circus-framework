#!/usr/bin/env bash
# Train only some tabular_ml scenarios (e.g. the ones a data product exports through
# `ml_exports`) with the same ETL -> training Jobs as `make k3s-pipeline`, which trains every
# scenario: SCENARIOS is set on the Jobs' manifests before they are applied.
#
# Usage: scripts/k3s_ml_train.sh <slug>[,<slug>...]
set -euo pipefail

SCENARIOS="${1:?usage: k3s_ml_train.sh <slug>[,<slug>...]}"
[[ "$SCENARIOS" =~ ^[a-z0-9_,]+$ ]] || { echo "❌ unsafe scenario list"; exit 1; }
NS=ai-circus
for job in etl-tabular training; do
    kubectl -n "$NS" delete job "$job" --ignore-not-found >/dev/null
    kubectl set env --local -f "k8s/jobs/${job}-job.yaml" "SCENARIOS=$SCENARIOS" -o yaml | kubectl apply -f - >/dev/null
    echo "▶ $job ($SCENARIOS)"
    kubectl -n "$NS" wait --for=condition=complete "job/$job" --timeout=900s >/dev/null
done
kubectl -n "$NS" rollout restart deployment/prediction >/dev/null
kubectl -n "$NS" rollout status deployment/prediction --timeout=300s >/dev/null
echo "✓ trained and served: $SCENARIOS"
