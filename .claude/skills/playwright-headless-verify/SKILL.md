# Playwright Headless Verify

## Overview

The `playwright` MCP server configured in root `.mcp.json` (`@playwright/mcp`) defaults to
launching the real **Google Chrome** browser (`--browser chrome`, the default channel), expected
at `/opt/google/chrome/chrome`. In this dev sandbox that binary isn't installed, and
`npx playwright install chrome` fails — it needs to `apt-get install` OS dependencies as root,
which requires an interactive `sudo` prompt this environment can't satisfy. So every
`mcp__playwright__*` tool call fails here with:

```
Error: Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome
```

This is a **sandbox limitation, not a broken app** — don't burn time retrying the MCP tools or
trying to `sudo` your way past it.

## When to use

- Any task that needs a real browser to verify rendering (chart output, CSS layout, SVG/Canvas
  content) and the `mcp__playwright__*` tools error out with the message above.
- Verifying isolated rendering logic (a chart component's SVG/Plotly output, a CSS fix) *without*
  needing to log into the running app.
- Verifying a full k3s-deployed app end-to-end through a real login and scenario page — see
  "Logging into the real running app" below. This is the step `k3s-deploy-verify` calls mandatory
  before calling any deploy "verified."

## The workaround: drive Chromium directly via `playwright-core`

`npx playwright install chromium` (no `chrome` channel, no OS deps, no sudo) downloads a
standalone Chromium build to `~/.cache/ms-playwright/chromium-<rev>/chrome-linux64/chrome` — this
works in this sandbox. Skip the MCP tool entirely and drive that binary directly with a small Node
script using `playwright-core` (a temporary dependency, not a repo dependency — install it in a
scratch directory, never in `ui-react/` or any service).

```bash
# One-time per session: make sure a chromium build is present (idempotent, no sudo)
npx playwright install chromium

# Find the exact executable (revision number changes across installs)
find ~/.cache/ms-playwright -maxdepth 2 -iname chrome -type f
```

```bash
mkdir -p /tmp/pw-verify && cd /tmp/pw-verify
npm init -y >/dev/null 2>&1
npm install playwright-core --no-save
```

```js
// /tmp/pw-verify/shot.js
const { chromium } = require('playwright-core');
(async () => {
  const browser = await chromium.launch({
    executablePath: '<path from the find command above>',
  });
  const page = await browser.newPage({ viewport: { width: 900, height: 500 } });
  page.on('pageerror', (err) => console.log('PAGEERROR:', err.message));
  await page.goto('http://localhost:<port>/whatever.html');
  await page.screenshot({ path: '/tmp/pw-verify/out.png' });
  await browser.close();
})();
```

Run with `node /tmp/pw-verify/shot.js`, then read `/tmp/pw-verify/out.png` with the Read tool to
inspect visually.

## If `node`/`npx` aren't on the host either: the Docker fallback

Some sandboxes have Docker but no Node at all (`node`/`npm`/`npx` all `command not found`), which
breaks the workaround above at the very first step. `docker pull mcr.microsoft.com/playwright:v1.49.1-noble`
(check `npm view playwright version` or the app's own `package.json` for which tag to match if it
matters) bundles Node + Chromium + every OS dependency already — no host install, no sudo. Run
`--network host` so the container's `localhost`/`*.localhost` resolve against this host's own
loopback (where Traefik/k3d's ingress is actually listening), and bind-mount your scratch dir in
place of a local Node install:

```bash
mkdir -p /tmp/pw-verify
# write check.js into /tmp/pw-verify first (same script shape as above, `require('playwright')`
# not `playwright-core` — the image already has matching browsers cached for that version)

docker run --rm --network host \
  -v /tmp/pw-verify:/work -w /work \
  mcr.microsoft.com/playwright:v1.49.1-noble \
  bash -c "npm init -y >/dev/null 2>&1 && npm install playwright@1.49.1 --no-save >/dev/null 2>&1 && node check.js"
```

Install `node_modules` *inside* the same mounted `-w` directory (not `/tmp` with the script under
a different mount) — Node resolves `require()` by walking up from the script's own path, and a
bind-mounted `/work` has no relation to the container's `/tmp`, so a mismatch gives
`Cannot find module 'playwright'` even though the install "succeeded."

## Logging into the real running app (bearer-token shortcut, k3s target only)

`aiopen.localhost` requires a real Logto login when `DEV_MODE=false` (the normal state on
docker-compose) — there are no credentials available to automate *that* OIDC flow, so don't script
it. But a k3s deployment (see `k3s-deploy-verify`) exposes a second, non-OIDC login path: a
`User`/`Password` form where `User` already defaults to `admin` and `Password` accepts the real
`ADMIN_API_KEY` value as a bearer-token shortcut. Automate that one — it's a real, intended login
path, not a bypass:

```js
await page.goto('http://aiopen.localhost', { waitUntil: 'networkidle' });
await page.fill('input[type="password"]', process.env.ADMIN_API_KEY);
await page.click('button:has-text("Log in")');
```

Never read or print `.env`'s content (root `AGENTS.md` §1) to get that value into the script.
Instead, mount `.env` read-only into the container and source it into an env var the *container's*
shell sets — `process.env.ADMIN_API_KEY` inside `check.js` then reads it silently, and it never
appears in any command, log line, or `console.log`:

```bash
docker run --rm --network host \
  -v /tmp/pw-verify:/work -w /work \
  -v <repo>/.env:/work/.env:ro \
  mcr.microsoft.com/playwright:v1.49.1-noble \
  bash -c "set -a; source /work/.env; set +a; node check.js"
```

A successful login lands on the scenario dashboard with zero failed/non-2xx requests (log
`page.on('requestfailed', ...)` and `page.on('response', ...)` for anything not `.ok()`). Opening a
scenario card can show `Loading dataset…` for several seconds while it fetches/renders a
multi-thousand-row sample — that's normal render time, not a hang; wait it out before concluding
something's broken.

## Isolating rendering-only bugs — don't fight app auth for those

For a bug that's purely about rendering (chart output, CSS layout, SVG/Canvas content) and doesn't
need the real backend at all, skip login entirely: isolate exactly the rendering logic in question
in a static HTML page served over plain HTTP (a real browser origin is required — `file://` scripts
get blocked), e.g.:

```bash
cd /tmp/pw-verify && python3 -m http.server 8931 >/dev/null 2>&1 &
```

Reconstruct the specific component's output by hand (copy the exact math/markup from the `.tsx`
source into a plain JS/HTML reproduction, or copy `node_modules/plotly.js-dist-min/plotly.min.js`
into the scratch dir and call `Plotly.newPlot` with the same `layout`/`data` shape the real code
builds) — this validates the actual bug hypothesis (e.g. confirmed empirically:
`layout.xaxis.title` as a bare string silently renders nothing in the pinned `plotly.js-dist-min`
version, `title: { text: ... }` is required) without needing the running containers at all.

## Cleanup

This is scratch work — delete the temp directory and kill any `http.server` you started when
done (`rm -rf /tmp/pw-verify`, `pkill -f 'http.server <port>'`). Never leave `node_modules`,
`package.json`, or test scripts behind in the actual repo working tree — `cd` into `/tmp` really
does reset per-command in this harness (backgrounding a command drops the cwd for the *next*
command), so double-check `pwd`/file paths after any `&`-backgrounded step rather than assuming a
later `rm -rf` targeted the right directory.

If you used the Docker fallback, `npm install` inside the container ran as root, so the host-side
`rm -rf /tmp/pw-verify` will fail partway with `Permission denied` on `node_modules`. Remove it the
same way it was created — from inside a container that has root on that mount:
```bash
docker run --rm -v /tmp/pw-verify:/work mcr.microsoft.com/playwright:v1.49.1-noble rm -rf /work/*
rmdir /tmp/pw-verify
```
Also remove the pulled image if you don't expect to need it again this session
(`docker rmi mcr.microsoft.com/playwright:v1.49.1-noble`) — it's a multi-GB download.

## References

- Root `AGENTS.md` §4 (Verification is Mandatory).
- `.mcp.json` — the `@playwright/mcp` server config this workaround routes around.
