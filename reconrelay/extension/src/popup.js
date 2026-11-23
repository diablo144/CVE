import { getConfig, saveConfig } from "./api.js";

const $ = (id) => document.getElementById(id);
const events = new Map();
let activeJobId = null;

function setStatus(kind, text) {
  const pill = $("status");
  pill.className = `pill pill-${kind}`;
  pill.textContent = text;
}

function renderCounts(counts) {
  const list = $("count-list");
  list.textContent = "";
  for (const [key, value] of Object.entries(counts)) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = key.replace(/_/g, " ");
    const count = document.createElement("b");
    count.textContent = String(value);
    item.append(name, count);
    list.append(item);
  }
  $("counts").hidden = false;
}

function renderEvent(event) {
  if (events.has(event.id)) return;
  events.set(event.id, event);

  const list = $("event-list");
  const item = document.createElement("li");
  const type = document.createElement("span");
  type.className = `t t-${event.type}`;
  type.textContent = event.type.toLowerCase();
  const data = document.createElement("span");
  data.className = "d";
  data.textContent = String(event.data).slice(0, 160);
  item.append(type, data);
  list.prepend(item);

  while (list.children.length > 300) list.lastChild.remove();
  $("event-count").textContent = String(events.size);
  $("empty").hidden = true;
}

function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
        return;
      }
      resolve(response || { ok: false, error: "no response" });
    });
  });
}

async function init() {
  const config = await getConfig();
  $("preset").value = config.preset;
  $("depth").value = String(config.maxDepth);

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) $("page-url").textContent = tab.url || tab.pendingUrl || "—";

  const probe = await send({ type: "rr/health" });
  if (probe.ok) {
    setStatus("on", probe.health.scopes_configured ? "online" : "online · no scopes");
  } else {
    setStatus("off", "unreachable");
    $("scan").disabled = true;
  }

  const attach = await send({ type: "rr/attach" });
  if (attach.ok) {
    activeJobId = attach.job.id;
    $("empty").textContent = `Reattached to ${attach.job.id} (${attach.job.status}).`;
  }
}

$("scan").addEventListener("click", async () => {
  $("scan").disabled = true;
  $("scan").textContent = "Submitting…";
  const response = await send({
    type: "rr/scan-now",
    options: {
      preset: $("preset").value,
      max_depth: Number($("depth").value) || 3,
    },
  });
  $("scan").disabled = false;
  $("scan").textContent = "Scan this page";

  if (!response.ok) {
    showError(response.error);
    return;
  }
  activeJobId = response.job.job_id;
  events.clear();
  $("event-list").textContent = "";
  $("event-count").textContent = "0";
  $("empty").hidden = true;
  await saveConfig({ preset: $("preset").value, maxDepth: Number($("depth").value) });
});

$("preview").addEventListener("click", async () => {
  const response = await send({ type: "rr/preview" });
  if (!response.ok) return showError(response.error);
  renderCounts(response.counts);
});

$("open-options").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});

function showError(message) {
  let node = document.querySelector(".error");
  if (!node) {
    node = document.createElement("p");
    node.className = "error";
    $("controls").append(node);
  }
  node.textContent = String(message).slice(0, 300);
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "rr/telemetry" && message.message.kind === "events") {
    for (const event of message.message.events) renderEvent(event);
  } else if (message.type === "rr/error") {
    showError(message.error);
  } else if (message.type === "rr/job-started") {
    activeJobId = message.job.job_id;
  }
});

init();
