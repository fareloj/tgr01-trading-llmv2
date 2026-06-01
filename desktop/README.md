# TGR-01 Ops Console

Electron operational dashboard for the Python CLI.

The desktop app does not implement trading logic. It invokes an allowlist of
existing Python commands through `backend/ops/run_action.py` and reads
SQLite-derived JSON state.

## Install

```powershell
cd .\desktop
npm install
```

## Browser Preview

```powershell
npx vite --host 127.0.0.1
```

Open `http://127.0.0.1:5173`.

The browser preview uses demonstration data because browser pages do not have
access to the Electron preload bridge.

## Electron Development

```powershell
npm run dev
```

If Electron reports that it failed to install correctly, verify whether
Windows Security quarantined `desktop\node_modules\electron\dist\electron.exe`
and reinstall the package after resolving the local quarantine policy. On this
workspace, the cached ZIP may also require controlled re-extraction:

```powershell
npm run repair-electron
```

## Backend Commands Exposed

- strict preflight with HTTP clock skew check;
- paper trading: 30 cycles / 30 or 60 seconds;
- paper trading: 100 cycles / 30 or 60 seconds;
- trade log analysis;
- entry analysis for approved and blocked orders;
- future movement evaluation.
- readiness and worker startup;
- curated RAG document/news ingestion;
- recent deterministic report inventory.
