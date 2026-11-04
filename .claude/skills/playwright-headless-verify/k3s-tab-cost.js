// What one tab costs on the running cluster: to open (by source), to keep open (idle CPU, frames, requests).
// Env: ADMIN_API_KEY, SCENARIO_TITLE (the card's full title), TAB (the tab's name), READY (CSS selector that
// means "drawn"), OUT_DIR (optional, for a full-page screenshot), TAG (optional), IDLE_SECONDS (default 30).
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');

function bySource(profile) {
  const nodes = new Map(profile.nodes.map((n) => [n.id, n]));
  const totals = {};
  profile.samples.forEach((id, i) => {
    const frame = nodes.get(id).callFrame;
    let key = 'browser (style, layout, paint, parse)';
    if (frame.url) key = /PlotlyChartImpl/.test(frame.url) ? 'plotly' : 'app js';
    else if (frame.functionName === '(idle)') key = 'idle';
    else if (frame.functionName === '(garbage collector)') key = 'gc';
    totals[key] = (totals[key] || 0) + (profile.timeDeltas[i] || 0) / 1e6;
  });
  return Object.entries(totals).filter(([k]) => k !== 'idle').map(([k, v]) => `${k} ${v.toFixed(2)} s`).join(' | ');
}

(async () => {
  const { SCENARIO_TITLE, TAB, READY, OUT_DIR } = process.env;
  const tag = process.env.TAG || 'run';
  const idleSeconds = Number(process.env.IDLE_SECONDS || 30);
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const page = await context.newPage();
  const problems = [];
  page.on('pageerror', (e) => problems.push(`PAGEERROR ${e.message.slice(0, 160)}`));
  page.on('response', (r) => {
    if (r.status() >= 400 && !/\/healthz|providers\//.test(r.url())) problems.push(`${r.status()} ${r.url().slice(0, 100)}`);
  });
  await page.goto('http://aiopen.localhost', { waitUntil: 'networkidle', timeout: 60000 });
  await page.fill('input[type="password"]', process.env.ADMIN_API_KEY);
  await page.click('button:has-text("Log in")');
  await page.waitForSelector('.topbar', { timeout: 60000 });
  await page.waitForTimeout(1500);
  // The exact title: other scenarios' descriptions can mention the same name.
  await page.locator(`text="${SCENARIO_TITLE}"`).first().click();
  await page.waitForTimeout(4000);

  const cdp = await context.newCDPSession(page);
  await cdp.send('Performance.enable');
  await cdp.send('Profiler.enable');
  await cdp.send('Network.enable');
  await cdp.send('Profiler.setSamplingInterval', { interval: 500 });
  let bytes = 0;
  cdp.on('Network.loadingFinished', (e) => { bytes += e.encodedDataLength; });
  const metrics = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((m) => [m.name, m.value]));

  const a = await metrics();
  await cdp.send('Profiler.start');
  const t0 = Date.now();
  await page.getByRole('button', { name: TAB }).first().click({ timeout: 20000 });
  await page.waitForSelector(READY, { state: 'attached', timeout: 120000 });
  const opened = Date.now() - t0;
  await page.waitForTimeout(3000);
  const openProfile = (await cdp.send('Profiler.stop')).profile;
  const b = await metrics();
  const downloaded = bytes;
  const dom = await page.evaluate(() => document.querySelectorAll('*').length);
  console.log(`[${tag}] open ${opened} ms | CPU ${(b.TaskDuration - a.TaskDuration).toFixed(2)} s | downloaded ${(downloaded / 1e6).toFixed(2)} MB | heap ${(b.JSHeapUsedSize / 1e6).toFixed(0)} MB | DOM ${dom}`);
  console.log(`[${tag}] open by source: ${bySource(openProfile)}`);

  // Let entrance and ambient motion rest (sceneLoop stops ambient motion after 15 s), then measure quiet.
  await page.waitForTimeout(20000);
  await page.evaluate(() => {
    window.__frames = 0;
    const raf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => { window.__frames += 1; return raf(cb); };
  });
  const requests = {};
  const note = (r) => {
    const u = new URL(r.url());
    const key = u.host + u.pathname.slice(0, 40);
    requests[key] = (requests[key] || 0) + 1;
  };
  page.on('request', note);
  const c = await metrics();
  await cdp.send('Profiler.start');
  await page.waitForTimeout(idleSeconds * 1000);
  const idleProfile = (await cdp.send('Profiler.stop')).profile;
  const d = await metrics();
  page.off('request', note);
  const frames = await page.evaluate(() => window.__frames);
  console.log(`[${tag}] idle CPU ${(((d.TaskDuration - c.TaskDuration) / idleSeconds) * 100).toFixed(2)}% | ${(frames / idleSeconds).toFixed(1)} frames/s | ${bySource(idleProfile)}`);
  console.log(`[${tag}] idle requests ${JSON.stringify(requests).slice(0, 240)}`);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(1000);
  console.log(`[${tag}] phone overflow ${await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)} px`);
  await page.setViewportSize({ width: 1360, height: 900 });
  if (OUT_DIR) await page.screenshot({ path: `${OUT_DIR}/${tag}-tab.png`, fullPage: true });
  console.log(`[${tag}] problems ${problems.length} ${[...new Set(problems)].slice(0, 5).join(' ; ')}`);
  await browser.close();
})().catch((e) => { console.log('FAILED', String(e).slice(0, 400)); process.exit(1); });
