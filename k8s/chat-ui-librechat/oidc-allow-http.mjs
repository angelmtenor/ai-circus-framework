// LibreChat's OpenID client (openid-client v6) accepts only https identity providers, and this lab's Keycloak
// speaks plain http (http://keycloak.localhost, http://keycloak:8080). This Node load hook turns on the library's
// own opt-out, `allowInsecureRequests`, for every configuration LibreChat discovers. Nothing else changes: the image
// is unmodified and this file is loaded with NODE_OPTIONS=--import (docs/adr/0026).
//
// LAB ONLY. With an https identity provider, delete this file and the NODE_OPTIONS line: nothing else depends on it.
// If a LibreChat upgrade changes openid-client, the hook stops the server with a clear error rather than silently
// signing nobody in.
import { registerHooks } from 'node:module';

const TARGET = /\/openid-client\/build\/index\.js$/;
const ANCHOR = 'export async function discovery(server, clientId, metadata, clientAuthentication, options) {';
const INJECT = `${ANCHOR}
    options = { ...options, execute: [...(options?.execute ?? []), allowInsecureRequests] };`;

registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (!TARGET.test(url)) {
      return result;
    }
    const source = String(result.source);
    if (!source.includes(ANCHOR)) {
      throw new Error('oidc-allow-http.mjs: openid-client changed its discovery(); update or remove this hook');
    }
    return { ...result, source: source.replace(ANCHOR, INJECT) };
  },
});
