// Shared defaults and helpers for the Paperless search pages.
// Settings come from config.js (window.PAPERLESS_SEARCH_CONFIG); anything missing falls back to DEFAULTS.
(function () {
  "use strict";
  var DEFAULTS = {
    backUrl: "",
    backLabel: "Back",
    locale: null,
    pdfjsBase: "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/",
    jspdfUrl: "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js",
    expiryField: "Expiry date",
    expiryWindowDays: 120,
    expiryMax: 6,
    viewGroups: [],
    viewIcons: {},
    searchPlaceholder: "Search documents…"
  };
  var user = window.PAPERLESS_SEARCH_CONFIG || {};
  var cfg = {};
  Object.keys(DEFAULTS).forEach(function (k) { cfg[k] = user[k] !== undefined && user[k] !== null ? user[k] : DEFAULTS[k]; });
  if (cfg.locale == null) cfg.locale = undefined;   // toLocaleDateString(null) throws; undefined = browser locale

  var loaded = {};
  function loadScript(url) {
    if (!loaded[url]) loaded[url] = new Promise(function (ok, ko) {
      var s = document.createElement("script");
      s.src = url; s.onload = ok; s.onerror = function () { delete loaded[url]; ko("could not load " + url); };
      document.head.appendChild(s);
    });
    return loaded[url];
  }

  // pdf.js is only fetched when a document is actually opened.
  function loadPdfjs() {
    var base = cfg.pdfjsBase.replace(/\/?$/, "/");
    return loadScript(base + "pdf.min.js").then(function () {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = base + "pdf.worker.min.js";
      return window.pdfjsLib;
    });
  }

  function cookie(name) {
    var m = document.cookie.match(new RegExp("(?:^|;\\s*)" + name + "=([^;]+)"));
    return m ? decodeURIComponent(m[1]) : "";
  }
  // Paperless sets the csrftoken cookie when its own UI is loaded; if it is missing, one visit of "/" drops it.
  function refreshCsrf() { return fetch("/", { credentials: "same-origin" }).catch(function () {}); }
  function ensureCsrf() { return cookie("csrftoken") ? Promise.resolve() : refreshCsrf(); }

  function formatDate(iso, short) {
    if (!iso) return "";
    var d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
    return d.toLocaleDateString(cfg.locale, short ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "long", year: "numeric" });
  }

  // Fills an <a data-back> element with the configured back link, or hides it.
  function setupBackLinks() {
    document.querySelectorAll("a[data-back]").forEach(function (a) {
      if (!cfg.backUrl) { a.hidden = true; return; }
      a.href = cfg.backUrl;
      var label = a.querySelector("[data-back-label]");
      if (label) label.textContent = cfg.backLabel;
    });
  }

  window.PS = { cfg: cfg, loadScript: loadScript, loadPdfjs: loadPdfjs, cookie: cookie, refreshCsrf: refreshCsrf,
                ensureCsrf: ensureCsrf, formatDate: formatDate, setupBackLinks: setupBackLinks };
})();
