/*
 * Content script: harvests the DOM of the page the operator is looking at.
 *
 * Deliberately DOM-only. Fetching external .js files is handed to the service
 * worker, because MV3 content-script fetches are subject to the page's CORS
 * policy while the service worker holds the extension's host permissions.
 */
(function () {
  "use strict";

  if (window.__reconrelayLoaded) return;
  window.__reconrelayLoaded = true;

  const H = self.RRHarvest;
  const MAX_JS_FILES = 12;
  const MAX_INLINE_CHARS = 250_000;

  function isHidden(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return true;
    if (el.hidden) return true;
    // offsetParent is null for display:none and for fixed-position elements;
    // the style check above already ruled the former out.
    return el.offsetParent === null && style.position !== "fixed";
  }

  function harvestDom() {
    const baseUrl = document.location.href;
    const urls = [];
    const hiddenUrls = [];
    const endpoints = [];
    const forms = [];
    const jsFiles = [];

    for (const anchor of document.querySelectorAll("a[href], area[href]")) {
      const href = anchor.getAttribute("href");
      if (!href || href.startsWith("#")) continue;
      const absolute = H.absolutise(href, baseUrl);
      if (!absolute) continue;
      if (isHidden(anchor)) hiddenUrls.push(absolute);
      else urls.push(absolute);
    }

    for (const form of document.querySelectorAll("form")) {
      const fields = Array.from(form.querySelectorAll("input, select, textarea")).map((field) => ({
        name: field.name || field.id || "",
        type: (field.type || "text").toLowerCase(),
        autocomplete: field.getAttribute("autocomplete") || "",
      }));
      forms.push({
        action: H.absolutise(form.getAttribute("action") || "", baseUrl) || baseUrl,
        method: (form.getAttribute("method") || "get").toLowerCase(),
        fields: fields.slice(0, 40),
      });
    }

    for (const script of document.querySelectorAll("script[src]")) {
      const src = H.absolutise(script.getAttribute("src") || "", baseUrl);
      if (src) jsFiles.push(src);
    }

    // Inline scripts: this is where config objects, API bases and keys hide.
    let inline = "";
    for (const script of document.querySelectorAll("script:not([src])")) {
      if (script.type && !/javascript|module|^$/i.test(script.type)) continue;
      inline += `\n${script.textContent || ""}`;
      if (inline.length > MAX_INLINE_CHARS) break;
    }

    const html = document.documentElement.outerHTML;
    const fromHtml = H.fromHtml(html, baseUrl);
    const fromInline = H.fromScript(inline, baseUrl);

    // Anything the browser loaded that the markup does not mention.
    if (window.performance && performance.getEntriesByType) {
      for (const entry of performance.getEntriesByType("resource")) {
        if (entry.initiatorType === "script" && /\.js(\?|$)/i.test(entry.name)) {
          jsFiles.push(entry.name);
        }
      }
    }

    return {
      page_url: baseUrl,
      page_title: document.title || "",
      harvested: {
        urls: H.dedupe([...urls, ...fromHtml.urls, ...fromInline.urls]),
        hidden_urls: H.dedupe(hiddenUrls, 200),
        endpoints: H.dedupe([...fromHtml.endpoints, ...fromInline.endpoints]),
        emails: H.dedupe(fromHtml.emails),
        comments: H.dedupe(fromHtml.comments, 40),
        js_files: H.dedupe(jsFiles, MAX_JS_FILES),
        forms: forms.slice(0, 25),
        cloud_assets: H.dedupe(fromHtml.cloudAssets.concat(fromInline.cloudAssets)),
        hosts: H.dedupe(fromHtml.hosts.concat(fromInline.hosts), 200),
        secrets: fromHtml.secrets.concat(fromInline.secrets).slice(0, 20),
        response_headers: {},
        api_calls: [],
      },
    };
  }

  let timer = null;
  function schedule() {
    if (timer) clearTimeout(timer);
    // Debounce: SPAs rewrite the DOM constantly, and we only want the settled
    // surface, not a payload per render.
    timer = setTimeout(() => {
      timer = null;
      try {
        chrome.runtime.sendMessage({ type: "rr/harvest", payload: harvestDom() });
      } catch {
        /* extension context invalidated (reload / update) */
      }
    }, 1200);
  }

  const observer = new MutationObserver(schedule);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["href", "src", "action"],
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message && message.type === "rr/harvest-now") {
      sendResponse(harvestDom());
      return true;
    }
    return undefined;
  });

  schedule();
})();
