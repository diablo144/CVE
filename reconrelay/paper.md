# ReconRelay: A Browser-Extension Client for an Authorization-First Reconnaissance Service

**Author:** [Your Name]

**Affiliation:** [School / Department]

**Date:** 20 November 2026

## Abstract

Reconnaissance tools collect information about web applications and connect findings into an attack-surface view, but their power makes scope control and responsible operation essential. This paper presents ReconRelay, a prototype that uses a browser extension as the client and a small self-hosted service as the execution layer. The extension gathers selected page and response metadata from a page the operator is viewing, then submits a seed payload to a FastAPI gateway. A recursive, event-driven Python engine expands approved seeds, stores jobs and events in Redis, and streams results to a popup and a DevTools graph panel. Server-side scope checks, event/depth/time budgets, and a separate opt-in gate for active probes are central controls. Automated tests cover policy, engine, API, worker, extraction, and graph behavior. The server suite passes 65 tests and the extension suite passes 21 tests. These results establish behavior under test fixtures; they do not establish real-world scanning accuracy, production readiness, or successful Oracle Cloud deployment. The paper describes the project problem, design, implementation requirements, tests, limitations, and future work.

**Keywords:** attack-surface mapping, browser extension, reconnaissance, scope control, WebSocket telemetry

## 1. Introduction

Security teams often begin an assessment with a domain, an application, or a page that they are authorized to examine. Established guidance treats testing authorization and planning as core parts of a security assessment, not optional administration [6]. Useful clues may already be present in that page: links, API paths, script references, form actions, hostnames, and technology indicators. A browser-based client can make this information easier to seed into a reconnaissance workflow because it observes a real page in the context where it is used. However, collecting browser data alone does not provide a recursive scan, a shared event history, or a live graph of related assets.

My project explores a split design: a browser extension performs bounded, page-local harvesting, while a server performs the recursive work and enforces the scan policy. The idea follows the six-tier architecture in my original design brief: browser client, gateway, queue, execution engine, persistence, and egress control [1]. I implemented a smaller prototype named ReconRelay to test the most important end-to-end path: harvest a seed, submit a job, expand in-scope events, and receive telemetry in the browser.

Prior work includes modular reconnaissance systems that pass structured findings between collection modules [2]. I use that work only as design context; ReconRelay is independently implemented in Python and JavaScript and does not embed, port, or redistribute the cited software. The project focuses on applying a bounded event flow to browser-derived seeds and a small self-hosted gateway.

This paper makes three contributions. First, it documents a browser-to-server design for interactive reconnaissance. Second, it explains how scope and resource controls are applied at the server rather than trusted to the client. Third, it reports a limited software-test evaluation and identifies what remains unverified before any real deployment.

## 2. Problem Definition, Objectives, and Safety Requirements

The project addresses a practical gap: a browser user can see useful application context, but collecting those clues does not by itself produce an organized recursive assessment or a live view of related assets. The project objective is to connect page-local discovery to a self-hosted analysis service while ensuring that scan authorization, recursion limits, and job state remain under server control.

The first goal is to make the browser a seed collector and control surface, not the authority on what the server may scan. A modified client can submit arbitrary JSON, so a server must independently check the requested target and every candidate seed. ReconRelay therefore uses an explicit authorized-scope list. When the list is empty, scans are refused. The configuration can represent domains, subdomain wildcards, or CIDR ranges; out-of-scope discovered assets are filtered before they enter the engine.

The second goal is to bound expansion. Recursive discovery can grow quickly when modules emit multiple events. The engine deduplicates by event type and value and enforces maximum depth, maximum events, and a scan deadline. A hard event ceiling is checked for each produced child rather than once per module batch. Active TCP probes require both a per-job request and a global server setting. This creates two independent gates and leaves active work disabled by default.

The third goal is to limit sensitive data collection. The extension does not submit page data automatically by default. It can redact common credential-like values in comments, URLs, selected headers, cookies, and findings. Cookie values are discarded, sensitive query parameters are replaced, and suspected secret values are represented by a fingerprint and context with the match redacted. Server-side event persistence trims response bodies to a short excerpt and byte count. These measures reduce exposure, but they are not a guarantee that every secret format is recognized or that every sensitive field has been removed. Operators should treat harvested material as sensitive.

Finally, the deployment should fit a small personal server. Redis is used as a shared job queue, event store, and publish/subscribe channel. The implementation uses a small Python worker instead of Celery. This reduces moving parts but also means that retry, scheduling, and failure-management features common in larger worker systems are not present.

## 3. Existing Systems and Project Gap

Many security assessment workflows combine separate command-line scanners, data sources, and result viewers. This can be effective for experienced operators, but it may require several tools and does not automatically connect the page currently being inspected to the scan’s seed and live result view.

### Related Work

