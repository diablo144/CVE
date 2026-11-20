/*
 * ReconRelay service worker (MV3).
 *
 * MV3 gives no persistent background page: this worker is torn down after ~30s
 * idle and every in-memory variable is lost. So all durable state lives in
 * chrome.storage.local, the telemetry socket is re-openable, and a periodic
 * alarm can recover a live job after a worker restart. Server heartbeats keep
 * active WebSocket traffic flowing between alarms.
 */

import {
  getConfig,
  health,
  saveConfig,
  submitJob,
  fetchJob,
  streamJob,
} from "./api.js";
import lib from "./harvest.mod.js";

const NETWORK_BUFFER_KEY = "networkBuffer";
const ACTIVE_JOB_KEY = "activeJob";
const MAX_API_CALLS = 200;
const MAX_JS_FILES = 12;

let stream = null;

/* ------------------------------------------------------------------- storage */

async function read(key, fallback) {
  const stored = await chrome.storage.local.get(key);
  return key in stored ? stored[key] : fallback;
}

async function write(key, value) {
  await chrome.storage.local.set({ [key]: value });
}

async function readNetworkStore() {
  const stored = await chrome.storage.session.get(NETWORK_BUFFER_KEY);
  return stored[NETWORK_BUFFER_KEY] || {};
}

async function writeNetworkStore(buffer) {
  await chrome.storage.session.set({ [NETWORK_BUFFER_KEY]: buffer });
}

/* ------------------------------------------------------------------ network */

function initWebRequest() {
  if (!chrome.webRequest?.onResponseStarted) return;

  chrome.webRequest.onResponseStarted.addListener(
    (details) => {
      if (details.tabId < 0) return;
      if (!/^https?:/.test(details.url)) return;
      bufferNetwork(details).catch(console.warn);
    },
    { urls: ["http://*/*", "https://*/*"] },
    ["responseHeaders", "extraHeaders"]
  );
}

async function bufferNetwork(details) {
  const buffer = await readNetworkStore();
  const tabKey = String(details.tabId);
  const entry = buffer[tabKey] || { headers: {}, api_calls: [] };

  const headers = lib.interestingHeaders(details.responseHeaders || []);
  for (const [name, value] of Object.entries(headers)) {
    entry.headers[name] = value;
  }

  if (lib.looksLikeApi(details.url) || details.statusCode >= 400) {
    entry.api_calls.push({
      url: lib.redactUrl(details.url),
      method: details.method,
      status: details.statusCode,
      type: details.type,
    });
    if (entry.api_calls.length > MAX_API_CALLS) {
      entry.api_calls = entry.api_calls.slice(-MAX_API_CALLS);
    }
  }

  buffer[tabKey] = entry;
  await writeNetworkStore(buffer);
}

async function readNetwork(tabId) {
  const buffer = await readNetworkStore();
  return buffer[String(tabId)] || { headers: {}, api_calls: [] };
}

async function takeNetwork(tabId) {
  const buffer = await readNetworkStore();
  const entry = buffer[String(tabId)] || { headers: {}, api_calls: [] };
  delete buffer[String(tabId)];
  await writeNetworkStore(buffer);
  return entry;
}

/* --------------------------------------------------------------------- jobs */

async function analyseJsFiles(jsFiles, baseUrl) {
  const out = { endpoints: [], urls: [], hosts: [], secrets: [], cloud_assets: [] };
  const config = await getConfig();

  for (const url of jsFiles.slice(0, MAX_JS_FILES)) {
    // Only pull scripts from the site under test — the server-side allowlist
    // will drop anything else anyway, and this keeps the extension from being
    // used to crawl third parties.
    if (!lib.sameSite(url, baseUrl)) continue;
    try {
      const response = await fetch(url, { credentials: "omit", redirect: "follow" });
      if (!response.ok) continue;
      const source = (await response.text()).slice(0, 500_000);
      const found = lib.fromScript(source, url);
      out.endpoints.push(...found.endpoints);
      out.urls.push(...found.urls);
      out.hosts.push(...found.hosts);
      out.secrets.push(...found.secrets);
      out.cloud_assets.push(...found.cloudAssets);
    } catch (error) {
      console.debug("js analysis skipped", url, error.message);
    }
  }

  // Never leave the operator's own server URL in the payload.
  const serverHost = lib.hostOf(config.serverUrl);
  out.endpoints = lib.dedupe(out.endpoints);
  out.urls = lib.dedupe(out.urls.filter((u) => lib.hostOf(u) !== serverHost));
  out.hosts = lib.dedupe(out.hosts.filter((h) => h !== serverHost));
  out.cloud_assets = lib.dedupe(out.cloud_assets);
  out.secrets = out.secrets.slice(0, 30);
  return out;
}

