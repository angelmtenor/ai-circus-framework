import { useAuth } from "react-oidc-context";
import { useEffect, useState } from "react";
import { verifyAdminKey, verifyEngineeringDemoKey } from "./apiClient";
import { config } from "./config";

export type Identity = {
  orgId: string;
  roles: string[];
  accessToken: string | null;
  // What the topbar shows — the org id itself for admin/engineering-demo/dev (a
  // human-readable string we chose), but the signed-in user's email for a real
  // Keycloak login (Organizations get an opaque auto-generated id, not a friendly
  // name, so showing orgId there would just be a confusing string like
  // "3f1e2b9a-...").
  label: string;
};

const DEV_STORAGE_KEY = "ai-circus-framework:dev-identity";
const ADMIN_STORAGE_KEY = "ai-circus-framework:admin-identity";
const ENGINEERING_DEMO_STORAGE_KEY = "ai-circus-framework:engineering-demo-identity";
// Must match ai_circus_shared.auth.ADMIN_ORG_ID — the backend resolves any request
// bearing a matching ADMIN_API_KEY bearer token to this org id regardless of what
// the client sends, but list_scenarios(org_id=...) needs the right value to show
// the same entitlements.
const ADMIN_ORG_ID = "admin";
// Must match ai_circus_shared.auth.ENGINEERING_DEMO_ORG_ID — same reasoning as
// ADMIN_ORG_ID above, for the narrower engineering-demo bearer key.
const ENGINEERING_DEMO_ORG_ID = "engineering-demo";

/**
 * Resolves the caller's identity — DEV_MODE bypass (mirrors the backend services'
 * AUTH_DISABLED) or a real Keycloak organization token.
 *
 * The Keycloak path is unverified against a live, browser-configured Keycloak realm
 * (this repo was built without interactive browser access). Unlike Logto, no
 * per-request token exchange is needed: the access token minted at sign-in already
 * carries both the `organization` and audience claims via server-side protocol
 * mappers, so `user.access_token` is used as-is.
 */
