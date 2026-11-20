/*
 * Tests for the extraction logic that decides what the extension sends.
 * Run with: node --test extension/tests/
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const H = require("../src/harvest.js");

const BASE = "https://app.acme.test/dashboard";

test("harvest.js exports the full surface", () => {
  for (const name of [
    "fromHtml",
    "fromScript",
    "findSecrets",
    "interestingHeaders",
    "looksLikeApi",
    "sameSite",
    "absolutise",
    "dedupe",
  ]) {
    assert.equal(typeof H[name], "function", `${name} missing`);
  }
});

test("fromHtml pulls absolute and relative references apart", () => {
  const html = `
    <a href="/internal/users">users</a>
    <a href="https://cdn.acme.test/a.js">cdn</a>
    <a href="//other.acme.test/x">proto relative</a>
    <a href="#anchor">skip</a>
    <img src="/logo.png">
    <form action="/login" method="post"></form>
  `;
  const result = H.fromHtml(html, BASE);

  assert.deepEqual(result.endpoints, ["/internal/users", "/login"]);
  assert.ok(result.urls.includes("https://cdn.acme.test/a.js"));
  assert.ok(result.urls.includes("https://other.acme.test/x"));
  assert.ok(!result.urls.some((u) => u.includes("#anchor")), "fragment links must be skipped");
  assert.ok(
    !result.urls.some((u) => u.endsWith("/logo.png")),
    "static assets must not be reported as endpoints"
  );
});

test("fromHtml keeps HTML comments, where internal hostnames live", () => {
  const result = H.fromHtml(
    "<div><!-- staging: https://stage-internal.acme.test admin/letmein --><p>hi</p></div>",
    BASE
  );
  assert.equal(result.comments.length, 1);
  assert.match(result.comments[0], /stage-internal\.acme\.test/);
});

test("fromHtml extracts emails and lowercases them", () => {
  const result = H.fromHtml("<p>Ops@Acme.test and dev+ci@ACME.TEST</p>", BASE);
  assert.deepEqual(result.emails, ["ops@acme.test", "dev+ci@acme.test"]);
});

test("fromHtml finds cloud storage buckets", () => {
  const result = H.fromHtml(
    `
    <script>
      const s3 = "https://acme-backups.s3.eu-west-1.amazonaws.com/dump.tar";
      const gcs = "https://storage.googleapis.com/acme-public/x";
      const azure = "https://acmestatic.blob.core.windows.net/assets";
    </script>
    `,
    BASE
  );
  assert.ok(result.cloudAssets.some((c) => c.startsWith("acme-backups.s3")));
  assert.ok(result.cloudAssets.includes("storage.googleapis.com/acme-public"));
  assert.ok(result.cloudAssets.some((c) => c.startsWith("acmestatic.blob.core.windows.net")));
});

test("findSecrets reports a redacted fingerprint, never the secret", () => {
  const awsKey = "AKIA" + "IOSFODNN7EXAMPLE";
  const stripeKey = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc";
  const githubToken = "ghp_" + "012345678901234567890123456789abcdef";
  const html = `<script>const awsKey="${awsKey}"; const stripe="${stripeKey}"; const token="${githubToken}";</script>`;
  const secrets = H.findSecrets(html);
  const ids = secrets.map((s) => s.id);

  assert.ok(ids.includes("aws-access-key-id"), ids.join(","));
  assert.ok(ids.includes("stripe-key"));
  assert.ok(ids.includes("github-token"));
  for (const secret of secrets) {
    assert.match(secret.fingerprint, /^.{0,6}….{0,4}$/, "fingerprint should be truncated");
    assert.ok(!html.includes(secret.fingerprint), "fingerprint must not be the raw secret");
    assert.ok(!secret.context.includes(secret.fingerprint.split("…")[0]), "context must redact the secret");
    assert.equal(typeof secret.length, "number");
  }
});

test("findSecrets catches credentials embedded in a URL", () => {
  const secrets = H.findSecrets('fetch("https://user:s3cr3t@api.acme.test/v1")');
  assert.ok(secrets.some((s) => s.id === "basic-auth-in-url"));
});

test("fromScript extracts quoted API paths and skips asset paths", () => {
  const result = H.fromScript(
    `
    axios.defaults.baseURL = "/api/v2";
    fetch("/graphql");
    import("/app.bundle.js");
    const url = "https://telemetry.acme.test/collect";
    `,
    BASE
  );
  assert.ok(result.endpoints.includes("/api/v2"));
  assert.ok(result.endpoints.includes("/graphql"));
  assert.ok(!result.endpoints.some((e) => e.endsWith(".js")), "bundle paths are noise");
  assert.ok(result.urls.includes("https://telemetry.acme.test/collect"));
  assert.ok(result.jsFiles.some((f) => f.endsWith("app.bundle.js")));
});

test("interestingHeaders keeps only the fingerprinting-relevant ones", () => {
  const headers = [
    { name: "Server", value: "nginx/1.25.3" },
    { name: "X-Powered-By", value: "PHP/8.2.1" },
    { name: "Content-Length", value: "1234" },
    { name: "Date", value: "Thu, 01 Jan 2026 00:00:00 GMT" },
    { name: "WWW-Authenticate", value: 'Basic realm="acme"' },
  ];
  const kept = H.interestingHeaders(headers);
  assert.deepEqual(Object.keys(kept).sort(), ["server", "www-authenticate", "x-powered-by"]);
  assert.equal(kept.server, "nginx/1.25.3");
});

test("interestingHeaders merges names but redacts cookie values and auth query strings", () => {
  const kept = H.interestingHeaders([
    { name: "Set-Cookie", value: "session=top-secret; HttpOnly; Secure" },
    { name: "set-cookie", value: "csrf=also-secret; Path=/" },
    { name: "Location", value: "/login?code=oauth-secret&next=%2Fhome" },
  ]);
  assert.equal(kept["set-cookie"], "session=[redacted], csrf=[redacted]");
  assert.equal(kept.location, "/login?code=%5Bredacted%5D&next=%2Fhome");
  assert.ok(!JSON.stringify(kept).includes("top-secret"));
  assert.ok(!JSON.stringify(kept).includes("oauth-secret"));
});

test("redactUrl removes credentials and sensitive query values", () => {
  const safe = H.redactUrl("https://alice:p%40ss@api.acme.test/items?api_key=secret&limit=5");
  assert.equal(safe, "https://api.acme.test/items?api_key=%5Bredacted%5D&limit=5");
  assert.equal(H.redactUrl("/api?token=abc&take=2", BASE), "/api?token=%5Bredacted%5D&take=2");
});

test("HTML comments redact common secret patterns", () => {
  const result = H.fromHtml("<!-- api_key=supersecretvalue123456 -->", BASE);
  assert.match(result.comments[0], /redacted generic-api-key/);
  assert.ok(!result.comments[0].includes("supersecretvalue123456"));
});

test("looksLikeApi recognises API surface", () => {
  assert.equal(H.looksLikeApi("https://x.test/api/v1/users"), true);
  assert.equal(H.looksLikeApi("https://x.test/graphql"), true);
  assert.equal(H.looksLikeApi("https://x.test/about"), false);
});

test("sameSite matches a host and its subdomains in both directions", () => {
  assert.equal(H.sameSite("https://api.acme.test/x", "https://acme.test/"), true);
  assert.equal(H.sameSite("https://acme.test/x", "https://deep.api.acme.test/"), true);
  assert.equal(H.sameSite("https://notacme.test/x", "https://acme.test/"), false);
  assert.equal(H.sameSite("https://acme.test.evil.com/", "https://acme.test/"), false);
});

test("absolutise resolves protocol-relative and root-relative URLs", () => {
  assert.equal(H.absolutise("//cdn.acme.test/a", BASE), "https://cdn.acme.test/a");
  assert.equal(H.absolutise("/a/b", BASE), "https://app.acme.test/a/b");
  assert.equal(H.absolutise("https://x.test/y", BASE), "https://x.test/y");
  assert.equal(H.absolutise("http://[", BASE), null);
});

test("dedupe preserves order and enforces the cap", () => {
  assert.deepEqual(H.dedupe(["a", "b", "a", " c ", ""]), ["a", "b", "c"]);
  const capped = H.dedupe(Array.from({ length: 50 }, (_, i) => `x${i}`), 10);
  assert.equal(capped.length, 10);
});

test("dedupe drops null and undefined without throwing", () => {
  assert.deepEqual(H.dedupe([null, "a", undefined, "a"]), ["a"]);
});
