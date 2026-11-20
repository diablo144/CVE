/*
 * Client for the ReconRelay gateway. Shared by the service worker, the popup
 * and the devtools panel.
 *
 * Auth model: the long-lived API key stays in chrome.storage.local and is used
 * once to mint a short-lived JWT. Only the JWT rides on requests, so a captured
 * request expires instead of granting permanent access.
 */

const DEFAULT_CONFIG = {
  serverUrl: "https://recon.example.com",
  apiKey: "",
  operator: "operator",
  preset: "recon",
  autoSubmit: false,
  activeModules: false,
  maxDepth: 3,
};

const TOKEN_MARGIN_MS = 60_000;

export async function getConfig() {
  const stored = await chrome.storage.local.get("config");
  return { ...DEFAULT_CONFIG, ...(stored.config || {}) };
}

export async function saveConfig(patch) {
  const current = await getConfig();
  const next = { ...current, ...patch };
  await chrome.storage.local.set({ config: next });
  await chrome.storage.local.remove("token");
  return next;
}

async function getToken(config, force = false) {
  if (!force) {
    const cached = await chrome.storage.local.get("token");
    const token = cached.token;
    if (token && token.expiresAt - Date.now() > TOKEN_MARGIN_MS) return token.value;
  }
  if (!config.apiKey) throw new Error("no API key configured");

  const response = await fetch(new URL("/v1/auth/token", config.serverUrl).href, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: config.apiKey, operator: config.operator }),
  });
  if (!response.ok) {
    throw new Error(`token request failed: HTTP ${response.status}`);
  }
  const body = await response.json();
  const value = {
    value: body.access_token,
    expiresAt: Date.now() + body.expires_in * 1000,
  };
  await chrome.storage.local.set({ token: value });
  return value.value;
}

export async function request(path, { method = "GET", body } = {}) {
  const config = await getConfig();
  const url = new URL(path, config.serverUrl).href;

  for (const attempt of [false, true]) {
    const token = await getToken(config, attempt);
    const response = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-Client-Version": chrome.runtime.getManifest().version,
      },
      body: body ? JSON.stringify(body) : undefined,
    });

    if (response.status === 401 && !attempt) continue; // refresh and retry once
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      const error = new Error(`HTTP ${response.status}: ${detail.slice(0, 300)}`);
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }
  throw new Error("authentication failed");
}

export async function health() {
  const config = await getConfig();
  const response = await fetch(new URL("/health", config.serverUrl).href);
  if (!response.ok) throw new Error(`health check failed: HTTP ${response.status}`);
  return response.json();
}

export async function submitJob(payload) {
  const response = await request("/v1/jobs", { method: "POST", body: payload });
  return response;
}

export async function fetchEvents(jobId, after = 0) {
  return request(`/v1/jobs/${jobId}/events?after=${after}&limit=500`);
}

export async function fetchJob(jobId) {
  return request(`/v1/jobs/${jobId}`);
}

/**
 * Live telemetry over WebSocket.
 *
 * MV3 service workers are killed after ~30s idle, so this is written to be
 * re-entered: the caller re-invokes it after a wake-up and the server replays
 * history before resuming the live tail.
 */
export async function streamJob(config, jobId, { onMessage, onClose } = {}) {
  const seen = new Set();
  let socket = null;
  let closed = false;
  let retries = 0;

  async function open() {
    if (closed) return;
    const token = await getToken(config, retries > 0);
    if (closed) return;
    const url = new URL(`/v1/ws/jobs/${jobId}?token=${encodeURIComponent(token)}`, config.serverUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(url.href);

    socket.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.kind === "events") {
        const fresh = (message.events || []).filter((item) => {
          if (seen.has(item.id)) return false;
          seen.add(item.id);
          return true;
        });
        if (fresh.length) onMessage?.({ ...message, events: fresh });
      } else if (onMessage) {
        onMessage(message);
      }
      if (message.kind === "job.finished") {
        closed = true;
        socket?.close(1000, "job finished");
      }
    };
    socket.onclose = (event) => {
      if (closed) return;
      // 4401 = bad token, 4404 = unknown job: neither is worth retrying.
      if (event.code === 4401 || event.code === 4404 || retries >= 5) {
        onClose?.(event);
        return;
      }
      retries += 1;
      setTimeout(open, Math.min(30_000, 1000 * 2 ** retries));
    };
    socket.onerror = () => socket?.close();
  }

  await open();

  return {
    close() {
      closed = true;
      socket?.close();
    },
  };
}
