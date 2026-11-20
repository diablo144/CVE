import { getConfig, saveConfig, health } from "./api.js";

const FIELDS = ["serverUrl", "apiKey", "operator", "preset", "maxDepth", "autoSubmit", "activeModules"];

function read() {
  const patch = {};
  for (const field of FIELDS) {
    const node = document.getElementById(field);
    patch[field] = node.type === "checkbox" ? node.checked : node.value;
  }
  patch.maxDepth = Math.min(6, Math.max(1, Number(patch.maxDepth) || 3));
  patch.serverUrl = patch.serverUrl.replace(/\/+$/, "");
  return patch;
}

function show(message, kind) {
  const node = document.getElementById("result");
  node.textContent = message;
  node.className = kind || "";
}

async function load() {
  const config = await getConfig();
  for (const field of FIELDS) {
    const node = document.getElementById(field);
    if (node.type === "checkbox") node.checked = Boolean(config[field]);
    else node.value = config[field];
  }
}

document.getElementById("save").addEventListener("click", async () => {
  const patch = read();
  if (!/^https?:\/\/.+/.test(patch.serverUrl)) {
    return show("Server URL must start with http:// or https://", "bad");
  }
  if (patch.serverUrl.startsWith("http://")) {
    show("Saved, but this is plain HTTP — the token is readable on the wire.", "bad");
  }
  await saveConfig(patch);
  show("Saved. Open a page and press the toolbar icon to scan.", "ok");
  return undefined;
});

document.getElementById("test").addEventListener("click", async () => {
  await saveConfig(read());
  show("Contacting gateway…");
  try {
    const info = await health();
    show(
      `Connected. Store: ${info.store}. Scopes configured: ${info.scopes_configured}.` +
        `${info.allow_any_scope ? " WARNING: allow_any_scope is ON." : ""}` +
        `${info.active_modules_allowed ? " Active modules permitted." : ""}`,
      info.scopes_configured ? "ok" : "bad"
    );
  } catch (error) {
    show(`Failed: ${error.message}`, "bad");
  }
});

load();
