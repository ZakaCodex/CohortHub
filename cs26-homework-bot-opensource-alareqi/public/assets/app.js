/* Mini App — المرحلة 1–4: الدخول، الربط، الزائر، الرئيسية، الصلاحيات، الإعدادات، الكاش الذكي لمنع استهلاك CDN، والنقل المباشر للبوت.
   بلا مكتبات. أول رسم من التخزين المحلي ثم يُحدَّث عند انتهاء صلاحية الكاش أو تغيّر الإصدار فقط. */
(() => {
'use strict';

const tg = window.Telegram && window.Telegram.WebApp;
const app = document.getElementById('app');
const toastEl = document.getElementById('toast');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ico = (n, cls = '') => `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;

/* ---------- هوية الجلسة ---------- */
function readInitData() {
  if (tg && tg.initData) return tg.initData;
  try { return new URLSearchParams(location.hash.slice(1)).get('tgWebAppData') || ''; } catch { return ''; }
}
const INIT = readInitData();
function userId() {
  try { return JSON.parse(new URLSearchParams(INIT).get('user')).id; } catch { return 'anon'; }
}
const UID = userId();
const DEMO = !INIT ? (/demo=(\w+)/.exec(location.hash) || [])[1] : null; // معاينة التصميم خارج تيليجرام فقط
const INITIAL_VIEW = (() => {
  try {
    const qv = new URLSearchParams(location.search).get('view');
    if (qv) return qv;
    const sp = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    return sp || '';
  } catch { return ''; }
})();
let deepLinkHandled = false;

/* ---------- تخزين محلي وكاش في الذاكرة لتقليل طلبات CDN والـ API بنسبة 90%+ ---------- */
const MEM = new Map();
const ls = {
  get(k) {
    if (MEM.has(k)) return MEM.get(k);
    try {
      const v = JSON.parse(localStorage.getItem(k));
      if (v !== null) MEM.set(k, v);
      return v;
    } catch { return null; }
  },
  set(k, v) {
    MEM.set(k, v);
    try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ممتلئ أو محجوب */ }
  },
  delPrefix(prefix) {
    const full = `bs2:${UID}:${prefix}`;
    for (const k of [...MEM.keys()]) {
      if (k.startsWith(full)) MEM.delete(k);
    }
    try {
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const k = localStorage.key(i);
        if (k && k.startsWith(full)) localStorage.removeItem(k);
      }
    } catch { /* */ }
  },
};
const BOOT_KEY = `bs2:${UID}:boot`;
const BOOT_TTL_MS = 300000; // دقيقتان لتفادي تكرار طلب bootstrap عند كل تنقل
const prefs = Object.assign({ theme: 'auto', accent: 'green' }, ls.get('bs2:prefs'));

function applyPrefs() {
  const d = document.documentElement;
  const sys = (tg && tg.colorScheme) || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  d.dataset.theme = (prefs.theme === 'auto' ? sys : prefs.theme);
  d.dataset.accent = prefs.accent;
  try {
    const bg = getComputedStyle(d).getPropertyValue('--bg').trim();
    if (tg && bg) { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); }
  } catch { /* إصدار قديم من تيليجرام */ }
}
const conn = navigator.connection || {};
if (conn.saveData || /(^|-)2g$/.test(conn.effectiveType || '')) document.documentElement.classList.add('lite');

/* ---------- الحالة ---------- */
const S = { boot: null, t: 0, stale: false, view: 'home', stack: [], flow: {}, loading: true, err: null };

/* هوية الدفعة وتوزيع المجموعات تأتي من الخادم (cohort.json + جداول المجموعات) — لا أسماء ثابتة في الواجهة */
const cohortInfo = () => (S.boot && S.boot.cohort) || {};
const theoryGroups = () => ((S.boot && S.boot.groups && S.boot.groups.theory) || []);
const practicalGroups = () => ((S.boot && S.boot.groups && S.boot.groups.practical) || []).map(g => (typeof g === 'string' ? g : g.id));
const firstTheory = () => theoryGroups()[0] || '';
const firstPractical = () => practicalGroups()[0] || '';
const cohortTitle = () => cohortInfo().title || 'الدفعة';
const lockedMsg = () => 'هذه الميزة خاصة ب' + (cohortInfo().student_noun || 'طلاب الدفعة');

/* ---------- الصلاحيات (الخادم هو الحَكَم، وهذا للإظهار فقط) ---------- */
const can = key => !!S.boot && (S.boot.perms || []).some(p => p.k === key);
const kind = () => (S.boot && S.boot.user.kind) || 'new';

const FEATURES = [
  { id: 'files',       t: 'الملفات',        i: 'folder', perm: 'files.view' },
  { id: 'quiz',        t: 'الاختبارات',     i: 'quiz',   perm: 'mcq.take' },
  { id: 'assistant',   t: 'المساعد الذكي',  i: 'chat',   perm: 'assistant.use' },
  { id: 'assignments', t: 'الواجبات',       i: 'tasks',  perm: 'assignments.submit' },
  { id: 'attendance',  t: 'حضوري',          i: 'cal',    perm: 'attendance.view_mine' },
  { id: 'sharek',      t: 'شارك',           i: 'poll',   perm: 'sharek.participate' },
  { id: 'affairs',     t: 'شؤوني',          i: 'idcard', perm: 'affairs.use' },
  { id: 'doc-assign',  t: 'واجبات الطلاب',  i: 'tasks',  perm: 'doctor.assignments', nolock: true },
];
const STAFF = [
  { id: 'a-create', t: 'إنشاء واجب',    i: 'tasks',  perm: 'assignments.create' },
  { id: 'a-notify', t: 'إرسال إشعار',   i: 'mega',   perm: 'notify.send' },
  { id: 'a-report', t: 'التقرير اليومي', i: 'report', perm: 'reports.write' },
  { id: 'a-upload', t: 'رفع ملف',       i: 'folder', perm: 'files.upload' },
  { id: 'a-mcq',    t: 'رفع اختبار',    i: 'quiz',   perm: 'mcq.upload' },
  { id: 'a-att',    t: 'جلسة الحضور',   i: 'cal',    perm: 'attendance.session' },
  { id: 'a-grv',    t: 'فتح التظلمات',  i: 'idcard', perm: 'grievances.open' },
  { id: 'a-sharek', t: 'إدارة وتجميع شارك', i: 'poll',   perm: 'sharek.publish' },
  { id: 'a-dev',    t: 'لوحة المطوّر',  i: 'shield', perm: 'roles.manage' },
];
const ALL = [...FEATURES, ...STAFF];
const ROLE_AR = { delegate: 'مندوب', deputy: 'نائب مندوب', committee: 'لجنة علمية', developer: 'مطوّر' };
const ACCENT_SWATCHES = [
  ['green', '#1D5A44'],
  ['teal', '#14625F'],
  ['blue', '#24558F'],
  ['violet', '#5B4496'],
  ['yellow', '#D4A017'],
];

/* ---------- الشبكة ---------- */
async function api(path, { method = 'GET', body, etag } = {}) {
  const maxAttempts = method === 'GET' ? 2 : 1;
  let lastErr;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 15000);
    try {
      const headers = { Authorization: `tma ${INIT}` };
      if (body) headers['Content-Type'] = 'application/json';
      if (etag) headers['If-None-Match'] = etag;
      const r = await fetch(path, {
        method,
        headers,
        body: body && JSON.stringify(body),
        signal: ctl.signal,
        cache: 'no-store',
      });
      if (r.status >= 500 && attempt + 1 < maxAttempts) {
        await new Promise(res => setTimeout(res, 350));
        continue;
      }
      const data = r.status === 304 ? null : await r.json().catch(() => null);
      return { status: r.status, data, etag: r.headers.get('ETag') };
    } catch (err) {
      lastErr = err;
      if (attempt + 1 < maxAttempts) {
        await new Promise(res => setTimeout(res, 350));
        continue;
      }
      throw lastErr;
    } finally {
      clearTimeout(timer);
    }
  }
}

function handleInitialDeepLink() {
  if (deepLinkHandled || !INITIAL_VIEW || !S.boot || kind() === 'new' || kind() === 'banned') return;
  deepLinkHandled = true;
  if (BS.openDeepLink) BS.openDeepLink(INITIAL_VIEW);
  else go(INITIAL_VIEW);
}

/* انتقال مباشر وفوري إلى محادثة البوت عند طلب إرسال ملف */
function openBotDirect(url) {
  if (url && url !== '#') {
    if (tg && tg.openTelegramLink) {
      tg.openTelegramLink(url);
      setTimeout(() => { try { tg.close(); } catch { /* */ } }, 120);
      return;
    }
    window.open(url, '_blank');
    return;
  }
  if (tg && tg.close) {
    toast('جارٍ نقلك إلى البوت…');
    setTimeout(() => { try { tg.close(); } catch { /* */ } }, 250);
  } else {
    toast('افتح محادثة البوت في تيليجرام وأرسل الملف الآن.');
  }
}

async function refresh(force = false) {
  if (DEMO) {
    S.boot = demoBoot(DEMO);
    S.loading = false;
    render();
    handleInitialDeepLink();
    return BS.afterBoot && BS.afterBoot();
  }
  const cached = ls.get(BOOT_KEY);
  if (cached && cached.data) {
    S.boot = cached.data;
    S.t = cached.t || 0;
    S.loading = false;
    render();
    handleInitialDeepLink();
    /* إذا كانت البيانات حديثة (أقل من دقيقتين) ولم يُطلب تحديث قسري، لا نرسل طلب شبكة جديدًا */
    if (!force && (Date.now() - (cached.t || 0)) < BOOT_TTL_MS) {
      if (BS.afterBoot) BS.afterBoot();
      return;
    }
  }
  if (!INIT) { S.loading = false; S.err = 'unauth'; return render(); }
  try {
    const r = await api('/api/bootstrap', { etag: force || !cached ? null : cached.etag });
    S.stale = false; S.err = null;
    if (r.status === 304 && cached) {
      S.t = Date.now();
      ls.set(BOOT_KEY, { ...cached, t: S.t });
    } else if (r.status === 200) {
      const oldVers = (cached && cached.data && cached.data.versions) || {};
      const newVers = (r.data && r.data.versions) || {};
      Object.keys(newVers).forEach(scope => {
        if (oldVers[scope] && oldVers[scope] !== newVers[scope]) ls.delPrefix(scope);
      });
      S.boot = r.data;
      S.t = Date.now();
      ls.set(BOOT_KEY, { data: r.data, etag: r.etag, t: S.t });
    } else if (r.status === 401) {
      S.err = 'unauth';
    } else {
      throw new Error('http ' + r.status);
    }
  } catch {
    S.stale = true;
    if (!S.boot) S.err = 'offline';
  }
  S.loading = false;
  handleInitialDeepLink();
  if (!S.view.startsWith('flow')) render();
  if (S.boot && BS.afterBoot) BS.afterBoot(force);
  if (force && S.boot) enter();
}

/* ---------- التنقل ---------- */
function enter() { if (window.BS && BS.enter) BS.enter(S.view); }
function go(view) { S.stack.push(S.view); S.view = view; render(); window.scrollTo(0, 0); enter(); }
function back() { S.view = S.stack.pop() || 'home'; render(); enter(); }
function tab(view) { S.stack = []; S.view = view; render(); window.scrollTo(0, 0); enter(); }
function syncBack() {
  if (!tg || !tg.BackButton) return;
  tg.BackButton[S.stack.length ? 'show' : 'hide']();
}
function toast(msg) {
  toastEl.textContent = msg; toastEl.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { toastEl.hidden = true; }, 2600);
}
const ago = t => {
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  return m < 1 ? 'الآن' : m < 60 ? `قبل ${m} دقيقة` : `قبل ${Math.round(m / 60)} ساعة`;
};

/* ---------- العرض ---------- */
function render() {
  if (S.boot && S.boot.cohort && S.boot.cohort.title) document.title = S.boot.cohort.title;
  syncBack();
  if (S.err === 'unauth') return (app.innerHTML = msgScreen('افتح التطبيق من داخل تيليجرام', 'تعذّر التحقق من حسابك. افتح التطبيق من زر «فتح التطبيق» داخل البوت.'));
  if (S.err === 'offline' && !S.boot) return (app.innerHTML = msgScreen('لا يوجد اتصال', 'تعذّر تحميل البيانات. تحقق من الإنترنت ثم أعد المحاولة.', true));
  if (!S.boot) return;
  const k = kind();
  if (k === 'banned') return (app.innerHTML = msgScreen('تعذّر الدخول', 'هذا الحساب غير مسموح له باستخدام التطبيق.'));
  if (k === 'new' || S.view.startsWith('flow')) return (app.innerHTML = renderFlow());

  const builtin = { home: renderHome, notifs: renderNotifs, more: renderMore, f: renderFeature };
  const [vn, va] = S.view.split(':');
  const body = (BS.views[vn] || builtin[vn] || renderHome)(va);

  /* شاشة المساعد الذكي والدعم تكون كاملة الشاشة بدون الـ navbar السفلي أو الـ topBar العام */
  const isSupportFull = (S.view === 'assistant' || S.view === 'af-view:sup');
  if (isSupportFull) {
    app.innerHTML = body;
    return;
  }

  const stale = S.stale ? `<div class="stale" data-act="reload" style="cursor:pointer">لا يوجد اتصال جيد — آخر تحديث ${ago(S.t)} (اضغط للتحديث)</div>` : '';

  /* شاشة الاختبارات بدون الشريط العلوي (الاختبار / رجوع) وبدون الـ navbar السفلي */
  const isQuizFull = (S.view === 'quiz' || S.view.startsWith('mcq-run'));
  if (isQuizFull) {
    app.innerHTML = stale + `<main style="padding-bottom:24px">${body}</main>`;
    return;
  }

  /* شاشات التبويبات الرئيسية (المزيد/شؤوني/الواجبات…) بلا شريط علوي: العنوان مكرر مع شريط التبويب السفلي. الرئيسية تحتفظ بشريطها (الجرس + التحديث) */
  const rootTab = !S.stack.length && S.view !== 'home';
  const hideDefaultTop = (S.view === 'files') || rootTab;
  const mainStyle = rootTab && S.view !== 'files' ? ' style="padding-top:16px"' : '';
  app.innerHTML = (hideDefaultTop ? '' : topBar()) + stale + `<main${mainStyle}>${body}</main>` + tabsBar();
}

function msgScreen(title, text, retry = false) {
  return `<main style="padding-top:72px"><div class="card empty"><b>${esc(title)}</b>${esc(text)}</div>
    ${retry ? '<button class="btn primary" style="margin-top:14px" data-act="retry">إعادة المحاولة</button>' : ''}</main>`;
}

function topBar() {
  const titles = Object.assign({ home: cohortTitle(), more: 'المزيد', notifs: 'الإشعارات' }, BS.titles);
  const fullKey = S.view;
  const baseKey = S.view.split(':')[0];
  const t = titles[fullKey] || titles[baseKey] || (ALL.find(f => 'f:' + f.id === S.view) || {}).t || cohortTitle();
  const isRoot = !S.stack.length;
  if (S.view === 'home' && isRoot) {
    const bell = `<button class="icon-btn" data-act="notifs" aria-label="الإشعارات">${ico('bell')}${S.home && S.home.unread ? `<span class="dot">${S.home.unread > 9 ? '9+' : S.home.unread}</span>` : ''}</button>`;
    const reload = `<button class="icon-btn" data-act="reload" aria-label="تحديث البيانات">${ico('refresh')}</button>`;
    return `<header class="top"><h1 style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">${esc(t)}</h1>${bell}${reload}</header>`;
  }
  const left = isRoot
    ? `<button class="icon-btn" data-act="notifs" aria-label="الإشعارات">${ico('bell')}${S.home && S.home.unread ? `<span class="dot">${S.home.unread > 9 ? '9+' : S.home.unread}</span>` : ''}</button>`
    : `<button class="back" data-act="back">${ico('back', 'sm')} رجوع</button>`;
  return `<header class="top"><h1>${esc(t)}</h1>${left}</header>`;
}

