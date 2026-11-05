// Idle check for every tab of one or more scenarios: after a tab has settled, how many animation frames per
// second and how much CPU does a page nobody touches still use? Exits 1 if any tab is over a limit, so a new
// tab that spins forever fails CI-style instead of being noticed by hand (feedback: quiet tabs).
// Env: ADMIN_API_KEY, SCENARIO_TITLES (comma-separated full card titles), SETTLE_SECONDS (default 18: sceneLoop
// rests ambient motion after 15 s), IDLE_SECONDS (default 8), MAX_FPS (default 2), MAX_CPU (percent, default 15),
// TABS (optional comma-separated tab names, default all), APP_URL (default http://aiopen.localhost), PW_MODULE (default playwright-core). Run via `make k3s-idle-check`.
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');

const num = (name, fallback) => Number(process.env[name] || fallback);
const SETTLE = num('SETTLE_SECONDS', 18);
const IDLE = num('IDLE_SECONDS', 8);
const MAX_FPS = num('MAX_FPS', 2);
const MAX_CPU = num('MAX_CPU', 15);
const APP_URL = process.env.APP_URL || 'http://aiopen.localhost';
const SHOT_DIR = process.env.SHOT_DIR;

async function openScenario(browser, title) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const page = await context.newPage();
  await page.goto(APP_URL, { waitUntil: 'networkidle', timeout: 60000 });
  await page.fill('input[type="password"]', process.env.ADMIN_API_KEY);
  await page.click('button:has-text("Log in")');
  await page.waitForSelector('.topbar', { timeout: 60000 });
  await page.waitForTimeout(1500);
  await page.locator(`text="${title}"`).first().click();
  await page.waitForSelector('.workspace-tabs button', { timeout: 30000 });
  return { context, page };
}

async function measure(page, cdp) {
  const metrics = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((m) => [m.name, m.value]));
  await page.evaluate(() => {
    window.__frames = 0;
    if (!window.__rafPatched) {
      const raf = window.requestAnimationFrame.bind(window);
      window.requestAnimationFrame = (cb) => { window.__frames += 1; return raf(cb); };
      window.__rafPatched = true;
    }
  });
  const a = await metrics();
  await page.waitForTimeout(IDLE * 1000);
  const b = await metrics();
  const frames = await page.evaluate(() => window.__frames);
  return { fps: frames / IDLE, cpu: ((b.TaskDuration - a.TaskDuration) / IDLE) * 100 };
}

(async () => {
  const titles = (process.env.SCENARIO_TITLES || '').split(',').map((t) => t.trim()).filter(Boolean);
  if (!titles.length || !process.env.ADMIN_API_KEY) {
    console.log('Set SCENARIO_TITLES and ADMIN_API_KEY');
    process.exit(2);
  }
  const browser = await chromium.launch();
  const failures = [];
  for (const title of titles) {
    const { context, page } = await openScenario(browser, title);
    const cdp = await context.newCDPSession(page);
    await cdp.send('Performance.enable');
    const only = (process.env.TABS || '').split(',').map((t) => t.trim()).filter(Boolean);
    const names = (await page.locator('.workspace-tabs button').allInnerTexts())
      .map((t) => t.trim())
      .filter((t) => t && (!only.length || only.includes(t)));
    // A tab that renders nothing is quiet too: a failed backend call is a failure as well.
    let failed = [];
    page.on('response', (r) => {
      if ((r.status() >= 500 || r.status() === 404) && r.url().includes('.localhost')) failed.push(`${r.status()} ${new URL(r.url()).pathname}`);
    });
    const report = async (label) => {
      if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${label.replace(/[^a-z0-9]+/gi, '_')}.png`, fullPage: true });
      if (failed.length) failures.push(`${title} / ${label}: ${failed.join(', ')}`);
      if (failed.length) console.log(`FAIL ${title} / ${label}: ${failed.join(', ')}`);
      failed = [];
    };
    for (const name of names) {
      await page.locator('.workspace-tabs button', { hasText: name }).first().click();
      await page.waitForTimeout(SETTLE * 1000);
      const { fps, cpu } = await measure(page, cdp);
      const bad = fps > MAX_FPS || cpu > MAX_CPU;
      if (bad) failures.push(`${title} / ${name}`);
      console.log(`${bad ? 'FAIL' : 'ok  '} ${title} / ${name}: ${fps.toFixed(1)} frames/s, CPU ${cpu.toFixed(1)}%`);
      await report(name);
      // A model tab holds two views: Predictions (just measured) and Insights.
      const insights = page.locator('[aria-label="Model section"] button', { hasText: 'Insights' });
      if (await insights.count()) {
        await insights.click();
        await page.waitForTimeout(SETTLE * 1000);
        const section = await measure(page, cdp);
        const sectionBad = section.fps > MAX_FPS || section.cpu > MAX_CPU;
        if (sectionBad) failures.push(`${title} / ${name} / Insights`);
        console.log(`${sectionBad ? 'FAIL' : 'ok  '} ${title} / ${name} / Insights: ${section.fps.toFixed(1)} frames/s, CPU ${section.cpu.toFixed(1)}%`);
        await report(`${name} Insights`);
        await page.locator('[aria-label="Model section"] button', { hasText: 'Predictions' }).click();
      }
    }
    await context.close();
  }
  await browser.close();
  console.log(failures.length ? `\n${failures.length} tab(s) over ${MAX_FPS} fps or ${MAX_CPU}% CPU idle` : `\nAll tabs quiet (<= ${MAX_FPS} fps, <= ${MAX_CPU}% CPU)`);
  process.exit(failures.length ? 1 : 0);
})().catch((e) => { console.log('FAILED', String(e).slice(0, 400)); process.exit(2); });
