// Agenda grouped by day. The list itself is rendered by Glance (custom-api widget, readable without JS);
// this script only adds "in 2 h 10" / "now" to today's timed events and greys out the ones that are over.
// Updated every 30 s and after each live.js refresh.
(function () {
  "use strict";
  function tick() {
    var t = Date.now() / 1000;
    document.querySelectorAll(".ag .ag-jour.auj .ag-ev[data-debut]").forEach(function (e) {
      var a = +e.dataset.debut, b = +e.dataset.fin || a + 3600; if (!a) return;
      var s = e.querySelector(".ag-s"); if (!s) { s = document.createElement("div"); s.className = "ag-s"; e.querySelector(".ag-tx").appendChild(s); }
      var d = s.querySelector(".ag-dans"); if (!d) { d = document.createElement("b"); d.className = "ag-dans"; s.appendChild(d); }
      var txt = "";
      if (t >= b) e.classList.add("passe");
      else if (t >= a) txt = "now";
      else {
        var m = Math.ceil((a - t) / 60);
        if (m <= 12 * 60) { var h = Math.floor(m / 60), r = m % 60; txt = "in " + (h ? h + " h" + (r ? " " + (r < 10 ? "0" : "") + r : "") : r + " min"); }
      }
      var before = s.childNodes.length > 1 || (s.firstChild && s.firstChild !== d);
      d.textContent = txt ? (before ? " · " : "") + txt : "";
    });
  }
  setInterval(tick, 30000);
  document.addEventListener("glance-maj", tick);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tick); else tick();
  new MutationObserver(function () { if (document.querySelector(".ag:not([data-ag])")) { document.querySelectorAll(".ag").forEach(function (x) { x.setAttribute("data-ag", ""); }); tick(); } })
    .observe(document.documentElement, { childList: true, subtree: true });
})();
