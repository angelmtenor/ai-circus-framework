import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { LogtoProvider, type LogtoConfig, UserScope } from "@logto/react";
import "./index.css";
import App from "./App.tsx";
import { Callback } from "./Callback.tsx";
import { config } from "./config";

const logtoConfig: LogtoConfig = {
  endpoint: config.logtoEndpoint,
  appId: config.logtoAppId,
  // Without this, the ID token has no `organizations` claim at all — the SDK
  // auto-adds the matching Organization resource once this scope is present (see
  // @logto/client's normalizeLogtoConfig) — useIdentity.ts's real-Logto path reads
  // that claim to resolve which tenant the caller belongs to.
  scopes: [UserScope.Organizations],
  // Pre-declared so useIdentity.ts's getAccessToken(config.logtoApiResource, orgId)
  // call can silently obtain a token for it later without a second consent redirect.
  resources: [config.logtoApiResource],
};

// No router in this SPA (single real route) — useIdentity.ts's real-Logto sign-in
// redirects to "/callback", so that's the one path worth branching on directly.
const isSignInCallback = window.location.pathname === "/callback";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LogtoProvider config={logtoConfig}>{isSignInCallback ? <Callback /> : <App />}</LogtoProvider>
  </StrictMode>,
);