function tabsBar() {
  const item = (view, label, icon, perm) => {
    const ok = !perm || can(perm);
    const locked = !ok && can('ui.locked_preview');
    if (!ok && !locked) return '';
    const cur = (BS.tabMap[S.view.split(':')[0]] || S.view) === view;
    return `<button data-act="tab" data-view="${view}" data-locked="${locked ? 1 : 0}" ${cur ? 'aria-current="page"' : ''}>
      ${ico(icon)}<span>${label}</span>${locked ? ico('lock', 'lk') : ''}</button>`;
  };
  const items = kind() === 'doctor'
    ? [item('home', 'الرئيسية', 'home'), item('f:doc-assign', 'واجبات الطلاب', 'tasks', 'doctor.assignments'), item('more', 'المزيد', 'more')]
    : [item('home', 'الرئيسية', 'home'), item('files', 'الملفات', 'folder', 'files.view'),
       item('assignments', 'الواجبات', 'tasks', 'assignments.submit'), item('affairs', 'شؤوني', 'idcard', 'affairs.use'),
       item('more', 'المزيد', 'more')];
  return `<nav class="tabs" aria-label="التنقل الرئيسي">${items.join('')}</nav>`;
}

function tileHTML(f, lockable = true) {
  const ok = can(f.perm);
  const locked = !ok && lockable && !f.nolock && can('ui.locked_preview');   // المقفل للضيف: ميزات الطالب فقط
  if (!ok && !locked) return '';
  return `<button class="tile ${locked ? 'locked' : ''}" data-act="feature" data-id="${f.id}" data-locked="${locked ? 1 : 0}">
    ${locked ? ico('lock', 'lk') : ''}${ico(f.i)}<span>${esc(f.t)}</span></button>`;
}

