#!/usr/bin/env bash
# The MCP lite profile (docs/adr/0027): the MCP Lite Stack alone on its own k3d cluster — Keycloak, the AI gateway
# (llm-gateway: models + MCP gateway, with Valkey), the example kit server and its API (crm-mcp + crm-api, Level 3)
# and one chat UI. Nothing else of the framework runs. `make k3s-mcp-lite`, `make k3s-mcp-lite-verify`, `make k3s-mcp-lite-down`.
#
# The cluster gets its own kubeconfig file (k3d kubeconfig write): this script never changes the default kubeconfig
# or its current context, so a framework cluster's commands keep pointing where they did. It holds host port 80
# (Traefik), as the framework cluster does: only one of them runs at a time.
#
# Usage: scripts/k3s_mcp_lite.sh up|verify|down
#   MCP_LITE_CLUSTER (default mcp-lite), CHAT_UI (open-webui | librechat), OBS (1 = add the observability bundle,
#   make k3s-obs-up, run against this cluster), K3S_VERSION (the Makefile's)
set -euo pipefail

ACTION="${1:-}"
CLUSTER="${MCP_LITE_CLUSTER:-mcp-lite}"
CHAT_UI="${CHAT_UI:-open-webui}"
OBS="${OBS:-}"
K3S_VERSION="${K3S_VERSION:-v1.35.5-k3s1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS=ai-circus
# Images the profile builds: the realm-baked Keycloak, the AI gateway and the example pair. Postgres and the chat
# UIs are official images the cluster pulls.
IMAGES=(llm-gateway crm-api crm-mcp)

cd "$REPO_ROOT"

case "$CHAT_UI" in
    open-webui) CHAT_DEPLOYMENTS=(open-webui) ; OTHER_UI=librechat ;;
    librechat) CHAT_DEPLOYMENTS=(librechat-mongodb librechat) ; OTHER_UI=open-webui ;;
    *) echo "❌ CHAT_UI must be open-webui or librechat (got '$CHAT_UI')" >&2; exit 1 ;;
esac

cluster_exists() { k3d cluster list "$CLUSTER" >/dev/null 2>&1; }

use_cluster_kubeconfig() {
    KUBECONFIG="$(k3d kubeconfig write "$CLUSTER")"
    export KUBECONFIG
}

