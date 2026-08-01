const { app, BrowserWindow, ipcMain } = require("electron");
const fs = require("fs");
const path = require("path");
const { ACTIONS } = require("./operations.cjs");


const PROJECT_DIR = path.resolve(__dirname, "..", "..");
const SCREENSHOT_PATH = path.join(PROJECT_DIR, "backend", "reports", "electron-smoke.png");
const invokedActions = [];

const smokeState = {
  database: { backend: "PostgreSQL", label: "smoke-test" },
  workers: {
    price_worker: { status: "healthy", age_seconds: 2 },
    news_worker: { status: "healthy", age_seconds: 3 }
  },
  latest_kline: { close: 400000, age_seconds: 30 },
  latest_news: { source: "smoke", age_seconds: 60, headline: "Smoke test headline" },
  clock: { status: "OK", skew_seconds: 0.1 },
  portfolio: { equity_brl: 9850, exposure_pct: 5, daily_reference_equity_brl: 10000, daily_drawdown_pct: 1.5, daily_drawdown_limit_pct: 10 },
  position: { quantity: 0.001, avg_cost_brl: 390000, reconciliation: { method: "smoke" } },
  rag: { documents: 1, chunks: 2 },
  external_rag: { status: "ready", dense_indexed: 2, lexical_indexed: 2 },
  logs: [{
    id: 3,
    timestamp: 1780000000,
    llm_action: "HOLD",
    action: "HOLD",
    llm_conviction: 60,
    system_reliability: 1,
    final_confidence: 0.6,
    execution_price: 400000,
    reasoning: "Smoke HOLD",
    snapshot: { technical: {}, data_health: {}, news_risk: { risk_level: "NORMAL" } }
  }],
  entry_evaluation: {
    entries: [
      { id: 1, kind: "approved", action: "BUY", timestamp: 1780000000, execution_price: 399000, technical: {}, horizons: { "5": { status: "data_gap" } } },
      { id: 2, kind: "blocked", action: "HOLD", timestamp: 1780000030, execution_price: 400000, technical: {}, horizons: {} }
    ]
  }
};

ipcMain.handle("ops:state", async () => smokeState);
ipcMain.handle("ops:run", async (_event, action) => {
  invokedActions.push(action);
  return { started: true, action };
});
ipcMain.handle("ops:stop", async () => ({ stopped: true }));

async function runSmokeTest() {
  const window = new BrowserWindow({
    width: 1080,
    height: 760,
    show: false,
    backgroundColor: "#0d1117",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });
  const rendererErrors = [];
  window.webContents.on("console-message", (event) => {
    if (event.level === "error") rendererErrors.push(event.message);
  });
  await window.loadFile(path.join(__dirname, "..", "dist", "index.html"));

  const result = await window.webContents.executeJavaScript(`(async () => {
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    await wait(250);
    const approved = document.querySelector('[data-evaluation-tab="approved"]');
    if (!approved) throw new Error('missing approved tab');
    approved.click();
    await wait(25);
    const approvedRows = document.querySelectorAll('tbody tr').length;
    const hasDataGap = [...document.querySelectorAll('.future.gap')].some(node => node.textContent.trim() === 'DATA GAP');
    const checkbox60 = document.querySelector('[data-horizon="60"]');
    if (!checkbox60) throw new Error('missing 60m checkbox');
    checkbox60.click();
    await wait(25);
    const has60Header = [...document.querySelectorAll('th')].some(node => node.textContent.trim() === '60m');
    const operations = document.querySelector('[data-section="settings"]');
    if (!operations) throw new Error('missing operations navigation');
    operations.click();
    await wait(25);
    const diagnostics = document.querySelector('[data-action="diagnostics"]');
    if (!diagnostics) throw new Error('missing diagnostics action');
    diagnostics.click();
    await wait(25);
    const availablePaperActions = new Set();
    const readPaperAction = () => availablePaperActions.add(document.querySelector('.pipeline button.primary')?.dataset.action);
    document.querySelector('[data-cycles="10"]').click();
    await wait(10);
    readPaperAction();
    const interval60DisabledForTen = document.querySelector('[data-interval="60"]').disabled;
    document.querySelector('[data-cycles="30"]').click();
    document.querySelector('[data-interval="30"]').click();
    await wait(10);
    readPaperAction();
    document.querySelector('[data-interval="60"]').click();
    await wait(10);
    readPaperAction();
    document.querySelector('[data-cycles="100"]').click();
    document.querySelector('[data-interval="30"]').click();
    await wait(10);
    readPaperAction();
    document.querySelector('[data-interval="60"]').click();
    await wait(10);
    readPaperAction();
    return {
      navButtons: document.querySelectorAll('nav button').length,
      actionButtons: document.querySelectorAll('.ops-footer button').length,
      approvedRows,
      hasDataGap,
      has60Header,
      activeNavigation: document.querySelector('nav button.active')?.textContent.trim(),
      diagnosticsEnabled: !diagnostics.disabled,
      interval60DisabledForTen,
      availablePaperActions: [...availablePaperActions].filter(Boolean).sort(),
      actionCoverage: [...new Set([
        ...document.querySelectorAll('[data-action]')
      ].map(node => node.dataset.action).concat([...availablePaperActions]))].sort(),
      previewBannerVisible: Boolean(document.querySelector('.preview-banner')),
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth
    };
  })()`);

  const image = await window.webContents.capturePage();
  fs.mkdirSync(path.dirname(SCREENSHOT_PATH), { recursive: true });
  fs.writeFileSync(SCREENSHOT_PATH, image.toPNG());

  const failures = [];
  if (result.navButtons !== 6) failures.push(`navButtons=${result.navButtons}`);
  if (result.actionButtons < 10) failures.push(`actionButtons=${result.actionButtons}`);
  if (result.approvedRows !== 1) failures.push(`approvedRows=${result.approvedRows}`);
  if (!result.hasDataGap) failures.push("data_gap evaluation not rendered explicitly");
  if (result.has60Header) failures.push("60m header remained after uncheck");
  if (result.activeNavigation !== "Operations") failures.push(`activeNavigation=${result.activeNavigation}`);
  if (!result.diagnosticsEnabled) failures.push("diagnostics disabled in Electron");
  if (!result.interval60DisabledForTen) failures.push("unsupported paper10/60 selection remained enabled");
  const expectedActions = Object.keys(ACTIONS).sort();
  const missingActions = expectedActions.filter(action => !result.actionCoverage.includes(action));
  if (missingActions.length) failures.push(`missing action coverage: ${missingActions.join(", ")}`);
  const expectedPaperActions = ["experiment100_30", "paper10", "paper100", "paper30", "paper30_60"];
  if (JSON.stringify(result.availablePaperActions) !== JSON.stringify(expectedPaperActions)) failures.push(`paper action mapping=${result.availablePaperActions}`);
  if (result.previewBannerVisible) failures.push("browser preview banner visible in Electron");
  if (result.documentWidth > result.viewportWidth + 2) failures.push(`horizontal overflow ${result.documentWidth}/${result.viewportWidth}`);
  if (!invokedActions.includes("diagnostics")) failures.push("diagnostics IPC was not invoked");
  if (rendererErrors.length) failures.push(`renderer errors: ${rendererErrors.join(" | ")}`);

  console.log(JSON.stringify({ ...result, invokedActions, rendererErrors, screenshot: SCREENSHOT_PATH }, null, 2));
  window.destroy();
  if (failures.length) throw new Error(failures.join("; "));
}

app.whenReady()
  .then(runSmokeTest)
  .then(() => app.exit(0))
  .catch(error => {
    console.error(error.stack || error.message);
    app.exit(1);
  });
