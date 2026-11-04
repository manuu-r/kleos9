// Opens a built Flutter web bundle served at http://127.0.0.1:8765/ in Chromium (software GL), clicks the
// floating action button of the qualification fixture and prints the visible counter before and after.
// Usage: unzip companion-web.zip into a directory, `python3 -m http.server 8765 --bind 127.0.0.1` there, then
//   NODE_PATH=$(npm root -g) node browse_web_bundle.js
// Browser evidence only: it does not qualify BLE, devices or any other browser.
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width: 480, height: 640 } });
  const errors = [], requests = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('request', r => { if (!r.url().startsWith('http://127.0.0.1:8765')) requests.push(r.url()); });
  await page.goto('http://127.0.0.1:8765/', { waitUntil: 'load' });
  await page.waitForSelector('flt-glass-pane', { state: 'attached', timeout: 60000 });
  await page.waitForTimeout(4000);
  await page.evaluate(() => document.querySelector('flt-semantics-placeholder')?.click());
  await page.waitForTimeout(1500);
  const read = async () => (await page.evaluate(() => document.body.innerText + ' ' + [...document.querySelectorAll('flt-semantics')].map(e => e.textContent).join(' ')));
  const before = await read();
  const png1 = await page.screenshot();
  await page.mouse.click(430, 590);  // floating action button, bottom-right
  await page.waitForTimeout(1500);
  const after = await read();
  const png2 = await page.screenshot();
  console.log(JSON.stringify({ before: before.match(/Local samples: \d+/)?.[0], after: after.match(/Local samples: \d+/)?.[0],
    screenshotsDiffer: Buffer.compare(png1, png2) !== 0, screenshotBytes: png1.length, errors, externalRequests: requests }));
  await browser.close();
})();
