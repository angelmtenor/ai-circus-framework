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

## Isolating what you're testing — don't fight app auth

`aiopen.localhost` requires a real Logto login when `DEV_MODE=false` (the normal state) — there
are no credentials available to automate that, so don't try to script a full login flow. Instead,
isolate exactly the rendering logic in question in a static HTML page served over plain HTTP (a
real browser origin is required — `file://` scripts get blocked), e.g.:

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

## References

- Root `AGENTS.md` §4 (Verification is Mandatory).
- `.mcp.json` — the `@playwright/mcp` server config this workaround routes around.
