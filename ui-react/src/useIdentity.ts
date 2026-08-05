import { useLogto } from "@logto/react";
import { useEffect, useState } from "react";
import { config } from "./config";

export type Identity = {
  orgId: string;
  roles: string[];
  accessToken: string | null;
};

const DEV_STORAGE_KEY = "ai-circus-framework:dev-identity";

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
    logOut: () => {
      if (config.devMode) {
        localStorage.removeItem(DEV_STORAGE_KEY);
        setIdentity(null);
        return;
      }
      signOut(window.location.origin);
    },
  };
}