function renderHome() {
  const b = S.boot, u = b.user, st = b.student;
  const first = (u.name || '').split(' ')[0] || 'بك';
  const chips = [];
  if (st) { chips.push(`<span class="chip">${esc(st.theory_group_id)}</span>`, `<span class="chip">عملي ${esc(st.practical_group_id)}</span>`); }
  if (u.kind === 'guest') chips.push('<span class="chip">زائر</span>');
  if (u.kind === 'doctor') chips.push('<span class="chip">دكتور</span>');
  (b.roles || []).forEach(r => chips.push(`<span class="chip">${ROLE_AR[r.role] || r.role}${r.group ? ' · <bdi>' + esc(r.group) + '</bdi>' : ''}</span>`));

  const now = u.kind === 'guest'
    ? `<div class="card"><b>أنت داخل كزائر</b><p class="hint" style="margin:4px 0 12px">الملفات والاختبارات والمساعد متاحة لك. باقي الميزات مقفلة حتى تربط حسابك كطالب.</p>
       <button class="btn primary" data-act="flow-link">أنا طالب: ربط حسابي</button></div>`
    : (BS.homeNow ? BS.homeNow() : '<div class="card empty"><b>لا شيء عاجل الآن</b>ستظهر هنا مواعيد التسليم القريبة وما يحتاج انتباهك.</div>');

  /* «حضوري» موجود داخل «شؤوني»، فلا يتكرر في الرئيسية لمن يملك «شؤوني» */
  const docTiles = `<button class="tile" data-act="doc-go" data-v="results">${ico('tasks')}<span>نتائج الواجبات</span></button>
    <button class="tile" data-act="doc-go" data-v="export">${ico('report')}<span>تصدير Excel</span></button>`;
  const feats = FEATURES.filter(f => !(f.id === 'attendance' && can('affairs.use')))
    .map(f => (f.id === 'doc-assign' ? (can(f.perm) ? docTiles : '') : tileHTML(f))).join('');
  const staff = STAFF.map(f => tileHTML(f, false)).join('');
  return `<section class="hero"><h2>أهلًا، ${esc(first)}</h2><div class="chips">${chips.join('')}</div></section>
    <div class="sec">يهمّني الآن</div>${now}
    <div class="sec">الأقسام</div><div class="grid">${feats}</div>
    ${staff ? `<div class="sec">الإدارة</div><div class="grid">${staff}</div>` : ''}
    <footer class="dev-credit"><div>Developed by Abdulrhman Alareqi</div><div>Contact <a href="tel:+967778917976">+967778917976</a></div></footer>`;
}

