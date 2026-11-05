// Браузерная проверка интерфейса в CI: React действительно отрисовал экран
// входа, а в консоли нет ошибок. Сборка и tsc не ловят ошибок времени
// выполнения — например, несовместимость версий React и ReactDOM или
// скрипт, заблокированный политикой CSP.
//   node deploy/ci/smoke-ui.mjs http://localhost:8080
import { chromium } from "playwright";

const url = process.argv[2] || "http://localhost:8080";
const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(url, { waitUntil: "networkidle" });
try {
  // Заголовок экрана входа (frontend/src/components/Login.tsx).
  await page.getByRole("heading", { name: "График отпусков" }).waitFor({ timeout: 15000 });
} catch (e) {
  console.error("Экран входа не отрисован. Текст страницы:\n" + (await page.innerText("body")).slice(0, 500));
  console.error("Ошибки в консоли:\n" + errors.join("\n"));
  throw e;
}
// Нарушения CSP браузер пишет в консоль как ошибки — они тоже провал.
await browser.close();
if (errors.length) {
  console.error("Ошибки в консоли браузера:\n" + errors.join("\n"));
  process.exit(1);
}
console.log("Интерфейс отрисован, ошибок в консоли нет");
