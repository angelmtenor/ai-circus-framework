const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const out = process.env.OUT_DIR;
(async () => {
  const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const bad = [];
  page.on('pageerror', (e) => bad.push('PAGEERROR: ' + e.message));
  page.on('requestfailed', (r) => bad.push('REQFAILED: ' + r.method() + ' ' + r.url() + ' ' + (r.failure() || {}).errorText));
  page.on('response', (r) => { if (!r.ok() && r.status() !== 304) bad.push('HTTP ' + r.status() + ': ' + r.url()); });

  await page.goto('http://aiopen.localhost', { waitUntil: 'networkidle', timeout: 60000 });
  await page.screenshot({ path: out + '/01-login.png' });
  const userField = await page.$('select, [role="combobox"]');
  console.log('login page: title=' + JSON.stringify(await page.title()) + ' hasUserDropdown=' + !!userField);

  await page.fill('input[type="password"]', process.env.ADMIN_API_KEY);
  await page.click('button:has-text("Log in")');
  await page.waitForLoadState('networkidle', { timeout: 60000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: out + '/02-after-login.png' });
  const cards = await page.$$('text=Customer Churn Prediction');
  console.log('after login: url=' + page.url() + ' churnCardVisible=' + (cards.length > 0));

  if (cards.length) {
    await cards[0].click();
    await page.waitForLoadState('networkidle', { timeout: 90000 });
    // "Loading dataset…" can take ~10s for the 5k-row sample — wait for it to clear
    try { await page.waitForSelector('text=/Loading dataset/i', { state: 'detached', timeout: 60000 }); } catch {}
    await page.waitForTimeout(2000);
    await page.screenshot({ path: out + '/03-churn-scenario.png', fullPage: false });
    const svg = await page.$$('svg, canvas, .js-plotly-plot');
    console.log('scenario page: url=' + page.url() + ' chartElements=' + svg.length);

    // Data & BI tab — depends on etl-tabular's output in SeaweedFS
    await page.click('text=Data & BI');
    try { await page.waitForSelector('text=/Loading dataset/i', { state: 'detached', timeout: 60000 }); } catch {}
    await page.waitForLoadState('networkidle', { timeout: 60000 });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: out + '/04-data-bi.png' });
    const plots = await page.$$('.js-plotly-plot');
    const rows = await page.$$('table tbody tr');
    console.log('data tab: plotlyCharts=' + plots.length + ' tableRows=' + rows.length);

    // ML Predictions tab — depends on training's model artifact + the prediction service
    await page.click('text=ML Predictions');
    await page.waitForLoadState('networkidle', { timeout: 60000 });
    await page.waitForTimeout(2000);
    const predictBtn = await page.$('button:has-text("Predict")');
    if (predictBtn) {
      await predictBtn.click();
      await page.waitForLoadState('networkidle', { timeout: 60000 });
      await page.waitForTimeout(4000);
    }
    await page.screenshot({ path: out + '/05-ml-predictions.png' });
    const body = await page.textContent('body');
    const hasResult = /churn|probab|stayed|exited/i.test(body);
    const hasError = /not trained|no model|error/i.test(body);
    console.log('predictions tab: predictButton=' + !!predictBtn + ' resultText=' + hasResult + ' errorText=' + hasError + ' plotly=' + (await page.$$('.js-plotly-plot')).length);
  }
  console.log('problems: ' + bad.length);
  bad.slice(0, 15).forEach((b) => console.log('  ' + b));
  await browser.close();
})().catch((e) => { console.log('SCRIPT ERROR: ' + e.message); process.exit(1); });