function renderFeature(id) {
  if (BS.routes[id] && BS.views[BS.routes[id]]) return BS.views[BS.routes[id]]();
  const f = ALL.find(x => x.id === id);
  if (!f) return renderHome();
  if (!can(f.perm)) return `<div class="card empty"><b>${ico('lock')}</b><b>مقفلة</b>${lockedMsg()}</div>`;
  return `<div class="card empty"><b>${esc(f.t)}</b>هذا القسم قيد البناء وسيُفتح قريبًا.</div>`;
}

function renderNotifs() {
  return `<div class="card empty"><b>لا إشعارات</b>ستصلك الإشعارات هنا، وفي البوت أيضًا.</div>`;
}

function renderMore() {
  const b = S.boot, u = b.user, st = b.student;
  const rows = [[ 'الاسم', u.name || '—' ]];
  if (st) rows.push(['الرقم الأكاديمي', `<bdi>${esc(st.academic_no)}</bdi>`], ['المجموعة النظرية', esc(st.theory_group_id)], ['المجموعة العملية', esc(st.practical_group_id)]);
  const seg = (key, opts) => `<div class="seg" role="group">${opts.map(([v, l]) => `<button data-act="pref" data-k="${key}" data-v="${v}" aria-pressed="${prefs[key] === v}">${l}</button>`).join('')}</div>`;
  const sw = ACCENT_SWATCHES
    .map(([v, c]) => `<button class="sw" style="background:${c}" data-act="pref" data-k="accent" data-v="${v}" aria-pressed="${prefs.accent === v}" aria-label="${v}">${ico('check')}</button>`).join('');
  return `<div class="card">${rows.map(([a, v]) => `<div class="row"><span>${a}</span><span>${v}</span></div>`).join('')}</div>
    ${u.kind === 'guest' ? '<button class="btn primary" style="margin-top:12px" data-act="flow-link">أنا طالب: ربط حسابي</button>' : ''}
    <div class="sec">المظهر</div>
    <div class="card">${seg('theme', [['auto', 'تلقائي'], ['light', 'نهاري'], ['dark', 'ليلي']])}
      <div class="sec" style="margin-top:16px">اللون الأساسي</div><div class="swatches">${sw}</div></div>
    ${BS.moreExtra ? BS.moreExtra() : ''}`;
}

