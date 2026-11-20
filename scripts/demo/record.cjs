// Drive the real web app (python -m t2s_eval.web) in Chromium and save one PNG per frame.
// Usage (inside mcr.microsoft.com/playwright:v1.65.0-noble): node record.cjs <app-url> <frames-dir>
// Writes <frames-dir>/frames.json = [{file, ms}], which make_gif.py turns into docs/demo.gif.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [url = 'http://127.0.0.1:8765', outDir = 'frames'] = process.argv.slice(2);

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({ args: ['--font-render-hinting=none'] });
  const page = await browser.newPage({ viewport: { width: 1000, height: 625 }, deviceScaleFactor: 1 });
  const frames = [];
  const shot = async (ms, opts = {}) => {
    const file = `f${String(frames.length).padStart(3, '0')}.png`;
    await page.screenshot({ path: path.join(outDir, file), ...opts });
    frames.push({ file, ms });
  };
  const score = async (holdMs) => {
    await page.hover('#run');
    await shot(200);
    await page.click('#run');
    await page.waitForFunction(() => document.querySelector('#summary:not(.stale)'));
    await page.mouse.move(990, 615);
    await shot(holdMs);
  };

  // 1. q03: worded differently from gold, same result -> correct
  await page.goto(url + '/#q03');
  await page.waitForFunction(() => document.getElementById('gold').value.length > 0);
  await page.evaluate(() => document.fonts.ready);
  await shot(700);
  await score(2500);

  // 2. q09: one word apart (GROUP BY name vs customer_id), different rows -> result_mismatch
  await page.selectOption('#pick', 'q09');
  await shot(500);
  await score(3100);

  // 3. fix the prediction by hand and score again -> correct
  await page.evaluate(() => {
    const t = document.getElementById('pred');
    const at = t.value.lastIndexOf('c.name') + 2;
    t.focus();
    t.setSelectionRange(at, at + 'name'.length);
  });
  await shot(350);
  for (const chunk of ['cus', 'tom', 'er_', 'id']) {
    await page.keyboard.type(chunk);
    await shot(chunk === 'id' ? 300 : 120, { caret: 'initial' });
  }
  await score(3000);

  fs.writeFileSync(path.join(outDir, 'frames.json'), JSON.stringify(frames, null, 1));
  const total = frames.reduce((s, f) => s + f.ms, 0);
  console.log(`${frames.length} frames, ${(total / 1000).toFixed(2)} s`);
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
