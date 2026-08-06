import { useLogto } from "@logto/react";
import { useEffect, useState } from "react";
import { config } from "./config";

export type Identity = {
  orgId: string;
  roles: string[];
  accessToken: string | null;
};

const DEV_STORAGE_KEY = "ai-circus-framework:dev-identity";
const ADMIN_STORAGE_KEY = "ai-circus-framework:admin-identity";
// Must match ai_circus_shared.auth.ADMIN_ORG_ID — the backend resolves any request
// bearing a matching ADMIN_API_KEY bearer token to this org id regardless of what
// the client sends, but list_scenarios(org_id=...) needs the right value to show
// the same entitlements.
const ADMIN_ORG_ID = "admin";

/**
 * Resolves the caller's identity — DEV_MODE bypass (mirrors the backend services'
 * AUTH_DISABLED and ui-streamlit's DEV_MODE) or a real Logto organization token.
 *
 * The Logto path is unverified against a live, browser-configured Logto tenant
 * (this repo was built without interactive browser access) — see
 * services/ui-streamlit/src/ui_streamlit/core/auth.py's docstring for the same
 * caveat. `getAccessToken(resource, organizationId)` is Logto's documented pattern
 * for a per-organization, API-scoped access token.
 */
export function useIdentity(): {
  identity: Identity | null;
  loading: boolean;
  logIn: (orgId: string, roles: string[]) => void;
  logInWithAdminKey: (adminKey: string) => void;
  logOut: () => void;
} {
  const { isAuthenticated, isLoading, signIn, signOut, getIdTokenClaims, getAccessToken } = useLogto();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (config.devMode) {
      const stored = localStorage.getItem(DEV_STORAGE_KEY);
      if (stored) setIdentity(JSON.parse(stored));
      setLoading(false);
      return;
    }

    const storedAdmin = localStorage.getItem(ADMIN_STORAGE_KEY);
    if (storedAdmin) {
      setIdentity(JSON.parse(storedAdmin));
      setLoading(false);
      return;
    }

    if (isLoading) return;
    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    (async () => {
      const claims = await getIdTokenClaims();
      const orgId = claims?.organization_id as string | undefined;
      if (!orgId) {
        setLoading(false);
        return;
      }
      const accessToken = await getAccessToken(config.logtoApiResource, orgId);
      setIdentity({ orgId, roles: [], accessToken: accessToken ?? null });
      setLoading(false);
    })();
  }, [isLoading, isAuthenticated, getIdTokenClaims, getAccessToken]);

  return {
    identity,
    loading,
    logIn: (orgId: string, roles: string[]) => {
      if (config.devMode) {
        const dev: Identity = { orgId, roles, accessToken: null };
        localStorage.setItem(DEV_STORAGE_KEY, JSON.stringify(dev));
        setIdentity(dev);
        return;
      }
      signIn(window.location.origin + "/callback");
    },
    logInWithAdminKey: (adminKey: string) => {
      // The key itself becomes the bearer token; the backend's own
      // resolve_caller_identity does the actual matching — this UI never needs to
      // verify the key client-side, a bad key just 401s on the first real request.
      const admin: Identity = { orgId: ADMIN_ORG_ID, roles: [], accessToken: adminKey };
      localStorage.setItem(ADMIN_STORAGE_KEY, JSON.stringify(admin));
      setIdentity(admin);
    },
    logOut: () => {
      const hadAdminIdentity = localStorage.getItem(ADMIN_STORAGE_KEY) !== null;
      localStorage.removeItem(DEV_STORAGE_KEY);
      localStorage.removeItem(ADMIN_STORAGE_KEY);
      if (config.devMode || hadAdminIdentity) {
        // Neither DEV_MODE nor the admin-key path involves a real Logto session.
        setIdentity(null);
        return;
      }
      signOut(window.location.origin);
    },
  };
}
