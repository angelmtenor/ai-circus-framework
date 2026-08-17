import { useLogto } from "@logto/react";
import { useEffect, useState } from "react";
import { verifyAdminKey, verifyEngineeringDemoKey } from "./apiClient";
import { config } from "./config";

export type Identity = {
  orgId: string;
  roles: string[];
  accessToken: string | null;
  // What the topbar shows — the org id itself for admin/engineering-demo/dev (a
  // human-readable string we chose), but the signed-in user's email for a real
  // Logto login (Organizations get an opaque auto-generated id, not a friendly
  // name, so showing orgId there would just be a confusing string like
  // "kgq3gjurzesp").
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
 * AUTH_DISABLED) or a real Logto organization token.
 *
 * The Logto path is unverified against a live, browser-configured Logto tenant
 * (this repo was built without interactive browser access). `getAccessToken(resource,
 * organizationId)` is Logto's documented pattern for a per-organization, API-scoped
 * access token.
 */
export function useIdentity(): {
  identity: Identity | null;
  loading: boolean;
  logtoError: string | null;
  logIn: (orgId: string, roles: string[]) => void;
  logInWithAdminKey: (adminKey: string) => Promise<void>;
  logInWithEngineeringDemoKey: (demoKey: string) => Promise<void>;
  logOut: () => void;
} {
  const { isAuthenticated, isLoading, error, signIn, signOut, getIdTokenClaims, getAccessToken } = useLogto();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(true);
  // Surfaced on the login screen instead of only `console.error`'d by the SDK
  // (see useLogto's proxy/handleError) — this whole real-Logto path went
  // unverified against a live tenant until now, so failures here need to be
  // visible without opening browser devtools.
  const [logtoError, setLogtoError] = useState<string | null>(null);

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

    if (isLoading) return;
    if (error) {
      setLogtoError(error.message);
      setLoading(false);
      return;
    }
    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    (async () => {
      try {
        const claims = await getIdTokenClaims();
        // The ID token carries every org the user belongs to as an `organizations`
        // array (there's no singular `organization_id` claim on it — that only
        // exists on an org-scoped *access* token) — this app assumes one org per
        // real user, same simplification as ADMIN_ORG_ID/ENGINEERING_DEMO_ORG_ID.
        const orgId = (claims?.organizations as string[] | undefined)?.[0];
        if (!orgId) {
          setLogtoError(
            `Signed in as ${claims?.username ?? claims?.sub}, but this account isn't a member of any ` +
              "Organization yet. If you meant a different account, sign out of Logto (or use a private " +
              "browser window) and sign in again.",
          );
          setLoading(false);
          return;
        }
        const accessToken = await getAccessToken(config.logtoApiResource, orgId);
        if (!accessToken) {
          setLogtoError(`Signed in to organization ${orgId}, but couldn't obtain an API access token for it.`);
          setLoading(false);
          return;
        }
        const label = (claims?.email ?? claims?.name ?? claims?.username ?? orgId) as string;
        setIdentity({ orgId, roles: [], accessToken, label });
        setLoading(false);
      } catch (e) {
        setLogtoError((e as Error).message);
        setLoading(false);
      }
    })();
  }, [isLoading, isAuthenticated, error, getIdTokenClaims, getAccessToken]);

  return {
    identity,
    loading,
    logtoError,
    logIn: (orgId: string, roles: string[]) => {
      if (config.devMode) {
        const dev: Identity = { orgId, roles, accessToken: null, label: orgId };
        localStorage.setItem(DEV_STORAGE_KEY, JSON.stringify(dev));
        setIdentity(dev);
        return;
      }
      signIn(window.location.origin + "/callback");
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
        // Neither DEV_MODE nor the admin-key/engineering-demo-key paths involve a real Logto session.
        setIdentity(null);
        return;
      }
      signOut(window.location.origin);
    },
  };
}