async function buildSeedPayload(tab, payload, options = {}) {
  const network = options.consumeNetwork === false
    ? await readNetwork(tab.id)
    : await takeNetwork(tab.id);
  const config = await getConfig();
  const sourceUrl = payload.page_url;
  const safePageUrl = lib.redactUrl(sourceUrl);
  const analysed = await analyseJsFiles(payload.harvested.js_files || [], sourceUrl);
  const rawHeaders = { ...(payload.harvested.response_headers || {}), ...network.headers };
  const responseHeaders = Object.fromEntries(
    Object.entries(rawHeaders).map(([name, value]) => {
      const safe = lib.interestingHeaders([{ name, value }])[name];
      return [name, safe || lib.redactText(String(value))];
    })
  );

  const harvested = {
    ...payload.harvested,
    urls: lib.dedupe([
      ...(payload.harvested.urls || []).map((url) => lib.redactUrl(url, sourceUrl)),
      ...analysed.urls.map((url) => lib.redactUrl(url, sourceUrl)),
    ]),
    endpoints: lib.dedupe([
      ...(payload.harvested.endpoints || []).map((url) => lib.redactUrl(url, sourceUrl)),
      ...analysed.endpoints.map((url) => lib.redactUrl(url, sourceUrl)),
    ]),
    js_files: lib.dedupe(
      (payload.harvested.js_files || []).map((url) => lib.redactUrl(url, sourceUrl)),
      MAX_JS_FILES
    ),
    forms: (payload.harvested.forms || []).map((form) => ({
      ...form,
      action: lib.redactUrl(form.action || sourceUrl, sourceUrl),
    })),
    comments: (payload.harvested.comments || []).map((text) => lib.redactText(text)),
    cloud_assets: lib.dedupe([
      ...(payload.harvested.cloud_assets || []),
      ...analysed.cloud_assets,
    ]),
    secrets: [...(payload.harvested.secrets || []), ...analysed.secrets].slice(0, 30),
    response_headers: responseHeaders,
    api_calls: network.api_calls.slice(-MAX_API_CALLS).map((call) => ({
      ...call,
      url: lib.redactUrl(call.url),
    })),
  };
  delete harvested.hidden_urls;
  delete harvested.hosts;

  return {
    target: options.target ? lib.redactUrl(options.target, sourceUrl) : safePageUrl,
    page_url: safePageUrl,
    page_title: payload.page_title,
    harvested,
    options: {
      preset: options.preset || config.preset,
      max_depth: options.max_depth || config.maxDepth,
      active: options.active ?? config.activeModules,
    },
    client: `extension/${chrome.runtime.getManifest().version}`,
  };
}

async function startScan(tab, payload, options = {}) {
  const seed = await buildSeedPayload(tab, payload, options);
  const created = await submitJob(seed);
  await write(ACTIVE_JOB_KEY, { jobId: created.job_id, startedAt: Date.now() });
  broadcast({ type: "rr/job-started", job: created });
  await setBadge(String(created.seeds));
  attachStream(created.job_id).catch(report);
  return created;
}

/* ---------------------------------------------------------------- telemetry */

function broadcast(message) {
  try {
    const pending = chrome.runtime.sendMessage(message);
    // No popup / DevTools panel open is normal; suppress the rejected promise.
    pending?.catch?.(() => {});
  } catch {
    /* extension context is shutting down */
  }
}

async function setBadge(text) {
  try {
    await chrome.action.setBadgeText({ text });
    await chrome.action.setBadgeBackgroundColor({ color: "#1f6feb" });
  } catch {
    /* action API unavailable in some contexts */
  }
}

async function attachStream(jobId) {
  stream?.close();
  const config = await getConfig();
  let count = 0;

  await chrome.alarms.create("rr-keepalive", { periodInMinutes: 1 });

  stream = await streamJob(config, jobId, {
    onMessage(message) {
      if (message.kind === "events") {
        count += message.events.length;
        setBadge(String(count));
      }
      broadcast({ type: "rr/telemetry", jobId, message });
      if (message.kind === "job.finished") {
        write(ACTIVE_JOB_KEY, null);
        chrome.alarms.clear("rr-keepalive");
        setBadge("");
      }
    },
    onClose(event) {
      stream = null;
      broadcast({ type: "rr/stream-closed", jobId, code: event?.code });
      chrome.alarms.clear("rr-keepalive");
      setBadge("");
    },
  });
}

