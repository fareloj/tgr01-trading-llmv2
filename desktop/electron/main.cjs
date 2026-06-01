const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

const PROJECT_DIR = path.resolve(__dirname, "..", "..");
const isDev = !app.isPackaged;
let mainWindow;
let activeProcess;

function safeSinceId(options = {}) {
  const sinceId = Number.parseInt(options.sinceId, 10);
  return Number.isFinite(sinceId) && sinceId > 0 ? String(sinceId) : "1";
}

const ACTIONS = {
  startWorkers: "start_workers",
  preflight: "preflight",
  paper30: "paper30",
  paper30_60: "paper30_60",
  paper100_30: "experiment100_30",
  paper100: "experiment100_60",
  analyzeLogs: "logs",
  analyzeEntries: "entries",
  evaluate: "future",
  readiness: "readiness",
  llmReview: "llm_review",
  ragDocs: "rag_docs",
  ragNews: "rag_news"
};

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1540,
    height: 980,
    minWidth: 1180,
    minHeight: 760,
    backgroundColor: "#0d1117",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  if (isDev) {
    mainWindow.loadURL("http://127.0.0.1:5173");
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

function emit(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

ipcMain.handle("ops:run", async (_event, action, options) => {
  if (!ACTIONS[action]) throw new Error(`Ação não permitida: ${action}`);
  if (activeProcess) throw new Error("Já existe um processo em execução.");
  const args = ["backend/ops/run_action.py", ACTIONS[action], "--since-id", safeSinceId(options)];
  activeProcess = spawn("python", args, { cwd: PROJECT_DIR, windowsHide: true });
  emit("ops:status", { running: true, action });
  activeProcess.stdout.on("data", data => emit("ops:output", data.toString()));
  activeProcess.stderr.on("data", data => emit("ops:output", data.toString()));
  activeProcess.on("close", code => {
    emit("ops:output", `\n[PROCESS] ${action} finalizado com código ${code}.\n`);
    emit("ops:status", { running: false, action, code });
    activeProcess = null;
  });
  return { started: true, action };
});

ipcMain.handle("ops:stop", async () => {
  if (!activeProcess) return { stopped: false };
  activeProcess.kill();
  return { stopped: true };
});

ipcMain.handle("ops:state", async () => {
  return new Promise((resolve, reject) => {
    const child = spawn("python", ["backend/tests/dashboard_state.py"], { cwd: PROJECT_DIR, windowsHide: true });
    let output = "";
    let error = "";
    child.stdout.on("data", data => { output += data.toString(); });
    child.stderr.on("data", data => { error += data.toString(); });
    child.on("close", code => {
      if (code !== 0) return reject(new Error(error || `dashboard_state.py terminou com código ${code}`));
      try {
        resolve(JSON.parse(output));
      } catch (parseError) {
        reject(parseError);
      }
    });
  });
});

app.whenReady().then(createWindow);
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
