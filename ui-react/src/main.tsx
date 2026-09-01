import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AuthProvider, type AuthProviderProps } from "react-oidc-context";
import "./index.css";
import App from "./App.tsx";
import { config } from "./config";

const oidcConfig: AuthProviderProps = {
  authority: config.keycloakIssuer,
  client_id: config.keycloakClientId,
  redirect_uri: window.location.origin + "/callback",
  response_type: "code", // PKCE is automatic for public clients under oidc-client-ts.
  // "organization" must be requested explicitly: two open Keycloak bugs
  // (keycloak#39402, keycloak#39403) make the `organization` claim silently
  // disappear from the token if this scope isn't requested (or if the client
  // scope's "Include in token scope" isn't enabled) — see useIdentity.ts's
  // real-Keycloak path, which reads that claim to resolve the caller's tenant.
  scope: "openid profile organization",
  // react-oidc-context's own callback handling: instead of a dedicated
  // `/callback` route/component (as Logto's SDK required), it exchanges the
  // auth code for tokens automatically and this callback just strips the
  // `code`/`state` query params so the URL doesn't stay pointing at the
  // one-time callback path once the browser lands back on "/".
  onSigninCallback: () => {
    window.history.replaceState({}, document.title, window.location.pathname === "/callback" ? "/" : window.location.pathname);
  },
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider {...oidcConfig}>
      <App />
    </AuthProvider>
  </StrictMode>,
);
