/* عامل الخدمة:
   - /assets/* المدموغة بـ ?v=hash: Cache-First دائمًا (صفر طلبات بعد أول تحميل).
   - الصفحة الرئيسية (index.html): Network-First مع احتياطي فوري من الكاش عند انقطاع الشبكة، لضمان ظهور أي تحديث جديد فور رفعه.
   - لا يلمس /api أبدًا. */
const V = 'bs2-shell-4dd93192';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(V).then(c => c.add('./').catch(() => {})).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/api/')) return;
  if (u.pathname.startsWith('/assets/')) {
    e.respondWith(caches.open(V).then(async c => {
      const hit = await c.match(r);
      if (hit) return hit;
      const res = await fetch(r);
      if (res && res.ok) c.put(r, res.clone());
      return res;
    }));
    return;
  }
  e.respondWith(caches.open(V).then(async c => {
    try {
      const res = await fetch(r, { cache: 'no-store' });
      if (res && res.ok) c.put(r, res.clone());
      return res;
    } catch {
      return (await c.match(r)) || (await c.match('./')) || Response.error();
    }
  }));
});