/* ---------- الدخول: ربط الطالب / الزائر ---------- */
function renderFlow() {
  const f = S.flow, shell = inner => `<main style="padding-top:28px">${inner}</main>`;
  const err = `<div class="err" role="alert">${esc(f.err || '')}</div>`;
  if (S.view === 'flow-link') {
    if (f.step === 'confirm') {
      const d = f.data;
      return shell(`<button class="back" data-act="flow-back">${ico('back', 'sm')} رجوع</button><h2>هل هذه بياناتك؟</h2>
        <div class="card"><div class="row"><span>الاسم</span><b>${esc(d.full_name)}</b></div>
        <div class="row"><span>المجموعة النظرية</span><b>${esc(d.theory_group_id)}</b></div>
        <div class="row"><span>المجموعة العملية</span><b>${esc(d.practical_group_id)}</b></div></div>${err}
        <button class="btn primary" data-act="link-confirm" ${f.busy ? 'disabled' : ''}>نعم، هذه بياناتي</button>
        <button class="btn" data-act="flow-link">رقم آخر</button>`);
    }
    return shell(`<button class="back" data-act="flow-back">${ico('back', 'sm')} رجوع</button><h2>ربط حسابك</h2>
      <p class="hint">أدخل رقمك الأكاديمي (أرقام فقط) لنعرض لك بياناتك قبل الربط.</p>
      <input id="f" class="field" inputmode="numeric" autocomplete="off" dir="ltr" placeholder="20260001" value="${esc(f.value || '')}">${err}
      <button class="btn primary" data-act="link-lookup" ${f.busy ? 'disabled' : ''}>التالي</button>`);
  }
  if (S.view === 'flow-guest') {
    return shell(`<button class="back" data-act="flow-back">${ico('back', 'sm')} رجوع</button><h2>الدخول كزائر</h2>
      <p class="hint">اكتب اسمك. ستتصفح الملفات والاختبارات وتستخدم المساعد الذكي (5 رسائل يوميًا).</p>
      <input id="f" class="field" autocomplete="off" maxlength="40" placeholder="الاسم" value="${esc(f.value || '')}">${err}
      <button class="btn primary" data-act="guest-save" ${f.busy ? 'disabled' : ''}>دخول</button>`);
  }
  return shell(`<h2 style="font-size:24px">أهلًا بك 👋</h2><p class="hint">تطبيق ${esc(cohortTitle())}${cohortInfo().level ? ' — ' + esc(cohortInfo().level) : ''}: الملفات والواجبات والحضور وكل ما يخص الدفعة. كيف تريد الدخول؟</p>
    <button class="btn primary" data-act="flow-link">🎓 أنا طالب في الدفعة</button>
    <button class="btn" data-act="flow-guest">👀 دخول كزائر</button>`);
}

