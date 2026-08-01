const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

const {
  ACTIONS,
  resolvePythonExecutable,
  safeSinceId,
  terminateProcessTree
} = require("./operations.cjs");

const PROJECT_DIR = path.resolve(__dirname, "..", "..");
const isDev = !app.isPackaged;
let mainWindow;
let activeProcess;
let pythonExecutable;

function getPythonExecutable() {
  if (!pythonExecutable) pythonExecutable = resolvePythonExecutable();
  return pythonExecutable;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1540,
    height: 980,
    minWidth: 1080,
    minHeight: 720,
    backgroundColor: "#0d1117",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true
    }
  });

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", event => event.preventDefault());

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
  if (!ACTIONS[action]) throw new Error(`Acao nao permitida: ${action}`);
  if (activeProcess) throw new Error("Ja existe um processo em execucao.");

  const args = ["backend/ops/run_action.py", ACTIONS[action], "--since-id", safeSinceId(options)];
  activeProcess = spawn(getPythonExecutable(), args, {
    cwd: PROJECT_DIR,
    windowsHide: true,
    detached: process.platform !== "win32"
  });
  const launchedProcess = activeProcess;
  emit("ops:status", { running: true, action });
  launchedProcess.stdout.on("data", data => emit("ops:output", data.toString()));
  launchedProcess.stderr.on("data", data => emit("ops:output", data.toString()));
  launchedProcess.on("error", error => {
    emit("ops:output", `\n[PROCESS ERROR] ${error.message}\n`);
    emit("ops:status", { running: false, action, code: -1 });
    if (activeProcess === launchedProcess) activeProcess = null;
  });
  launchedProcess.on("close", code => {
    emit("ops:output", `\n[PROCESS] ${action} finalizado com codigo ${code}.\n`);
    emit("ops:status", { running: false, action, code });
    if (activeProcess === launchedProcess) activeProcess = null;
  });
  return { started: true, action };
});

ipcMain.handle("ops:stop", async () => {
  if (!activeProcess) return { stopped: false };
  const processToStop = activeProcess;
  await terminateProcessTree(processToStop);
  emit("ops:output", "\n[PROCESS] Encerramento da arvore de processos solicitado.\n");
  return { stopped: true };
});

ipcMain.handle("ops:state", async () => {
  return new Promise((resolve, reject) => {
    const child = spawn(getPythonExecutable(), ["backend/tests/dashboard_state.py"], {
      cwd: PROJECT_DIR,
      windowsHide: true
    });
    let output = "";
    let error = "";
    child.stdout.on("data", data => { output += data.toString(); });
    child.stderr.on("data", data => { error += data.toString(); });
    child.on("error", reject);
    child.on("close", code => {
      if (code !== 0) return reject(new Error(error || `dashboard_state.py terminou com codigo ${code}`));
      try {
        resolve(JSON.parse(output));
      } catch (parseError) {
        reject(parseError);
      }
    });
  });
});

app.whenReady().then(createWindow);
app.on("before-quit", () => {
  if (activeProcess) void terminateProcessTree(activeProcess);
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
