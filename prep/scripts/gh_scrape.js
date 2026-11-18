// Google Hotels list for Niagara Falls: name, price per night (CAD), rating, reviews, stars.
// One results page at a time, slowly; stops if a block page appears.
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const out = process.argv[3], url = process.argv[2], maxPages = Number(process.argv[4] || 30);
  const b = await chromium.launch();
  const ctx = await b.newContext({ locale: 'en-CA', userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36' });
  const p = await ctx.newPage();
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForTimeout(5000);
  const pages = [];
  for (let k = 0; k < maxPages; k++) {
    const t = await p.evaluate(() => document.body.innerText);
    if (/unusual traffic|not a robot|recaptcha/i.test(t)) { console.log('block page: stopping'); break; }
    pages.push(t);
    const shown = (/Showing results (\d+)/.exec(t) || [])[1];
    const next = p.locator('button:has-text("Next"), [role=button]:has-text("Next")').last();
    if (!(await next.count())) break;
    await next.scrollIntoViewIfNeeded();
    await next.click();
    try {
      await p.waitForFunction(s => { const m = /Showing results (\d+)/.exec(document.body.innerText); return m && m[1] !== s; }, shown, { timeout: 15000 });
    } catch (e) { console.log('no further page after', shown); break; }
    await p.waitForTimeout(3000 + Math.random() * 2000);
  }
  fs.writeFileSync(out, JSON.stringify(pages));
  console.log('pages', pages.length);
  await b.close();
})();
