#!/usr/bin/env bash
# Creates/updates the k8s Secrets k8s/base/*.yaml + k8s/jobs/*.yaml reference by name
# but deliberately never define inline — same "no real credentials committed to git"
# rule as .env itself. Re-run any time .env, infra/traefik/console.htpasswd, or
# infra/seaweedfs/s3.json changes; `kubectl apply` makes this idempotent.
#
# Usage: ./scripts/k3s_generate_secrets.sh
#
# .env stays the single source of truth (`make bootstrap` is unchanged) — this script
# fans it out into one small Secret per workload instead of one `app-env` blob every
# pod received via envFrom (that blanket approach meant a compromise of the lowest-
# trust service leaked every credential in the system, including a shared Postgres
# user with GRANT ALL on every database). The KEY lists below mirror docker-
# compose.yml's own per-service `environment:` blocks exactly — each service there
# already only lists the keys it uses. Keep the two in sync by hand if either changes:
#   - <service>-secrets / postgres-credentials / logto-secrets: one Secret per
#     workload, each containing only the .env keys that workload actually reads.
#   - traefik-basicauth: infra/traefik/console.htpasswd, for the admin-basicauth
#     Middleware (k8s/base/middleware.yaml) gating logto-admin/seaweedfs-console.
#   - seaweedfs-s3-config: infra/seaweedfs/s3.json, mounted into the seaweedfs pod.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="ai-circus"
ENV_FILE="$REPO_ROOT/.env"

for f in .env infra/traefik/console.htpasswd infra/seaweedfs/s3.json; do
  if [ ! -f "$REPO_ROOT/$f" ]; then
    echo "❌ $f is missing — run 'make bootstrap' first." >&2
    exit 1
  fi
done

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# "<secret-name>|COMMA,SEPARATED,ENV,KEYS" — keys are pulled out of the shared .env by
# name, one Secret per workload, so nobody has to hand-copy values anywhere.
SECRET_SPECS=(
  "postgres-credentials|POSTGRES_USER,POSTGRES_PASSWORD,POSTGRES_DB"
  "logto-secrets|POSTGRES_USER,POSTGRES_PASSWORD,LOGTO_ENDPOINT_COOKIE_SECRET"
  "platform-registry-secrets|POSTGRES_USER,POSTGRES_PASSWORD,ADMIN_API_KEY,ENGINEERING_DEMO_API_KEY,LITELLM_MASTER_KEY,AUTH_DISABLED,LOGTO_ISSUER,LOGTO_API_RESOURCE_INDICATOR"
  "llm-gateway-secrets|LITELLM_MASTER_KEY,OPENAI_API_KEY,GOOGLE_API_KEY,AZURE_OPENAI_API_KEY,AZURE_OPENAI_API_BASE,DEEPSEEK_API_KEY,GROQ_API_KEY,OPENROUTER_API_KEY,ANTHROPIC_API_KEY"
  "prediction-secrets|OBJECT_STORE_ACCESS_KEY,OBJECT_STORE_SECRET_KEY,AUTH_DISABLED,ADMIN_API_KEY,ENGINEERING_DEMO_API_KEY,LOGTO_ISSUER,LOGTO_API_RESOURCE_INDICATOR"
  "assistant-secrets|POSTGRES_USER,POSTGRES_PASSWORD,OBJECT_STORE_ACCESS_KEY,OBJECT_STORE_SECRET_KEY,LITELLM_MASTER_KEY,AUTH_DISABLED,ADMIN_API_KEY,ENGINEERING_DEMO_API_KEY,LOGTO_ISSUER,LOGTO_API_RESOURCE_INDICATOR"
  "rag-agent-secrets|POSTGRES_USER,POSTGRES_PASSWORD,GOOGLE_API_KEY,VOYAGE_API_KEY,LITELLM_MASTER_KEY,AUTH_DISABLED,ADMIN_API_KEY,ENGINEERING_DEMO_API_KEY,LOGTO_ISSUER,LOGTO_API_RESOURCE_INDICATOR"
  "form-agent-secrets|POSTGRES_USER,POSTGRES_PASSWORD,GOOGLE_API_KEY,VOYAGE_API_KEY,OBJECT_STORE_ACCESS_KEY,OBJECT_STORE_SECRET_KEY,LITELLM_MASTER_KEY,AUTH_DISABLED,ADMIN_API_KEY,ENGINEERING_DEMO_API_KEY,LOGTO_ISSUER,LOGTO_API_RESOURCE_INDICATOR"
  "agui-voice-secrets|VOICE_STT_PROVIDER,VOICE_TTS_PROVIDER,VOICE_WHISPER_MODEL,VOICE_PIPER_VOICE_ID,VOICE_PIPER_VOICE_ID_ES,DEEPGRAM_API_KEY,ELEVENLABS_API_KEY,ELEVENLABS_VOICE_ID,CARTESIA_API_KEY,CARTESIA_VOICE_ID,AUTH_DISABLED,ADMIN_API_KEY,ENGINEERING_DEMO_API_KEY,LOGTO_ISSUER,LOGTO_API_RESOURCE_INDICATOR"
  "etl-tabular-secrets|OBJECT_STORE_ACCESS_KEY,OBJECT_STORE_SECRET_KEY"
  "etl-vectorize-secrets|OBJECT_STORE_ACCESS_KEY,OBJECT_STORE_SECRET_KEY,GOOGLE_API_KEY,VOYAGE_API_KEY,LITELLM_MASTER_KEY"
  "training-secrets|OBJECT_STORE_ACCESS_KEY,OBJECT_STORE_SECRET_KEY"
)

for spec in "${SECRET_SPECS[@]}"; do
  name="${spec%%|*}"
  keys="${spec#*|}"
  pattern="^($(echo "$keys" | tr ',' '|'))="
  kubectl create secret generic "$name" \
    --from-env-file=<(grep -E "$pattern" "$ENV_FILE") \
    -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
done

kubectl create secret generic traefik-basicauth \
  --from-file=users="$REPO_ROOT/infra/traefik/console.htpasswd" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic seaweedfs-s3-config \
  --from-file=s3.json="$REPO_ROOT/infra/seaweedfs/s3.json" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

echo "▶ ${#SECRET_SPECS[@]} per-workload Secrets + traefik-basicauth + seaweedfs-s3-config applied in namespace $NAMESPACE"
