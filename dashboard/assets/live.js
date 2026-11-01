// Refresh Glance widgets without reloading the page.
// Every 60 s (and as soon as the tab is visible again) the page content is fetched from Glance and the
// content of the widgets that changed is swapped in place. To avoid breaking anything:
//  - interactive widgets are left alone (tabs, carousels, "show more" lists, videos, input fields),
//  - an open <details> stays open after the update,
//  - nothing moves during the 4 s after a click (time for the command to leave),
//  - horizontal scroll positions of poster rows are kept.
// A banner says so when the page could not be refreshed for 2.5 min (VPN down, server unreachable, expired session).
(function () {
  var HOME_SLUG = "home";   // slug of the first page in glance.yml (used when the URL is "/")
  var lastClick = 0;
  document.addEventListener("click", function () { lastClick = Date.now(); }, true);

  function slug() {
    var s = location.pathname.replace(/^\/+|\/+$/g, "");
    return s || HOME_SLUG;
  }
  document.documentElement.setAttribute("data-page", slug());   // page-specific styles

  var lastOk = Date.now(), banner = null;
  function offline(ok) {
    if (ok) {
      lastOk = Date.now();
      if (banner && !banner.hidden && !banner.classList.contains("ok")) {   // back to normal
        banner.classList.add("ok"); banner.textContent = "Up to date again";
        setTimeout(function () { banner.hidden = true; banner.classList.remove("ok"); }, 2200);
      }
      return;
    }
    if (Date.now() - lastOk < 150000) return;
    if (!banner) {
      // a real button, large and away from the status bar; reloads without cache. Meanwhile the page retries every 10 s.
      banner = document.createElement("button"); banner.type = "button"; banner.id = "acc-horsligne";
      banner.addEventListener("click", function () {
        banner.textContent = "Reloading…"; banner.classList.add("charge");
        setTimeout(function () { location.replace(location.pathname + "?r=" + Date.now()); }, 60);
      });
      document.body.appendChild(banner);
    }
    var d = new Date(lastOk);
    if (!banner.classList.contains("charge")) banner.textContent = "Not updated since " + d.getHours() + ":" + ("0" + d.getMinutes()).slice(-2) + " · tap here to reload";
    banner.hidden = false;
    clearTimeout(offline.t); offline.t = setTimeout(refresh, 10000);
  }

  function untouchable(w) {
    return w.querySelector("iframe:not([style*=\"display:none\"]), video, input, textarea, .widget-group-header, .carousel-container, .collapsible-container, [data-collapse-after], .masonry")
      || w.classList.contains("widget-type-group");
  }

  var SCROLLERS = ".mi-rangee, .mi-puces";

  async function refresh() {
    if (document.visibilityState !== "visible" || Date.now() - lastClick < 4000) return;
    if (Date.now() - (window.haErreur || 0) < 15000) return;   // leave time to read "not sent" (webhooks.js)
    var rep;
    try {
      rep = await fetch("/api/pages/" + slug() + "/content/", { cache: "no-store", redirect: "manual" });
      if (!rep.ok) { offline(false); return; }
    } catch (e) { offline(false); return; }
    offline(true);
    var fresh = document.createElement("div");
    fresh.innerHTML = await rep.text();
    var olds = document.querySelectorAll("#page-content .widget");
    var news = fresh.querySelectorAll(".widget");
    if (olds.length !== news.length) return;           // structure changed: wait for a reload
    for (var i = 0; i < olds.length; i++) {
      var a = olds[i].querySelector(".widget-content"), n = news[i].querySelector(".widget-content");
      if (!a || !n || untouchable(olds[i]) || a.innerHTML === n.innerHTML) continue;
      var open = [];
      a.querySelectorAll("details").forEach(function (d, k) { if (d.open) open.push(k); });
      var scroll = [];
      a.querySelectorAll(SCROLLERS).forEach(function (c, k) { scroll[k] = c.scrollLeft; });
      a.innerHTML = n.innerHTML;
      a.querySelectorAll(SCROLLERS).forEach(function (c, k) { if (scroll[k]) c.scrollLeft = scroll[k]; });
      // lazy images inserted with innerHTML do not load any more (empty posters): load them now
      a.querySelectorAll("img[loading=lazy]").forEach(function (im) { im.loading = "eager"; var u = im.src; im.src = ""; im.src = u; });
      var dets = a.querySelectorAll("details");
      open.forEach(function (k) { if (dets[k]) dets[k].open = true; });
    }
    document.dispatchEvent(new CustomEvent("glance-maj"));
  }

  setInterval(refresh, 60000);
  // after tapping a home tile, show the new state quickly (5 s and 12 s) instead of waiting a minute
  document.addEventListener("click", function (ev) {
    if (ev.target.closest && ev.target.closest(".acc-hm-t, .acc-hm-scn")) { setTimeout(refresh, 5000); setTimeout(refresh, 12000); }
  }, true);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible") refresh(); });
  window.addEventListener("online", function () { setTimeout(refresh, 1500); });   // network back: catch up right away
})();
