/* Service worker: keeps the app and the list on the phone, replays offline checks/adds as soon as the network is
   back (Background Sync), and refreshes the list when the server pushes "the list changed". */
const VERSION = 'grocery-v1';
const BASE = new URL(self.registration.scope).pathname;   // e.g. /grocery/
const SHELL = [BASE, BASE + 'index.html', BASE + 'manifest.webmanifest', BASE + 'icon-192.png', BASE + 'icon-512.png'];
const DATA = 'grocery-data';
const K_LIST = BASE + '_list', K_OPS = BASE + '_ops';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('grocery-v') && k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// App shell: cache first (instant start, even offline), then a silent update.
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.includes('/api/')) return;
  if (!url.pathname.startsWith(BASE)) return;
  e.respondWith(caches.open(VERSION).then(async c => {
    const key = url.pathname === BASE ? BASE + 'index.html' : url.pathname;
    const cached = await c.match(key);
    const network = fetch(e.request).then(r => { if (r.ok) c.put(key, r.clone()); return r; }).catch(() => null);
    if (cached) { e.waitUntil(network); return cached; }
    const r = await network;
    return r || new Response('Offline and nothing cached yet', { status: 503 });
  }));
});

// ---- data shared between page and service worker (Cache API, simple and reliable) ----
async function read(key) { const c = await caches.open(DATA); const r = await c.match(key); return r ? r.json() : null; }
async function write(key, val) { const c = await caches.open(DATA); await c.put(key, new Response(JSON.stringify(val), { headers: { 'Content-Type': 'application/json' } })); }

async function fetchT(url, opts = {}, ms = 12000) { const c = new AbortController(); const t = setTimeout(() => c.abort(), ms); try { return await fetch(url, { ...opts, signal: c.signal }); } finally { clearTimeout(t); } }
async function tellPages(msg) {
  const cs = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  cs.forEach(c => c.postMessage(msg));
}

// Replays pending operations, then refreshes the cached list.
async function sync() {
  const ops = (await read(K_OPS)) || [];
  let list;
  if (ops.length) {
    const r = await fetchT(BASE + 'api/ops', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ops }) });
    if (!r.ok) throw new Error('sync ' + r.status);
    const d = await r.json();
    // only drop what was sent (the page may have queued more meanwhile)
    const left = ((await read(K_OPS)) || []).slice(ops.length);
    await write(K_OPS, left);
    list = d.list;
  } else {
    const r = await fetchT(BASE + 'api/list');
    if (!r.ok) throw new Error('list ' + r.status);
    list = await r.json();
  }
  await write(K_LIST, list);
  await tellPages({ type: 'list', list, pendingOps: ((await read(K_OPS)) || []).length });
  return list;
}

// If the page is open and visible, it sends the ops itself (avoids a double send when the network comes back).
async function pageVisible() { const cs = await self.clients.matchAll({ type: 'window' }); return cs.some(c => c.visibilityState === 'visible'); }
self.addEventListener('sync', e => { if (e.tag === 'grocery-ops') e.waitUntil(pageVisible().then(v => v ? null : sync()).catch(err => console.log('sync later', err))); });
self.addEventListener('periodicsync', e => { if (e.tag === 'grocery-refresh') e.waitUntil(sync().catch(() => {})); });

// Push from the server: the list changed, reload it now, app open or not.
self.addEventListener('push', e => {
  let msg = {}; try { msg = e.data ? e.data.json() : {}; } catch (_) {}
  e.waitUntil((async () => {
    const before = await read(K_LIST);
    let list = null;
    try { list = await sync(); } catch (_) {}
    const cs = await self.clients.matchAll({ type: 'window' });
    const visible = cs.some(c => c.visibilityState === 'visible');
    const changed = !before || !list || before.version !== list.version;
    if (msg.type === 'test' || (changed && !visible)) {
      const n = list ? list.remaining : (msg.remaining ?? '?');
      await self.registration.showNotification('Groceries', {
        body: msg.type === 'test' ? 'Notifications work, the list is ready offline.' : n + ' item' + (n == 1 ? '' : 's') + ' to get · list updated on this phone',
        tag: 'grocery', renotify: false, silent: true, icon: BASE + 'icon-192.png', badge: BASE + 'icon-192.png', data: { url: BASE }
      });
    }
  })());
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window' }).then(cs => {
    const c = cs.find(x => x.url.includes(BASE)); return c ? c.focus() : self.clients.openWindow(BASE);
  }));
});
self.addEventListener('message', e => { if (e.data && e.data.type === 'sync') e.waitUntil(sync().catch(() => {})); });
