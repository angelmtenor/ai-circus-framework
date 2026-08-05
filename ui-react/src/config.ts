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

export const config = {
  devMode: import.meta.env.VITE_DEV_MODE === "true",
  devOrgId: import.meta.env.VITE_DEV_ORG_ID ?? "demo",
  platformRegistryUrl: import.meta.env.VITE_PLATFORM_REGISTRY_URL ?? "http://localhost:8010",
  predictionUrl: import.meta.env.VITE_PREDICTION_URL ?? "http://prediction.localhost",
  assistantUrl: import.meta.env.VITE_ASSISTANT_URL ?? "http://assistant.localhost",
  ragAgentUrl: import.meta.env.VITE_RAG_AGENT_URL ?? "http://rag-agent.localhost",
  logtoEndpoint: import.meta.env.VITE_LOGTO_ENDPOINT ?? "http://logto.localhost",
  logtoAppId: import.meta.env.VITE_LOGTO_APP_ID ?? "",
  logtoApiResource: import.meta.env.VITE_LOGTO_API_RESOURCE_INDICATOR ?? "https://api.ai-circus-framework.local",
};
