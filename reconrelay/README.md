# ReconRelay

A **BBOT-inspired, authorization-first recon prototype** with a Manifest V3 browser extension as the client and a Python gateway/worker you can run on an Oracle Cloud Always Free VM. The browser harvests what the operator's current page exposes; the server runs a recursive event loop and streams graph events back over WebSocket.

This is not a port of BBOT and is not a full vulnerability scanner. It is a small, auditable starting point that mirrors BBOT's event-loop/module shape and can be extended with additional modules.

## Safety model

- **The server allowlist is authoritative.** Set `RR_AUTHORIZED_SCOPES` to domains/CIDRs you own or are explicitly authorized to test. The default is empty and rejects every job. Do not set `RR_ALLOW_ANY_SCOPE=true` on a reachable server.
- Active TCP probes require both an `active` job and `RR_ALLOW_ACTIVE_MODULES=true`. Keep the global switch off unless there is a clear, authorized need.
- Extension defaults to manual scans; `autoSubmit` is off. Harvested DOM/script details are not retained in extension storage in manual mode. The response-metadata interceptor keeps a bounded per-tab buffer in `chrome.storage.session` (cleared when the browser session ends, the tab closes, or a scan consumes it); cookie values and sensitive URL parameters are redacted. Clicking **Preview payload** is local to the extension; clicking **Scan this page** sends it to your configured server.
- Script inspection is limited to same-site files and 12 scripts, each capped at 500 KB. The server does not persist full response bodies, but each HTTP event retains a byte count and up to a 400-character excerpt for triage. Suspected secrets are fingerprinted and the matched value is redacted from context.
- `chrome.webRequest` can read selected response headers and API/error URLs in MV3, but cannot block requests or inspect WebSocket frames. It does not capture request/response bodies.
- Use HTTPS. The browser WebSocket API cannot set an Authorization header, so the short-lived JWT appears in the WS query string. The supplied Nginx config disables access logging for the WS endpoint to avoid recording that token. mTLS is **not** implemented.

Only scan infrastructure you own or have written permission to assess. This project does not provide stealth, evasion, or authorization bypass.

## What maps to your six-tier diagram

| Tier | ReconRelay implementation | Difference from the diagram |
|---|---|---|
| 1. Browser extension | MV3 content harvester (links, hidden links, forms, scripts, inline JS, comments, email/cloud hints, redacted secret findings); response-header/API metadata interceptor; popup preview/live feed; DevTools force-graph panel. | Browser API limits apply: no blocking and no WS-frame inspection. |
| 2. Gateway | FastAPI auth, scope policy, job REST API and WebSocket telemetry; host Nginx TLS reverse proxy config. | API key → short-lived HS256 JWT, not mTLS. Nginx runs on the host, Compose only binds the API to loopback. |
| 3. Queue | Redis list for jobs plus Redis pub/sub and event lists; in-memory backend for local tests. | No Celery/Flower; the deliberately small `Worker` consumes the Redis queue directly. |
| 4. Recursive engine | BBOT-shaped event types and module protocol; event queue, dedupe, scope inheritance, depth/event/time ceilings. Modules: seed speculation, DNS resolve, HTTP probe/fingerprint, body extraction, certificate-transparency lookup, optional TCP port probes. | No external BBOT binary or wrappers for Shodan, Whois, Subfinder, httpx, Masscan, or Nuclei. The `passive` preset uses certificate-transparency lookups and DNS resolution (through the configured DNS resolver); `recon` adds HTTP; `active` adds a small fixed TCP-port set. |
| 5. Persistence | Redis stores job records and event history; events expire after seven days. | Neo4j and PostgreSQL are not implemented. The DevTools graph is reconstructed client-side from the event log. |
| 6. Egress / OPSEC | Normal host/container networking only. | WireGuard and proxy rotation are not implemented. If you need a VPN egress path, configure it outside the application and verify routing/firewall behavior yourself. |

For a modest personal deployment, an Oracle Always Free VM can run the API, Redis and one async worker; practical scan volume is constrained by VM resources, target response times, network limits, and Oracle account/region availability. Start with one worker and low event/depth limits. This repository does not validate a live Oracle account or guarantee free-tier capacity.

## Repository layout

```text
reconrelay/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── deploy/
│   ├── nginx.conf
│   └── setup-oracle.sh
├── extension/
│   ├── manifest.json
│   ├── icons/
│   ├── src/                 # MV3 worker, content script, popup, options, DevTools graph
│   └── tests/               # Node's built-in test runner, no npm dependencies
└── server/
    ├── app/                 # FastAPI, auth, policy, store, engine, modules, worker
    └── tests/               # pytest suite; deterministic fake networking
```

