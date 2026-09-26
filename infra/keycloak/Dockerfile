# Pre-built ("optimized") Keycloak — the platform's identity provider. The stock image run
# with plain `start` re-runs Quarkus augmentation on every boot (measured ~12.5s on the
# k3d cluster, before DB migration and realm import even begin); `kc.sh build` does it
# once here, and `start --optimized` skips it at runtime. Built by `make k3s-build` as
# ai-circus/keycloak:local and by docker compose; the pin below is the one place to bump
# the Keycloak version (DB migrations then run automatically, forward-only).
ARG KEYCLOAK_VERSION=26.7.4

FROM quay.io/keycloak/keycloak:${KEYCLOAK_VERSION} AS builder
# Build-time options only — they are baked into the server and must match at runtime.
ENV KC_DB=postgres KC_HEALTH_ENABLED=true
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:${KEYCLOAK_VERSION}
COPY --from=builder /opt/keycloak/ /opt/keycloak/
# The realm bootstrap `--import-realm` loads on first boot (skipped once the realm
# exists) — baked in, so docker-compose and k8s read the one file (see README.md).
COPY realm-export.json /opt/keycloak/data/import/realm-export.json
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized", "--import-realm"]