/* ---------------------------------------------------------------- listeners */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tab = sender.tab;

  if (message?.type === "rr/harvest" && tab) {
    (async () => {
      const config = await getConfig();
      // The default mode never persists harvested DOM or secrets. Automatic
      // scans are explicitly opt-in and rate-limited once per URL per 30 min.
      if (!config.autoSubmit) return;
      const stampKey = `autoSubmit:${tab.id}`;
      const previous = await read(stampKey, null);
      if (
        previous?.url === message.payload.page_url &&
        Date.now() - previous.at < 30 * 60 * 1000
      ) return;
      await write(stampKey, { url: message.payload.page_url, at: Date.now() });
      try {
        await startScan(tab, message.payload);
      } catch (error) {
        await chrome.storage.local.remove(stampKey);
        report(error);
      }
    })().catch(report);
    sendResponse({ ok: true });
    return true;
  }

  if (message?.type === "rr/scan-now") {
    (async () => {
      const targetTab = message.tabId
        ? await chrome.tabs.get(message.tabId)
        : (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
      if (!targetTab?.id) throw new Error("no active tab");

      let payload = message.payload;
      if (!payload) {
        payload = await chrome.tabs.sendMessage(targetTab.id, { type: "rr/harvest-now" });
      }
      if (!payload) throw new Error("harvest returned nothing");
      sendResponse({ ok: true, job: await startScan(targetTab, payload, message.options || {}) });
    })().catch((error) => {
      report(error);
      sendResponse({ ok: false, error: String(error.message || error) });
    });
    return true; // async sendResponse
  }

  if (message?.type === "rr/preview") {
    (async () => {
      const targetTab = (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
      if (!targetTab?.id) throw new Error("no active tab");
      const payload = await chrome.tabs.sendMessage(targetTab.id, { type: "rr/harvest-now" });
      const seed = await buildSeedPayload(targetTab, payload, { consumeNetwork: false });
      sendResponse({ ok: true, seed, counts: summarise(seed) });
    })().catch((error) => {
      report(error);
      sendResponse({ ok: false, error: String(error.message || error) });
    });
    return true;
  }

  if (message?.type === "rr/health") {
    health()
      .then((body) => sendResponse({ ok: true, health: body }))
      .catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }

  if (message?.type === "rr/save-config") {
    saveConfig(message.patch)
      .then((config) => sendResponse({ ok: true, config }))
      .catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }

  if (message?.type === "rr/attach") {
    (async () => {
      const active = await read(ACTIVE_JOB_KEY, null);
      if (!active) return sendResponse({ ok: false, error: "no active job" });
      const job = await fetchJob(active.jobId);
      await attachStream(active.jobId);
      sendResponse({ ok: true, job: job.job });
    })().catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }

  return undefined;
});

function summarise(seed) {
  const harvested = seed.harvested || {};
  return {
    urls: (harvested.urls || []).length,
    endpoints: (harvested.endpoints || []).length,
    js_files: (harvested.js_files || []).length,
    emails: (harvested.emails || []).length,
    cloud_assets: (harvested.cloud_assets || []).length,
    api_calls: (harvested.api_calls || []).length,
    secrets: (harvested.secrets || []).length,
    comments: (harvested.comments || []).length,
  };
}

function report(error) {
  console.error("[ReconRelay]", error);
  broadcast({ type: "rr/error", error: String(error?.message || error) });
}

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const buffer = await readNetworkStore();
  if (buffer[String(tabId)]) {
    delete buffer[String(tabId)];
    await writeNetworkStore(buffer);
  }
});

chrome.runtime.onInstalled.addListener(async () => {
  const current = await getConfig();
  await write("config", current);
  await setBadge("");
});

// The alarm periodically revalidates the persisted job reference and can
// reattach a stream if MV3 has restarted the worker in between heartbeats.
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "rr-keepalive") return;
  const active = await read(ACTIVE_JOB_KEY, null);
  if (!active || Date.now() - active.startedAt > 60 * 60 * 1000) {
    await chrome.alarms.clear("rr-keepalive");
    await write(ACTIVE_JOB_KEY, null);
    return;
  }
  try {
    const response = await fetchJob(active.jobId);
    if (["queued", "running"].includes(response.job.status)) {
      if (!stream) await attachStream(active.jobId);
    } else {
      await chrome.alarms.clear("rr-keepalive");
      await write(ACTIVE_JOB_KEY, null);
    }
  } catch (error) {
    console.debug("active job reattach deferred", error.message);
  }
});

initWebRequest();