export function useIdentity(): {
  identity: Identity | null;
  loading: boolean;
  keycloakError: string | null;
  logIn: (orgId: string, roles: string[]) => void;
  logInWithAdminKey: (adminKey: string) => Promise<void>;
  logInWithEngineeringDemoKey: (demoKey: string) => Promise<void>;
  logOut: () => void;
} {
  const auth = useAuth();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(true);
  // Surfaced on the login screen instead of only `console.error`'d by the SDK —
  // this whole real-Keycloak path went unverified against a live realm until now,
  // so failures here need to be visible without opening browser devtools.
  const [keycloakError, setKeycloakError] = useState<string | null>(null);

  useEffect(() => {
    if (config.devMode) {
      const stored = localStorage.getItem(DEV_STORAGE_KEY);
      if (stored) setIdentity(JSON.parse(stored));
      setLoading(false);
      return;
    }

    const storedAdmin = sessionStorage.getItem(ADMIN_STORAGE_KEY);
    if (storedAdmin) {
      setIdentity(JSON.parse(storedAdmin));
      setLoading(false);
      return;
    }

    const storedEngineeringDemo = sessionStorage.getItem(ENGINEERING_DEMO_STORAGE_KEY);
    if (storedEngineeringDemo) {
      setIdentity(JSON.parse(storedEngineeringDemo));
      setLoading(false);
      return;
    }

    if (auth.isLoading) return;
    if (auth.error) {
      setKeycloakError(auth.error.message);
      setLoading(false);
      return;
    }
    if (!auth.isAuthenticated || !auth.user) {
      setLoading(false);
      return;
    }

    const claims = auth.user.profile;
    // Keycloak's `organization` claim is `{alias: {id, groups}}` — keyed by org
    // *alias*, not id, with the id nested inside (unlike Logto's flat
    // `organizations: string[]`). This app assumes one org per real user (same
    // simplification as ADMIN_ORG_ID/ENGINEERING_DEMO_ORG_ID), so just take the
    // first (only) entry's `.id`.
    const orgClaim = claims.organization as Record<string, { id: string }> | undefined;
    const orgAlias = orgClaim ? Object.keys(orgClaim)[0] : undefined;
    const orgId = orgClaim && orgAlias ? orgClaim[orgAlias].id : undefined;
    if (!orgId) {
      setKeycloakError(
        `Signed in as ${claims.preferred_username ?? claims.sub}, but this account isn't a member of any ` +
          "Organization yet. If you meant a different account, sign out of Keycloak (or use a private " +
          "browser window) and sign in again.",
      );
      setLoading(false);
      return;
    }
    const accessToken = auth.user.access_token;
    if (!accessToken) {
      setKeycloakError(`Signed in to organization ${orgId}, but couldn't obtain an access token for it.`);
      setLoading(false);
      return;
    }
    const label = (claims.email ?? claims.name ?? claims.preferred_username ?? orgId) as string;
    setIdentity({ orgId, roles: [], accessToken, label });
    setLoading(false);
  }, [auth.isLoading, auth.isAuthenticated, auth.error, auth.user]);

  return {
    identity,
    loading,
    keycloakError,
    logIn: (orgId: string, roles: string[]) => {
      if (config.devMode) {
        const dev: Identity = { orgId, roles, accessToken: null, label: orgId };
        localStorage.setItem(DEV_STORAGE_KEY, JSON.stringify(dev));
        setIdentity(dev);
        return;
      }
      void auth.signinRedirect();
    },
    logInWithAdminKey: async (adminKey: string) => {
      // Verified against a real admin-gated endpoint first — /entitlements itself has
      // no auth check (see apiClient.verifyAdminKey), so without this a bad key would
      // still land on the scenario picker and only fail once a scenario's own
      // chat/predict call 401s. "No valid credential" should mean "not logged in",
      // not "logged in, then every scenario errors."
      const valid = await verifyAdminKey(config.platformRegistryUrl, adminKey);
      if (!valid) {
        throw new Error("Invalid admin key.");
      }
      // The key itself becomes the bearer token; the backend's own
      // resolve_caller_identity does the actual matching on every subsequent request.
      // sessionStorage (not localStorage): this is a real admin credential, not a
      // dev-mode placeholder — it should not persist once the tab/browser closes.
      const admin: Identity = { orgId: ADMIN_ORG_ID, roles: [], accessToken: adminKey, label: ADMIN_ORG_ID };
      sessionStorage.setItem(ADMIN_STORAGE_KEY, JSON.stringify(admin));
      setIdentity(admin);
    },
    logInWithEngineeringDemoKey: async (demoKey: string) => {
      // Same verify-before-commit reasoning as logInWithAdminKey above, against the
      // dedicated /auth/verify-engineering-demo-key endpoint (this key isn't admin-gated).
      const valid = await verifyEngineeringDemoKey(config.platformRegistryUrl, demoKey);
      if (!valid) {
        throw new Error("Invalid engineering demo key.");
      }
      const engineeringDemo: Identity = {
        orgId: ENGINEERING_DEMO_ORG_ID,
        roles: [],
        accessToken: demoKey,
        label: ENGINEERING_DEMO_ORG_ID,
      };
      sessionStorage.setItem(ENGINEERING_DEMO_STORAGE_KEY, JSON.stringify(engineeringDemo));
      setIdentity(engineeringDemo);
    },
    logOut: () => {
      const hadAdminIdentity = sessionStorage.getItem(ADMIN_STORAGE_KEY) !== null;
      const hadEngineeringDemoIdentity = sessionStorage.getItem(ENGINEERING_DEMO_STORAGE_KEY) !== null;
      localStorage.removeItem(DEV_STORAGE_KEY);
      sessionStorage.removeItem(ADMIN_STORAGE_KEY);
      sessionStorage.removeItem(ENGINEERING_DEMO_STORAGE_KEY);
      if (config.devMode || hadAdminIdentity || hadEngineeringDemoIdentity) {
        // Neither DEV_MODE nor the admin-key/engineering-demo-key paths involve a real Keycloak session.
        setIdentity(null);
        return;
      }
      void auth.signoutRedirect({ post_logout_redirect_uri: window.location.origin });
    },
  };
}