function setFlow(view, patch) { S.view = view; S.flow = patch || {}; render(); const i = document.getElementById('f'); if (i) i.focus(); }

async function flowCall(path, body, onOk) {
  S.flow.busy = true; S.flow.err = ''; render();
  try {
    const r = await api(path, { method: 'POST', body });
    S.flow.busy = false;
    if (r.status === 422) S.flow.err = 'المدخل غير صالح. راجعه وحاول مرة أخرى.';
    else if (r.status !== 200 || !r.data) S.flow.err = 'تعذّر الاتصال. حاول مرة أخرى.';
    else return onOk(r.data);
  } catch { S.flow.busy = false; S.flow.err = 'تعذّر الاتصال. حاول مرة أخرى.'; }
  render();
}

const flowFinish = async () => { S.flow = {}; S.stack = []; S.view = 'home'; await refresh(true); render(); };

/* ---------- الأحداث ---------- */
const actions = {
  retry: () => { S.err = null; refresh(true); },
  reload: async () => {
    MEM.clear();
    ['home', 'files', 'asg', 'att', 'affairs', 'ai', 'rep', 'sharek', 'mcq', 'doc'].forEach(p => ls.delPrefix(p));
    toast('جارٍ تحديث البيانات…');
    const rb = document.querySelector('.icon-btn[data-act="reload"]');
    if (rb) rb.classList.add('spin');
    if (navigator.serviceWorker) {
      try {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map(r => r.update()));
      } catch { /* */ }
    }
    await refresh(true);
    if (!S.stale) toast('تم تحديث البيانات ✅');
  },
  back: () => back(),
  notifs: () => go('notifs'),
  tab: d => (d.locked === '1' ? toast(lockedMsg()) : tab(d.view)),
  feature: d => (d.locked === '1' ? toast(lockedMsg()) : go(BS.routes[d.id] || 'f:' + d.id)),
  pref: d => { prefs[d.k] = d.v; ls.set('bs2:prefs', prefs); applyPrefs(); render(); },
  'flow-link': () => setFlow('flow-link', { step: 'input' }),
  'flow-guest': () => setFlow('flow-guest', {}),
  'flow-back': () => { S.flow = {}; S.view = 'home'; render(); },
  'link-lookup': () => {
    const v = (document.getElementById('f') || {}).value || ''; S.flow.value = v;
    if (DEMO) return toast('عرض تجريبي');
    flowCall('/api/link/lookup', { academic_no: v }, d => {
      if (d.status === 'ok') { S.flow = { step: 'confirm', data: d, no: v }; return render(); }
      if (d.status === 'already_linked') { toast('حسابك مرتبط مسبقًا ✅'); return flowFinish(); }
      S.flow.err = { not_found: 'الرقم غير موجود في قائمة الدفعة.', taken: 'هذا الرقم مرتبط بحساب آخر. تواصل مع المطوّر.',
        rate_limited: 'محاولات كثيرة. انتظر عشر دقائق.' }[d.status] || 'تعذّر التحقق.';
      render();
    });
  },
  'link-confirm': () => flowCall('/api/link/confirm', { academic_no: S.flow.no }, d => {
    if (d.status === 'ok' || d.status === 'already_linked') { toast('تم الربط ✅'); return flowFinish(); }
    S.flow.err = d.status === 'taken' ? 'هذا الرقم مرتبط بحساب آخر.' : d.status === 'rate_limited' ? 'محاولات كثيرة. انتظر عشر دقائق.' : 'تعذّر الربط.';
    render();
  }),
  'guest-save': () => {
    const v = (document.getElementById('f') || {}).value || ''; S.flow.value = v;
    if (DEMO) return toast('عرض تجريبي');
    flowCall('/api/guest', { name: v }, d => {
      if (d.status === 'ok') return flowFinish();
      S.flow.err = 'الاسم غير صالح. اكتب اسمًا من حرفين إلى 40 حرفًا.'; render();
    });
  },
};
document.addEventListener('click', e => {
  const dtBox = e.target.closest('.dt-pick');
  if (dtBox) {
    const inp = dtBox.querySelector('input');
    if (inp && e.target !== inp) {
      try { if (inp.showPicker) inp.showPicker(); else inp.focus(); } catch { inp.focus(); }
    }
    return;
  }
  const el = e.target.closest('[data-act]');
  if (el && actions[el.dataset.act]) actions[el.dataset.act](el.dataset);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.id === 'f') {
    actions[S.view === 'flow-guest' ? 'guest-save' : 'link-lookup']();
  }
});
if (tg) { try { tg.ready(); tg.expand(); tg.BackButton.onClick(() => (S.view.startsWith('flow') ? actions['flow-back']() : back())); } catch { /* */ } }