BBOT is a public modular reconnaissance scanner that passes structured events between modules [2]. I cite it as background for the event-driven design; ReconRelay is a separate implementation and does not embed or port it. The project gap addressed here is the browser-context seed workflow, server-enforced scope policy, and live browser view. This paper does not claim a measured advantage over existing scanners.

## 4. Proposed System and Methodology

### 4.1 Browser extension

The extension follows Chrome’s Manifest V3 model, with a module service worker, a content script, a popup, an options page, and a DevTools page [3]. The content script reads the current document and extracts links, hidden links, forms and field metadata, script URLs, inline script hints, resource entries, email-like strings, cloud-storage patterns, and possible secrets. It sends a seed payload to the background service worker. Script-file analysis is performed by the service worker, limited to same-site URLs and a fixed number and size of scripts.

A `webRequest` listener records a bounded set of response headers and API-like or unsuccessful response URLs, within the limits of Chrome’s extension API [4]. This is metadata collection only. It does not intercept request bodies, block traffic, or inspect WebSocket frames. The popup provides preset and depth controls, a preview of harvested counts, scan submission, and a live event list. The DevTools panel draws a dependency-free force-directed graph from events and their parent identifiers. No graph database is needed for this prototype because the panel reconstructs its view from the event stream.

Manual submission is the normal mode. An optional auto-submit preference is disabled by default and rate-limited per URL and tab. Network metadata is held in session storage with a bounded per-tab buffer, not used as a durable scan database. This balances convenience against the risk of storing information from unrelated pages.

### 4.2 Gateway, authentication, and telemetry

The FastAPI gateway exposes health, token issuance, job submission and lookup, event history, cancellation, and a WebSocket route for live telemetry. The extension exchanges a configured API key for a short-lived HS256 JWT and uses the token for HTTP requests. Since browser WebSocket APIs do not provide a general mechanism for setting an authorization header, the token is passed in the WebSocket query string. The supplied Nginx configuration disables access logging for the WebSocket path to reduce the chance that this token is written to routine proxy logs.

The prototype does not implement mutual TLS. Production deployment should use HTTPS, a strong random JWT secret, a separate high-entropy API key, and a narrowly scoped allowlist. The provided Compose file binds the API to loopback so the host reverse proxy can be the public entry point. Redis is not published to the host network.

### 4.3 Queue, event engine, and persistence

When a job is accepted, the gateway filters the submitted seeds and enqueues a job record. A worker removes the job and runs a recursive event loop. Seed events are placed in the queue; modules watch specified event types and produce new events. The prototype includes seed speculation, certificate-transparency lookups, DNS resolution, HTTP probing and fingerprinting, response extraction, and an optional limited TCP port probe. Each event records its type, value, parent, depth, module, and selected metadata.

Redis stores job records, queued job identifiers, event history, and pub/sub messages using its list and publish/subscribe data structures [5]. The WebSocket handler opens a subscription before reading event history, then sends the backfill and live messages. This ordering is intended to avoid a gap between history and the live tail. The extension can reconnect and deduplicate events by identifier. Persisted response events remove the full body but keep a short excerpt and byte count for triage.

The prototype does not include Neo4j or PostgreSQL. Redis is the only persistent backend in the deployment design, and event history expires after seven days. This is a conscious reduction from the original diagram, not a claim that a graph or relational database is unnecessary for larger systems. Similarly, the prototype does not include WireGuard or a proxy rotator. Egress routing is an operational concern outside this code and should not be described as implemented.

## 5. Implementation Requirements and Deployment

The prototype’s server dependencies are Python 3.11 or later, FastAPI, Redis, and an ASGI server. The supplied Dockerfile and Compose configuration target Linux on either ARM64 or AMD64, with Nginx acting as the public TLS reverse proxy. The extension is a Manifest V3 unpacked extension for a compatible Chromium browser and uses the browser’s storage, tabs, alarms, webRequest, and DevTools APIs. Node.js is needed only to run the extension’s test suite; it is not a runtime dependency of the extension.

For deployment, the operator must provide an HTTPS hostname, a strong JWT secret, an independent API key, and an explicit list of authorized domains or address ranges. Redis and the API should remain private behind the reverse proxy. The deployment files include conservative resource limits and a single worker as a starting configuration, but they do not establish a guaranteed Oracle free-tier capacity. Docker and a live Oracle instance were unavailable during this evaluation, so deployment sizing and operation remain to be validated.

## 6. Results and Evaluation

I evaluated the implementation with automated tests rather than live targets. The Python suite contains 65 tests across policy, engine, API, and worker behavior. The tests exercise authorization decisions, seed filtering, recursion and deduplication, depth and event limits, worker lifecycle, job state, and API responses using fake network clients. The Node suite contains 21 tests covering extraction, redaction, URL handling, header filtering, and graph layout behavior. JavaScript syntax, manifest references, icon files, Compose YAML, and the deployment shell script were also checked.

