// Home page icons: replaces the emojis inside .acc-* blocks with Lucide line icons (assets/icons.svg, ISC licence),
// same colour and size as the text. Emojis come from the Glance templates as well as from the scripts' JSON; they are
// converted here on insertion (MutationObserver, before display), without touching the scripts.
(function () {
  var M = {"💡": "lightbulb", "🔊": "volume-2", "🔍": "search", "🛒": "shopping-cart", "💧": "droplet", "🧊": "snowflake", "📄": "file-text", "🔄": "refresh-cw", "🏋": "dumbbell", "🌙": "moon", "😴": "bed", "📺": "tv", "🌬": "wind", "🤖": "bot", "🫧": "washing-machine", "🌀": "fan", "🍽": "utensils", "📅": "calendar", "🔭": "telescope", "⏭": "skip-forward", "＋": "plus", "📷": "camera", "🌧": "cloud-rain", "🧺": "shirt", "⚠": "triangle-alert", "☄": "sparkles", "⏳": "hourglass", "🛰": "satellite", "🚀": "rocket", "🔋": "battery-low", "📟": "monitor-smartphone", "💳": "credit-card", "🌿": "leaf", "🌕": "moon-star", "🎮": "gamepad-2", "💻": "laptop", "📦": "package", "🗑": "trash-2", "🛋": "sofa", "🛏": "bed", "🚪": "door-open", "🎁": "gift", "💶": "banknote", "🖥": "server", "🧳": "luggage", "🏊": "waves", "📖": "book-open", "🔇": "volume-x", "☀": "sun", "🌤": "cloud-sun", "⛅": "cloud-sun", "☁": "cloud", "🌦": "cloud-sun-rain", "🌨": "cloud-snow", "⛈": "cloud-lightning", "🌡": "thermometer"};
  var re = new RegExp("(" + Object.keys(M).map(function (k) { return k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("|") + ")\uFE0F?", "g");
  function svg(n) {
    var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", "ic"); s.setAttribute("aria-hidden", "true");
    var u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", "/assets/icons.svg#" + n);
    s.appendChild(u); return s;
  }
  function traiter(racine) {
    var w = document.createTreeWalker(racine, NodeFilter.SHOW_TEXT, null), t, liste = [];
    while ((t = w.nextNode())) {
      var p = t.parentElement;
      if (!p || !p.closest('[class*="acc-"]') || p.closest("textarea, script, style")) continue;
      re.lastIndex = 0;
      if (re.test(t.nodeValue)) liste.push(t);
    }
    liste.forEach(function (t) {
      var f = document.createDocumentFragment(), txt = t.nodeValue, i = 0, m;
      re.lastIndex = 0;
      while ((m = re.exec(txt))) {
        if (m.index > i) f.appendChild(document.createTextNode(txt.slice(i, m.index)));
        f.appendChild(svg(M[m[1]])); i = m.index + m[0].length;
      }
      if (i < txt.length) f.appendChild(document.createTextNode(txt.slice(i)));
      t.parentNode.replaceChild(f, t);
    });
  }
  new MutationObserver(function (ms) {
    ms.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) traiter(n); }); });
  }).observe(document.documentElement, {childList: true, subtree: true});
  if (document.body) traiter(document.body);
})();
