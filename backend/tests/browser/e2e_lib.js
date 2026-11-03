// Helpers for driving the REAL Flutter web app in headless Chromium through its accessibility tree.
// Usage: NODE_PATH=$(npm root -g) node your_script.js   (Playwright's Chromium is preinstalled in cloud sessions.)
//   const { launch, open, nodes, click } = require('./e2e_lib');
// Software GL flags are required in headless sandboxes. `open` enables Flutter semantics so labels are readable.
// `nodes(page)` lists {role,label,x,y,w,h}; `click(page, /regex/)` clicks the first element whose label matches.
const { chromium } = require('playwright');
exports.launch = async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const net = [];
  page.on('response', r => { if (r.url().includes('127.0.0.1:8000')) net.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  page.on('pageerror', e => net.push('PAGEERROR ' + String(e).slice(0, 160)));
  return { browser, page, net };
};
exports.open = async (page, url = 'http://localhost:5180/') => {
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForSelector('flt-glass-pane', { state: 'attached', timeout: 60000 });
  await page.waitForTimeout(5000);
  await page.evaluate(() => document.querySelector('flt-semantics-placeholder')?.click());
  await page.waitForTimeout(2000);
};
exports.nodes = (page) => page.evaluate(() => [...document.querySelectorAll('flt-semantics')].map(e => {
  const r = e.getBoundingClientRect();
  return { role: e.getAttribute('role') || '', label: (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 90), x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width), h: Math.round(r.height) };
}).filter(n => n.w > 0 && n.h > 0 && n.label));
exports.click = async (page, pattern) => {
  const list = await exports.nodes(page);
  const hit = list.find(n => pattern.test(n.label));
  if (!hit) throw new Error('no element matching ' + pattern + ' among: ' + list.map(n => n.label).slice(0, 25).join(' | '));
  await page.mouse.click(hit.x, hit.y);
  await page.waitForTimeout(1500);
  return hit;
};
