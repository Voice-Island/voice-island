// Renders each screen of screens.html as a 2880x1800 PNG (like a MacBook Air Retina screenshot).
// npm i -D playwright && npx playwright install chromium, then: node web/test/vision/render.mjs
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "playwright";

const here = path.dirname(fileURLToPath(import.meta.url));

const page = await (await chromium.launch()).newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const url = c => "file://" + path.join(here, "screens.html") + (c ? `?case=${c}` : "");
await page.goto(url());
const cases = await page.evaluate(() => window.CASES);
for (const c of cases) {
  await page.goto(url(c));
  await page.screenshot({ path: path.join(here, "screens", `${c}.png`) });
  console.log(c);
}
process.exit(0);
