const { chromium } = require('playwright');
const out = process.env.OUT_DIR;
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const bad = [];
  page.on('pageerror', (e) => bad.push('PAGEERROR: ' + e.message));
  page.on('requestfailed', (r) => bad.push('REQFAILED: ' + r.url()));
  page.on('response', (r) => { if (!r.ok() && r.status() !== 304) bad.push('HTTP ' + r.status() + ': ' + r.url()); });

  await page.goto('http://aiopen.localhost', { waitUntil: 'networkidle', timeout: 60000 });
  await page.fill('input[type="password"]', process.env.ADMIN_API_KEY);
  await page.click('button:has-text("Log in")');
  await page.waitForLoadState('networkidle');
  await page.click('text=Customer Churn Prediction');
  await page.waitForLoadState('networkidle');
  await page.click('text=ML Predictions');
  await page.waitForLoadState('networkidle');

  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('prediction.localhost') && r.request().method() === 'POST', { timeout: 60000 }),
    page.click('button:has-text("Run Customer Churn Prediction")'),
  ]);
  const body = await resp.json().catch(() => null);
  console.log('prediction POST ' + resp.url().replace(/^http:\/\//, '') + ' -> HTTP ' + resp.status());
  console.log('response keys: ' + (body ? Object.keys(body).join(', ') : '(non-JSON)'));
  await page.waitForTimeout(3000);
  await page.screenshot({ path: out + '/06-prediction-result.png', fullPage: true });
  const plots = await page.$$('.js-plotly-plot');
  const txt = await page.textContent('body');
  const m = txt.match(/(\d{1,3}(\.\d+)?\s*%)/g);
  console.log('after click: plotlyCharts=' + plots.length + ' percentagesOnPage=' + (m ? m.slice(0, 4).join(' ') : 'none'));
  console.log('problems: ' + bad.length); bad.slice(0, 10).forEach((b) => console.log('  ' + b));
  await browser.close();
})().catch((e) => { console.log('SCRIPT ERROR: ' + e.message); process.exit(1); });
