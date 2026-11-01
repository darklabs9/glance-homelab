// Reliable buttons for Home Assistant webhooks.
// Glance templates post plain HTML forms to Home Assistant webhooks (action="http://<ha>/api/webhook/<id>").
// A hidden-iframe post cannot tell whether the order arrived (VPN down, HA restarting...), so this script
// intercepts every such form and sends it with fetch() through a same-origin reverse-proxy route
// (/ha-webhook/<id>, see the Caddy snippet in the README), which makes the answer readable:
// "sending…" while it goes, "✓" only when HA answered, otherwise "not sent · retry".
// Nothing changes in glance.yml: forms keep their normal action and still work without this script.
(function () {
  var HA = /^https?:\/\/[^\/]+\/api\/webhook\//;
  var PROXY = "/ha-webhook/";
  var native = HTMLFormElement.prototype.submit;
  var lastButton = null;

  var st = document.createElement("style");
  st.textContent =
    ".ha-envoi{opacity:.6!important;cursor:progress}" +
    ".ha-err{box-shadow:inset 0 0 0 2px hsl(32,85%,58%)!important;opacity:1!important}" +
    ".acc-hm-t.ha-err .acc-hm-st{color:hsl(32,85%,64%)!important}" +
    ".acc-hm-t.on.ha-err .acc-hm-st{color:hsl(24,85%,36%)!important}" +
    "button.ha-err:not(.acc-hm-t):not(.acc-hm-scn){background:hsl(28,60%,32%)!important;color:#fff!important}" +
    ".acc-hm-scn.ha-err::after{content:'not sent';display:block;font-size:11px;font-weight:600;color:hsl(32,85%,64%)}" +
    ".ha-msg{color:hsl(32,85%,64%);font-size:13px;margin:6px 0 0}" +
    ".ha-arme{box-shadow:inset 0 0 0 2px hsl(32,85%,58%)!important;background:hsla(32,85%,50%,.14)!important}" +
    ".ha-arme .acc-hm-st,.ha-arme .ha-conf{color:hsl(32,90%,66%)!important}" +
    ".ha-conf{display:block;font-size:12px;font-weight:500}";
  document.head.appendChild(st);

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button");
    if (b) lastButton = b;
  }, true);

  // Buttons with data-confirm="..." (robot, anything noisy) need a second tap within 4 s
  function disarm(b) {
    b._armed = 0; b.classList.remove("ha-arme");
    var s = b.querySelector(".acc-hm-st"), c = b.querySelector(".ha-conf");
    if (s && b.dataset.st !== undefined) s.textContent = b.dataset.st;
    if (c) c.remove();
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button[data-confirm]");
    if (!b || b.disabled) return;
    if (b._armed && Date.now() - b._armed < 4000) { disarm(b); return; }   // second tap: let it go
    e.preventDefault(); e.stopImmediatePropagation();
    b._armed = Date.now(); b.classList.add("ha-arme");
    var s = b.querySelector(".acc-hm-st");
    if (s) { b.dataset.st = s.textContent; s.textContent = b.dataset.confirm; }
    else { var c = document.createElement("span"); c.className = "ha-conf"; c.textContent = b.dataset.confirm; (b.lastElementChild && b.lastElementChild.classList.contains("acc-hm-tx") ? b.lastElementChild : b).appendChild(c); }
    setTimeout(function () { if (b._armed && Date.now() - b._armed >= 3900) disarm(b); }, 4000);
  }, true);

  function proxied(f) {
    var a = f.getAttribute("action") || "";
    return HA.test(a) ? PROXY + a.replace(HA, "") : null;
  }

  function message(f, txt) {
    var m = f.nextElementSibling && f.nextElementSibling.classList.contains("ha-msg") ? f.nextElementSibling : null;
    if (!txt) { if (m) m.remove(); return; }
    if (!m) { m = document.createElement("p"); m.className = "ha-msg"; f.after(m); }
    m.textContent = txt;
  }

  async function send(f, b) {
    var url = proxied(f);
    if (f._haSending && Date.now() - f._haSending < 1500) return;   // double tap while sending
    f._haSending = Date.now();
    try { await post(f, b, url); } finally { f._haSending = 0; }
  }

  async function post(f, b, url) {
    var data;
    try { data = b && b.form === f && b.name ? new FormData(f, b) : new FormData(f); }
    catch (e) { data = new FormData(f); if (b && b.name) data.append(b.name, b.value); }

    var tile = b && b.querySelector(".acc-hm-st");
    var finalText = b ? b.textContent : "";                      // what the page wanted to show ("✓ Done"...)
    var check = /^\s*[✓✔]/.test(finalText);
    if (b) {
      var bar = b.querySelector(".ha-bar") || document.createElement("i");   // sending line under the button
      bar.className = "ha-bar"; b.appendChild(bar);
      requestAnimationFrame(function () { bar.classList.add("go"); });
      b.classList.remove("ha-err"); b.classList.add("ha-envoi"); b.style.opacity = ""; b.disabled = true;
      if (tile) tile.textContent = "sending…"; else if (check) b.textContent = "Sending…";
    }
    message(f, "");

    var error = "";
    try {
      var r = await fetch(url, { method: "POST", body: new URLSearchParams(data), credentials: "same-origin",
                                 redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(8000) });
      if (r.type === "opaqueredirect" || r.status === 0 || r.status === 404 || r.status === 405) {
        // safety net: the /ha-webhook/ route does not answer as expected (session, proxy...) ->
        // send it the old way (hidden iframe), without confirmation: never worse than before
        native.call(f);
        if (b) { b.classList.remove("ha-envoi"); if (tile) { tile.textContent = "sent"; b.disabled = false; } else if (check) b.textContent = finalText; else b.disabled = false; }
        return;
      }
      if (!r.ok) error = "not sent · retry";
    } catch (e) {
      error = e && e.name === "TimeoutError" ? "no answer · retry" : "not sent · retry";
    }

    if (!b) { message(f, error); return; }
    b.classList.remove("ha-envoi");
    var tr = b.querySelector(".ha-bar");
    if (tr) { if (error) tr.remove(); else { tr.classList.add("ok"); setTimeout(function () { tr.remove(); }, 900); } }
    if (error) {
      window.haErreur = Date.now();                               // live.js waits before refreshing this widget
      b.classList.add("ha-err"); b.disabled = false;
      if (tile) { tile.textContent = error; b.title = error; }
      else if (check) { b.textContent = "↻ Not sent"; b.title = error; }
      else message(f, error);
      return;
    }
    if (tile && b.hasAttribute("data-toggle")) {                  // lights: the light spreads through the tile
      var on = !b.classList.contains("on");
      b.classList.toggle("on", on); b.classList.add("ha-allume");
      tile.textContent = on ? "on" : "off"; b.disabled = false;
      setTimeout(function () { b.classList.remove("ha-allume"); }, 700);
    }
    else if (tile) { tile.textContent = "received"; b.disabled = false; }  // live.js shows the real state at 5 s and 12 s
    if (b.closest(".acc-rbtns") || (b.closest(".acc-col") && b.closest(".acc-row"))) todoDone(b);
    else if (check) b.textContent = finalText;                      // stays "✓ ..." and greyed out
    else if (f.id === "cpt-form") metersOk(f, b);
    else { b.disabled = false; }
  }

  // ---------- meter readings form ----------
  // Before sending: nothing filled in, or an index lower than the current one (Home Assistant would refuse it) -> say so.
  // After HA answered: "✓ Saved", fields emptied, displayed indexes and date updated at once
  // (live.js does not refresh this widget: it contains a form).
  function label(i) { return i.getAttribute("data-label") || i.name; }
  function meterMsg(f, txt, err) {
    var m = f.querySelector("#cpt-msg"); if (!m) return;
    m.textContent = txt; m.style.color = err ? "hsl(32,85%,64%)" : "hsl(140,55%,62%)";
  }
  document.addEventListener("submit", function (e) {
    var f = e.target; if (f.id !== "cpt-form") return;
    var seen = 0, low = [];
    f.querySelectorAll("input").forEach(function (i) {
      var v = (i.value || "").replace(",", ".").trim(); if (!v) return; seen++;
      var cur = parseFloat(i.getAttribute("placeholder"));
      if (!isNaN(cur) && parseFloat(v) < cur) low.push(label(i) + ": " + v + " is lower than the current index (" + cur + ")");
    });
    if (!seen) { e.preventDefault(); e.stopImmediatePropagation(); meterMsg(f, "Nothing to save: fill in at least one meter.", true); return; }
    if (low.length) { e.preventDefault(); e.stopImmediatePropagation(); meterMsg(f, low.join(" · ") + ". Fix it before saving.", true); }
  }, true);
  function metersOk(f, b) {
    var ok = [], card = f.closest(".widget") || document;
    f.querySelectorAll("input").forEach(function (i) {
      var v = (i.value || "").replace(",", ".").trim(); if (!v) return;
      ok.push(label(i) + " " + v); i.placeholder = String(Math.round(parseFloat(v))); i.value = "";
      card.querySelectorAll('[data-meter-index="' + i.name + '"]').forEach(function (x) { x.textContent = String(Math.round(parseFloat(v))); });
    });
    b.textContent = "✓ Saved"; b.disabled = false;
    setTimeout(function () { b.textContent = "Save readings"; }, 6000);
    meterMsg(f, "Received by Home Assistant: " + ok.join(" · ") + ".", false);
    var r = card.querySelector(".acc-ct-r");
    if (r) { r.classList.remove("warn"); var t = r.querySelector(".t"); if (t) t.textContent = "Meters read today"; }
    card.querySelectorAll(".acc-ct-h .color-subdue").forEach(function (x) { x.textContent = "read today"; });
  }

  // ---------- to-do: the finished line leaves; empty card = "all done" ----------
  var reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  function key(row) { var t = row && row.querySelector(".acc-tx .t"); return t ? t.textContent.trim() : ""; }
  function remember(k) {
    try { var o = JSON.parse(sessionStorage.getItem("haDone") || "{}"); o[k] = Date.now(); sessionStorage.setItem("haDone", JSON.stringify(o)); } catch (e) {}
  }
  function removeRow(row, extra) {
    var card = row.closest(".acc-card");
    [row, extra].forEach(function (x) { if (x) x.classList.add("ha-part"); });
    setTimeout(function () {
      [row, extra].forEach(function (x) { if (x) x.remove(); });
      if (card && !card.querySelector(".acc-row")) empty(card);
      var n = card && card.querySelector(".acc-ch span:last-child");
      if (n && /^\d+$/.test(n.textContent)) n.textContent = Math.max(0, parseInt(n.textContent, 10) - 1) || "";
    }, reduced ? 50 : 380);
  }
  function empty(card) {
    var d = document.createElement("div"); d.className = "acc-row ha-vide";
    d.innerHTML = '<div class="acc-chip ok"><svg class="ha-chk" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg></div><div class="acc-tx"><div class="t">All done</div><div class="s">nothing left for today</div></div>';
    card.appendChild(d);
  }
  function todoDone(b) {
    var extra = b.closest(".acc-rbtns");
    var row = extra ? extra.previousElementSibling : b.closest(".acc-row");
    if (!row || !row.classList.contains("acc-row")) return;
    if (!extra && row.nextElementSibling && row.nextElementSibling.classList.contains("acc-rbtns")) extra = row.nextElementSibling;
    remember(key(row));
    removeRow(row, extra);
  }
  // after a refresh (live.js), do not bring back what was just done (15 min)
  document.addEventListener("glance-maj", function () {
    var o = {}; try { o = JSON.parse(sessionStorage.getItem("haDone") || "{}"); } catch (e) {}
    document.querySelectorAll(".acc-col .acc-row").forEach(function (row) {
      var k = key(row); if (!k || !o[k] || Date.now() - o[k] > 900000) return;
      var extra = row.nextElementSibling && row.nextElementSibling.classList.contains("acc-rbtns") ? row.nextElementSibling : null;
      var card = row.closest(".acc-card"); row.remove(); if (extra) extra.remove();
      if (card && !card.querySelector(".acc-row")) empty(card);
    });
  });

  // bubbling phase: the page's own onsubmit checks run first; if they refuse, do nothing
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!proxied(f) || e.defaultPrevented) return;
    e.preventDefault();
    send(f, e.submitter || null);
  });

  // "Done", "Request"... buttons call this.form.submit() directly
  HTMLFormElement.prototype.submit = function () {
    if (!proxied(this)) return native.call(this);
    send(this, lastButton && lastButton.form === this ? lastButton : null);
  };
})();