The tests and implementation described here are available in the project source and test suite [7]. The tests provide evidence that the implemented components work together under controlled fixtures. In particular, they check that an empty or restrictive policy does not silently authorize arbitrary targets, that derived events inherit scope only from accepted parents, that the event ceiling cannot be exceeded by one large module batch, and that the worker persists state transitions. The extension tests verify that cookie values and selected credential patterns are redacted and that graph coordinates remain finite for overlapping nodes.

These results have clear limits. The tests do not measure precision or recall of asset discovery, scan speed, network load, false-positive rates, or performance on Oracle’s free-tier hardware. The extension has not been loaded into a real Chrome profile in this environment. Docker was unavailable, so the images and Compose deployment were not built or run. No external target was scanned. Therefore, the evaluation supports only a prototype-level claim: the code paths and safety controls represented by the tests behave as expected in a local test environment.

## 7. Discussion, Limitations, and Future Work

The browser/server split provides a useful separation of responsibilities. The extension has access to the page the operator is viewing, while the server owns scan authorization, budgets, job state, and recursive execution. This makes it possible to improve the interface without weakening the server policy. It also creates operational risks: an API key stored in browser extension storage can be exposed if the user’s browser profile is compromised, and a stolen key could be used to create jobs within the configured scope. Key rotation and strict server configuration are therefore necessary.

The prototype deliberately implements a small module set and does not include integrations such as vulnerability-template scanners, large-scale port scanners, external data providers, distributed task retries, or multi-tenant access isolation. Job-read routes are authenticated but are not isolated by operator, making the current design appropriate only for a trusted single-operator deployment. Rate limiting is also not yet a comprehensive gateway feature.

The network-interception model has browser-specific limitations. It receives selected response metadata but cannot provide arbitrary traffic visibility. Consent and data minimization remain important because browser pages may contain personal, account, or session information. Redaction is best effort and must not be treated as a complete secret-detection system. A future version should add explicit consent boundaries, configurable data retention, audit logging that excludes tokens, stronger key management, and adversarial tests for malformed payloads and redaction bypasses.

A further evaluation should use a laboratory environment with domains and applications owned by the researcher. It should measure discovery precision, request counts, memory use, event growth, and completion time across controlled page sizes and scope shapes. Deployment tests should verify TLS, WebSocket proxy behavior, container restart recovery, Redis persistence, and firewall rules on the intended Oracle instance. Such work would test practical capacity rather than infer it from a local unit-test run.

## 8. Conclusion

ReconRelay demonstrates a feasible prototype for using a browser extension as the client of a self-hosted, recursive reconnaissance service. Its central design decision is to keep authorization and scan limits on the server, while the extension provides page-local harvesting and interactive telemetry. The implementation includes a FastAPI gateway, a Redis-backed queue and event store, a small event-driven engine, and popup and DevTools views.

The current results are intentionally modest: automated tests pass, but the extension and containers have not been validated in a real browser or deployed on Oracle Cloud. The system omits several tiers and tools from the original architecture, including Celery, dedicated graph/relational databases, external vulnerability scanners, and VPN/proxy egress. My next step would be to validate the unpacked extension and Docker deployment in a controlled lab, then extend the evaluation before describing the system as production-ready. In this form, ReconRelay is best understood as a cautious starting point for further authorized research and engineering.

## References

[1] [Your Name], “Six-Tier Browser Extension and Server Architecture for Reconnaissance,” unpublished project design brief, 2026.

[2] Black Lantern Security, “BBOT: The recursive internet scanner for hackers,” GitHub repository. [Online]. Available: https://github.com/blacklanternsecurity/bbot. Accessed: Nov. 20, 2026.

[3] Google Chrome for Developers, “Manifest V3” and “Extension service worker lifecycle.” [Online]. Available: https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3/ and https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle/. Accessed: Nov. 20, 2026.

[4] Google Chrome for Developers, “chrome.webRequest API.” [Online]. Available: https://developer.chrome.com/docs/extensions/reference/api/webRequest. Accessed: Nov. 20, 2026.

[5] Redis Ltd., “Redis Pub/Sub” and “Redis Lists.” [Online]. Available: https://redis.io/docs/latest/develop/interact/pubsub/ and https://redis.io/docs/latest/develop/data-types/lists/. Accessed: Nov. 20, 2026.

[6] K. Scarfone, M. Souppaya, A. Cody, and A. Orebaugh, *Technical Guide to Information Security Testing and Assessment*, NIST Special Publication 800-115, Gaithersburg, MD, USA, Sep. 2008, doi: 10.6028/NIST.SP.800-115.

[7] [Your Name], “ReconRelay prototype,” source code, tests, and deployment notes, unpublished software project, 2026. [Online]. Available: project files `reconrelay/server/`, `reconrelay/extension/`, and `reconrelay/README.md`.
