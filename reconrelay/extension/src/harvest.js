/*
 * Pure extraction logic: no DOM, no chrome.* APIs.
 *
 * Loaded as a plain script by the content script (exposes window.RRHarvest) and
 * required directly by the Node test suite (module.exports), so the regexes that
 * decide what gets sent to the server are covered by tests.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RRHarvest = factory();
  }
})(typeof self !== "undefined" ? self : globalThis, function () {
  "use strict";

  const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
  const HTML_COMMENT_RE = /<!--([\s\S]{0,400}?)-->/g;

  const ATTR_REF_RE =
    /(?:href|src|srcset|action|content|data-url|data-src|data-endpoint|poster|formaction)\s*=\s*["']([^"'\s>]{2,400})["']/gi;
  const QUOTED_PATH_RE =
    /["'](\/(?:api|v\d+|graphql|rest|internal|admin|auth|oauth|webhook|service)[A-Za-z0-9_\-/{}:.?=&]{0,200})["']/g;

  const CLOUD_RE = [
    /([a-z0-9][a-z0-9.-]{1,60})\.s3[.-]([a-z0-9-]+)\.amazonaws\.com/gi,
    /([a-z0-9][a-z0-9.-]{1,60})\.s3\.amazonaws\.com/gi,
    /storage\.googleapis\.com\/([a-z0-9][a-z0-9._-]{1,60})/gi,
    /([a-z0-9][a-z0-9.-]{1,60})\.blob\.core\.windows\.net/gi,
    /([a-z0-9][a-z0-9.-]{1,60})\.(?:cdn\.)?digitaloceanspaces\.com/gi,
  ];

  // Findings only: known credential patterns are fingerprinted; raw matches
  // are never included in the result payload.
  const SECRET_RE = [
    { id: "aws-access-key-id", re: /\b(AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b/g },
    { id: "aws-secret-access-key", re: /aws_secret_access_key["'\s:=]{1,4}([A-Za-z0-9/+=]{40})/gi },
    { id: "google-api-key", re: /\bAIza[0-9A-Za-z\-_]{35}\b/g },
    { id: "stripe-key", re: /\b(sk|rk)_(live|test)_[0-9A-Za-z]{16,}\b/g },
    { id: "github-token", re: /\bgh[pousr]_[0-9A-Za-z]{36,}\b/g },
    { id: "slack-token", re: /\bxox[baprs]-[0-9A-Za-z-]{10,}\b/g },
    { id: "jwt", re: /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g },
    { id: "private-key-block", re: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g },
    { id: "generic-api-key", re: /\b(api[_-]?key|apikey|access[_-]?token|client[_-]?secret)["'\s:=]{1,4}["']?([A-Za-z0-9\-_.]{16,})/gi },
    { id: "basic-auth-in-url", re: /\b[a-z][a-z0-9+.-]*:\/\/[^/\s:@]+:[^/\s:@]+@[^\s"'<>]+/gi },
  ];

  const INTERESTING_HEADERS = new Set([
    "server", "x-powered-by", "x-aspnet-version", "x-aspnetmvc-version",
    "x-generator", "x-runtime", "x-drupal-cache", "x-redirect-by",
    "x-amz-request-id", "x-amz-id-2", "x-goog-storage-class",
    "x-served-by", "x-vercel-id", "x-azure-ref", "x-nc", "cf-ray",
    "www-authenticate", "set-cookie", "location", "access-control-allow-origin",
    "content-security-policy", "strict-transport-security", "x-frame-options",
    "x-content-type-options", "via", "public-key-pins",
  ]);

  const SKIP_SCHEMES = new Set(["data:", "blob:", "javascript:", "mailto:", "tel:", "about:"]);
  const SENSITIVE_PARAM_RE = /(?:token|key|secret|password|passwd|auth|session|credential|signature|^code$)/i;

  function dedupe(list, cap) {
    const seen = new Set();
    const out = [];
    for (const raw of list) {
      if (raw === null || raw === undefined) continue;
      const value = String(raw).trim();
      if (!value || seen.has(value)) continue;
      seen.add(value);
      out.push(value);
      if (out.length >= (cap || 500)) break;
    }
    return out;
  }

  function isSkippable(value) {
    const lower = value.toLowerCase();
    for (const scheme of SKIP_SCHEMES) if (lower.startsWith(scheme)) return true;
    return /\.(png|jpe?g|gif|svg|webp|ico|css|woff2?|ttf|eot|mp4|webm|avif)(\?|$)/i.test(lower);
  }

  function redactUrl(value, baseUrl) {
    try {
      const parsed = baseUrl ? new URL(value, baseUrl) : new URL(value);
      parsed.username = "";
      parsed.password = "";
      for (const key of Array.from(parsed.searchParams.keys())) {
        if (SENSITIVE_PARAM_RE.test(key)) parsed.searchParams.set(key, "[redacted]");
      }
      if (SENSITIVE_PARAM_RE.test(parsed.hash)) parsed.hash = "#[redacted]";
      if (baseUrl && !/^[a-z][a-z0-9+.-]*:/i.test(value) && !value.startsWith("//")) {
        return `${parsed.pathname}${parsed.search}${parsed.hash}`;
      }
      return parsed.href;
    } catch {
      return value;
    }
  }

  function redactText(text) {
    let safe = String(text);
    for (const { id, re } of SECRET_RE) {
      re.lastIndex = 0;
      safe = safe.replace(re, () => `[redacted ${id}]`);
    }
    return safe;
  }

  function absolutise(value, baseUrl) {
    try {
      if (value.startsWith("//")) {
        const scheme = (baseUrl.match(/^([a-z][a-z0-9+.-]*):/i) || [, "https"])[1];
        return new URL(`${scheme}:${value}`).href;
      }
      return new URL(value, baseUrl).href;
    } catch {
      return null;
    }
  }

  function extractHostnames(text) {
    const hosts = [];
    const re = /(?:https?:)?\/\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)/g;
    let match;
    while ((match = re.exec(text)) !== null) hosts.push(match[1].toLowerCase());
    return dedupe(hosts, 300);
  }

  function fromHtml(html, baseUrl) {
    const urls = [];
    const endpoints = [];
    const comments = [];
    let match;

    ATTR_REF_RE.lastIndex = 0;
    while ((match = ATTR_REF_RE.exec(html)) !== null) {
      const value = match[1];
      if (value.startsWith("#") || isSkippable(value)) continue;
      if (value.startsWith("/") && !value.startsWith("//")) endpoints.push(value);
      else {
        const absolute = absolutise(value, baseUrl);
        if (absolute) urls.push(absolute);
      }
    }

    QUOTED_PATH_RE.lastIndex = 0;
    while ((match = QUOTED_PATH_RE.exec(html)) !== null) endpoints.push(match[1]);

    HTML_COMMENT_RE.lastIndex = 0;
    while ((match = HTML_COMMENT_RE.exec(html)) !== null) {
      const body = match[1].trim();
      if (body && body.length <= 400) comments.push(redactText(body));
    }

    return {
      urls: dedupe(urls),
      endpoints: dedupe(endpoints),
      emails: dedupe((html.match(EMAIL_RE) || []).map((e) => e.toLowerCase())),
      comments: dedupe(comments, 60),
      hosts: extractHostnames(html),
      cloudAssets: fromCloudPatterns(html),
      secrets: findSecrets(html),
    };
  }

  function fromScript(source, baseUrl) {
    const endpoints = [];
    const urls = [];
    let match;

    QUOTED_PATH_RE.lastIndex = 0;
    while ((match = QUOTED_PATH_RE.exec(source)) !== null) endpoints.push(match[1]);

    const quoted = /["'](\/[A-Za-z0-9][A-Za-z0-9_\-/.{}]{2,120})["']/g;
    while ((match = quoted.exec(source)) !== null) {
      if (/\.(js|css|png|svg|json|map)$/i.test(match[1])) continue;
      endpoints.push(match[1]);
    }

    const absolute = /["'](https?:\/\/[A-Za-z0-9._~:/?#[\]@!$&'()*+,;=%-]{4,300})["']/g;
    while ((match = absolute.exec(source)) !== null) {
      if (!isSkippable(match[1])) urls.push(match[1]);
    }

    return {
      endpoints: dedupe(endpoints),
      urls: dedupe(urls),
      hosts: extractHostnames(source),
      cloudAssets: fromCloudPatterns(source),
      secrets: findSecrets(source),
      jsFiles: dedupe(
        Array.from(source.matchAll(/["']([^"']+\.js)(?:\?[^"']*)?["']/g), (m) =>
          absolutise(m[1], baseUrl)
        ).filter(Boolean)
      ),
    };
  }

  function fromCloudPatterns(text) {
    const found = [];
    for (const re of CLOUD_RE) {
      re.lastIndex = 0;
      let match;
      while ((match = re.exec(text)) !== null) found.push(match[0].toLowerCase());
    }
    return dedupe(found, 100);
  }

  function findSecrets(text) {
    const found = [];
    for (const { id, re } of SECRET_RE) {
      re.lastIndex = 0;
      let match;
      let count = 0;
      while ((match = re.exec(text)) !== null && count < 5) {
        const value = match[0];
        const before = text.slice(Math.max(0, match.index - 40), match.index);
        const after = text.slice(match.index + value.length, match.index + value.length + 20);
        found.push({
          id,
          fingerprint: `${value.slice(0, 6)}…${value.slice(-4)}`,
          length: value.length,
          context: `${before}[redacted ${id}]${after}`,
        });
        count += 1;
        if (match.index === re.lastIndex) re.lastIndex += 1;
      }
    }
    return found;
  }

  function safeHeader(name, value) {
    if (name === "set-cookie") {
      const cookieName = String(value).split("=", 1)[0].trim().slice(0, 80);
      return `${cookieName || "cookie"}=[redacted]`;
    }
    if (name === "location") return redactUrl(String(value), "https://redact.invalid/");
    return redactText(String(value)).slice(0, 300);
  }

  function interestingHeaders(headers) {
    const out = {};
    for (const header of headers || []) {
      const name = (header.name || "").toLowerCase();
      if (!INTERESTING_HEADERS.has(name)) continue;
      const value = safeHeader(name, header.value || "");
      out[name] = out[name] ? `${out[name]}, ${value}` : value;
    }
    return out;
  }

  function looksLikeApi(url) {
    return /\/(api|v\d+|graphql|rest|internal|webhook|oauth)\b/i.test(url);
  }

  function hostOf(url) {
    try {
      return new URL(url).hostname.toLowerCase();
    } catch {
      return "";
    }
  }

  function sameSite(a, b) {
    const ha = hostOf(a);
    const hb = hostOf(b);
    if (!ha || !hb) return false;
    return ha === hb || ha.endsWith(`.${hb}`) || hb.endsWith(`.${ha}`);
  }

  return {
    fromHtml,
    fromScript,
    findSecrets,
    extractHostnames,
    fromCloudPatterns,
    interestingHeaders,
    looksLikeApi,
    hostOf,
    sameSite,
    absolutise,
    redactUrl,
    redactText,
    dedupe,
    INTERESTING_HEADERS,
  };
});