## Run tests locally

From the repository root:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider reconrelay/server
node --test reconrelay/extension/tests/*.test.js
```

Expected at authoring time: server suite **65 passed**; extension Node suite **21 passed**. Tests use fake network clients and do not send traffic to external targets.

## Run the server locally without Docker

Python 3.11+ is recommended.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r reconrelay/server/requirements.txt
cd reconrelay/server
RR_API_KEYS=local-development-key RR_JWT_SECRET='replace-this-local-secret' \
RR_AUTHORIZED_SCOPES=example.com,*.example.com RR_USE_MEMORY_STORE=true \
RR_INLINE_WORKER=true uvicorn app.main:app --host 0.0.0.0 --port 8000
```

For local development only, you can use `http://127.0.0.1:8000` as the extension server URL. Do not expose that setup to the internet: use a real secret, a strict scope allowlist, HTTPS, and Redis-backed deployment for a remote server. Health is at `/health`; interactive API docs are at `/docs`.

## Deploy to an Oracle VM with Docker Compose

1. Create an Ubuntu VM and DNS name. The setup script installs Docker/Compose, Nginx and Certbot, but does **not** alter Oracle Cloud Security Lists/NSGs or the host firewall. Allow inbound TCP 80/443 only; do not expose ports 6379 or 8000.
2. On the VM, copy this `reconrelay/` directory. Run `deploy/setup-oracle.sh` as a normal sudo-capable user, then reconnect so Docker group membership applies.
3. Create the real environment file and generate independent secrets:

   ```bash
   cd reconrelay
   cp .env.example .env
   openssl rand -hex 32  # use one output for RR_JWT_SECRET
   openssl rand -hex 32  # use a different output for RR_API_KEYS
   chmod 600 .env
   ```

   Edit `.env`: set your real allowlisted domains/CIDRs and secrets. Never use the examples or enable `RR_ALLOW_ANY_SCOPE` on an internet-facing machine. Leave active modules disabled initially.
4. Point DNS to the VM and open TCP 80/443 at both the Oracle cloud firewall and host firewall. Temporarily stop Nginx to issue the first certificate, then replace `recon.example.com` everywhere in `deploy/nginx.conf` with your DNS name:

   ```bash
   sudo systemctl stop nginx
   sudo certbot certonly --standalone -d YOUR_DOMAIN
   sudo systemctl start nginx
   sudo cp deploy/nginx.conf /etc/nginx/sites-available/reconrelay
   sudo ln -sfn /etc/nginx/sites-available/reconrelay /etc/nginx/sites-enabled/reconrelay
   sudo nginx -t && sudo systemctl reload nginx
   ```

5. Build and start the containers from `reconrelay/`:

   ```bash
   docker compose up -d --build
   docker compose ps
   curl -fsS https://YOUR_DOMAIN/health
   ```

6. In Chrome, open `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose `reconrelay/extension/`. Open the extension's **Settings**; set the HTTPS server URL and the exact `RR_API_KEYS` value, choose an operator name and save. Test connection, visit an allowlisted page, preview, then start a scan.

To inspect logs: `docker compose logs -f api worker redis`. Nginx access logs are disabled only for `/v1/ws/`; avoid logging authorization headers at any proxy. Back up Redis volume data if scan history matters. The app's JWT is 8 hours by default; API key rotation requires updating `.env` and restarting services.

## Endpoints

- `GET /health`
- `POST /v1/auth/token`
- `POST /v1/jobs`
- `GET /v1/jobs`, `GET /v1/jobs/{id}`, `GET /v1/jobs/{id}/events?after=&limit=`
- `POST /v1/jobs/{id}/cancel`
- `WS /v1/ws/jobs/{id}?token=...`

## Current limitations

- This is an initial recon foundation, not production-hardened BBOT parity. There is no distributed task retry/dead-letter queue, database backup automation, per-operator quota, TLS client authentication, or automated proxy/VPN policy.
- Jobs are authenticated but not isolated by operator on read endpoints; treat the gateway as a single-operator service and do not distribute API keys to untrusted users.
- The scope policy, event/depth/time budgets and low concurrency are the primary guardrails; review them before enabling active modules.
- Docker, Nginx and Oracle deployment were authored but **not executed or validated** in the build environment (Docker was unavailable). The extension has syntax and Node unit tests, but has not been loaded into a real Chrome profile here.
