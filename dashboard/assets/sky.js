// Animated sky header for the Home page.
// Draws the local sky at the real time of day: sun and Moon computed (real Moon phase), bright planets, sky colours
// from day to night, drifting clouds, rain / snow / thunderstorm from the hourly forecast, windows that light up at
// dusk. The whole horizon is shown (N on the left, E, S in the middle, W, N on the right). A slider moves up to +24 h.
// The rain alert sits under the sky ("Light rain in 1 h 30 (20:00)", "No rain in the next 48 h").
// Data: weather_hours.py (cron) -> copied into data-brut by the widget, then re-read every 10 min from data-src
// (same origin as Glance, no other network call).
// The widget contains an <input type="hidden">, so live.js never replaces it (the canvas would be destroyed every
// minute otherwise); this script refreshes itself.
// The alert follows the same rule as weather_hours.py (function alert): keep both in sync.
(function () {
  "use strict";
  var RM = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  var RAD = Math.PI / 180, LAT = 50.85, LON = 4.35;   // replaced by lat/lon from the data
  var MOIS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  var JRS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, u) { return a + (b - a) * u; }
  function hx(h) { return [parseInt(h.substr(1, 2), 16), parseInt(h.substr(3, 2), 16), parseInt(h.substr(5, 2), 16)]; }
  function mix(a, b, u) { return [lerp(a[0], b[0], u), lerp(a[1], b[1], u), lerp(a[2], b[2], u)]; }
  function rgba(c, a) { return "rgba(" + (c[0] | 0) + "," + (c[1] | 0) + "," + (c[2] | 0) + "," + (a == null ? 1 : a) + ")"; }
  function prep(K) { return K.map(function (r) { return [r[0]].concat(r.slice(1).map(hx)); }); }
  function keyed(K, v) {
    if (v >= K[0][0]) return K[0].slice(1);
    for (var i = 0; i < K.length - 1; i++) {
      var a = K[i], b = K[i + 1];
      if (v <= a[0] && v >= b[0]) { var u = (a[0] - v) / (a[0] - b[0]), o = []; for (var j = 1; j < a.length; j++) o.push(mix(a[j], b[j], u)); return o; }
    }
    return K[K.length - 1].slice(1);
  }
  // sky colours by sun altitude (degrees): top, middle, horizon
  var CLAIR = prep([[30, "#3d79c6", "#73a6dd", "#b7d5f0"], [12, "#4677b8", "#86acd6", "#d6e3ee"], [5, "#45679f", "#a3a6c0", "#f0c99a"], [0.5, "#36477a", "#9c7a90", "#f2a066"], [-3, "#26315d", "#5c5379", "#c47a6c"], [-7, "#151d42", "#2b335d", "#5a5476"], [-12, "#0a1129", "#121a3a", "#1f2746"]]);
  var GRIS = prep([[30, "#8796ab", "#a5b1c1", "#c9d1db"], [5, "#6f7a92", "#9097a8", "#b7aea8"], [0.5, "#4d5675", "#77707f", "#a18376"], [-5, "#232a42", "#353548", "#4f4550"], [-12, "#11151f", "#191c28", "#2a2730"]]);
  var NUA = prep([[20, "#f5f7fb", "#c9d1dc"], [4, "#f3e3cf", "#a9a3b3"], [0, "#f0b98f", "#7d6f86"], [-4, "#8d7b93", "#4a4a68"], [-9, "#3b4260", "#272d45"], [-14, "#2c3247", "#1d2234"]]);
  var TOIT = prep([[8, "#4b5467"], [0, "#2c3042"], [-8, "#0d111b"]]);
  function skyCols(sa, cover, rain) {
    var a = keyed(CLAIR, sa), b = keyed(GRIS, sa), m = clamp(Math.pow(cover, 1.4) * 0.85 + rain * 0.12, 0, 0.95);
    return [mix(a[0], b[0], m), mix(a[1], b[1], m), mix(a[2], b[2], m)];
  }

  // ---------- astronomy (t = hours since 1970, UTC) ----------
  function jd2000(t) { return t / 24 - 10957.5; }          // days since J2000.0
  function wrapDeg(H) { return ((H + 180) % 360 + 360) % 360 - 180; }
  function lst(d) { return (280.46061837 + 360.98564736629 * d + LON) * RAD; }
  function horiz(ra, dec, d) {
    var H = lst(d) - ra, la = LAT * RAD;
    var alt = Math.asin(Math.sin(la) * Math.sin(dec) + Math.cos(la) * Math.cos(dec) * Math.cos(H));
    var az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(la) - Math.tan(dec) * Math.cos(la)) / RAD + 180;
    return { alt: alt / RAD, H: wrapDeg(H / RAD), az: az };
  }
  var cacheSun = {};
  function sun(t) {
    var k = Math.round(t * 60); if (cacheSun.k === k) return cacheSun.v;
    var d = jd2000(t), g = (357.529 + 0.98560028 * d) * RAD, q = 280.459 + 0.98564736 * d;
    var L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * RAD, e = (23.439 - 0.00000036 * d) * RAD;
    var v = horiz(Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)), Math.asin(Math.sin(e) * Math.sin(L)), d);
    v.lon = L; cacheSun = { k: k, v: v }; return v;
  }
  function sunAlt(t) { return sun(t).alt; }
  function moon(t) {
    var d = jd2000(t), L = (218.316 + 13.176396 * d) * RAD, M = (134.963 + 13.064993 * d) * RAD, F = (93.272 + 13.229350 * d) * RAD;
    var lon = L + 6.289 * RAD * Math.sin(M), lat = 5.128 * RAD * Math.sin(F), e = 23.4397 * RAD;
    var ra = Math.atan2(Math.sin(lon) * Math.cos(e) - Math.tan(lat) * Math.sin(e), Math.cos(lon));
    var dec = Math.asin(Math.sin(lat) * Math.cos(e) + Math.cos(lat) * Math.sin(e) * Math.sin(lon));
    var v = horiz(ra, dec, d), el = ((lon - sun(t).lon) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
    v.k = (1 - Math.cos(el)) / 2; v.croit = el < Math.PI; return v;
  }
  function dark(t) { return clamp((-sunAlt(t) - 4) / 9, 0, 1); }
  // bright planets at their real place (simplified orbital elements, ~1° accuracy)
  var PLA = [
    ["Venus", [76.6799, 2.46590E-5], [3.3946, 2.75E-8], [54.8910, 1.38374E-5], 0.723330, [0.006773, -1.302E-9], [48.0052, 1.6021302244], "#fffbea", 2.7, -3],
    ["Mars", [49.5574, 2.11081E-5], [1.8497, -1.78E-8], [286.5016, 2.92961E-5], 1.523688, [0.093405, 2.516E-9], [18.6021, 0.5240207766], "#ffa173", 2.1, -7],
    ["Jupiter", [100.4542, 2.76854E-5], [1.3030, -1.557E-7], [273.8777, 1.64505E-5], 5.20256, [0.048498, 4.469E-9], [19.8950, 0.0830853001], "#fff3dc", 2.5, -6],
    ["Saturn", [113.6634, 2.38980E-5], [2.4886, -1.081E-7], [339.3939, 2.97661E-5], 9.55475, [0.055546, -9.499E-9], [316.9670, 0.0334442282], "#f6dfa0", 2.1, -7]
  ];
  function orbite(N, inc, w, a, e, M) {            // heliocentric ecliptic position (AU)
    var E = M + e * Math.sin(M) * (1 + e * Math.cos(M));
    for (var k = 0; k < 5; k++) E = E - (E - e * Math.sin(E) - M) / (1 - e * Math.cos(E));
    var xv = a * (Math.cos(E) - e), yv = a * Math.sqrt(1 - e * e) * Math.sin(E), v = Math.atan2(yv, xv), r = Math.sqrt(xv * xv + yv * yv), u = v + w;
    return [r * (Math.cos(N) * Math.cos(u) - Math.sin(N) * Math.sin(u) * Math.cos(inc)), r * (Math.sin(N) * Math.cos(u) + Math.cos(N) * Math.sin(u) * Math.cos(inc)), r * Math.sin(u) * Math.sin(inc)];
  }
  var cachePla = {};
  function planetes(t) {
    var k = Math.round(t * 12); if (cachePla.k === k) return cachePla.v;   // recomputed every 5 min of sky time
    var d0 = jd2000(t), d = d0 + 1.5, ecl = (23.4393 - 3.563E-7 * d) * RAD;
    var S = orbite(0, 0, (282.9404 + 4.70935E-5 * d) * RAD, 1, 0.016709 - 1.151E-9 * d, (356.0470 + 0.9856002585 * d) * RAD);
    var v = PLA.map(function (p) {
      function f(c) { return (c[0] + c[1] * d) * RAD; }
      var h = orbite(f(p[1]), f(p[2]), f(p[3]), p[4], p[5][0] + p[5][1] * d, f(p[6])), x = h[0] + S[0], y = h[1] + S[1], z = h[2];
      var ye = y * Math.cos(ecl) - z * Math.sin(ecl), ze = y * Math.sin(ecl) + z * Math.cos(ecl);
      var o = horiz(Math.atan2(ye, x), Math.atan2(ze, Math.sqrt(x * x + ye * ye)), d0); o.nom = p[0]; o.col = p[7]; o.r = p[8]; o.nuit = p[9]; return o;
    });
    cachePla = { k: k, v: v }; return v;
  }

  // ---------- times and texts ----------
  function D(t) { return new Date(t * 3600000); }
  function hm(t) { var d = D(t + 1 / 7200), m = d.getMinutes(); return { h: d.getHours(), m: m < 10 ? "0" + m : "" + m }; }
  function fmt(t) { var x = hm(t); return x.h + ":" + x.m; }
  function dur(h) { h = Math.max(0, h); var H = Math.floor(h), M = Math.round((h - H) * 60); if (M === 60) { H++; M = 0; } return H ? H + " h " + (M < 10 ? "0" + M : M) : M + " min"; }
  function jourIdx(t) { var d = D(t); return Math.floor((d.getTime() - d.getTimezoneOffset() * 60000) / 86400000); }
  function jour(t) { var d = D(t); return JRS[d.getDay()] + " " + d.getDate() + " " + MOIS[d.getMonth()]; }
  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

  // ---------- fixed scenery (seeded) ----------
  function alea(seed) { return function () { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }; }
  var R = alea(2026), i, j;
  var STARS = []; for (i = 0; i < 110; i++) STARS.push({ x: R(), y: R() * 0.72, r: R() < 0.12 ? 1.5 : R() * 0.8 + 0.45, p: R() * 6.28, v: 0.6 + R() * 1.4 });
  var CLOUDS = []; for (i = 0; i < 16; i++) { var pf = [], n = 5 + Math.floor(R() * 3); for (j = 0; j < n; j++) pf.push({ dx: (j / (n - 1) - 0.5) * 2 * (28 + R() * 12), dy: -R() * 16 - (j > 0 && j < n - 1 ? 6 : 0), r: 12 + R() * 13 }); CLOUDS.push({ k: (i + R() * 0.8) / 16, x: R() * 1.3, y: 0.07 + R() * 0.42, sc: 0.65 + R() * 0.65, v: 0.0018 + R() * 0.0035, pf: pf }); }
  CLOUDS.sort(function (a, b) { return a.y - b.y; });
  var DROPS = []; for (i = 0; i < 90; i++) DROPS.push({ x: R(), y: R(), l: 8 + R() * 8, v: 0.55 + R() * 0.4 });
  var FLAKES = []; for (i = 0; i < 80; i++) FLAKES.push({ x: R(), y: R(), r: 0.9 + R() * 1.6, v: 0.05 + R() * 0.07, p: R() * 6.28 });
  // rooftops (virtual width 400, height in px from the bottom): x, width, height, type
  var SEGS = [[0, 30, 26, "p"], [30, 24, 34, "p"], [54, 38, 22, "f"], [92, 16, 30, "p"], [108, 34, 58, "T"], [142, 30, 28, "p"], [172, 40, 20, "f"], [212, 26, 32, "p"], [238, 22, 40, "p"], [260, 14, 94, "S"], [274, 26, 36, "p"], [300, 44, 22, "f"], [344, 28, 30, "p"], [372, 28, 24, "p"]];
  var ROOF = [], WIN = [];
  ROOF.push([0, 0]);
  SEGS.forEach(function (s) {
    var x = s[0], w = s[1], h = s[2];
    if (s[3] === "p") ROOF.push([x, h], [x + w / 2, h + w * 0.42], [x + w, h]);
    else if (s[3] === "f") ROOF.push([x, h], [x + w, h]);
    else if (s[3] === "T") { for (var k = 0; k < 5; k++) { var yy = k % 2 === 0 ? h + 7 : h; ROOF.push([x + k * w / 5, yy], [x + (k + 1) * w / 5, yy]); } }
    else ROOF.push([x, 40], [x + w * 0.18, 40], [x + w / 2, h], [x + w * 0.82, 40], [x + w, 40]);
    if ((s[3] === "p" || s[3] === "f") && h >= 22) { WIN.push({ x: x + w * 0.26, h: h - 7, a: R(), b: R(), c: R() }); if (w > 27) WIN.push({ x: x + w * 0.62, h: h - 8, a: R(), b: R(), c: R() }); }
    if (s[3] === "T") WIN.push({ x: x + w * 0.45, h: h - 18, a: R(), b: R(), c: R(), tour: 1 });
  });
  ROOF.push([400, 0]);
  function winLit(wi, t) {
    var sa = sunAlt(t), d = D(t), hh = d.getHours() + d.getMinutes() / 60, L = (hh + 12) % 24;
    if (sa > 1.5 - wi.c * 3) return false;
    if (L >= 10.6 + wi.a * 2.3 && L < 18.4 + wi.b * 1.2) return false;   // off from ~22:40-0:50 until 6:00
    return wi.tour ? (sa < -6) : true;
  }

  // clouds: softened sprites, cached by colour
  var SPR = {}, nSPR = 0;
  function sprite(ci, nc, sc, s) {
    var key = ci + "|" + nc[0].map(function (v) { return v >> 3; }).join() + "|" + nc[1].map(function (v) { return v >> 3; }).join() + "|" + Math.round(sc * 40);
    if (SPR[key]) return SPR[key]; if (nSPR > 260) { SPR = {}; nSPR = 0; }
    var c = CLOUDS[ci], minx = 1e9, maxx = -1e9, miny = 1e9, maxy = -1e9;
    c.pf.forEach(function (p) { minx = Math.min(minx, p.dx - p.r); maxx = Math.max(maxx, p.dx + p.r); miny = Math.min(miny, p.dy - p.r); maxy = Math.max(maxy, p.dy + p.r); });
    var pad = 6 * s, cw = Math.ceil((maxx - minx) * sc + pad * 2), ch = Math.ceil((maxy - miny) * sc + pad * 2), cv = document.createElement("canvas"); cv.width = cw; cv.height = ch; var x = cv.getContext("2d");
    var ox = -minx * sc + pad, oy = -miny * sc + pad, gr = x.createLinearGradient(0, pad, 0, ch - pad);
    gr.addColorStop(0, rgba(nc[0])); gr.addColorStop(0.55, rgba(mix(nc[0], nc[1], 0.45))); gr.addColorStop(1, rgba(nc[1]));
    try { x.filter = "blur(" + (1.8 * s).toFixed(1) + "px)"; } catch (e) { }
    x.fillStyle = gr; x.beginPath();
    c.pf.forEach(function (p) { var px = ox + p.dx * sc, py = oy + p.dy * sc; x.moveTo(px + p.r * sc, py); x.arc(px, py, p.r * sc, 0, 6.283); });
    x.moveTo(ox + minx * sc * 0.85, oy + maxy * sc * 0.55);
    x.ellipse(ox + (minx + maxx) / 2 * sc, oy + maxy * sc * 0.55, (maxx - minx) / 2 * sc * 0.92, Math.max(4 * s, (maxy * 0.45) * sc), 0, 0, 6.283); x.fill();
    var o = { c: cv, ox: ox, oy: oy }; SPR[key] = o; nSPR++; return o;
  }

  // ---------- one instance per [data-ciel] block ----------
  function Ciel(root) {
    var DATA = [], ACT = null, T0 = 0, T1 = 0, VIG = null, RAD = null, lastFetch = 0;

    function charger(j) {
      if (!j || !j.heures || j.heures.length < 2) return false;
      DATA = j.heures.map(function (r) { return [r[0] / 3600, r[1], r[2], r[3], r[4], r[5]]; });
      ACT = j.actuel ? { t: j.actuel.ts / 3600, temp: j.actuel.temp, nuages: j.actuel.nuages } : null;
      T0 = Math.min(DATA[0][0], ACT ? ACT.t : DATA[0][0]); T1 = DATA[DATA.length - 1][0]; VIG = j.vigilance || null; RAD = j.radar || null;
      if (typeof j.lat === "number" && typeof j.lon === "number" && (j.lat !== LAT || j.lon !== LON)) { LAT = j.lat; LON = j.lon; cacheSun = {}; cachePla = {}; }
      return true;
    }
    try { charger(JSON.parse(root.getAttribute("data-brut") || "null")); } catch (e) { }
    if (!DATA.length) return;   // nothing to draw: the widget's fallback text stays

    root.innerHTML =
      '<section class="ciel-carte" aria-label="Weather">' +
      '<div class="ciel-ciel"><canvas role="img" aria-label="Sky"></canvas><div class="ciel-scrim"></div>' +
      '<div class="ciel-haut"><div><div class="ciel-heure" data-heure></div><div class="ciel-date" data-date></div><button type="button" class="ciel-apercu" data-retour hidden></button></div>' +
      '<div class="ciel-temp"><b data-temp></b><span data-cond></span></div></div></div>' +
      // the alert sits in a bar under the sky (it would hide the rooftops otherwise)
      '<div class="ciel-barre"><div class="ciel-annonce"><span class="ciel-alerte" data-alerte></span><span class="ciel-irm" data-irm hidden></span></div></div>' +
      '<div class="ciel-curseur"><div class="ciel-bande"><canvas aria-hidden="true"></canvas></div>' +
      '<input class="ciel-range" type="range" min="0" max="24" step="0.25" value="0" aria-label="Move forward in time" data-range>' +
      '<div class="ciel-grad" data-grad aria-hidden="true"></div></div>' +
      '</section><input type="hidden" class="ciel-garde">';
    root.classList.add("ciel-pret");
    function q(sel) { return root.querySelector(sel); }
    var cv = q(".ciel-ciel canvas"), range = q("[data-range]"), bande = q(".ciel-bande canvas"), grad = q("[data-grad]"),
      retour = q("[data-retour]"), alerte = q("[data-alerte]"), irm = q("[data-irm]");
    function setTxt(el, html) { if (el && el._h !== html) { el.innerHTML = html; el._h = html; } }

    // weather at time t (interpolated), precipitation "around" t
    function idx(t) { var a = 0, b = DATA.length - 1; if (t <= DATA[0][0]) return 0; while (b - a > 1) { var m = (a + b) >> 1; if (DATA[m][0] <= t) a = m; else b = m; } return a; }
    function wx(t) {
      t = clamp(t, T0, T1);
      var i = Math.min(idx(t), DATA.length - 2), a = DATA[i], b = DATA[i + 1], u = clamp((t - a[0]) / (b[0] - a[0]), 0, 1), p = 0, ty = "r", fog = 0;
      for (var k = Math.max(0, i - 2); k < Math.min(DATA.length, i + 3); k++) {
        var d = DATA[k], f = 1 - Math.abs(t - d[0]) / 0.9;
        if (f <= 0) continue;
        if (d[3] > 0 || d[2] === "o") { var v = f * Math.min(1, 0.45 + d[3] * 3); if (v > p) { p = v; ty = d[2] === "n" ? "n" : d[2] === "o" ? "o" : "r"; } }
      }
      fog = lerp(a[2] === "f" ? 1 : 0, b[2] === "f" ? 1 : 0, u);   // fog: interpolated between the two neighbouring hours
      var temp = lerp(a[1], b[1], u), cover = lerp(a[4], b[4], u) / 100;
      if (ACT && t < DATA[0][0]) { temp = ACT.temp; cover = ACT.nuages / 100; }
      else if (ACT && t - ACT.t < 1) { var w2 = clamp(t - ACT.t, 0, 1); temp = lerp(ACT.temp, temp, w2); }
      var n = DATA[clamp(Math.round(t - DATA[0][0]) , 0, DATA.length - 1)], mm = n[3];
      // over the 2 h the (fresh) rain radar covers, the radar draws the rain, so the picture matches the text
      var R = radarFrais();
      if (R && R.jusqua && t * 3600 <= R.jusqua && ty !== "o") {
        p = 0; mm = 0;
        if (R.pluie && R.deb) {
          var rd = R.deb / 3600, rf = (R.fin || R.jusqua) / 3600, ec = t < rd ? rd - t : t > rf ? t - rf : 0, fr = 1 - ec / 0.25;
          // a few drops = sparse rain, 3 mm/h and more = full shower
          if (fr > 0) { p = fr * Math.min(1, 0.35 + R.max * 0.2); mm = R.max; if (ty !== "n") ty = "r"; }
        }
      }
      return { temp: temp, cover: Math.max(cover, p > 0 ? 0.97 : 0, fog * 0.9), wind: lerp(a[5], b[5], u), rain: ty === "r" ? p : 0, snow: ty === "n" ? p : 0, orage: ty === "o" ? p : 0, p: p, mm: mm, fog: fog };
    }
    function radarFrais() { return RAD && (Date.now() / 1000 - RAD.genere) < 2100 ? RAD : null; }
    function cond(t) {
      var w = wx(t), sa = sunAlt(t);
      if (w.orage > 0.3) return "Thunderstorm";
      if (w.snow > 0.3) return w.mm >= 1 ? "Snow" : "A few flakes";
      if (w.rain > 0.3) return w.mm >= 7.5 ? "Heavy rain" : w.mm >= 2.5 ? "Rain" : w.mm >= 0.4 ? "Light rain" : "A few drops";   // usual thresholds (light < 2.5 mm/h)
      if (w.fog > 0.5) return "Fog";
      if (w.cover > 0.92) return sa > -6 ? "Overcast" : "Overcast night";
      if (w.cover > 0.6) return sa > -6 ? "Cloudy" : "Hazy night";
      if (w.cover > 0.3) return sa > -6 ? "Partly cloudy" : "Stars between clouds";
      return sa > -6 ? (sa > 0 ? "Sunny" : "Twilight") : "Clear sky";
    }

    // ---------- drawing ----------
    function drawCloud(ctx, ci, nc, x, y, sc, s, a) { var sp = sprite(ci, nc, sc, s); ctx.globalAlpha = a; ctx.drawImage(sp.c, x - sp.ox, y - sp.oy); }
    function drawScene(t, clock) {
      var ctx = cv.getContext("2d"), W = cv.width, H = cv.height, s = cv._s || 1; if (!W || !H) return;
      var w = wx(t), sa = sunAlt(t), dk = dark(t), cover = w.cover, col = skyCols(sa, cover, w.p);
      var hz = H - 50 * s, top = 84 * s;
      function X(Hd) { return W * (0.5 + Hd / 170); }
      function XA(az) { return W * (0.5 + wrapDeg(az - 180) / 360); }   // whole horizon: N left, E, S middle, W, N right
      var PANO = true;   // whole horizon; false = older south-facing view
      function Y(a) { return hz - Math.min(a, 31) / 31 * (hz - top); }
      // Moon and planets over the whole vault (0-90°)
      function YA(a) { return hz - Math.sqrt(clamp(a, 0, 90) / 90) * (hz - 80 * s); }   // 80 px: never under the time and date
      var g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, rgba(col[0])); g.addColorStop(0.58, rgba(col[1])); g.addColorStop(1, rgba(col[2]));
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      if (dk > 0) { var gl = ctx.createLinearGradient(0, H * 0.45, 0, H); gl.addColorStop(0, "rgba(255,170,90,0)"); gl.addColorStop(1, "rgba(255,160,90," + (0.12 * dk * (0.4 + 0.6 * cover)) + ")"); ctx.fillStyle = gl; ctx.fillRect(0, H * 0.45, W, H * 0.55); }
      // stars
      var sv = dk * Math.pow(1 - cover, 1.2);
      if (sv > 0.02) { ctx.fillStyle = "#fff"; STARS.forEach(function (st) { var tw = RM ? 1 : 0.6 + 0.4 * Math.sin(clock * st.v + st.p); ctx.globalAlpha = clamp(sv * tw, 0, 1); ctx.beginPath(); ctx.arc(st.x * W, st.y * hz, st.r * s, 0, 6.283); ctx.fill(); }); ctx.globalAlpha = 1; }
      // moon (real phase; waning = mirrored drawing)
      var mo = moon(t);
      if (mo.alt > -3) {
        var mx = PANO ? XA(mo.az) : X(mo.H), my = YA(mo.alt), mr = 10 * s, k = mo.k, mv = clamp(0.35 + 0.65 * Math.max(dk, 0.25), 0, 1) * (1 - 0.55 * cover);
        var mg = ctx.createRadialGradient(mx, my, mr * 0.5, mx, my, mr * 5); mg.addColorStop(0, "rgba(220,228,255," + (0.28 * mv * k) + ")"); mg.addColorStop(1, "rgba(220,228,255,0)"); ctx.fillStyle = mg; ctx.fillRect(mx - mr * 5, my - mr * 5, mr * 10, mr * 10);
        ctx.save(); ctx.translate(mx, my); if (!mo.croit) ctx.scale(-1, 1);
        if (dk > 0.05) { ctx.globalAlpha = mv * dk; ctx.fillStyle = "rgba(60,68,92,.55)"; ctx.beginPath(); ctx.arc(0, 0, mr, 0, 6.283); ctx.fill(); }
        ctx.globalAlpha = mv; ctx.fillStyle = "#eef1f8"; ctx.beginPath(); ctx.arc(0, 0, mr, -Math.PI / 2, Math.PI / 2, false);
        if (k >= 0.5) ctx.ellipse(0, 0, mr * (2 * k - 1), mr, 0, Math.PI / 2, 3 * Math.PI / 2, false); else ctx.ellipse(0, 0, mr * (1 - 2 * k), mr, 0, Math.PI / 2, -Math.PI / 2, true);
        ctx.fill(); ctx.restore(); ctx.globalAlpha = 1;
      }
      // bright planets (clouds, drawn afterwards, pass in front)
      var vNuit = clamp((0.97 - cover) / 0.5, 0, 1);
      var pris = [], rc = cv.getBoundingClientRect();
      [q(".ciel-haut > div"), q(".ciel-temp")].forEach(function (el) {     // labels never go under the time or temperature
        if (!el || !rc.width) return; var r = el.getBoundingClientRect(), k = W / rc.width;
        pris.push([(r.left - rc.left - 4) * k, (r.top - rc.top - 2) * k, (r.right - rc.left + 4) * k, (r.bottom - rc.top + 2) * k]);
      });
      function libre(b) { return b[0] > 4 * s && b[2] < W - 4 * s && b[1] > 0 && pris.every(function (o) { return b[2] < o[0] || b[0] > o[2] || b[3] < o[1] || b[1] > o[3]; }); }
      function etiquette(txt, x, y, a) {
        ctx.font = "600 " + (10.5 * s).toFixed(1) + "px Inter, system-ui, sans-serif"; var tw = ctx.measureText(txt).width, th = 11 * s, m = 7 * s;
        var essais = [[x + m, y - th / 2], [x - m - tw, y - th / 2], [x - tw / 2, y + m * 0.9], [x - tw / 2, y - m * 0.9 - th]], k, b;
        for (k = 0; k < essais.length; k++) { b = [essais[k][0], essais[k][1], essais[k][0] + tw, essais[k][1] + th]; if (libre(b)) break; }
        if (k === essais.length) return;            // no room: the dot stays, without a label
        pris.push(b); ctx.globalAlpha = a * 0.85; ctx.fillStyle = "#fff"; ctx.shadowColor = "rgba(0,0,0,.6)"; ctx.shadowBlur = 3 * s;
        ctx.fillText(txt, b[0], b[1] + th * 0.82); ctx.shadowBlur = 0;
      }
      function point(x, y, r, c, a) {
        var gl = ctx.createRadialGradient(x, y, 0, x, y, r * 4.5); gl.addColorStop(0, rgba(hx(c), 0.45 * a)); gl.addColorStop(1, rgba(hx(c), 0)); ctx.globalAlpha = 1; ctx.fillStyle = gl; ctx.fillRect(x - r * 5, y - r * 5, r * 10, r * 10);
        ctx.globalAlpha = a; ctx.fillStyle = c; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.283); ctx.fill();
      }
      if (vNuit > 0) {
        var vus = [];
        planetes(t).forEach(function (p) {
          if (p.alt < 1 || sa > p.nuit || (!PANO && Math.abs(p.H) > 86)) return;
          var a = vNuit * clamp((p.nuit - sa) / 3, 0, 1) * clamp(p.alt / 4, 0, 1); if (a <= 0.03) return;
          var x = PANO ? XA(p.az) : X(p.H), y = YA(p.alt); point(x, y, p.r * s, p.col, a); pris.push([x - 4 * s, y - 4 * s, x + 4 * s, y + 4 * s]); vus.push([p.nom, x, y, a]);
        });
        vus.forEach(function (v) { etiquette(v[0], v[1], v[2], v[3]); });
        ctx.globalAlpha = 1;
      }
      // sun
      if (sa > -4) {
        var su = sun(t), ux = PANO ? XA(su.az) : X(su.H), uy = Y(sa), ur = 13 * s, warm = clamp((8 - sa) / 10, 0, 1), sc = mix([255, 246, 214], [255, 150, 80], warm), sv2 = (1 - 0.75 * cover) * clamp((sa + 4) / 4, 0, 1);
        var sg = ctx.createRadialGradient(ux, uy, 0, ux, uy, ur * (6 + 4 * cover)); sg.addColorStop(0, rgba(sc, 0.55 * clamp((sa + 4) / 4, 0, 1))); sg.addColorStop(1, rgba(sc, 0)); ctx.fillStyle = sg; ctx.fillRect(ux - ur * 10, uy - ur * 10, ur * 20, ur * 20);
        ctx.fillStyle = rgba(sc, sv2); ctx.beginPath(); ctx.arc(ux, uy, ur, 0, 6.283); ctx.fill();
      }
      // cloud veil + drifting clouds
      var nc = keyed(NUA, sa), vo = clamp((cover - 0.55) / 0.45, 0, 1);
      if (vo > 0) { var vg = ctx.createLinearGradient(0, 0, 0, hz); vg.addColorStop(0, rgba(nc[1], 0.5 * vo)); vg.addColorStop(1, rgba(mix(nc[1], col[2], 0.5), 0.2 * vo)); ctx.fillStyle = vg; ctx.fillRect(0, 0, W, hz); }
      CLOUDS.forEach(function (c, ci) {
        var a = clamp((cover - c.k) * 5, 0, 1); if (a <= 0) return;
        var drift = RM ? 0 : clock * c.v * (0.6 + w.wind / 20), x = (((c.x + drift) % 1.3) + 1.3) % 1.3 * W - 0.15 * W, y = c.y * hz, scl = c.sc * W / 412;
        drawCloud(ctx, ci, nc, x, y, scl, s, a * 0.92);
      });
      ctx.globalAlpha = 1;
      // fog: light veil near the rooftops
      if (w.fog > 0.05) { var fg = ctx.createLinearGradient(0, H * 0.35, 0, H); fg.addColorStop(0, "rgba(200,206,216,0)"); fg.addColorStop(1, rgba(mix(nc[1], [200, 206, 216], 0.6), 0.55 * w.fog)); ctx.fillStyle = fg; ctx.fillRect(0, H * 0.35, W, H * 0.65); }
      // snow, thunderstorm, rain
      if (w.snow > 0.02) { ctx.fillStyle = "rgba(248,250,255,.92)"; ctx.beginPath(); var nf = Math.floor(FLAKES.length * w.snow);
        for (var f2 = 0; f2 < nf; f2++) { var fl = FLAKES[f2], fy = ((fl.y + (RM ? 0 : clock * fl.v)) % 1) * H, fx = fl.x * W + (RM ? 0 : Math.sin(clock * 0.9 + fl.p) * 6 * s); ctx.moveTo(fx + fl.r * s, fy); ctx.arc(fx, fy, fl.r * s, 0, 6.283); } ctx.fill(); }
      if (w.orage > 0.3 && !RM) { var ph = clock % 5.3; if (ph < 0.09 || (ph > 0.2 && ph < 0.26)) { ctx.fillStyle = "rgba(236,232,255,.28)"; ctx.fillRect(0, 0, W, H);
        ctx.strokeStyle = "rgba(255,252,235,.95)"; ctx.lineWidth = 2.2 * s; ctx.lineJoin = "round"; ctx.beginPath(); var bx = W * 0.63, by = H * 0.16; ctx.moveTo(bx, by); ctx.lineTo(bx - 12 * s, by + 38 * s); ctx.lineTo(bx + 2 * s, by + 40 * s); ctx.lineTo(bx - 14 * s, by + 86 * s); ctx.stroke(); } }
      if (w.rain > 0.02) { ctx.strokeStyle = "rgba(205,218,238," + (0.35 + 0.3 * w.rain) + ")"; ctx.lineWidth = 1.1 * s; ctx.beginPath(); var nd = Math.floor(DROPS.length * w.rain);
        for (var q2 = 0; q2 < nd; q2++) { var dp = DROPS[q2], dy = ((dp.y + (RM ? 0 : clock * dp.v)) % 1) * H, dx = dp.x * W; ctx.moveTo(dx, dy); ctx.lineTo(dx - 2.2 * s, dy + dp.l * s); } ctx.stroke(); }
      // rooftops and windows
      var kx = W / 400; ctx.fillStyle = rgba(keyed(TOIT, sa)[0]); ctx.beginPath(); ctx.moveTo(0, H);
      ROOF.forEach(function (p) { ctx.lineTo(p[0] * kx, H - p[1] * s * 0.98); }); ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
      WIN.forEach(function (wi) { if (!winLit(wi, t)) return; var x = wi.x * kx, y = H - wi.h * s * 0.98; ctx.fillStyle = "rgba(255,196,110,.25)"; ctx.fillRect(x - 3 * s, y - 3 * s, 10 * s, 12 * s); ctx.fillStyle = "#ffd27e"; ctx.fillRect(x, y, 4 * s, (wi.tour ? 7 : 5) * s); });
      if (PANO) {
        ctx.textAlign = "center"; ctx.shadowColor = "rgba(0,0,0,.55)"; ctx.shadowBlur = 3 * s;
        [["N", 0], ["NE", 45], ["E", 90], ["SE", 135], ["S", 180], ["SW", 225], ["W", 270], ["NW", 315], ["N", 360]].forEach(function (c) {
          var big = c[0].length === 1, x = W * c[1] / 360; x = Math.min(Math.max(x, 9 * s), W - 9 * s);
          ctx.font = (big ? "700 " + (11 * s).toFixed(1) : "600 " + (9 * s).toFixed(1)) + "px Inter, system-ui, sans-serif";
          ctx.globalAlpha = big ? 0.9 : 0.55; ctx.fillStyle = "#fff"; ctx.fillText(c[0], x, H - 7 * s);
        });
        ctx.shadowBlur = 0; ctx.globalAlpha = 1; ctx.textAlign = "start";
      }
    }

    // ---------- displayed time (follows real time, or the slider) ----------
    function realT() { return Date.now() / 3600000; }
    var ST = { follow: true, abs: 0, anim: null };
    function ease(u) { return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2; }
    function cur(rt, ms) {
      if (ST.anim) { var u = clamp((ms - ST.anim.t0) / ST.anim.dur, 0, 1), to = ST.anim.toNow ? rt : ST.anim.to, v = lerp(ST.anim.from, to, ease(u)); if (u >= 1) { if (ST.anim.toNow) ST.follow = true; else { ST.follow = false; ST.abs = to; } ST.anim = null; } return v; }
      return ST.follow ? rt : ST.abs;
    }
    function goNow(ms, rt) { var from = cur(rt, ms); if (RM) { ST.follow = true; ST.anim = null; return; } ST.anim = { from: from, to: rt, toNow: true, t0: ms, dur: Math.min(2200, 600 + Math.abs(rt - from) * 110) }; }

    function maxSpan(rt) { return Math.max(1, Math.min(24, T1 - rt)); }
    function bandeDessin(rt) {
      var span = maxSpan(rt), dpr = Math.min(2, window.devicePixelRatio || 1), W = Math.round(bande.clientWidth * dpr), H = Math.round(bande.clientHeight * dpr); if (!W) return;
      if (bande.width !== W) bande.width = W; if (bande.height !== H) bande.height = H; var ctx = bande.getContext("2d");
      for (var x = 0; x < W; x += 2) { var t = rt + x / W * span, w = wx(t), c = skyCols(sunAlt(t), w.cover, w.p); ctx.fillStyle = rgba(mix(c[1], c[2], 0.35)); ctx.fillRect(x, 0, 2, H); }
      var html = "", step = 3, d0 = D(rt), h0 = d0.getHours() + d0.getMinutes() / 60, first = rt + (Math.ceil(h0 / step) * step - h0);
      for (var t2 = first; t2 < rt + span; t2 += step) { var pct = (t2 - rt) / span * 100; if (pct < 4 || pct > 96) continue; html += '<span style="left:' + pct.toFixed(1) + '%">' + D(t2 + 0.01).getHours() + ":00</span>"; }
      setTxt(grad, html); range.max = span.toFixed(2);
    }
    range.addEventListener("input", function () { var rt = realT(), v = parseFloat(range.value); ST.anim = null; if (v < 0.13) ST.follow = true; else { ST.follow = false; ST.abs = rt + v; } kick(); });
    retour.addEventListener("click", function () { goNow(performance.now(), realT()); kick(); });

    // ---------- when will it rain / snow? (same rule as weather_hours.py) ----------
        function typeP(d) { if (d[2] === "o") return "o"; if (d[3] > 0) return d[2] === "n" ? "n" : "r"; return null; }
    function episodes(rt) {
      var E = [], c = null, V = null;
      DATA.forEach(function (d) {
        var t = d[0]; if (t + 1 <= rt) return;
        var ty = typeP(d);
        if (ty && c && c.ty === ty && Math.abs(t - c.fin) < 0.02) { c.fin = t + 1; c.max = Math.max(c.max, d[3]); }
        else if (ty) { c = { ty: ty, deb: t, fin: t + 1, max: d[3] }; E.push(c); } else c = null;
        if (d[5] >= 45) { if (V && Math.abs(t - V.fin) < 0.02) { V.fin = t + 1; V.max = Math.max(V.max, d[5]); } else { V = { ty: "v", deb: t, fin: t + 1, max: d[5] }; E.push(V); } } else V = null;
      });
      return E;
    }
    function label(e) { var m = e.max; if (e.ty === "o") return m >= 6 ? "Violent thunderstorm" : "Thunderstorm"; if (e.ty === "n") return (m < 0.5 ? "Light" : m < 1.5 ? "Moderate" : "Heavy") + " snow"; return (m < 2.5 ? "Light" : m < 7.5 ? "Moderate" : "Heavy") + " rain"; }   // usual thresholds in mm/h
    function delai(h) {
      if (h < 1) { var m = Math.max(5, Math.round(h * 60 / 5) * 5); return m >= 60 ? "1 h" : m + " min"; }
      if (h < 3) { var H = Math.floor(h), M = Math.round((h - H) * 60 / 5) * 5; if (M === 60) { H++; M = 0; } return H + " h" + (M ? " " + (M < 10 ? "0" + M : M) : ""); }
      return Math.round(h) + " h";
    }
    function heureRonde(t) { return D(t + 0.5).getHours(); }
    function quand2(t, rt) {
      var h = heureRonde(t), j = jourIdx(t + 0.5) - jourIdx(rt), hn = D(rt).getHours();
      if (j === 0) return h + ":00";
      if (j === 1 && h === 0) return "midnight";
      if (j === 1 && h < 6 && hn >= 12) return "tonight " + h + ":00";
      if (j === 1) return "tomorrow " + h + ":00";
      return JRS[D(t + 0.5).getDay()] + " " + h + ":00";
    }
    var ICA = {
      r: '<path fill="currentColor" d="M12 3.5c-2.6 3.6-4.4 6.2-4.4 8.6a4.4 4.4 0 0 0 8.8 0c0-2.4-1.8-5-4.4-8.6z"/>',
      n: '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 2v18M4.2 6.5l15.6 9M4.2 15.5l15.6-9M9.5 3.5 12 6l2.5-2.5M9.5 18.5 12 16l2.5 2.5"/>',
      o: '<path fill="currentColor" d="M13.5 1 5 12.5h6L9.5 21 19 8.5h-6.2z"/>',
      sec: '<circle cx="12" cy="12" r="4.2" fill="currentColor"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M5.3 18.7l1.5-1.5M17.2 6.8l1.5-1.5"/>'
    };
    function majAlerte(rt) {
      var E = episodes(rt), P = E.filter(function (e) { return e.ty !== "v"; }), V = E.filter(function (e) { return e.ty === "v"; }), html, cl;
      // rain radar, if less than 35 min old: it wins for the next 2 h
      var R = RAD && (rt * 3600 - RAD.genere) < 2100 ? RAD : null;
      function hm(sec) { var d = new Date(sec * 1000), m = d.getMinutes(); return d.getHours() + ":" + (m < 10 ? "0" : "") + m; }
      if (R && R.jusqua && !(R.pluie && R.deb)) P = P.filter(function (x) { return x.fin * 3600 > R.jusqua; });
      if (R && R.pluie && R.deb) {
        var nr = (R.max < 2.5 ? "Light" : R.max < 7.5 ? "Moderate" : "Heavy") + " rain"; cl = "r";
        html = R.deb <= rt * 3600 + 180 ? nr + (R.fin ? " until " + hm(R.fin) : " now") : nr + " in " + delai((R.deb - rt * 3600) / 3600) + " <em>(" + hm(R.deb) + ")</em>";
      }
      else if (R && R.jusqua && P.length && P[0].deb * 3600 < R.jusqua && P[0].ty !== "o") { cl = "sec"; html = "No rain before " + hm(R.jusqua); }
      else if (!P.length) { cl = "sec"; html = "No rain in the next 48 h"; }
      else {
        var e = P[0], nom = label(e), d = e.deb - rt; cl = e.ty;
        if (d <= 0.05) html = nom + " until " + heureRonde(e.fin) + ":00";
        else if (d >= (e.ty === "o" ? 24 : 12)) { html = "No " + (e.ty === "n" ? "snow" : e.ty === "o" ? "thunderstorm" : "rain") + " before " + quand2(e.deb, rt); cl = "sec"; }
        else html = nom + " in " + delai(d) + " <em>(" + quand2(e.deb, rt) + ")</em>";
        if (e.ty === "o") { var vm = 0; DATA.forEach(function (r) { if (r[0] >= e.deb && r[0] < e.fin && r[5] > vm) vm = r[5]; }); if (vm >= 30) html += " · wind " + Math.round(vm) + " km/h"; }
        else { var v = V.filter(function (x) { return x.fin > e.deb - 1 && x.deb < e.fin + 1; })[0]; if (v) html += " · wind " + Math.round(v.max) + " km/h"; }
      }
      setTxt(alerte, '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICA[cl] + "</svg><span>" + html + "</span>");
      alerte.className = "ciel-alerte " + cl;
      if (VIG && VIG.niveau) { irm.className = "ciel-irm " + VIG.niveau; setTxt(irm, "<i></i>" + VIG.texte); irm.hidden = false; } else irm.hidden = true;
      alerte._rt = rt;
    }

    function taille(c) { var dpr = Math.min(2, window.devicePixelRatio || 1), W = Math.round(c.clientWidth * dpr), H = Math.round(c.clientHeight * dpr); if (W && (c.width !== W || c.height !== H)) { c.width = W; c.height = H; } c._s = dpr; }
    function textes(t, rt) {
      var w = wx(t), x = hm(t);
      setTxt(q("[data-heure]"), x.h + "<i>:</i>" + x.m); setTxt(q("[data-date]"), cap(jour(t))); setTxt(q("[data-temp]"), Math.round(w.temp) + "°"); setTxt(q("[data-cond]"), cond(t));
      // "Now" only shows once the slider has moved
      var ap = retour, diff = t - rt; if (diff > 0.08) { ap.hidden = false; setTxt(ap, '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/></svg>Now <em>· in ' + dur(diff) + "</em>"); } else ap.hidden = true;
      var lab = "Sky " + (diff > 0.08 ? "in " + dur(diff) + ", " : "") + jour(t) + " at " + fmt(t) + ": " + cond(t).toLowerCase() + ", " + Math.round(w.temp) + " degrees";
      if (cv._lab !== lab) { cv._lab = lab; cv.setAttribute("aria-label", lab); }
    }
    function dessiner(ms) {
      var rt = Math.max(realT(), T0); taille(cv);
      var t = clamp(cur(rt, ms), T0, T1);
      drawScene(t, ms / 1000); textes(t, rt);
      if (!bande._rt || Math.abs(bande._rt - rt) > 0.05 || !bande.width) { bande._rt = rt; bandeDessin(rt); }
      if (!alerte._rt || Math.abs(alerte._rt - rt) >= 1 / 60) majAlerte(rt);
      var val = clamp(t - rt, 0, parseFloat(range.max)); if (document.activeElement !== range || ST.anim) range.value = val;
      range.setAttribute("aria-valuetext", (val < 0.13 ? "now" : "in " + dur(val)) + ", " + jour(t) + " " + fmt(t) + ", " + Math.round(wx(t).temp) + " degrees, " + cond(t).toLowerCase());
    }

    // ---------- re-read the data every 10 min (same origin) ----------
    var src = root.getAttribute("data-src");
    function relire() {
      if (!src || Date.now() - lastFetch < 9 * 60000) return; lastFetch = Date.now();
      fetch(src + (src.indexOf("?") < 0 ? "?" : "&") + "t=" + Date.now(), { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { if (j && charger(j)) { bande._rt = 0; alerte._rt = 0; kick(); } })
        .catch(function () { });
    }
    lastFetch = Date.now();


    // ---------- loop: 30 fps max, only while on screen ----------
    var visible = true, raf = 0, lastMs = 0;
    function vivant() { return document.documentElement.contains(root); }
    function boucle(ms) { raf = 0; if (!vivant() || !visible || document.hidden) return; if (ms - lastMs >= 33) { lastMs = ms; dessiner(ms); } if (!RM || ST.anim) raf = requestAnimationFrame(boucle); }
    function kick() { if (!raf) raf = requestAnimationFrame(boucle); }
    if ("IntersectionObserver" in window) new IntersectionObserver(function (es) { visible = es[0].isIntersecting; if (visible) kick(); }).observe(root);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { relire(); kick(); } });
    var minuterie = setInterval(function () { if (!vivant()) { clearInterval(minuterie); return; } relire(); if (!document.hidden) kick(); }, 20000);
    window.addEventListener("resize", function () { bande._rt = 0; kick(); });
    kick();
  }

  function monter() {
    var els = document.querySelectorAll("[data-ciel]:not([data-ciel-ok])");
    for (var k = 0; k < els.length; k++) { els[k].setAttribute("data-ciel-ok", ""); try { Ciel(els[k]); } catch (e) { if (window.console) console.warn("sky.js", e); } }
  }
  monter();
  new MutationObserver(monter).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("glance-maj", monter);
})();