/* ---------- معاينة تجريبية (تعمل فقط حين لا يوجد initData: #demo=student|guest|delegate|developer|doctor|new) ---------- */
function demoBoot(k) {
  const P = (key, s = 'own', g = null, c = null) => ({ k: key, s, g, c });
  const base = [P('files.view','any'), P('mcq.take','any'), P('assistant.use'), P('assignments.submit'), P('sharek.participate'), P('attendance.view_mine'), P('affairs.use')];
  const grp = 'CS2_G1';
  const stu = { academic_no: '20260017', full_name: 'سارة محمد العلي', theory_group_id: grp, practical_group_id: 'G2' };
  const subjects = [
    { id: 1, name: 'نظم تشغيل', tag: 'نظم_تشغيل', doctors: { theory: [{ id: 1, name: 'د. أحمد المنصوري' }], practical: [{ id: 2, name: 'أ. طارق السامعي' }, { id: 3, name: 'أ. ريم الحكيمي' }] } },
    { id: 2, name: 'شبكات الحاسوب ٢', tag: 'شبكات_الحاسوب', doctors: { theory: [{ id: 4, name: 'د. خالد الهاشمي' }], practical: [{ id: 5, name: 'أ. سامي المقطري' }, { id: 6, name: 'أ. هدى الشامي' }] } },
    { id: 3, name: 'قواعد البيانات', tag: 'قواعد_البيانات', doctors: { theory: [{ id: 7, name: 'د. وفاء الغالبي' }], practical: [{ id: 8, name: 'أ. سلمى العريقي' }] } },
    { id: 4, name: 'ثقافة وطنية', tag: 'ثقافة_وطنية', doctors: { theory: [{ id: 9, name: 'د. فؤاد الشرعبي' }], practical: [] } }
  ];
  const groups = { theory: ['CS2_G1', 'CS2_G2', 'CS2_G3'], practical: ['G1','G2','G3','G4','G5','G6','G7','G8','G9'].map((id, i) => ({ id, theory: 'CS2_G' + (1 + Math.floor(i / 3)) })) };
  const cohort = { title: 'دفعة علوم الحاسوب 26', student_noun: 'طالب علوم الحاسوب دفعة 26', level: '' };
  const mk = (kindV, name, student, roles, perms) => ({ cohort, user: { telegram_id: 1, kind: kindV, name }, student, roles, perms, subjects, groups, versions: {}, settings: {} });
  switch (k) {
    case 'guest': return mk('guest', 'زائر تجريبي', null, [], [P('files.view','any'), P('mcq.take','any'), P('assistant.use'), P('ui.locked_preview','any')]);
    case 'doctor': return mk('doctor', 'د. أحمد المنصوري', null, [], [P('doctor.assignments')]);
    case 'new': return mk('new', '', null, [], []);
    case 'delegate': return mk('student', stu.full_name, stu, [{ role: 'delegate', group: grp }],
      [...base, P('grievances.open','group',grp), P('notify.send','group',grp), P('assignments.create','group',grp), P('assignments.manage','group',grp), P('sharek.publish','group',grp), P('attendance.session','group',grp), P('attendance.scan','group',grp), P('attendance.admin','group',grp)]);
    case 'committee': return mk('student', stu.full_name, stu, [{ role: 'committee', group: grp }],
      [...base, P('notify.send','group',grp), P('assignments.create','group',grp), P('assignments.manage','group',grp), P('files.upload','any'), P('files.delete','own'), P('mcq.upload','any'), P('reports.write','group',grp), P('sharek.publish','group',grp), P('attendance.session','group',grp,'delegate_absent'), P('attendance.scan','group',grp)]);
    case 'developer': return mk('student', stu.full_name, stu, [{ role: 'developer', group: null }],
      [...base, ...['assignments.create','assignments.manage','files.delete','notify.send','reports.write','files.upload','mcq.upload','attendance.session','attendance.scan','attendance.admin','grievances.open','sharek.publish','roles.manage','tickets.reply','doctor.assignments'].map(x => P(x, 'any'))]);
    default: return mk('student', stu.full_name, stu, [], base);
  }
}

const BS = window.BS = { S, tg, esc, ico, ls, prefs, applyPrefs, api, can, kind, go, back, tab, toast, render, refresh, actions, openBotDirect, lockedMsg, theoryGroups, practicalGroups, firstTheory, firstPractical, cohortTitle, cohortInfo, UID, DEMO, INIT, ACCENT_SWATCHES,
  views: {}, titles: {}, routes: {}, tabMap: {} };
setTimeout(() => { applyPrefs(); refresh(); }, 0);   // بعد تنفيذ features.js (defer بالترتيب)
if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('sw.js', { updateViaCache: 'all' }).catch(() => {});
})();
