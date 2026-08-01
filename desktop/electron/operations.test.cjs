const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const test = require("node:test");

const { ACTIONS, pythonCandidates, resolvePythonExecutable, safeSinceId, terminateProcessTree } = require("./operations.cjs");

test("desktop exposes every allowlisted operational action", () => {
  assert.deepEqual(new Set(Object.values(ACTIONS)), new Set([
    "diagnostics", "start_workers", "preflight", "paper10", "paper30", "paper30_60",
    "paper100", "experiment100_30", "experiment100_60", "logs", "entries", "future", "readiness",
    "llm_review", "rag_docs", "rag_news", "external_rag"
  ]));
});

test("since id rejects invalid and non-positive values", () => {
  assert.equal(safeSinceId({ sinceId: "303" }), "303");
  assert.equal(safeSinceId({ sinceId: "0" }), "1");
  assert.equal(safeSinceId({ sinceId: "oops" }), "1");
});

test("python discovery honors configuration and resolves the launcher", () => {
  const candidates = pythonCandidates("win32", { TGR_PYTHON_EXECUTABLE: "D:\\Python\\python.exe" });
  assert.equal(candidates[0].command, "D:\\Python\\python.exe");
  const calls = [];
  const executable = resolvePythonExecutable({
    platform: "win32",
    env: {},
    probe(command, args) {
      calls.push([command, args]);
      return command === "py"
        ? { status: 0, stdout: "C:\\Python311\\python.exe\r\n" }
        : { status: 1, stdout: "" };
    }
  });
  assert.equal(executable, "C:\\Python311\\python.exe");
  assert.deepEqual(calls[0][1].slice(0, 1), ["-3.11"]);
});

test("windows stop terminates the complete process tree", async () => {
  const calls = [];
  const stopped = await terminateProcessTree({ pid: 4321, kill() {} }, {
    platform: "win32",
    spawnProcess(command, args, options) {
      calls.push({ command, args, options });
      const process = new EventEmitter();
      queueMicrotask(() => process.emit("close", 0));
      return process;
    }
  });
  assert.equal(stopped, true);
  assert.equal(calls[0].command, "taskkill");
  assert.deepEqual(calls[0].args, ["/PID", "4321", "/T", "/F"]);
});