# Another running k3d cluster holds port 80 (the framework's, normally): say which one and how to pause it.
refuse_if_port_80_taken() {
    local others
    others=$(k3d cluster list -o json | python3 -c '
import json, sys
me = sys.argv[1]
for c in json.load(sys.stdin):
    if c["name"] != me and c.get("serversRunning", 0) > 0:
        print(c["name"])' "$CLUSTER")
    if [ -n "$others" ]; then
        echo "❌ k3d cluster(s) running and holding port 80: $others" >&2
        echo "   pause it first (state is kept): make k3s-pause K3S_CLUSTER=$(echo "$others" | head -1)" >&2
        echo "   and bring it back afterwards:    make k3s-resume K3S_CLUSTER=$(echo "$others" | head -1)" >&2
        exit 1
    fi
}

up() {
    refuse_if_port_80_taken
    local existed=false
    if cluster_exists; then
        k3d cluster start "$CLUSTER" >/dev/null
        existed=true
        echo "✓ k3d cluster '$CLUSTER' exists"
    else
        k3d cluster create "$CLUSTER" -p "80:80@loadbalancer" --image "rancher/k3s:$K3S_VERSION" \
            --kubeconfig-update-default=false --kubeconfig-switch-context=false
    fi
    use_cluster_kubeconfig

    echo "── images ──"
    for svc in "${IMAGES[@]}"; do
        docker build -q -f "services/$svc/Dockerfile" -t "ai-circus/$svc:local" . >/dev/null
        echo "✓ ai-circus/$svc:local"
    done
    docker build -q -t ai-circus/keycloak:local infra/keycloak >/dev/null
    echo "✓ ai-circus/keycloak:local"
    local refs=()
    for svc in "${IMAGES[@]}" keycloak; do refs+=("ai-circus/$svc:local"); done
    k3d image import "${refs[@]}" -c "$CLUSTER" >/dev/null
    echo "✓ images imported into '$CLUSTER'"

    echo "── secrets ──"
    # Every key the profile needs exists in .env before any Secret is made (the chat UIs', the kit servers').
    python3 scripts/chat_ui_identity.py --keys-only
    ./scripts/k3s_generate_secrets.sh >/dev/null
    uv run --project libs/mcp-kit --quiet python scripts/mcp_tls_certs.py --k8s

    echo "── identity, AI gateway, kit server ──"
    kubectl kustomize --load-restrictor LoadRestrictionsNone k8s/mcp-lite | kubectl apply -f - >/dev/null
    if $existed; then
        # A same-tag import never restarts a pod: restart what was just rebuilt so it runs the new image.
        kubectl -n "$NS" rollout restart deployment/keycloak deployment/llm-gateway deployment/crm-api deployment/crm-mcp \
            >/dev/null
    fi
    kubectl -n "$NS" rollout status statefulset/postgres --timeout=300s
    kubectl -n "$NS" rollout status deployment/valkey --timeout=300s
    echo "⏳ keycloak (a first boot imports the realm)…"
    kubectl -n "$NS" rollout status deployment/keycloak --timeout=600s
    echo "⏳ llm-gateway (a new cluster downloads its local embedding model first, ~670 MB)…"
    kubectl -n "$NS" rollout status deployment/llm-gateway --timeout=900s
    kubectl -n "$NS" rollout status deployment/crm-api --timeout=300s
    kubectl -n "$NS" rollout status deployment/crm-mcp --timeout=300s
    # The live realm up to realm-export.json (clients, secrets, scopes) and the demo tenant; no registry here.
    python3 scripts/chat_ui_identity.py --cluster "$CLUSTER" --no-entitlements

    echo "── chat UI: $CHAT_UI ──"
    # One chat UI at a time: switching removes the other's pods (its data volume stays).
    if [ "$OTHER_UI" = librechat ]; then
        kubectl -n "$NS" delete deployment,service librechat librechat-mongodb --ignore-not-found >/dev/null
        kubectl -n "$NS" delete ingressroute librechat --ignore-not-found >/dev/null
    else
        kubectl -n "$NS" delete deployment,service,ingressroute open-webui --ignore-not-found >/dev/null
    fi
    kubectl apply -k "k8s/mcp-lite/$CHAT_UI" >/dev/null
    for deployment in "${CHAT_DEPLOYMENTS[@]}"; do
        kubectl -n "$NS" rollout status "deployment/$deployment" --timeout=900s
    done

    if [ "$OBS" = 1 ]; then
        echo "── observability ──"
        # The framework's own bundle target, aimed at this cluster by the exported KUBECONFIG: collector,
        # VictoriaMetrics + vmalert, Loki, Tempo, Grafana; then every deployed service exports to it.
        make --no-print-directory k3s-obs-up
    fi

    local url=http://chat.localhost
    [ "$CHAT_UI" = librechat ] && url=http://librechat.localhost
    echo
    echo "✓ MCP lite stack up on k3d cluster '$CLUSTER' — $url"
    echo "  sign in as analyst@lite-demo.local or manager@lite-demo.local (password: CHAT_UI_DEMO_PASSWORD in .env)"
    [ "$OBS" = 1 ] && echo "  Grafana:   http://grafana.localhost (dashboard \"AI Circus · MCP servers\")"
    echo "  check it:  make k3s-mcp-lite-verify      kubectl:  export KUBECONFIG=\$(k3d kubeconfig write $CLUSTER)"
}

# Forward a Service to a free local port; prints the port. Every forward is stopped when the script exits.
FORWARDS=()
stop_forwards() {
    local pid
    for pid in "${FORWARDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    rm -f "${FORWARD_LOGS[@]}" 2>/dev/null || true
}
FORWARD_LOGS=()
forward() {  # forward <namespace> <service> <remote port> -> sets FORWARDED_PORT
    local log port=""
    log="$(mktemp)"
    FORWARD_LOGS+=("$log")
    kubectl -n "$1" port-forward "svc/$2" ":$3" >"$log" 2>&1 &
    FORWARDS+=("$!")
    for _ in $(seq 1 50); do
        port=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\) .*/\1/p' "$log" | head -1)
        [ -n "$port" ] && break
        sleep 0.2
    done
    [ -n "$port" ] || { echo "❌ could not forward $2: $(cat "$log")" >&2; exit 1; }
    FORWARDED_PORT="$port"
}

verify() {
    cluster_exists || { echo "❌ no k3d cluster '$CLUSTER': make k3s-mcp-lite" >&2; exit 1; }
    use_cluster_kubeconfig
    trap stop_forwards EXIT
    forward "$NS" llm-gateway 4000
    local args=(--gateway "http://127.0.0.1:$FORWARDED_PORT")
    if kubectl -n "$NS" get deployment librechat >/dev/null 2>&1; then args+=(--client librechat); fi
    # With the observability bundle deployed, the calls the check makes must reach its three stores.
    if kubectl get namespace observability >/dev/null 2>&1; then
        forward observability victoriametrics 8428 && args+=(--metrics "http://127.0.0.1:$FORWARDED_PORT")
        forward observability loki 3100 && args+=(--logs "http://127.0.0.1:$FORWARDED_PORT")
        forward observability tempo 3200 && args+=(--traces "http://127.0.0.1:$FORWARDED_PORT")
    fi
    uv run --project libs/mcp-kit --quiet python scripts/mcp_lite_check.py "${args[@]}"
}

down() {
    if cluster_exists; then
        k3d cluster delete "$CLUSTER"
        echo "✓ k3d cluster '$CLUSTER' deleted (volumes included). The framework cluster: make k3s-resume"
    else
        echo "✓ no k3d cluster '$CLUSTER'"
    fi
}

case "$ACTION" in
    up) up ;;
    verify) verify ;;
    down) down ;;
    *) echo "usage: $0 up|verify|down" >&2; exit 1 ;;
esac
