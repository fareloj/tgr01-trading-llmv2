const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("tgrOps", {
  run: (action, options) => ipcRenderer.invoke("ops:run", action, options),
  stop: () => ipcRenderer.invoke("ops:stop"),
  state: () => ipcRenderer.invoke("ops:state"),
  onOutput: callback => {
    const listener = (_event, value) => callback(value);
    ipcRenderer.on("ops:output", listener);
    return () => ipcRenderer.removeListener("ops:output", listener);
  },
  onStatus: callback => {
    const listener = (_event, value) => callback(value);
    ipcRenderer.on("ops:status", listener);
    return () => ipcRenderer.removeListener("ops:status", listener);
  }
});
