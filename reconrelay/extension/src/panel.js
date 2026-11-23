/*
 * DevTools panel: live mini-graph viewer.
 *
 * No third-party libraries — MV3's CSP forbids remote code and bundling a graph
 * library into an extension you load unpacked is more friction than a 200-line
 * force layout.
 */
import "./graph.js";

const Graph = self.RRGraph;

const canvas = document.getElementById("graph");
const ctx = canvas.getContext("2d");
const graph = Graph.createGraph({ maxNodes: 800 });

const visible = new Set();
let selected = null;
let animation = null;
let jobId = null;

function resize() {
  const ratio = window.devicePixelRatio || 1;
  const box = canvas.parentElement.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(box.width * ratio));
  canvas.height = Math.max(1, Math.floor(box.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  draw();
}

function draw() {
  const box = canvas.parentElement.getBoundingClientRect();
  ctx.clearRect(0, 0, box.width, box.height);
  if (!graph.nodes.size) return;

  graph.project(box.width, box.height);

  ctx.lineWidth = 1;
  for (const edge of graph.edges) {
    const a = graph.nodes.get(edge.source);
    const b = graph.nodes.get(edge.target);
    if (!a || !b) continue;
    if (!visible.has(a.type) || !visible.has(b.type)) continue;
    ctx.strokeStyle = "rgba(139,148,158,0.28)";
    ctx.beginPath();
    ctx.moveTo(a.sx, a.sy);
    ctx.lineTo(b.sx, b.sy);
    ctx.stroke();
  }

  ctx.font = "10px ui-monospace, Menlo, monospace";
  for (const node of graph.nodes.values()) {
    if (!visible.has(node.type)) continue;
    ctx.beginPath();
    ctx.arc(node.sx, node.sy, node.r, 0, Math.PI * 2);
    ctx.fillStyle = Graph.colourFor(node.type);
    ctx.globalAlpha = selected && selected !== node ? 0.45 : 1;
    ctx.fill();
    if (selected === node) {
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "#e6edf3";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.lineWidth = 1;
    }
    ctx.globalAlpha = 1;

    if (node.r > 6 || node.depth === 0) {
      const label = String(node.data).slice(0, 32);
      ctx.fillStyle = "rgba(230,237,243,0.75)";
      ctx.fillText(label, node.sx + node.r + 3, node.sy + 3);
    }
  }
}

function kick(iterations = 40) {
  if (animation) cancelAnimationFrame(animation);
  let remaining = iterations;
  const tick = () => {
    graph.step();
    draw();
    remaining -= 1;
    if (remaining > 0) animation = requestAnimationFrame(tick);
  };
  animation = requestAnimationFrame(tick);
}

function renderLegend() {
  const counts = graph.counts();
  const legend = document.getElementById("legend");
  const filters = document.getElementById("filters");
  legend.textContent = "";
  filters.textContent = "";

  const types = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  if (!types.length) {
    const empty = document.createElement("div");
    empty.id = "empty";
    empty.textContent = "No events yet.";
    legend.append(empty);
    return;
  }

  for (const type of types) {
    if (!visible.has(type)) visible.add(type);

    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = visible.has(type);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) visible.add(type);
      else visible.delete(type);
      draw();
    });
    label.append(checkbox, document.createTextNode(` ${type}`));
    filters.append(label);

    const row = document.createElement("div");
    row.className = "row";
    const dot = document.createElement("span");
    dot.innerHTML = `<span class="dot" style="background:${Graph.colourFor(type)}"></span>`;
    const name = document.createElement("span");
    name.textContent = type;
    const count = document.createElement("b");
    count.textContent = String(counts[type]);
    row.append(dot, name, count);
    legend.append(row);
  }
}

function showDetail(node) {
  const detail = document.getElementById("detail");
  if (!node) {
    detail.innerHTML = "<pre>Click a node to inspect it.</pre>";
    return;
  }
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(
    { type: node.type, data: node.data, module: node.module, depth: node.depth, ...node.pretty },
    null,
    2
  );
  detail.textContent = "";
  detail.append(pre);
}

canvas.addEventListener("click", (event) => {
  const box = canvas.getBoundingClientRect();
  selected = graph.nodeAt(event.clientX - box.left, event.clientY - box.top);
  showDetail(selected);
  draw();
});

canvas.addEventListener("mousemove", (event) => {
  const box = canvas.getBoundingClientRect();
  const hovered = graph.nodeAt(event.clientX - box.left, event.clientY - box.top);
  canvas.style.cursor = hovered ? "pointer" : "default";
  canvas.title = hovered ? `${hovered.type}: ${hovered.data}` : "";
});

document.getElementById("fit").addEventListener("click", () => kick(80));
document.getElementById("clear").addEventListener("click", () => {
  graph.clear();
  selected = null;
  showDetail(null);
  renderLegend();
  draw();
});

document.getElementById("scan").addEventListener("click", async () => {
  const tabId = chrome.devtools.inspectedWindow.tabId;
  chrome.runtime.sendMessage(
    {
      type: "rr/scan-now",
      tabId,
      options: { preset: document.getElementById("preset").value },
    },
    (response) => {
      const status = document.getElementById("status");
      if (!response?.ok) {
        status.textContent = `error: ${response?.error || "unknown"}`;
        return;
      }
      jobId = response.job.job_id;
      status.textContent = `scanning ${jobId}`;
      graph.clear();
      renderLegend();
    }
  );
});

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "rr/telemetry" && message.message.kind === "events") {
    const added = graph.addEvents(message.message.events);
    if (added) {
      renderLegend();
      kick(25);
    }
  } else if (message.type === "rr/telemetry" && message.message.kind === "job.finished") {
    document.getElementById("status").textContent =
      `${message.message.status} · ${message.message.stats?.total_events ?? 0} events`;
  } else if (message.type === "rr/error") {
    document.getElementById("status").textContent = `error: ${message.error}`;
  }
});

window.addEventListener("resize", resize);
function attachActiveJob() {
  chrome.runtime.sendMessage({ type: "rr/attach" }, (response) => {
    if (response?.ok) {
      jobId = response.job.id;
      document.getElementById("status").textContent = `${jobId} · ${response.job.status}`;
    }
  });
}

resize();
attachActiveJob();
