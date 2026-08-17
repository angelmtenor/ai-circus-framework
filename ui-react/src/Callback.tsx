import { useHandleSignInCallback } from "@logto/react";

/**
 * Landing page for Logto's redirect back after a real sign-in (see useIdentity.ts's
 * `signIn(window.location.origin + "/callback")`). Exchanges the auth code for
 * tokens, then sends the browser back to `/` where App.tsx picks up the resulting
 * session via useLogto()/useIdentity.ts.
 */
export function Callback() {
  const { isLoading, error } = useHandleSignInCallback(() => {
    window.location.replace("/");
  });

  if (error) return <div className="app-loading">Sign-in failed: {error.message}</div>;
  return <div className="app-loading">{isLoading ? "Signing you in…" : "Redirecting…"}</div>;
}
