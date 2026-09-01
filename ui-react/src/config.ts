/**
 * Runtime config for the React SPA.
 *
 * Unlike the Python services (server-side, so docker-compose env vars reach them
 * directly), this bundle runs in the *browser* — it can only reach the backend
 * services through Traefik's public *.localhost hostnames (or platform-registry's
 * published host-loopback port), never docker-internal service names.
 *
 * Baked in at build time via Vite env vars (falling back to this demo's default
 * Traefik hostnames) rather than injected at container-start — a real multi-environment
 * deployment would want a `/config.js` injected by the container entrypoint instead;
 * left as a documented follow-up (see root README "Reserved for later").
 */

// Single standardized ceiling for every dataset/prediction row-limit control in the
// UI (dataset sample size, batch predict size, evaluation/explainability sample
// size) — mirrors prediction/src/prediction/api.py's own MAX_ROWS. Previously these
// were a scatter of small, inconsistent caps (200/300/etc) across different views.
export const MAX_ROWS = 30000;

export const config = {
  devMode: import.meta.env.VITE_DEV_MODE === "true",
  devOrgId: import.meta.env.VITE_DEV_ORG_ID ?? "demo",
  platformRegistryUrl: import.meta.env.VITE_PLATFORM_REGISTRY_URL ?? "http://localhost:8010",
  predictionUrl: import.meta.env.VITE_PREDICTION_URL ?? "http://prediction.localhost",
  assistantUrl: import.meta.env.VITE_ASSISTANT_URL ?? "http://assistant.localhost",
  ragAgentUrl: import.meta.env.VITE_RAG_AGENT_URL ?? "http://rag-agent.localhost",
  formAgentUrl: import.meta.env.VITE_FORM_AGENT_URL ?? "http://form-agent.localhost",
  voiceUrl: import.meta.env.VITE_VOICE_URL ?? "http://agui-voice.localhost",
  keycloakIssuer: import.meta.env.VITE_KEYCLOAK_ISSUER ?? "http://keycloak.localhost/realms/ai-circus",
  keycloakClientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "",
  keycloakAudience: import.meta.env.VITE_KEYCLOAK_AUDIENCE ?? "https://api.ai-circus-framework.local",
};
