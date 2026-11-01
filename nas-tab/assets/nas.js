/* Glance "NAS" page. Reads nas-state.json (written every minute by collector/nas-state.py) and draws:
   a live map (internet -> router -> host -> entry services -> container groups), a list view, the "To do" list,
   outages over 30 days, recent updates, backup / UPS, scheduled tasks, links, and a stale-data banner.
   Read only: there are no action buttons. The widget holds an <input type="hidden"> so Glance does not replace it. */
(function () {
  "use strict";
  // Glance inserts the page content after this script has run: mount as soon as the block shows up
  function mount() { document.querySelectorAll("[data-nas-app]").forEach(function (r) { if (!r.dataset.mounted) { r.dataset.mounted = "1"; init(r); } }); }
  mount();
  new MutationObserver(mount).observe(document.documentElement, { childList: true, subtree: true });

function init(root) {
  var SRC = root.getAttribute("data-src") || "/assets/nas-state.json";
  var STALE_S = parseInt(root.getAttribute("data-stale-after") || "300", 10);   // 5 missed runs
  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var D = null, byId = {}, current = null, origin = null;

  var ICO = {
    globe: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
    box: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 16.5h.01M11 16.5h.01M8 9a6 6 0 0 1 8 0M5.5 6.5a10 10 0 0 1 13 0"/></svg>',
    nas: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M8 7h8M8 11h8M8 17h.01M12 17h.01"/></svg>',
    search: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    chev: '<svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
    ok: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M5 12.5 10 17 19 7"/></svg>',
    x: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>'
  };

  root.innerHTML =
    '<div class="page" id="nas-page">' +
    '<div class="head"><div><h1 id="nas-title">Server</h1><div class="sub" id="nas-sub">loading…</div></div>' +
    '<div class="global-state" id="nas-state-g"><span class="pulse" aria-hidden="true"></span><span id="nas-state-txt">…</span></div></div>' +
    '<div class="stale" id="nas-stale" role="status" hidden></div>' +
    '<div class="tools"><label class="search">' + ICO.search + '<input id="nas-q" type="search" placeholder="Search a service, an IP…" aria-label="Search a service"></label>' +
    '<div class="toggle" role="group" aria-label="View"><button type="button" id="nas-b-map" aria-pressed="true">Map</button><button type="button" id="nas-b-list" aria-pressed="false">List</button></div></div>' +
    '<section class="map" id="nas-map" aria-label="Network map"><svg class="links" id="nas-links" aria-hidden="true"></svg>' +
    '<div class="top-node" id="nas-n-internet"><div class="ico">' + ICO.globe + '</div><div><div class="name" id="nas-i-name">Internet</div><div class="det" id="nas-i-det"></div></div><div class="right" id="nas-i-speed"></div></div>' +
    '<div class="top-node" id="nas-n-router"><div class="ico">' + ICO.box + '</div><div><div class="name" id="nas-r-name">Router</div><div class="det" id="nas-r-det"></div></div><div class="right" id="nas-r-ip"></div></div>' +
    '<div class="host" id="nas-n-host"><div class="line1"><div class="ico">' + ICO.nas + '</div><div><div class="name" id="nas-h-name">Host</div><div class="det mono" id="nas-h-det"></div></div></div><div class="tiles" id="nas-tiles"></div></div>' +
    '<div class="entries" id="nas-entries"></div><div class="groups" id="nas-groups"></div></section>' +
    '<section class="list" aria-label="Container list"><div class="list-wrap"><table><thead><tr><th scope="col">Service</th><th scope="col">IP</th><th scope="col">Up</th><th scope="col">RAM</th></tr></thead><tbody id="nas-list-body"></tbody></table></div></section>' +
    '<div class="sections">' +
    '<section class="block" aria-labelledby="nas-h-todo"><h2 id="nas-h-todo">To do<span class="more" id="nas-todo-n">0</span></h2><div id="nas-todo"></div></section>' +
    '<section class="block outages" id="outages" aria-labelledby="nas-h-out" hidden><h2 id="nas-h-out">Outages · 30 days<span class="more" id="nas-out-more"></span></h2><div id="nas-out"></div></section>' +
    '<section class="block" aria-labelledby="nas-h-upd"><h2 id="nas-h-upd">Recent updates</h2><div id="nas-upd"></div></section>' +
    '<section class="block" id="nas-b-backup" aria-labelledby="nas-h-bk" hidden><h2 id="nas-h-bk">Backup &amp; power</h2><div id="nas-bk"></div></section>' +
    '<section class="block" aria-labelledby="nas-h-tasks"><h2 id="nas-h-tasks">Scheduled tasks<span class="more mono" id="nas-tasks-n"></span></h2><div id="nas-tasks"></div></section>' +
    '<section class="block" id="nas-b-links" aria-labelledby="nas-h-links" hidden><h2 id="nas-h-links">Links</h2><div class="link-grid" id="nas-links-list"></div></section>' +
    '</div>' +
    '<p class="note">Data collected every minute on the host by nas-state.py. This page is read only.</p>' +
    '</div>' +
    '<div class="veil" id="nas-veil"></div>' +
    '<div class="sheet" id="nas-sheet" role="dialog" aria-modal="true" aria-labelledby="nas-s-name" hidden><div class="grip" aria-hidden="true"></div>' +
    '<div class="st"><span class="dot" id="nas-s-dot"></span><div><h3 id="nas-s-name">—</h3><div class="group-n" id="nas-s-group"></div></div><button class="close" type="button" id="nas-s-close" aria-label="Close">' + ICO.x + '</button></div>' +
    '<div class="facts" id="nas-s-facts"></div><div class="caution" id="nas-s-warn" hidden></div>' +
    '<div class="acts"><a class="btn prim" id="nas-s-open" href="#" target="_blank" rel="noopener">Open</a><a class="btn" id="nas-s-logs" href="#" target="_blank" rel="noopener">Logs</a></div></div>';

  function $(id) { return document.getElementById(id); }
  function el(t, cls, txt) { var e = document.createElement(t); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
  function safeUrl(u) { return typeof u === "string" && /^https?:\/\/[a-z0-9.:\[\]-]+(\/[^\s"'<>]*)?$/i.test(u) ? u : ""; }
  function shortIp(ip) { var p = ((D || {}).labels || {}).short_prefix; ip = String(ip || ""); return p ? ip.split(p).join(".") : ip; }
  function isUp(f) { return f && f.state === "running" && f.health !== "unhealthy"; }
  function dotOf(f) { if (!f || f.state !== "running" || f.health === "unhealthy") return "down"; if (f.update_available) return "warn"; return ""; }

  /* ---------------------------------------------------------------- map */
  function renderTop(d) {
    var i = d.internet || {}, s = d.system || {}, L = d.labels || {};
    $("nas-title").textContent = L.host || "Server";
    var g = new Date(d.generated || Date.now());
    $("nas-sub").textContent = "updated " + hmD(g);
    var tot = 0, ok = 0, seen = {};   // a container can be both a service and attached to another: count it once
    (d.services || []).forEach(function (x) { if (seen[x.c]) return; seen[x.c] = 1; tot++; if (isUp(x)) ok++; });
    Object.keys(d.deps || {}).forEach(function (k) { if (seen[k]) return; seen[k] = 1; tot++; if (isUp(d.deps[k])) ok++; });
    $("nas-state-txt").textContent = ok + " / " + tot + " running";
    $("nas-state-g").style.color = ok < tot ? "var(--down)" : "";
    $("nas-i-name").textContent = L.isp || "Internet";
    $("nas-i-det").textContent = (i.down_since ? "Down since " + i.down_since + " · " : "") +
      (i.outages_7d != null ? i.outages_7d + " outage" + (i.outages_7d === 1 ? "" : "s") + " this week" : "") + (i.last_outage ? " · last " + i.last_outage : "");
    var sp = $("nas-i-speed"); sp.innerHTML = "";
    if (i.down != null) { sp.appendChild(el("b", null, String(i.down))); sp.appendChild(document.createTextNode("↓ ")); sp.appendChild(el("b", null, String(i.up))); sp.appendChild(document.createTextNode("↑")); sp.appendChild(el("br")); sp.appendChild(document.createTextNode("Mbps · " + i.ping + " ms")); }
    $("nas-r-name").textContent = L.router || "Router";
    var wifiSrc = ((d.outages || {}).sources || {}).wifi;
    $("nas-r-det").textContent = wifiSrc ? "Wi-Fi" + (i.wifi_last ? " · last outage " + i.wifi_last : " stable") : "";
    var rip = $("nas-r-ip"); rip.innerHTML = ""; if (L.router_ip) { rip.appendChild(el("b", null, L.router_ip)); rip.appendChild(el("br")); rip.appendChild(document.createTextNode("gateway")); }
    $("nas-h-name").textContent = L.host || "Host";
    $("nas-h-det").textContent = (L.host_ip ? L.host_ip + " · " : "") + "up " + (s.uptime_days != null ? s.uptime_days + " d" : "?");
    var t = $("nas-tiles"); t.innerHTML = "";
    function tile(v, l, warn) { var x = el("div", "t" + (warn ? " attention" : "")); x.appendChild(el("div", "v", v)); x.appendChild(el("div", "l", l)); t.appendChild(x); }
    var c = s.containers || {};
    tile((c.running || 0) + "/" + (c.total || 0), "Containers", c.running < c.total);
    if (s.cpu_temp != null) tile(s.cpu_temp + "°", "CPU", s.cpu_temp >= 80);
    if (s.ram) tile(s.ram.used + "/" + s.ram.total, "Memory GB", s.ram.used / s.ram.total > 0.9);
    if (s.disk) tile(s.disk.used + "/" + s.disk.total, "Disk TB", s.disk.pct >= 85);
    var raid = s.raid || [];
    if (raid.length) {
      var bad = raid.filter(function (r) { return r.status.indexOf("_") >= 0; });
      var main = bad[0] || raid[raid.length - 1];
      tile(main.status, "RAID" + (main.level ? " " + main.level.replace("raid", "") : "") + ((s.disk_temps || []).length ? " · " + s.disk_temps.join("° / ") + "°" : ""), !!bad.length);
    }
    if (s.ups) tile(s.ups.charge != null ? s.ups.charge + " %" : "?", "UPS · " + (s.ups.state || "?"), s.ups.state !== "mains");
  }

  function openFrom(b, s, dep) { b.addEventListener("click", function () { openSheet(s, b, dep); }); }
  function renderMap(d) {
    byId = {};
    (d.services || []).forEach(function (s) { byId[s.c] = s; });
    var entryIds = (d.groups || []).filter(function (g) { return g.entry; }).map(function (g) { return g.id; });
    var ent = $("nas-entries"); ent.innerHTML = "";
    (d.services || []).filter(function (s) { return entryIds.indexOf(s.group) >= 0 && !s.hidden; }).forEach(function (s) {
      var b = el("button", "entry"); b.type = "button"; b.dataset.c = s.c;
      var n = el("span", "n"); n.appendChild(el("span", "dot " + dotOf(s))); n.appendChild(document.createTextNode(s.name));
      b.appendChild(n); b.appendChild(el("span", "d", String(s.desc || "").split(" ·")[0]));
      if (s.sensitive) b.appendChild(el("span", "sens", "sensitive"));
      openFrom(b, s); ent.appendChild(b);
    });
    ent.hidden = !ent.firstChild;
    var gr = $("nas-groups"), open = {};
    gr.querySelectorAll(".group").forEach(function (g) { open[g.dataset.g] = g.dataset.open; });
    gr.innerHTML = "";
    (d.groups || []).filter(function (g) { return !g.entry; }).forEach(function (g) {
      var members = (d.services || []).filter(function (s) { return s.group === g.id; });
      if (!members.length) return;
      var nb = members.reduce(function (a, s) { return a + 1 + (s.deps ? s.deps.length : 0); }, 0);
      var box = el("section", "group"); box.dataset.g = g.id;
      var o = open[g.id] || "false"; box.dataset.open = o;   // groups start collapsed
      var head = el("button", "group-head"); head.type = "button"; head.setAttribute("aria-expanded", o);
      head.innerHTML = ICO.chev; head.appendChild(el("span", "t", g.title || g.id)); head.appendChild(el("span", "c", nb + " container" + (nb > 1 ? "s" : "")));
      var dots = el("span", "dots"); dots.setAttribute("aria-hidden", "true");
      members.forEach(function (s) { dots.appendChild(el("i", dotOf(s))); });
      head.appendChild(dots);
      head.addEventListener("click", function () { var v = box.dataset.open !== "true"; box.dataset.open = v ? "true" : "false"; head.setAttribute("aria-expanded", v ? "true" : "false"); draw(); });
      box.appendChild(head);
      var chips = el("div", "chips");
      members.forEach(function (s) {
        var b = el("button", "chip" + (s.update_available ? " upd" : "")); b.type = "button"; b.dataset.c = s.c;
        b.appendChild(el("span", "dot " + dotOf(s)));
        var t = el("span", "txt"); t.appendChild(el("span", "n", s.name)); t.appendChild(el("span", "m", shortIp(s.ip))); b.appendChild(t);
        if (s.deps && s.deps.length) b.appendChild(el("span", "attached", "+" + s.deps.length));
        b.setAttribute("aria-label", s.name + ", " + (s.desc || "") + (isUp(s) ? "" : ", down"));
        openFrom(b, s); chips.appendChild(b);
      });
      box.appendChild(chips); gr.appendChild(box);
    });
    var body = $("nas-list-body"); body.innerHTML = "";
    var all = [];
    (d.services || []).forEach(function (s) {
      all.push({ n: s.name, c: s.c, ip: s.ip, up: s.up, ram: s.ram, s: s, f: s });
      (s.deps || []).forEach(function (x) { var f = (d.deps || {})[x] || {}; all.push({ n: x, c: x, ip: "attached to " + s.name, up: f.up, ram: f.ram, s: s, dep: x, f: f }); });
    });
    var seen = {}; all = all.filter(function (r) { if (seen[r.c]) return false; seen[r.c] = 1; return true; });
    all.sort(function (a, b) { return a.c.localeCompare(b.c); });
    all.forEach(function (r) {
      var tr = el("tr"); tr.dataset.q = (r.n + " " + r.c + " " + r.ip).toLowerCase();
      var td = el("td"), b = el("button"); b.type = "button"; b.appendChild(el("span", "dot " + dotOf(r.f))); b.appendChild(el("span", null, r.n));
      openFrom(b, r.s, r.dep); td.appendChild(b); tr.appendChild(td);
      tr.appendChild(el("td", "col-ip", shortIp(r.ip))); tr.appendChild(el("td", "col-up", r.up || "—")); tr.appendChild(el("td", "col-ram", r.ram || "—"));
      body.appendChild(tr);
    });
    filter();
    setTimeout(draw, 30);
  }

  /* animated links between the nodes */
  var NS = "http://www.w3.org/2000/svg";
  function pos(e) { var r = e.getBoundingClientRect(), c = $("nas-map").getBoundingClientRect(); return { x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height }; }
  function path(svg, d, idx, cut) {
    var p = document.createElementNS(NS, "path"); p.setAttribute("d", d); p.id = "nas-l" + idx; if (cut) p.setAttribute("class", "cut"); svg.appendChild(p);
    if (calm || cut) return;
    for (var k = 0; k < 2; k++) {
      var c = document.createElementNS(NS, "circle"); c.setAttribute("r", "2.4");
      var a = document.createElementNS(NS, "animateMotion"); a.setAttribute("dur", (2.6 + idx * 0.35) + "s"); a.setAttribute("repeatCount", "indefinite"); a.setAttribute("begin", "-" + (k * 1.3 + idx * 0.2) + "s");
      var m = document.createElementNS(NS, "mpath"); m.setAttributeNS("http://www.w3.org/1999/xlink", "href", "#nas-l" + idx); m.setAttribute("href", "#nas-l" + idx);
      a.appendChild(m); c.appendChild(a); svg.appendChild(c);
    }
  }
  function draw() {
    var map = $("nas-map"), svg = $("nas-links");
    if (!map || !map.offsetParent) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var i = 0, cut = !!(D && (D.internet || {}).down_since);   // red only when the line itself is down
    var a = pos($("nas-n-internet")), b = pos($("nas-n-router")), n = pos($("nas-n-host"));
    var entries = $("nas-entries"), below = entries.hidden ? n : pos(entries);
    var cx = a.x + a.w / 2;
    path(svg, "M" + cx + " " + (a.y + a.h) + " V" + b.y, i++, cut);
    path(svg, "M" + cx + " " + (b.y + b.h) + " V" + n.y, i++);
    if (!entries.hidden) path(svg, "M" + cx + " " + (n.y + n.h) + " V" + below.y, i++);
    var wide = map.getBoundingClientRect().width >= 700, bottom = below.y + below.h;
    var gs = $("nas-groups").querySelectorAll(".group");
    if (!gs.length) return;
    if (!wide) {
      var x = 10, last = pos(gs[gs.length - 1]);
      path(svg, "M" + cx + " " + bottom + " V" + (bottom + 10) + " H" + x + " V" + (last.y + 26), i++);
      Array.prototype.forEach.call(gs, function (g) { var p = pos(g); path(svg, "M" + x + " " + (p.y + 26) + " H" + p.x, i++); });
    } else {
      Array.prototype.forEach.call(gs, function (g) { var p = pos(g); var gx = p.x + p.w / 2; path(svg, "M" + cx + " " + bottom + " V" + (bottom + 11) + " H" + gx + " V" + p.y, i++); });
    }
  }
  var resizeT; window.addEventListener("resize", function () { clearTimeout(resizeT); resizeT = setTimeout(draw, 120); });

  /* search & view toggle */
  function filter() {
    var v = $("nas-q").value.trim().toLowerCase();
    root.querySelectorAll(".chip,.entry").forEach(function (b) {
      var s = byId[b.dataset.c]; if (!s) return;
      var hay = (s.name + " " + s.c + " " + s.ip + " " + (s.desc || "") + " " + (s.deps || []).join(" ")).toLowerCase();
      var ok = !v || hay.indexOf(v) >= 0;
      b.classList.toggle("dim", !!v && !ok); b.classList.toggle("found", !!v && ok);
      if (v && ok) { var g = b.closest(".group"); if (g && g.dataset.open !== "true") g.querySelector(".group-head").click(); }
    });
    root.querySelectorAll("#nas-list-body tr").forEach(function (tr) { tr.classList.toggle("dim", !!v && tr.dataset.q.indexOf(v) < 0); });
  }
  $("nas-q").addEventListener("input", filter);
  function view(l) { $("nas-page").classList.toggle("view-list", l); $("nas-b-map").setAttribute("aria-pressed", l ? "false" : "true"); $("nas-b-list").setAttribute("aria-pressed", l ? "true" : "false"); if (!l) setTimeout(draw, 20); }
  $("nas-b-map").addEventListener("click", function () { view(false); });
  $("nas-b-list").addEventListener("click", function () { view(true); });

  /* ---------------------------------------------------------------- container sheet */
  function fact(l, v, wide) { var d = el("div", "fact" + (wide ? " wide" : "")); d.appendChild(el("div", "l", l)); d.appendChild(el("div", "v", v == null || v === "" ? "—" : String(v))); return d; }
  function openSheet(s, from, dep) {
    current = { s: s, dep: dep || null }; origin = from || null;
    var f = dep ? ((D.deps || {})[dep] || {}) : s;
    var g = (D.groups || []).filter(function (x) { return x.id === s.group; })[0];
    $("nas-s-name").textContent = dep || s.name;
    $("nas-s-group").textContent = dep ? "attached to " + s.name : ((g ? g.title : "") + (s.desc ? " · " + s.desc : ""));
    $("nas-s-dot").className = "dot " + dotOf(f);
    var ff = $("nas-s-facts"); ff.innerHTML = "";
    ff.appendChild(fact("Image", f.image)); ff.appendChild(fact("Version", f.version)); ff.appendChild(fact("Address", f.ip)); ff.appendChild(fact("Up for", f.up));
    ff.appendChild(fact("CPU", f.cpu)); ff.appendChild(fact("Memory", f.ram));
    ff.appendChild(fact("Health", f.state !== "running" ? "stopped (" + f.state + ")" : f.health === "unhealthy" ? "unhealthy" : f.health === "starting" ? "starting…" : (f.health === "healthy" ? "healthy" : "running") + (f.update_available ? " · update available" : "")));
    ff.appendChild(fact("Last update", f.updated || "—"));
    if (!dep && safeUrl(s.url)) ff.appendChild(fact("Web address", s.url.replace(/^https?:\/\//, ""), true));
    if (!dep && s.deps && s.deps.length) ff.appendChild(fact("Attached", s.deps.join(", "), true));
    var w = $("nas-s-warn");
    if (!dep && s.sensitive) { w.hidden = false; w.textContent = "Sensitive service: restarting it cuts " + (typeof s.sensitive === "string" ? s.sensitive : "this service") + "."; } else w.hidden = true;
    var o = $("nas-s-open"), u = dep ? "" : safeUrl(s.url);
    disable(o, u);
    var lu = safeUrl(((D.labels || {}).logs_url || "").split("{name}").join(encodeURIComponent(dep || s.c)));
    disable($("nas-s-logs"), lu);
    $("nas-sheet").hidden = false;
    requestAnimationFrame(function () { $("nas-sheet").classList.add("open"); $("nas-veil").classList.add("open"); });
    $("nas-s-close").focus();
  }
  function disable(a, u) {
    if (u) { a.href = u; a.removeAttribute("aria-disabled"); a.style.pointerEvents = ""; a.style.opacity = ""; }
    else { a.href = "#"; a.setAttribute("aria-disabled", "true"); a.style.pointerEvents = "none"; a.style.opacity = ".45"; }
  }
  function closeSheet() { $("nas-sheet").classList.remove("open"); $("nas-veil").classList.remove("open"); setTimeout(function () { $("nas-sheet").hidden = true; }, calm ? 0 : 250); if (origin) origin.focus(); }
  $("nas-s-close").addEventListener("click", closeSheet); $("nas-veil").addEventListener("click", closeSheet);
  document.addEventListener("keydown", function (e) {
    if (!root.isConnected) return;
    var fi = $("nas-sheet"); if (!fi || fi.hidden) return;
    if (e.key === "Escape") closeSheet();
    if (e.key === "Tab") { var f = fi.querySelectorAll("button:not([disabled]),a[href]:not([aria-disabled])"); var a = f[0], z = f[f.length - 1]; if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); } else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); } }
  });

  /* ---------------------------------------------------------------- lower blocks */
  function row(dot, t, s, h, tag) {
    var r = el("div", "row"); r.appendChild(el("span", "dot" + (dot ? " " + dot : "")));
    var q = el("div", "q"); q.appendChild(el("div", "t", t)); if (s) q.appendChild(el("div", "s", s)); r.appendChild(q);
    if (h) r.appendChild(el("span", "h", h)); if (tag) r.appendChild(tag); return r;
  }
  function tag(cls, txt) { return el("span", "tag " + cls, txt); }
  function renderLower(d) {
    var b = $("nas-todo"); b.innerHTML = "";
    var pb = d.problems || [];
    $("nas-todo-n").textContent = pb.length;
    if (!pb.length) {
      var v = el("div", "empty"); var ring = el("span", "ok-ring"); ring.innerHTML = ICO.ok; v.appendChild(ring);
      v.appendChild(el("span", null, "Nothing to do · everything runs, nothing to update")); b.appendChild(v);
    }
    pb.forEach(function (p) {
      var lv = p.level === "down" ? "down" : p.level === "warn" ? "warn" : "";
      var r = row(lv, p.title, p.detail, "", tag(lv || "ok", p.level === "down" ? "down" : p.level === "warn" ? "to do" : "info"));
      r.dataset.pb = p.id || "";
      b.appendChild(r);
    });
    var up = $("nas-upd"); up.innerHTML = "";
    ((d.updates || {}).recent || []).forEach(function (m) { up.appendChild(row("", m.c, (m.from || "?") + " → " + (m.to || "?"), m.when)); });
    if (!up.firstChild) up.appendChild(el("div", "empty", "No update in the last two weeks"));
    var mr = Array.prototype.slice.call(up.children).filter(function (x) { return x.classList.contains("row"); }).slice(3);
    fold(up, mr, "upd", "Show " + mr.length + " more");
    var bk = $("nas-bk"); bk.innerHTML = ""; var s = d.backup, u = (d.system || {}).ups;
    if (s) {
      var kOk = s.age_h != null && s.age_h <= (s.max_age_h || 30);
      bk.appendChild(row(kOk ? "" : "down", s.label || "Backup", s.last ? "last " + s.last : "no backup found", "", tag(kOk ? "ok" : "down", kOk ? "OK" : "check")));
    }
    if (u) {
      var uOk = u.state === "mains";
      bk.appendChild(row(uOk ? "" : "down", "UPS", (u.state || "?") + (u.charge != null ? " · " + u.charge + " %" : "") + (u.load != null ? " · load " + u.load + " %" : "") + ((d.labels || {}).ups_note ? " · " + d.labels.ups_note : ""), "", tag(uOk ? "ok" : "down", uOk ? "OK" : u.state || "?")));
    }
    $("nas-b-backup").hidden = !bk.firstChild;
    renderTasks(d);
    var ln = $("nas-links-list"); ln.innerHTML = "";
    (d.links || []).forEach(function (l) { var url = safeUrl(l.u); if (!url) return; var a = el("a", "btn", l.t); a.href = url; a.target = "_blank"; a.rel = "noopener"; ln.appendChild(a); });
    $("nas-b-links").hidden = !ln.firstChild;
  }

  var FOLDS = {};   // folded or not, kept between two refreshes
  function fold(box, hidden, key, label) {
    if (!hidden.length) return;
    hidden.forEach(function (x) { x.classList.add("folded"); });
    var bt = el("button", "show-more"); bt.type = "button";
    var upd = function () {
      box.setAttribute("data-folded", FOLDS[key] ? "false" : "true");
      bt.setAttribute("aria-expanded", FOLDS[key] ? "true" : "false");
      bt.innerHTML = ""; bt.appendChild(document.createTextNode(FOLDS[key] ? "Show less" : label)); bt.insertAdjacentHTML("beforeend", ICO.chev);
    };
    bt.addEventListener("click", function () { FOLDS[key] = !FOLDS[key]; upd(); });
    upd(); box.appendChild(bt);
  }
  var WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  function dayKey(x) { return x.getFullYear() + "-" + ("0" + (x.getMonth() + 1)).slice(-2) + "-" + ("0" + x.getDate()).slice(-2); }
  function hmD(x) { return ("0" + x.getHours()).slice(-2) + ":" + ("0" + x.getMinutes()).slice(-2); }
  function durS(s) { if (s < 60) return s + " s"; var m = Math.round(s / 60); if (m < 60) return m + " min"; var h = Math.floor(m / 60), r = m % 60; return h + " h" + (r ? " " + ("0" + r).slice(-2) : ""); }
  function dayName(k, now) {
    var y = new Date(now); y.setDate(y.getDate() - 1);
    if (k === dayKey(now)) return "Today";
    if (k === dayKey(y)) return "Yesterday";
    var x = new Date(k + "T12:00"); return WD[x.getDay()] + " " + x.getDate() + "/" + (x.getMonth() + 1);
  }

  // Outages · 30 days (only the types that have a configured source)
  var TYPE_NAMES = { net: "Internet", wifi: "Wi-Fi", power: "Power" };
  function level(s) { return s < 60 ? "m" : (s <= 900 ? "c" : "l"); }
  function renderOutages(d) {
    var c = d.outages, z = $("nas-out"), sec = $("outages");
    var types = ["net", "wifi", "power"].filter(function (t) { return c && c.sources && c.sources[t]; });
    sec.hidden = !types.length; if (!types.length) return;
    var now = new Date(d.generated || Date.now()), ev = (c.events || []).filter(function (x) { return types.indexOf(x.type) >= 0; }), on = c.ongoing || [];
    z.innerHTML = "";
    var todayK = dayKey(now), today = ev.filter(function (x) { return x.t.slice(0, 10) === todayK; });
    var st = el("div", "o-state" + (on.length ? " down" : "")), ring = el("div", "o-ring");
    if (on.length) ring.textContent = "!"; else ring.innerHTML = ICO.ok;
    var q = el("div", "q");
    if (on.length) {
      q.appendChild(el("div", "t", "Outage in progress"));
      q.appendChild(el("div", "s", on.map(function (x) { return TYPE_NAMES[x.type] + " since " + hmD(new Date(x.since)); }).join(" · ")));
    } else if (today.length) {
      q.appendChild(el("div", "t", today.length + " outage" + (today.length > 1 ? "s" : "") + " today"));
      q.appendChild(el("div", "s", "last: " + hmD(new Date(today[0].t)) + ", " + TYPE_NAMES[today[0].type] + " " + durS(today[0].s)));
    } else {
      q.appendChild(el("div", "t", "No outage today"));
      if (ev.length) q.appendChild(el("div", "s", "last: " + dayName(ev[0].t.slice(0, 10), now).toLowerCase() + " " + hmD(new Date(ev[0].t)) + ", " + TYPE_NAMES[ev[0].type] + " " + durS(ev[0].s)));
      else q.appendChild(el("div", "s", "none in the last 30 days"));
    }
    st.appendChild(ring); st.appendChild(q); z.appendChild(st);
    var nums = el("div", "o-nums");
    nums.style.gridTemplateColumns = "repeat(" + types.length + ",minmax(0,1fr))";
    types.forEach(function (t) {
      var e = ev.filter(function (x) { return x.type === t; }), tot = e.reduce(function (a, x) { return a + x.s; }, 0), mic = e.filter(function (x) { return x.s < 60; }).length;
      var b = el("div", "c" + (e.length ? "" : " none")); b.appendChild(el("div", "v", String(e.length))); b.appendChild(el("div", "l", TYPE_NAMES[t]));
      b.appendChild(el("div", "l2", tot ? durS(tot) + " in total" + (mic ? " · " + mic + " micro" : "") : "none")); nums.appendChild(b);
    });
    z.appendChild(nums);
    var g = el("div", "o-grid"), days = [];
    for (var i = 29; i >= 0; i--) { var x = new Date(now); x.setDate(x.getDate() - i); days.push(dayKey(x)); }
    types.forEach(function (t) {
      g.appendChild(el("div", "n", TYPE_NAMES[t]));
      var r = el("div", "o-days"); r.setAttribute("aria-hidden", "true");
      days.forEach(function (k, i) {
        var m = ev.filter(function (x) { return x.type === t && x.t.slice(0, 10) === k; }).reduce(function (a, x) { return Math.max(a, x.s); }, 0);
        r.appendChild(el("i", (m ? level(m) : "") + (i === 29 ? " today" : "")));
      });
      g.appendChild(r);
    });
    var ax = el("div", "o-axis"), d0 = new Date(days[0] + "T12:00"), d1 = new Date(days[15] + "T12:00");
    ax.appendChild(el("span", null, d0.getDate() + "/" + (d0.getMonth() + 1))); ax.appendChild(el("span", null, d1.getDate() + "/" + (d1.getMonth() + 1))); ax.appendChild(el("span", null, "today"));
    g.appendChild(ax);
    var lg = el("div", "o-legend"); lg.appendChild(el("span", "m", "under 1 min")); lg.appendChild(el("span", "c", "up to 15 min")); lg.appendChild(el("span", "l", "longer"));
    g.appendChild(lg); z.appendChild(g);
    var list = el("div", "o-list"), perDay = {}, keys = [];
    ev.forEach(function (x) { var k = x.t.slice(0, 10); if (!perDay[k]) { perDay[k] = []; keys.push(k); } perDay[k].push(x); });
    var blocks = keys.map(function (k) {
      var b = el("div", "o-d"); b.appendChild(el("div", "o-day", dayName(k, now)));
      perDay[k].forEach(function (x) {
        var n = level(x.s);
        b.appendChild(row(n === "l" ? "down" : n === "c" ? "warn" : "", TYPE_NAMES[x.type] + " · " + hmD(new Date(x.t)), x.note || "", durS(x.s)));
      });
      list.appendChild(b); return b;
    });
    z.appendChild(list);
    fold(list, blocks.slice(1), "outages", "Show " + (blocks.length - 1) + " more day" + (blocks.length > 2 ? "s" : ""));
    $("nas-out-more").textContent = on.length ? "ongoing" : ev.length + " in 30 d";
  }

  // Scheduled tasks: "Coming up" in run order, "Continuous" folded; details on tap
  var T_OPEN = {};
  function nextLabel(iso, now) {
    if (!iso) return ["?", ""];
    var x = new Date(iso), mins = Math.round((x - now) / 60000);
    if (mins < 0) return ["running", ""];
    if (mins < 60) return [hmD(x), "in " + Math.max(1, mins) + " min"];
    if (dayKey(x) === dayKey(now)) return [hmD(x), x.getHours() < 6 ? "tonight" : "today"];
    var tm = new Date(now); tm.setDate(tm.getDate() + 1);
    if (dayKey(x) === dayKey(tm)) return [hmD(x), "tomorrow"];
    return [hmD(x), WD[x.getDay()] + " " + x.getDate() + "/" + (x.getMonth() + 1)];
  }
  function taskLine(x, now) {
    var det = el("details", "task"); if (T_OPEN[x.name]) det.open = true;
    det.addEventListener("toggle", function () { T_OPEN[x.name] = det.open; });
    var sm = el("summary"), nl = nextLabel(x.next, now), tq = el("span", "tq", nl[0]);
    if (nl[1]) tq.appendChild(el("small", null, nl[1]));
    var q = el("span", "tqq"); q.appendChild(el("span", "tn", x.name)); if (x.key) q.appendChild(el("span", "tag ok tkey", "key"));
    q.appendChild(el("span", "th", x.schedule || ""));
    sm.appendChild(tq); sm.appendChild(q); det.appendChild(sm);
    var dd = el("div", "t-detail"); dd.appendChild(el("p", null, x.desc || "No description at the top of the script."));
    var dl = el("dl");
    [["Schedule", x.schedule], ["Last run", x.last || "not seen yet"], ["Script", x.script || "inline command"]].forEach(function (p) {
      dl.appendChild(el("dt", null, p[0])); dl.appendChild(el("dd", null, p[1] || ""));
    });
    dd.appendChild(dl); det.appendChild(dd); return det;
  }
  function renderTasks(d) {
    var cr = $("nas-tasks"), ta = d.tasks || [], now = new Date(d.generated || Date.now());
    cr.innerHTML = ""; $("nas-tasks-n").textContent = ta.length;
    if (!ta.length) { cr.appendChild(el("div", "empty", "No cron files found")); return; }
    var P = ta.filter(function (x) { return x.freq !== "continuous"; }).sort(function (a, b) { return (a.next || "9") < (b.next || "9") ? -1 : 1; });
    var C = ta.filter(function (x) { return x.freq === "continuous"; }).sort(function (a, b) { return a.name.localeCompare(b.name); });
    if (P.length) {
      cr.appendChild(el("div", "t-sub", "Coming up"));
      var bv = el("div", "t-box"), lv = P.map(function (x) { var l = taskLine(x, now); bv.appendChild(l); return l; });
      cr.appendChild(bv); fold(bv, lv.slice(6), "tnext", "Show " + (lv.length - 6) + " more");
    }
    if (C.length) {
      cr.appendChild(el("div", "t-sub", "Continuous · " + C.length));
      cr.appendChild(el("p", "o-foot t-expl", "Run every minute up to every hour: data collection, watchdogs."));
      var bc = el("div", "t-box"), lc = C.map(function (x) { var l = taskLine(x, now); bc.appendChild(l); return l; });
      cr.appendChild(bc); fold(bc, lc, "tcont", "Show the " + lc.length + " continuous tasks");
    }
  }

  /* ---------------------------------------------------------------- loading */
  function render(d) {
    D = d;
    try { renderTop(d); } catch (e) { console.error("nas top", e); }
    try { renderMap(d); } catch (e) { console.error("nas map", e); }
    try { renderLower(d); } catch (e) { console.error("nas lower", e); }
    try { renderOutages(d); } catch (e) { console.error("nas outages", e); }
    try { target(); } catch (e) { console.error("nas anchor", e); }
    freshness();
  }
  // If nas-state.py stops updating the file (cron stopped, host frozen), say so at the top instead of showing the
  // old state as if it were current.
  function freshness() {
    var z = $("nas-stale"); if (!z || !D || !D.generated_ts) return;
    var age = Date.now() / 1000 - D.generated_ts, x = new Date(D.generated_ts * 1000);
    var stale = age > STALE_S;
    z.hidden = !stale; $("nas-state-g").classList.toggle("stale-g", stale);
    if (stale) z.textContent = "Data frozen since " + hmD(x) +
      (age < 5400 ? " (" + Math.round(age / 60) + " min)" : " (" + Math.round(age / 3600) + " h)") +
      ": the collector is not running, what you see here may be wrong.";
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) freshness(); });
  // deep link "#problem-<id>" highlights one row of the To do list (e.g. from a notification)
  var lastTarget = "";
  function target() {
    var h = decodeURIComponent((location.hash || "").replace(/^#/, ""));
    if (h.indexOf("problem-") !== 0 || h === lastTarget) return;
    lastTarget = h; var id = h.slice(8);
    var r = Array.prototype.slice.call(document.querySelectorAll("#nas-todo .row[data-pb]")).filter(function (x) { return x.dataset.pb === id; })[0];
    var zone = r || $("nas-todo");
    zone.classList.add("nas-target");
    zone.scrollIntoView({ block: "center", behavior: calm ? "auto" : "smooth" });
    setTimeout(function () { zone.classList.remove("nas-target"); }, 4000);
  }
  window.addEventListener("hashchange", target);
  function load() {
    fetch(SRC + (SRC.indexOf("?") < 0 ? "?" : "&") + "t=" + Date.now(), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(render)
      .catch(function () { $("nas-sub").textContent = "host data unavailable (nas-state.json)"; freshness(); });
  }
  load();
  var timer = setInterval(function () { if (!root.isConnected) { clearInterval(timer); return; } if (!document.hidden) load(); }, 30000);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(draw);
}
})();
