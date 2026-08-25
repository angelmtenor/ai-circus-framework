#!/usr/bin/env bash
# Creates/updates the k8s Secrets k8s/base/*.yaml reference by name but deliberately
# never define inline — same "no real credentials committed to git" rule as .env
# itself. Re-run any time .env, infra/traefik/console.htpasswd, or
# infra/seaweedfs/s3.json changes; `kubectl apply` makes this idempotent.
#
# Usage: ./scripts/k3s_generate_secrets.sh
#   - app-env: every KEY=VALUE in .env, imported as one Secret's data (see
#     k8s/base/configmap.yaml's header comment for why pods can safely receive all of
#     it via envFrom even when most keys don't apply to that particular service).
#   - traefik-basicauth: infra/traefik/console.htpasswd, for the admin-basicauth
#     Middleware (k8s/base/middleware.yaml) gating logto-admin/seaweedfs-console.
#   - seaweedfs-s3-config: infra/seaweedfs/s3.json, mounted into the seaweedfs pod.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="ai-circus"

for f in .env infra/traefik/console.htpasswd infra/seaweedfs/s3.json; do
  if [ ! -f "$REPO_ROOT/$f" ]; then
    echo "❌ $f is missing — run 'make bootstrap' first." >&2
    exit 1
  fi
done

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic app-env \
  --from-env-file="$REPO_ROOT/.env" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic traefik-basicauth \
  --from-file=users="$REPO_ROOT/infra/traefik/console.htpasswd" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic seaweedfs-s3-config \
  --from-file=s3.json="$REPO_ROOT/infra/seaweedfs/s3.json" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

echo "▶ app-env, traefik-basicauth, seaweedfs-s3-config Secrets applied in namespace $NAMESPACE"
