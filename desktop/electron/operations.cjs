const { spawn, spawnSync } = require("child_process");

const ACTIONS = Object.freeze({
  diagnostics: "diagnostics",
  startWorkers: "start_workers",
  preflight: "preflight",
  paper10: "paper10",
  paper30: "paper30",
  paper30_60: "paper30_60",
  paper100: "paper100",
  experiment100_30: "experiment100_30",
  experiment100_60: "experiment100_60",
  analyzeLogs: "logs",
  analyzeEntries: "entries",
  evaluate: "future",
  readiness: "readiness",
  llmReview: "llm_review",
  ragDocs: "rag_docs",
  ragNews: "rag_news",
  externalRag: "external_rag"
});

function safeSinceId(options = {}) {
  const sinceId = Number.parseInt(options.sinceId, 10);
  return Number.isFinite(sinceId) && sinceId > 0 ? String(sinceId) : "1";
}

function pythonCandidates(platform = process.platform, env = process.env) {
  const configured = String(env.TGR_PYTHON_EXECUTABLE || "").trim();
  const candidates = configured ? [{ command: configured, prefix: [] }] : [];
  if (platform === "win32") candidates.push({ command: "py", prefix: ["-3.11"] });
  candidates.push({ command: "python", prefix: [] }, { command: "python3", prefix: [] });
  return candidates;
}

function resolvePythonExecutable({ probe = spawnSync, platform = process.platform, env = process.env } = {}) {
  const check = "import sys, sqlalchemy; print(sys.executable)";
  for (const candidate of pythonCandidates(platform, env)) {
    const result = probe(candidate.command, [...candidate.prefix, "-c", check], {
      encoding: "utf8",
      windowsHide: true
    });
    if (result.status === 0) {
      const executable = String(result.stdout || "").trim().split(/\r?\n/).filter(Boolean).at(-1);
      if (executable) return executable;
    }
  }
  throw new Error("Python compativel nao encontrado. Configure TGR_PYTHON_EXECUTABLE.");
}

function terminateProcessTree(child, { platform = process.platform, spawnProcess = spawn } = {}) {
  if (!child || !child.pid) return Promise.resolve(false);
  if (platform !== "win32") {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch (_error) {
      child.kill("SIGTERM");
    }
    return Promise.resolve(true);
  }
  return new Promise(resolve => {
    const killer = spawnProcess("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
    killer.once("error", () => {
      child.kill();
      resolve(true);
    });
    killer.once("close", () => resolve(true));
  });
}

module.exports = { ACTIONS, pythonCandidates, resolvePythonExecutable, safeSinceId, terminateProcessTree };
