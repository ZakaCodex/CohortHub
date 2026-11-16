/* المرحلة 2 و 3 في الواجهة:
   - المرحلة 2: الملفات (بالتصميم المعتمد مع شريط البحث المدمج بجوار كلمة «الملفات» في الأعلى وفلترة فورية محلية 0ms)، الواجبات (مع فلترة مفتوحة/مسلّمة/فائتة وإمكانية تعديل إرسال الملف من التطبيق والانتقال المباشر للبوت)، رفع ملف، إنشاء واجب، الإشعارات، الإعدادات.
   - المرحلة 3: الحضور النظري، شؤوني (بطاقات فقط في الرئيسية وتفتح كل خدمة في شاشة كاملة مستقلة)،
                فتح التظلمات وتجميعها، وشاشة الدعم والمساعد الذكي الكاملة بالتصميم المعتمد (58918.jpg) بدون شريط التنقل السفلي أثناء المحادثة.
   تعتمد على BS من app.js مع كاش ذكي بـ TTL لمنع استهلاك طلبات CDN. */
(() => {
'use strict';
const B = window.BS, S = B.S, esc = B.esc, ico = B.ico, can = B.can;

/* ---------- ثوابت ونصوص ---------- */
const PART = { theory: 'نظري', practical: 'عملي' };
const TYPES = [['lecture', 'محاضرة'], ['summary', 'ملخص'], ['exam', 'نموذج'], ['exercises', 'تمارين'], ['translation', 'ترجمة'], ['reference', 'مراجع'], ['benefit', 'فائدة']];
const LABELS = { lecture: ['المحاضرة', 'رقم المحاضرة', 'محاضرة'], lecture_en: ['Lecture', 'رقم الـ Lecture', 'Lecture'], lab: ['Lab', 'رقم الـ Lab', 'Lab'], module: ['Module', 'رقم الـ Module', 'Module'], chapter: ['Chapter', 'رقم الـ Chapter', 'Chapter'] };
const isLectureLabel = k => k === 'lecture' || k === 'lecture_en';
const NEEDS_NUMBER = ['summary', 'exercises', 'translation'];
const STATE = { open: ['مفتوح', 'ok'], submitted: ['مسلَّم', 'ok'], late: ['متأخر', 'warn'], missed: ['فاته', 'bad'] };
const TICKET_CATS = [['bug', 'بلاغ عطل'], ['complaint', 'شكوى'], ['inquiry', 'استفسار']];
const ERR = {
  number_required: 'اختر رقمًا (مطلوب لهذا النوع).', label_required: 'اختر نوع الرقم.', exam_kind_required: 'حدّد نصفي أو نهائي.',
  number_out_of_range: 'الرقم خارج النطاق (المحاضرات حتى 12، والباقي حتى 20).', due_in_past: 'يجب أن يكون الموعد بعد خمس دقائق على الأقل.',
  closes_in_past: 'يجب أن يكون الموعد بعد خمس دقائق على الأقل.',
  invalid_due: 'الموعد غير صالح.', invalid_group: 'المجموعة غير صالحة.', invalid_subject: 'اختر المادة.', due_too_far: 'الموعد بعيد جدًا.',
  no_draft: 'انتهت المسودة. ابدأ من جديد.', invalid_deadline: 'الموعد غير صالح.',
  group_has_open_session: 'توجد جلسة حضور مفتوحة حاليًا لهذه المجموعة. أغلقها أولًا.',
  lecture_already_closed: 'هذه المحاضرة مسجّلة ومغلقة سابقًا (المندوب أو المطوّر فقط يستطيع إعادة فتحها).',
  session_not_found: 'لم تُعقد جلسة لهذه المحاضرة بعد. افتح الجلسة وأغلقها أولًا قبل التصحيح.',
  reason_required: 'اكتب سبب التصحيح (حرفين على الأقل).',
  reason_too_short: 'اكتب سبب التظلم بوضوح (3 أحرف على الأقل).',
  body_too_short: 'اكتب تفاصيل البلاغ (4 أحرف على الأقل).',
  daily_limit_reached: 'استنفدت الحد اليومي المسموح لهذا الإجراء.',
  student_not_found: 'الرقم الأكاديمي غير موجود في قائمة الدفعة.',
  invalid_academic_no: 'أدخل رقمًا أكاديميًا صالحًا.',
  no_submission_files: 'لا توجد ملفات محفوظة لهذا التسليم.',
  session_not_open: 'هذه الجلسة لم تعد مفتوحة.',
  session_has_history: 'لا يمكن إلغاء هذه الجلسة لأنها أُغلقت سابقًا أو فيها غياب/تصحيحات معتمدة. استخدم «تصحيح الحضور» بدلًا من ذلك.',
};
const failMsg = r => (r.status === 403 ? 'لا تملك صلاحية لهذا الإجراء.' : r.status === 0 ? 'لا يوجد اتصال. حاول مرة أخرى.' : ERR[r.data && (r.data.detail || r.data.status)] || 'تعذّر الحفظ. راجع المدخلات.');

const fmtDT = iso => new Intl.DateTimeFormat('ar-u-nu-latn', { timeZone: 'Asia/Aden', weekday: 'long', day: 'numeric', month: 'long', hour: 'numeric', minute: '2-digit', hour12: true }).format(new Date(iso));
const fmtD = iso => new Intl.DateTimeFormat('ar-u-nu-latn', { timeZone: 'Asia/Aden', day: 'numeric', month: 'long' }).format(new Date(iso));
const fmtTime = iso => {
  try {
    return new Intl.DateTimeFormat('ar-u-nu-latn', { timeZone: 'Asia/Aden', hour: 'numeric', minute: '2-digit', hour12: true }).format(iso ? new Date(iso) : new Date());
  } catch { return ''; }
};
const left = iso => {
  const m = Math.round((new Date(iso) - Date.now()) / 60000);
  if (m <= 0) return 'انتهى الموعد';
  if (m < 60) return `يتبقى ${m} دقيقة`;
  const h = Math.round(m / 60);
  return h < 48 ? `يتبقى ${h} ساعة` : `يتبقى ${Math.round(h / 24)} يومًا`;
};
const isOpenForSubmit = a => {
  if (!a) return false;
  if (typeof a.can_submit === 'boolean') return a.can_submit;
  const st = a.status_db || a.status;
  const closes = a.closes_at || a.due_at;
  return st === 'open' && (!closes || new Date(closes) > Date.now());
};
const subjName = id => ((S.boot.subjects || []).find(s => s.id === id) || {}).name || '';
const chip = (t, cls = '') => `<span class="chip ${cls}">${esc(t)}</span>`;
const pill = (label, act, data, on) => `<button class="pill" data-act="${act}" ${data} aria-pressed="${!!on}">${esc(label)}</button>`;
const seg = (act, opts, cur) => `<div class="seg" role="group">${opts.map(([v, l]) => `<button data-act="${act}" data-v="${v}" aria-pressed="${cur === v}">${esc(l)}</button>`).join('')}</div>`;
const skel = n => Array.from({ length: n }, () => '<div class="sk sk-row"></div>').join('');
const empty = (t, s) => `<div class="card empty"><b>${esc(t)}</b>${esc(s || '')}</div>`;
const ask = (msg, ok) => (B.tg && B.tg.showConfirm ? B.tg.showConfirm(msg, y => y && ok()) : window.confirm(msg) && ok());
const rerender = () => { if (!S.view.startsWith('flow')) B.render(); };

/* أيقونات SVG مخصصة لتصميم الملفات والمساعد الذكي */
const F_ICONS = {
  spark: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/><path d="M19 16l.9 2.1L22 19l-2.1.9L19 22l-.9-2.1L16 19l2.1-.9L19 16z"/></svg>',
  search: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
  grid: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/></svg>',
  lecture: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M7 12l3-3 2 2 4-4"/></svg>',
  summary: '<svg viewBox="0 0 24 24"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>',
  exam: '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
  exercises: '<svg viewBox="0 0 24 24"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  translation: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
  reference: '<svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
  benefit: '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V18h6v-1.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/></svg>',
  plane: '<svg viewBox="0 0 24 24"><path d="M3 11.5l18-8-8 18-2.5-7.5L3 11.5z"/><path d="M10.5 14L21 3.5"/></svg>',
  headset: '<svg viewBox="0 0 24 24"><path d="M4 13v-1a8 8 0 0 1 16 0v1"/><rect x="3" y="13" width="4" height="6" rx="2"/><rect x="17" y="13" width="4" height="6" rx="2"/><path d="M19 19a3 3 0 0 1-3 3h-3"/></svg>',
  arrowRight: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  db: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="M7 10v12M15 5.88L14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88z"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M17 14V2M9 18.12L10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88z"/></svg>',
};

/* أزرار واضحة لاختيار التاريخ من التقويم والوقت من الساعة */
function dtPickerHTML(dateField, dateVal, timeField, timeVal) {
  return `<div class="dt-grid">
    <div class="dt-pick">
      <div class="dt-pick-head">📅 اختر التاريخ من التقويم</div>
      <input type="date" data-f="${dateField}" value="${esc(dateVal || '')}">
    </div>
    <div class="dt-pick">
      <div class="dt-pick-head">⏰ اختر الوقت من الساعة</div>
      <input type="time" data-f="${timeField}" value="${esc(timeVal || '20:00')}">
    </div>
  </div>`;
}

/* ---------- الشبكة والكاش الذكي بـ TTL (يمنع تكرار الطلبات لـ Vercel CDN) ---------- */
const SWR_TTL_MS = 120000; // دقيقتان
async function call(path, method = 'GET', body) {
  if (B.DEMO) return { status: 200, data: demoWrite(path, method, body) };
  try { return await B.api(path, { method, body }); } catch { return { status: 0, data: null }; }
}
async function swr(key, path, apply, force = false, ttlMs = SWR_TTL_MS) {
  const k = `bs2:${B.UID}:${key}`, c = B.ls.get(k);
  if (c && c.data !== undefined) {
    apply(c.data, true);
    if (!force && (Date.now() - (c.t || 0)) < ttlMs) return;
  }
  if (B.DEMO) return apply(demoRead(path), false);
  try {
    const r = await B.api(path, { etag: (!force && c) ? c.etag : null });
    if (r.status === 200) { B.ls.set(k, { data: r.data, etag: r.etag, t: Date.now() }); apply(r.data, false); }
    else if (r.status === 304 && c) { B.ls.set(k, { ...c, t: Date.now() }); apply(c.data, false); }
    else if (!c) apply(null, false);
  } catch { if (!c) apply(null, false); }
}
B.swr = swr;

/* ---------- الحالة ---------- */
const P = S.p2 = {
  f: { subject: '', part: '', type: '', allItems: null, q: '', smart: null, searching: false },
  a: { tab: 'mine', filter: 'all', mine: null, staff: null },
  d: {}, r: {}, n: null, set: null, home: null,
  u: null, w: null, sd: { tab: 'sub', ext: false },
  /* المرحلة 3 */
  att: { mine: null },
  affairs: { tab: 'grv', subs: null, bar: null, grv: null, editId: null, reason: '', busy: false, err: '' },
  ai: { info: null, q: '', pendingQ: '', busy: false, err: '', showTicket: false, tCat: 'inquiry', tBody: '', tBusy: false, tickets: null, staffTab: 'chat', staffTickets: null, replyId: null, replyText: '', reactions: {} },
  attAdm: { tab: 'live', group: '', subject_id: null, lecture_no: 1, delegate_absent: false, data: null, sessDetail: null, scanCode: '', lastScan: null, devLabel: '', newKey: null, cSubj: null, cLec: 1, cNo: '', cStatus: 'present', cReason: '', busy: false, err: '' },
  grvAdm: { list: null, detail: {}, form: null, busy: false, err: '' },
};

/* حفظ المسودات محليًا تلقائيًا (الواجب، التظلم، البلاغ) لاستكمالها بعد انقطاع الإنترنت */
const draftKey = k => `bs2:${B.UID}:draft:${k}`;
const setPath = (path, v) => {
  if (path.startsWith('grvAdmForm.') && P.grvAdm.form) {
    P.grvAdm.form[path.split('.')[1]] = v;
    return;
  }
  const [o, k] = path.split('.');
  (P[o] || (P[o] = {}))[k] = v;
  if (path === 'w.description') B.ls.set(draftKey('asg_desc'), v);
  if (path === 'affairs.reason' && P.affairs.editId) B.ls.set(draftKey('grv_' + P.affairs.editId), v);
  if (path === 'ai.tBody') B.ls.set(draftKey('ticket_body'), v);
};
document.addEventListener('input', e => {
  const f = e.target.dataset && e.target.dataset.f;
  if (!f) return;
  setPath(f, e.target.value);
  const pv = document.querySelector('.preview');
  if (pv && f.startsWith('u.') && P.u) pv.textContent = captionPreview(P.u);
});

/* ---------- التوجيه المباشر من إشعارات البوت أو مركز الإشعارات ---------- */
B.openDeepLink = rawView => {
  const v = String(rawView || '').trim();
  if (!v) return;
  if (v === 'affairs-grv') { B.tab('affairs'); B.go('af-view:grv'); }
  else if (v === 'affairs-att') { B.tab('affairs'); B.go('af-view:att'); }
  else if (v === 'affairs-bar') { B.tab('affairs'); B.go('af-view:bar'); }
  else if (v === 'affairs-sub') { B.tab('affairs'); B.go('af-view:sub'); }
  else if (v === 'affairs') { B.tab('affairs'); }
  else if (v === 'assistant-staff') { P.ai.staffTab = 'staff'; B.go('assistant'); }
  else if (v === 'assistant') { P.ai.staffTab = 'chat'; B.go('assistant'); }
  else if (v === 'files') { B.tab('files'); }
  else if (v === 'assignments') { B.tab('assignments'); }
  else { B.go(v); }
};

/* ---------- الرئيسية: «يهمّني الآن» ---------- */
B.afterBoot = (force = false) => {
  if (!can('assignments.submit') && !can('affairs.use') && !can('sharek.participate')) return;
  swr('home', '/api/home', d => { if (d) { S.home = d; P.home = d; if (S.view === 'home') rerender(); } }, force);
};
B.homeNow = () => {
  const h = P.home || {};
  const items = (h.items || []).filter(a => !a.submitted_at && isOpenForSubmit(a));
  const grvs = h.grievances || [];
  const shList = h.sharek || ((S.p4 && S.p4.sh && S.p4.sh.list) || []).filter(c =>
    (c.kind === 'poll' && (c.my_vote === null || c.my_vote === undefined)) ||
    (c.kind === 'submission' && !c.is_finished)
  );
  /* إعلانات المندوب واللجنة: تظهر ٢٤ ساعة فقط من وقت إرسالها (نتحقق هنا أيضًا لأن الرئيسية تُخزَّن مؤقتًا) */
  const anns = (h.announcements || []).filter(x => x.created_at && (Date.now() - new Date(x.created_at).getTime()) < 24 * 3600e3);
  const cards = [];

  anns.forEach(x => {
    cards.push(`<button class="item" data-act="go-notifs"><div class="item-main"><b>📣 ${esc(x.title || 'إعلان')}</b>
      <p class="desc" style="margin:4px 0 0;white-space:pre-line;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">${esc(x.body || '')}</p>
      <div class="meta">${chip('إعلان', 'warn')}${chip(x.grp || 'الجميع')}</div></div>
      <div class="item-side"><small>${fmtDT(x.created_at)}</small></div></button>`);
  });

  items.forEach(a => {
    const urgent = (new Date(a.due_at) - Date.now()) < 24 * 3600e3;
    cards.push(`<button class="item" data-act="asg" data-id="${a.id}"><div class="item-main"><b>📝 ${esc(a.subject)} · واجب ${a.number}</b>
      <div class="meta">${chip(PART[a.part])}${chip(a.grp)}</div></div>
      <div class="item-side ${urgent ? 'warn-t' : ''}">${left(a.due_at)}</div></button>`);
  });

  grvs.forEach(g => {
    cards.push(`<button class="item" data-act="go-grv" data-id="${g.id}"><div class="item-main"><b>⚖️ تظلم مفتوح: ${esc(g.subject)}</b>
      <div class="meta">${chip(PART[g.part])}${g.submitted ? chip('قدّمت تظلمك (قابل للتعديل)', 'ok') : chip('متاح للتقديم', 'warn')}</div></div>
      <div class="item-side">${left(g.closes_at)}</div></button>`);
  });

  shList.forEach(c => {
    const kindLbl = c.kind === 'poll' ? '📊 استفتاء مفتوح' : '📥 مشاركة مفتوحة';
    cards.push(`<button class="item" data-act="go-sharek" data-id="${c.id}"><div class="item-main"><b>🗳 ${esc(c.title)}</b>
      <div class="meta">${chip(kindLbl, 'ok')}${chip(c.grp || 'الجميع')}</div></div>
      <div class="item-side"><small>شارك الآن</small></div></button>`);
  });

  if (!cards.length) return empty('لا شيء عاجل الآن', 'ستظهر هنا الواجبات والتظلمات والمشاركات المفتوحة وإعلانات اليوم.');
  return cards.join('');
};

/* =====================================================================
   الملفات (شريط علوي مدمج يجمع كلمة «الملفات» مع شريط وأيقونة البحث مباشرة، بدون أي عناوين مكررة)
   ===================================================================== */
function loadFiles(force = false) {
  const ver = (S.boot && S.boot.versions && S.boot.versions.files) || 0;
  swr(`files:all:v${ver}`, '/api/files?limit=80', d => {
    P.f.allItems = Array.isArray(d) ? d : [];
    rerender();
  }, force);
}
function filteredFiles() {
  const f = P.f;
  if (f.allItems === null) return null;
  const qNorm = (f.q || '').trim().toLowerCase();
  return f.allItems.filter(it => {
    if (f.subject !== '' && String(it.subject_id) !== String(f.subject)) return false;
    if (f.part && it.part !== f.part) return false;
    if (f.type && it.content_type !== f.type) return false;
    if (qNorm && f.smart === null) {
      const hay = `${it.title || ''} ${it.subject || subjName(it.subject_id)} ${it.description || ''}`.toLowerCase();
      if (!hay.includes(qNorm)) return false;
    }
    return true;
  });
}
function fileCard(it) {
  const ctype = it.content_type || 'lecture';
  const iconSvg = F_ICONS[ctype] || F_ICONS.lecture;
  const partCls = it.part === 'practical' ? 'practical' : 'theory';
  const partLbl = PART[it.part] || 'نظري';
  const subj = it.subject || subjName(it.subject_id);
  const dateStr = it.created_at ? fmtD(it.created_at) : '';
  const hasAdmin = (it.mine && can('files.upload')) || it.can_delete;

  return `<div class="f-card" style="flex-wrap:wrap">
    <div style="display:flex;align-items:center;gap:13px;width:100%">
      <div class="f-icon-box ${esc(ctype)}">${iconSvg}</div>
      <div class="f-card-body">
        <b class="f-card-title">${esc(it.title)}</b>
        <div class="f-card-meta">
          <span class="f-part-tag ${partCls}">${esc(partLbl)}</span>
          <span>${esc(subj)}${dateStr ? ' · ' + esc(dateStr) : ''}</span>
        </div>
        ${it.description ? `<p class="desc" style="margin:4px 0 0;font-size:13px">${esc(it.description)}</p>` : ''}
      </div>
      <button class="f-send-circle" data-act="f-send" data-id="${it.id}" aria-label="إرسال في البوت" title="أرسل لي في البوت">${F_ICONS.plane}</button>
    </div>
    ${hasAdmin ? `<div class="f-card-admin" style="width:100%">
      ${it.mine && can('files.upload') ? `<button class="btn sm" style="min-height:34px;font-size:12.5px" data-act="f-replace" data-id="${it.id}">استبدال</button>` : ''}
      ${it.can_delete ? `<button class="btn sm danger" style="min-height:34px;font-size:12.5px" data-act="f-del" data-id="${it.id}">حذف</button>` : ''}
    </div>` : ''}
  </div>`;
}
B.views.files = () => {
  const f = P.f, subs = S.boot.subjects || [];
  const items = f.smart !== null ? f.smart : filteredFiles();

  const topSearchHeader = `<div class="f-top-bar">
    ${S.stack.length ? `<button class="f-top-back" data-act="back" aria-label="رجوع">${ico('back', 'sm')}</button>` : ''}
    <h1 class="f-top-title">الملفات</h1>
    <div class="f-search-box">
      <span class="f-search-spark">${F_ICONS.spark}</span>
      <input data-f="f.q" placeholder="ابحث أو اسأل: ملخص Module 9..." value="${esc(f.q)}">
      ${(f.q || f.smart !== null) ? `<button class="f-search-clear" data-act="f-smart-clear" aria-label="مسح">✕</button>` : ''}
    </div>
    <button class="f-search-btn" data-act="f-smart-go" aria-label="بحث ذكي" ${f.searching ? 'disabled' : ''}>${F_ICONS.search}</button>
  </div>`;

  if (f.smart !== null) {
    const sList = !f.smart.length
      ? empty('لم أجد ملفًا مطابقًا', 'جرّب كتابة اسم المادة أو نوع الملف مثل: ملخص شبكات.')
      : f.smart.map(fileCard).join('');
    return `${topSearchHeader}
      <div style="display:flex;justify-content:space-between;align-items:center;margin:4px 2px 10px">
        <b style="font-size:14.5px">✨ نتائج البحث الذكي (${f.smart.length})</b>
        <button class="btn sm" style="width:auto;min-height:32px;padding:0 12px;font-size:12.5px" data-act="f-smart-clear">عرض كل الملفات</button>
      </div>
      ${sList}
      ${can('files.upload') ? `<button class="fab" data-act="f-upload">${ico('folder', 'sm')} رفع ملف</button>` : ''}`;
  }

  const subjPills = [
    `<button class="f-subj-pill" data-act="f-subj" data-id="" aria-pressed="${f.subject === ''}">كل المواد</button>`,
    ...subs.map(s => `<button class="f-subj-pill" data-act="f-subj" data-id="${s.id}" aria-pressed="${String(f.subject) === String(s.id)}">${esc(s.name)}</button>`),
  ].join('');

  const typePills = [
    `<button class="f-type-pill" data-act="f-type" data-id="" aria-pressed="${f.type === ''}">${F_ICONS.grid}<span>الكل</span></button>`,
    ...TYPES.map(([v, l]) => `<button class="f-type-pill" data-act="f-type" data-id="${v}" aria-pressed="${f.type === v}">${F_ICONS[v] || F_ICONS.lecture}<span>${esc(l)}</span></button>`),
  ].join('');

  const partSeg = `<div class="f-seg" role="group">
    <button data-act="f-part" data-v="" aria-pressed="${f.part === ''}">الكل</button>
    <button data-act="f-part" data-v="theory" aria-pressed="${f.part === 'theory'}">نظري</button>
    <button data-act="f-part" data-v="practical" aria-pressed="${f.part === 'practical'}">عملي</button>
  </div>`;

  const list = items === null ? skel(4)
    : !items.length ? empty('لا توجد ملفات مطابقة', 'جرّب اختيار مادة أو نوع آخر.')
    : items.map(fileCard).join('');

  return `${topSearchHeader}
    <div class="f-sec-lbl">المادة</div>
    <div class="scroll-x">${subjPills}</div>
    <div class="f-sec-lbl">نوع الملف</div>
    <div class="scroll-x">${typePills}</div>
    ${partSeg}
    <div>${list}</div>
    ${can('files.upload') ? `<button class="fab" data-act="f-upload">${ico('folder', 'sm')} رفع ملف</button>` : ''}`;
};
Object.assign(B.actions, {
  /* الفلترة فورية محليًا في 0ms دون أي طلب شبكة */
  'f-subj': d => { P.f.subject = d.id; rerender(); },
  'f-part': d => { P.f.part = d.v; rerender(); },
  'f-type': d => { P.f.type = d.id; rerender(); },
  'f-smart-go': async () => {
    const inp = document.querySelector('[data-f="f.q"]');
    if (inp) P.f.q = inp.value;
    const q = (P.f.q || '').trim();
    if (!q) return B.toast('اكتب ما تبحث عنه أولًا.');
    P.f.searching = true; rerender();
    const r = await call('/api/files/smart-search', 'POST', { query: q });
    P.f.searching = false;
    if (r.status === 200 && r.data) P.f.smart = r.data.items || [];
    else B.toast('تعذّر البحث الآن.');
    rerender();
  },
  'f-smart-clear': () => { P.f.smart = null; P.f.q = ''; rerender(); },
  'f-send': async d => {
    const r = await call(`/api/files/${d.id}/send`, 'POST');
    B.toast(r.status === 200 ? 'أُرسل الملف إلى محادثتك مع البوت ✅' : r.status === 0 ? 'لا يوجد اتصال.' : 'تعذّر الإرسال. افتح البوت أولًا ثم أعد المحاولة.');
  },
  'f-del': d => ask('حذف هذا الملف؟ يُحذف من المجتمع أيضًا ويبقى في السلة 30 يومًا.', async () => {
    const r = await call(`/api/files/${d.id}`, 'DELETE');
    if (r.status === 200) {
      P.f.allItems = (P.f.allItems || []).filter(x => String(x.id) !== d.id);
      B.ls.delPrefix('files');
      B.toast('تم الحذف');
      rerender();
    } else B.toast(failMsg(r));
  }),
  'f-upload': () => { P.u = newUpload(); B.go('upload'); },
  'f-replace': d => {
    const it = (P.f.allItems || []).find(x => String(x.id) === d.id);
    if (!it) return;
    P.u = Object.assign(newUpload(), { subject_id: it.subject_id, part: it.part, content_type: it.content_type, exam_kind: it.exam_kind || '',
      label_kind: it.label_kind || 'lecture', numbers: it.numbers || [], description: it.description || '', replaces_id: it.id });
    B.go('upload');
  },
});

/* ---------- رفع ملف: ينقل المستخدم مباشرة إلى البوت فور الحفظ ---------- */
const newUpload = () => {
  const last = B.ls.get(`bs2:${B.UID}:lastfile`) || {};
  return { subject_id: last.subject_id || B.ls.get(`bs2:${B.UID}:lastsubj`) || null, part: last.part || 'theory', doctor_id: last.doctor_id || null,
    content_type: last.content_type || 'lecture', exam_kind: '', label_kind: last.label_kind || 'lecture', numbers: [], description: '', replaces_id: null, busy: false, err: '' };
};
const hashtag = t => String(t || '').trim().replace(/[^\p{L}\p{N}_]+/gu, '_').replace(/^_+|_+$/g, '');
function uploadDoctors(u) {
  const s = (S.boot.subjects || []).find(x => x.id === u.subject_id);
  return (s && s.doctors && s.doctors[u.part]) || [];
}
function uploadError(u) {
  if (!u.subject_id) return 'اختر المادة.';
  const docs = uploadDoctors(u);
  if (docs.length > 1 && !docs.some(d => d.id === u.doctor_id)) return 'اختر مدرس المقرر.';
  if (u.content_type === 'exam' && !u.exam_kind) return 'حدّد نصفي أو نهائي.';
  if (u.content_type !== 'exam' && NEEDS_NUMBER.includes(u.content_type) && !u.numbers.length) return 'اختر رقمًا (مطلوب لهذا النوع).';
  return '';
}
function captionPreview(u) {
  const s = (S.boot.subjects || []).find(x => x.id === u.subject_id);
  const docs = uploadDoctors(u);
  const pickedDoc = docs.length === 1 ? docs[0] : docs.find(d => d.id === u.doctor_id);
  const docTag = pickedDoc ? `#${hashtag(pickedDoc.name)}` : (docs.length > 1 ? '#اختر_المدرس' : '#الدكتور (يُحدَّد تلقائيًا)');
  const lines = [`🔹 نـوع المحتوى : #${hashtag(TYPES.find(t => t[0] === u.content_type)[1])}`, `🔹 اسـم المقرر : #${s ? hashtag(s.tag) : '…'}`];
  if (u.numbers.length && u.content_type !== 'exam') { const [tag, title] = LABELS[u.label_kind]; lines.push(`🔹 ${title}: ` + u.numbers.map(n => `#${tag}_${n}`).join(' ')); }
  lines.push(`🔹 مُدرس المقرر : ${docTag}`, `🔹 نوع المحاضرة : #${PART[u.part]}`);
  if (u.content_type === 'exam' && u.exam_kind) lines.push(`🔹 نوع النموذج : #${u.exam_kind === 'midterm' ? 'نصفي' : 'نهائي'}`);
  return lines.join('\n') + (u.description ? `\n\n${u.description}` : '');
}
B.views.upload = () => {
  const u = P.u || (P.u = newUpload()), subs = S.boot.subjects || [], docs = uploadDoctors(u), err = uploadError(u);
  const max = isLectureLabel(u.label_kind) ? 12 : 20;
  const nums = Array.from({ length: max }, (_, i) => i + 1).map(n => `<button class="num" data-act="u-num" data-n="${n}" aria-pressed="${u.numbers.includes(n)}">${n}</button>`).join('');
  const docPicker = docs.length > 1
    ? `<div class="sec">مُدرس المقرر</div><div class="scroll-x wrap">${docs.map(d => pill(d.name, 'u-doc', `data-id="${d.id}"`, u.doctor_id === d.id)).join('')}</div>`
    : '';
  return `<div class="card">
    <b>📤 بيانات الملف قبل الإرسال</b>
    ${u.replaces_id ? '<div class="stale" style="margin:10px 0">سيحلّ هذا الملف محلّ النسخة القديمة ويُحذف القديم من المجتمع.</div>' : ''}
    <div class="sec">المادة</div><div class="scroll-x wrap">${subs.map(s => pill(s.name, 'u-subj', `data-id="${s.id}"`, u.subject_id === s.id)).join('')}</div>
    <div class="sec">الجزء</div>${seg('u-part', [['theory', 'نظري'], ['practical', 'عملي']], u.part)}
    ${docPicker}
    <div class="sec">نوع المحتوى</div><div class="scroll-x wrap">${TYPES.map(([v, l]) => pill(l, 'u-type', `data-id="${v}"`, u.content_type === v)).join('')}</div>
    ${u.content_type === 'exam' ? `<div class="sec">النموذج</div>${seg('u-exam', [['midterm', 'نصفي'], ['final', 'نهائي']], u.exam_kind)}` : `
      <div class="sec">الرقم <small class="muted">${NEEDS_NUMBER.includes(u.content_type) ? '(مطلوب)' : '(اختياري)'}</small></div>
      ${seg('u-label', Object.entries(LABELS).map(([k, v]) => [k, v[2]]), u.label_kind)}
      <div class="nums">${nums}</div>`}
    <div class="sec">وصف <small class="muted">(اختياري)</small></div>
    <textarea class="field area" data-f="u.description" maxlength="300" rows="2" placeholder="مثال: مهم للنصفي">${esc(u.description)}</textarea>
    <div class="sec">معاينة رسالة المجتمع</div><pre class="preview" dir="auto">${esc(captionPreview(u))}</pre>
    <div class="err" role="alert">${esc(u.err || err)}</div>
    <button class="btn primary" style="margin-top:8px" data-act="u-save" ${u.busy || err ? 'disabled' : ''}>📤 حفظ والانتقال للبوت لإرسال الملف الآن</button>
  </div>`;
};
Object.assign(B.actions, {
  'u-subj': d => { P.u.subject_id = +d.id; P.u.doctor_id = null; P.u.err = ''; B.ls.set(`bs2:${B.UID}:lastsubj`, +d.id); rerender(); },
  'u-part': d => { P.u.part = d.v; P.u.doctor_id = null; P.u.err = ''; rerender(); },
  'u-doc': d => { P.u.doctor_id = +d.id; P.u.err = ''; rerender(); },
  'u-type': d => { P.u.content_type = d.id; P.u.err = ''; if (d.id === 'exam') P.u.numbers = []; rerender(); },
  'u-exam': d => { P.u.exam_kind = d.v; rerender(); },
  'u-label': d => { P.u.label_kind = d.v; P.u.numbers = P.u.numbers.filter(n => n <= (isLectureLabel(d.v) ? 12 : 20)); rerender(); },
  'u-num': d => { const n = +d.n, a = P.u.numbers; P.u.numbers = a.includes(n) ? a.filter(x => x !== n) : [...a, n].sort((x, y) => x - y); rerender(); },
  'u-save': async () => {
    const u = P.u, docs = uploadDoctors(u);
    const resolvedDocId = docs.length === 1 ? docs[0].id : (docs.some(d => d.id === u.doctor_id) ? u.doctor_id : null);
    u.busy = true; u.err = ''; rerender();
    const body = { subject_id: u.subject_id, part: u.part, doctor_id: resolvedDocId, content_type: u.content_type, exam_kind: u.content_type === 'exam' ? u.exam_kind : null,
      label_kind: u.numbers.length && u.content_type !== 'exam' ? u.label_kind : null, numbers: u.content_type === 'exam' ? [] : u.numbers,
      description: u.description || null, replaces_id: u.replaces_id };
    const r = await call('/api/files/draft', 'POST', body);
    u.busy = false;
    if (r.status !== 200) { u.err = failMsg(r); return rerender(); }
    B.ls.set(`bs2:${B.UID}:lastfile`, { subject_id: u.subject_id, part: u.part, content_type: u.content_type, label_kind: u.label_kind });
    B.ls.delPrefix('files');
    P.f.allItems = null;
    P.upDone = r.data;
    /* نقل فوري ومباشر إلى محادثة البوت */
    B.openBotDirect(r.data && r.data.bot_link);
  },
  'open-bot': () => B.openBotDirect(P.upDone && P.upDone.bot_link),
  'u-again': () => { P.u = newUpload(); S.view = 'upload'; rerender(); },
});
B.views['upload-done'] = () => `<div class="card"><h3 style="margin:0 0 6px">الانتقال إلى البوت</h3>
  <p class="hint" style="margin:0 0 14px">أرسل الملف نفسه في محادثة البوت وسينشره في مجتمع المادة بالصيغة المعتمدة.</p>
  <button class="btn primary" data-act="open-bot">افتح البوت وأرسل الملف</button><button class="btn" data-act="u-again">رفع ملف آخر</button></div>`;

/* ---------- الواجبات: الطالب (مع فلترة: الكل / مفتوحة / مسلّمة / فائتة وتعديل إرسال الملف) ---------- */
const isStaff = () => can('assignments.manage');
function loadAssignments(force = false) {
  if (P.a.tab === 'staff' && isStaff()) {
    swr('asg-staff', '/api/staff/assignments', d => { P.a.staff = d || []; rerender(); }, force);
  } else {
    swr('asg', '/api/assignments', d => { P.a.mine = d || []; rerender(); }, force);
  }
}
const card = a => {
  const [lbl, cls] = STATE[a.state] || STATE.open;
  const canEdit = a.state === 'submitted' && isOpenForSubmit(a);
  return `<div class="item col">
    <button style="display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;text-align:start;padding:0" data-act="asg" data-id="${a.id}">
      <div class="item-main"><b>${esc(a.subject)} · واجب ${a.number}</b>
        <div class="meta">${chip(PART[a.part])}${chip(a.grp)}${a.state === 'submitted' && a.is_late ? chip('سُلّم متأخرًا', 'warn') : ''}</div></div>
      <div class="item-side">${chip(lbl, cls)}<small>${a.state === 'open' || a.state === 'late' ? left(a.due_at) : fmtD(a.due_at)}</small></div>
    </button>
    ${canEdit ? `<div class="row-actions" style="margin-top:10px;padding-top:8px;border-top:1px dashed var(--line)">
      <button class="btn sm primary" data-act="edit-asg-bot" data-id="${a.id}">✏️ تعديل إرسال الملف في البوت</button>
      <button class="btn sm" data-act="asg" data-id="${a.id}">التفاصيل</button>
    </div>` : ''}
  </div>`;
};
B.views.assignments = () => {
  const a = P.a, tabs = isStaff() ? `<div style="margin-bottom:10px">${seg('a-tab', [['mine', 'واجباتي'], ['staff', 'الإدارة']], a.tab)}</div>` : '';
  let body;
  if (a.tab === 'staff' && isStaff()) {
    body = a.staff === null ? skel(3) : !a.staff.length ? empty('لا واجبات منشورة', 'أنشئ أول واجب من الزر بالأسفل.')
      : a.staff.map(x => `<button class="item" data-act="staff" data-id="${x.id}"><div class="item-main"><b>${esc(x.subject)} · واجب ${x.number}</b>
          <div class="meta">${chip(PART[x.part])}${chip(x.grp)}${x.status === 'closed' ? chip('مغلق', 'bad') : ''}</div></div>
          <div class="item-side"><b>${x.submitted}/${x.total}</b><small>سلّموا</small></div></button>`).join('');
  } else {
    const mine = a.mine;
    if (mine === null) {
      body = skel(3);
    } else {
      const openList = mine.filter(x => x.state === 'open' || x.state === 'late');
      const subList = mine.filter(x => x.state === 'submitted');
      const missList = mine.filter(x => x.state === 'missed');
      const curList = a.filter === 'open' ? openList : a.filter === 'submitted' ? subList : a.filter === 'missed' ? missList : mine;

      const filterBar = `<div class="scroll-x" style="margin-bottom:12px">
        ${pill(`الكل (${mine.length})`, 'a-flt', 'data-v="all"', a.filter === 'all')}
        ${pill(`🟢 مفتوحة (${openList.length})`, 'a-flt', 'data-v="open"', a.filter === 'open')}
        ${pill(`✅ مسلّمة (${subList.length})`, 'a-flt', 'data-v="submitted"', a.filter === 'submitted')}
        ${pill(`🔴 فائتة (${missList.length})`, 'a-flt', 'data-v="missed"', a.filter === 'missed')}
      </div>`;

      body = filterBar + (!curList.length ? empty('لا توجد واجبات في هذا التصنيف', 'اختر تصنيفًا آخر من الأعلى.') : curList.map(card).join(''));
    }
  }
  return `${tabs}${body}${can('assignments.create') ? `<button class="fab" data-act="w-new">${ico('tasks', 'sm')} واجب جديد</button>` : ''}`;
};
B.views.asg = id => {
  const d = P.d[id];
  if (!d) return skel(3);
  const [lbl, cls] = STATE[d.state] || STATE.open, sub = d.submission || {};
  const canSend = d.state === 'open' || d.state === 'late' || (d.state === 'submitted' && isOpenForSubmit(d));
  return `<div class="card"><div class="chips" style="margin-bottom:8px">${chip(PART[d.part])}${chip(d.grp)}${chip(lbl, cls)}${sub.is_late ? chip('سُلّم متأخرًا', 'warn') : ''}</div>
    <h2 style="margin:0 0 4px;font-size:20px">${esc(d.subject)} · واجب ${d.number}</h2>
    ${d.description ? `<p style="margin:8px 0">${esc(d.description)}</p>` : ''}
    <div class="row"><span>آخر موعد</span><b>${fmtDT(d.due_at)}</b></div>
    ${canSend ? `<div class="row"><span>المتبقي</span><b>${left(d.due_at)}</b></div>` : ''}
    ${d.attachments.length ? `<div class="row"><span>المرفقات</span><b>${d.attachments.length}</b></div>` : ''}
    ${d.allow_text ? '<div class="row"><span>طريقة التسليم</span><b>ملفات أو نصوص أو روابط</b></div>' : ''}
    ${sub.received_at ? '<div class="row"><span>الدكتور</span><b>استلم تسليمك ✅</b></div>' : ''}</div>
    ${canSend && can('assignments.submit') ? `<button class="btn primary" style="margin-top:14px" data-act="${d.state === 'submitted' ? 'edit-asg-bot' : 'open-asg-bot'}" data-id="${d.id}">${d.state === 'submitted' ? '✏️ تعديل إرسال الملف في البوت' : '📤 الانتقال للبوت لتسليم الواجب'}</button>
      <p class="hint" style="text-align:center">${d.state === 'submitted' ? 'أخطأت في إرسال الملف؟ سينقلك مباشرة إلى البوت لإرسال الملف الصحيح ليحلّ محلّ السابق.' : (d.allow_text ? 'ينقلك مباشرة إلى محادثة البوت لإرسال ملفات (أي صيغة عدا الصور) أو كتابة نص أو رابط.' : 'ينقلك مباشرة إلى محادثة البوت لإرسال ملف أو أكثر (أي صيغة عدا الصور).')}</p>` : ''}
    ${d.state === 'submitted' ? `<button class="btn" style="margin-top:10px" data-act="restore-my-sub" data-id="${d.id}">📥 استرجاع ملفات تسليمي في البوت</button>` : ''}`;
};
async function openAssignmentBot(aid, isEdit = false) {
  const cachedDetail = P.d[aid];
  const cachedRow = (P.a.mine || []).find(x => String(x.id) === String(aid))
    || (P.affairs.subs || []).find(x => String(x.id) === String(aid));
  const src = cachedDetail || cachedRow;
  if (src) {
    const link = isEdit ? (src.edit_bot_link || src.bot_link) : (src.bot_link || src.edit_bot_link);
    if (link) return B.openBotDirect(link);
  }
  const r = await call(`/api/assignments/${aid}`);
  if (r.status === 200 && r.data) {
    P.d[aid] = r.data;
    const link = isEdit ? (r.data.edit_bot_link || r.data.bot_link) : r.data.bot_link;
    return B.openBotDirect(link);
  }
  B.openBotDirect(null);
}
Object.assign(B.actions, {
  'a-tab': d => { P.a.tab = d.v; rerender(); loadAssignments(); },
  'a-flt': d => { P.a.filter = d.v; rerender(); },
  asg: d => { B.go('asg:' + d.id); },
  'open-asg-bot': d => openAssignmentBot(d.id, false),
  'edit-asg-bot': d => openAssignmentBot(d.id, true),
  'restore-my-sub': async d => {
    const r = await call(`/api/assignments/${d.id}/my-files/send`, 'POST');
    B.toast(r.status === 200 ? 'أُرسلت ملفات تسليمك إلى محادثتك مع البوت ✅' : failMsg(r));
  },
  staff: d => B.go('staff:' + d.id),
});

/* ---------- الواجبات: الإدارة ---------- */
B.views.staff = id => {
  const r = P.r[id];
  if (!r) return skel(3);
  const a = r.assignment, sd = P.sd, closed = a.status_db === 'closed';
  const rows = sd.tab === 'sub'
    ? r.submitted.map(s => `<div class="row"><span>${esc(s.name)}<small class="muted"> · <bdi>${esc(s.no)}</bdi></small></span><span>${s.late ? chip('متأخر', 'warn') : ''}${s.received ? chip('استُلم', 'ok') : ''}</span></div>`)
    : r.missing.map(s => `<div class="row"><span>${esc(s.name)}</span><small class="muted"><bdi>${esc(s.no)}</bdi></small></div>`);
  return `<div class="card"><div class="chips" style="margin-bottom:6px">${chip(PART[a.part])}${chip(a.grp)}${closed ? chip('مغلق', 'bad') : chip('مفتوح', 'ok')}</div>
    <h2 style="margin:0 0 4px;font-size:20px">${esc(a.subject)} · واجب ${a.number}</h2>
    <div class="row"><span>آخر موعد</span><b>${fmtDT(a.due_at)}</b></div></div>
    <div style="margin:14px 0 8px">${seg('sd-tab', [['sub', `سلّموا (${r.submitted.length})`], ['miss', `لم يسلّموا (${r.missing.length})`]], sd.tab)}</div>
    <div class="card" style="padding:4px 16px">${rows.join('') || '<p class="hint" style="text-align:center">لا أحد هنا.</p>'}</div>
    ${sd.ext ? `<div class="card" style="margin-top:12px"><b>موعد جديد (بتوقيت صنعاء)</b>
        ${dtPickerHTML('sd.date', sd.date || '', 'sd.time', sd.time || '20:00')}
        <div class="err" role="alert">${esc(sd.err || '')}</div><button class="btn primary" data-act="sd-extend-ok" data-id="${a.id}">تأكيد التمديد للمجموعة</button></div>`
      : `<div class="row-actions" style="margin-top:14px"><button class="btn" data-act="sd-extend">📅 تمديد الموعد</button>${closed ? '' : '<button class="btn" data-act="sd-close" data-id="' + a.id + '">إغلاق الآن</button>'}<button class="btn danger" data-act="sd-del" data-id="${a.id}">حذف</button></div>`}`;
};
const reloadStaff = (id, force = false) => swr(`roster:${id}`, `/api/staff/assignments/${id}`, d => { if (d) { P.r[id] = d; rerender(); } }, force);
Object.assign(B.actions, {
  'sd-tab': d => { P.sd.tab = d.v; rerender(); },
  'sd-extend': () => { P.sd.ext = true; P.sd.date = P.sd.date || tomorrow(); rerender(); },
  'sd-extend-ok': async d => {
    const sd = P.sd, r = await call(`/api/assignments/${d.id}/extend`, 'POST', { due_local: `${sd.date} ${sd.time || '20:00'}` });
    if (r.status !== 200) { sd.err = r.data && r.data.detail === 'invalid_due' ? 'الموعد الجديد يجب أن يكون بعد الموعد الحالي.' : failMsg(r); return rerender(); }
    sd.ext = false; sd.err = ''; B.ls.delPrefix('asg'); B.toast('تم التمديد'); reloadStaff(d.id, true); loadAssignments(true);
  },
  'sd-close': d => ask('إغلاق الواجب الآن؟ لن يقبل تسليمات جديدة.', async () => {
    const r = await call(`/api/assignments/${d.id}/close`, 'POST');
    B.ls.delPrefix('asg'); B.toast(r.status === 200 ? 'تم الإغلاق' : failMsg(r)); reloadStaff(d.id, true); loadAssignments(true);
  }),
  'sd-del': d => ask('حذف الواجب؟ تبقى التسليمات ويمكن استرجاعه خلال 30 يومًا.', async () => {
    const r = await call(`/api/assignments/${d.id}`, 'DELETE');
    if (r.status === 200) { B.ls.delPrefix('asg'); B.toast('تم الحذف'); loadAssignments(true); B.back(); } else B.toast(failMsg(r));
  }),
});

/* ---------- إنشاء واجب: ثلاث خطوات سلسة ثم الإرفاق المباشر من البوت ---------- */
const tomorrow = () => new Date(Date.now() + 86400e3 + 3 * 3600e3).toISOString().slice(0, 10);
const groupsAllowed = (part, permKey = 'assignments.create') => {
  const g = S.boot.groups || { theory: [], practical: [] };
  const okTheory = t => (S.boot.perms || []).some(p => p.k === permKey && (p.s === 'any' || (p.s === 'group' && p.g === t)));
  return part === 'theory' ? g.theory.filter(okTheory) : g.practical.filter(x => okTheory(x.theory)).map(x => x.id);
};
const newWizard = () => {
  const last = B.ls.get(`bs2:${B.UID}:lastasg`) || {};
  const savedDesc = B.ls.get(draftKey('asg_desc')) || '';
  return { step: 1, subject_id: last.subject_id || null, part: last.part || 'theory', group: last.group || '', description: savedDesc, allow_text: false, date: tomorrow(), time: '20:00', busy: false, err: '' };
};
B.views.newasg = () => {
  const w = P.w || (P.w = newWizard()), subs = S.boot.subjects || [], groups = groupsAllowed(w.part);
  if (!groups.includes(w.group)) w.group = groups[0] || '';
  const dots = `<div class="steps">${[1, 2, 3].map(i => `<i class="${w.step >= i ? 'on' : ''}"></i>`).join('')}</div>`;
  if (w.step === 1) return `<div class="card">${dots}<b>الخطوة 1: المادة والمجموعة</b>
    <div class="sec">المادة</div><div class="scroll-x wrap">${subs.map(s => pill(s.name, 'w-subj', `data-id="${s.id}"`, w.subject_id === s.id)).join('')}</div>
    <div class="sec">الجزء</div>${seg('w-part', [['theory', 'نظري'], ['practical', 'عملي']], w.part)}
    <div class="sec">المجموعة</div><div class="scroll-x wrap">${groups.map(g => pill(w.part === 'practical' ? `${g} عملي` : g, 'w-group', `data-id="${g}"`, w.group === g)).join('') || '<span class="muted">لا تملك صلاحية على مجموعة.</span>'}</div>
    <button class="btn primary" style="margin-top:16px" data-act="w-next" ${w.subject_id && w.group ? '' : 'disabled'}>التالي</button></div>`;
  if (w.step === 2) return `<div class="card">${dots}<b>الخطوة 2: وصف الواجب</b>
    <div class="sec">الوصف <small class="muted">(اختياري — يُحفظ تلقائيًا)</small></div>
    <textarea class="field area" data-f="w.description" maxlength="800" rows="5" placeholder="ما المطلوب من الطلاب؟">${esc(w.description)}</textarea>
    <button class="btn primary" style="margin-top:16px" data-act="w-next">التالي</button><button class="btn" data-act="w-prev">رجوع</button></div>`;
  return `<div class="card">${dots}<b>الخطوة 3: تحديد موعد التسليم والنشر</b>
    <div class="sec">آخر موعد للتسليم <small class="muted">(بتوقيت صنعاء)</small></div>
    ${dtPickerHTML('w.date', w.date, 'w.time', w.time)}
    <div class="sec">طريقة التسليم</div>
    <button class="btn ${w.allow_text ? 'primary' : ''}" data-act="w-allow-text" aria-pressed="${w.allow_text ? 'true' : 'false'}">${w.allow_text ? '✅ السماح بإرسال نصوص وروابط: مفعّل' : '☐ السماح بإرسال نصوص وروابط'}</button>
    <p class="hint" style="margin:6px 0 0">${w.allow_text ? 'سيقبل البوت ملفات ونصوصًا وروابط لهذا الواجب.' : 'الافتراضي: الملفات فقط (أي صيغة عدا الصور).'}</p>
    <div class="card" style="margin-top:14px;background:var(--surface-2)"><div class="row"><span>المادة</span><b>${esc(subjName(w.subject_id))}</b></div>
      <div class="row"><span>المجموعة</span><b>${esc(w.group)} · ${PART[w.part]}</b></div>
      ${w.description ? `<div class="row"><span>الوصف</span><b>${esc(w.description.slice(0, 60))}</b></div>` : ''}</div>
    <div class="err" role="alert">${esc(w.err)}</div>
    <button class="btn primary" data-act="w-publish" ${w.busy ? 'disabled' : ''}>🚀 نشر الواجب الآن</button>
    <button class="btn" data-act="w-attach" ${w.busy ? 'disabled' : ''}>📎 الانتقال للبوت لإرفاق ملف ثم النشر</button>
    <button class="btn" data-act="w-prev">رجوع</button></div>`;
};
const wBody = w => ({ subject_id: w.subject_id, part: w.part, group: w.group, due_local: `${w.date} ${w.time}`, description: w.description || null, allow_text: !!w.allow_text });
async function wDraft() {
  const w = P.w; w.busy = true; w.err = ''; rerender();
  const r = await call('/api/assignments/draft', 'POST', wBody(w));
  w.busy = false;
  if (r.status !== 200) { w.err = failMsg(r); rerender(); return null; }
  B.ls.set(`bs2:${B.UID}:lastasg`, { subject_id: w.subject_id, part: w.part, group: w.group });
  B.ls.set(draftKey('asg_desc'), '');
  return r.data;
}
Object.assign(B.actions, {
  'w-new': () => { P.w = newWizard(); B.go('newasg'); },
  'w-subj': d => { P.w.subject_id = +d.id; rerender(); },
  'w-part': d => { P.w.part = d.v; P.w.group = ''; rerender(); },
  'w-group': d => { P.w.group = d.id; rerender(); },
  'w-next': () => { P.w.step++; rerender(); window.scrollTo(0, 0); },
  'w-prev': () => { P.w.step--; rerender(); },
  'w-allow-text': () => { P.w.allow_text = !P.w.allow_text; rerender(); },
  'w-publish': async () => {
    const dr = await wDraft(); if (!dr) return;
    const r = await call('/api/assignments/publish', 'POST', { draft_id: dr.draft_id });
    if (r.status !== 200) { P.w.err = failMsg(r); return rerender(); }
    B.ls.delPrefix('asg');
    P.wDone = r.data; loadAssignments(true); S.view = 'newasg-done'; rerender();
  },
  'w-attach': async () => {
    const dr = await wDraft(); if (!dr) return;
    P.wDraft = dr;
    /* نقل فوري ومباشر إلى البوت لإرسال مرفقات الواجب */
    B.openBotDirect(dr.bot_link);
  },
  'w-publish-now': async () => {
    const r = await call('/api/assignments/publish', 'POST', { draft_id: P.wDraft.draft_id });
    if (r.status !== 200) return B.toast(failMsg(r));
    B.ls.delPrefix('asg');
    P.wDone = r.data; loadAssignments(true); S.view = 'newasg-done'; rerender();
  },
  'w-open-bot': () => B.openBotDirect(P.wDraft && P.wDraft.bot_link),
  'w-finish': () => { B.tab('assignments'); },
});
B.views['newasg-wait'] = () => `<div class="card"><h3 style="margin:0 0 6px">أرسل المرفقات في البوت</h3>
  <p class="hint" style="margin:0 0 14px">أرسل الملفات هناك ثم اضغط «نشر الواجب».</p>
  <button class="btn primary" data-act="w-open-bot">افتح البوت</button><button class="btn" data-act="w-publish-now">نشر الآن</button></div>`;
B.views['newasg-done'] = () => `<div class="card empty"><b>تم نشر الواجب رقم ${P.wDone ? P.wDone.number : ''} ✅</b>وصل الإشعار للطلاب وللمجتمع.</div>
  <button class="btn primary" style="margin-top:14px" data-act="w-finish">عرض الواجبات</button>`;

/* ---------- الإشعارات مع التوجيه المباشر للوجهة داخل التطبيق ---------- */
function notifTargetView(x) {
  const k = x.kind || '';
  const p = x.payload || {};
  if (k === 'assignment_new' || k === 'assignment_reminder') return p.assignment_id ? `asg:${p.assignment_id}` : 'assignments';
  if (k === 'grievance_open') return 'affairs-grv';
  if (k === 'grievance_compiled') return p.cycle_id ? `grv-cycle:${p.cycle_id}` : 'grv-admin';
  if (k === 'sharek_new') return 'sharek';
  if (k === 'ticket_new') return 'assistant-staff';
  if (k === 'ticket_reply') return 'assistant';
  if (k === 'report_reminder') return 'reports';
  return '';
}
B.views.notifs = () => {
  const n = P.n;
  if (n === null) return skel(4);
  if (!n.length) return empty('لا إشعارات', 'ستصلك الإشعارات هنا، وفي البوت أيضًا.');
  return n.map(x => {
    const dest = notifTargetView(x);
    return `<div class="item col ${x.read_at ? '' : 'unread'}">
      <div class="item-main"><b>${esc(x.title)}</b><p class="desc" style="margin:2px 0 0">${esc(x.body)}</p>
        <div class="meta"><span>${fmtDT(x.created_at)}</span></div>
      </div>
      ${dest ? `<div class="row-actions"><button class="btn sm primary" data-act="notif-go" data-dest="${esc(dest)}">فتح الوجهة الآن ◀</button></div>` : ''}
    </div>`;
  }).join('');
};
B.actions['notif-go'] = d => {
  if (d.dest && B.openDeepLink) B.openDeepLink(d.dest);
};

/* ---------- إعدادات الإشعارات ---------- */
const SET_ROWS = [['notify_assignment', 'إشعار عند نشر واجب جديد'], ['notify_sharek', 'إشعار عند مشاركة جديدة'],
  ['remind_24', 'تذكير قبل الموعد بـ 24 ساعة'], ['remind_3', 'تذكير قبل الموعد بـ 3 ساعات'], ['remind_1', 'تذكير قبل الموعد بساعة']];
function renderNotificationSwitches() {
  if (!can('affairs.use')) return '';
  const s = P.set;
  if (!s) return `<div class="sec">الإشعارات</div>${skel(2)}`;
  return `<div class="sec">الإشعارات</div><div class="card" style="padding:4px 16px">${SET_ROWS.map(([k, l]) =>
    `<div class="row"><span style="color:var(--text)">${l}</span><button class="tg" role="switch" aria-checked="${!!s[k]}" aria-label="${l}" data-act="set" data-k="${k}"><i></i></button></div>`).join('')}</div>
    <p class="hint">التذكيرات تصل فقط لمن لم يسلّم الواجب. الإعلانات اليدوية من المشرفين تصلك دائمًا.</p>`;
}
B.moreExtra = renderNotificationSwitches;
B.actions.set = async d => {
  const k = d.k, was = P.set[k];
  P.set[k] = !was; rerender();
  const r = await call('/api/settings', 'PUT', { [k]: !was });
  if (r.status === 200) { P.set = r.data; B.ls.set(`bs2:${B.UID}:settings`, { data: r.data, t: Date.now() }); }
  else { P.set[k] = was; B.toast('تعذّر الحفظ'); }
  rerender();
};


/* =====================================================================
   المرحلة 3 — 1) حضوري (للطالب: المحاضرات النظرية 1–12 لكل مادة)
   ===================================================================== */
function loadMyAttendance(force = false) {
  swr('att-mine', '/api/attendance/mine', d => { P.att.mine = d || []; rerender(); }, force);
}
function renderMyAttendanceList() {
  const rows = P.att.mine;
  if (rows === null) return skel(3);
  if (!rows.length) return empty('لا توجد مواد مسجّلة', 'سيظهر هنا سجل حضورك في المحاضرات النظرية.');
  const totHeld = rows.reduce((a, r) => a + (r.held || 0), 0);
  const totPres = rows.reduce((a, r) => a + (r.present || 0), 0);
  const totAbs = rows.reduce((a, r) => a + (r.absent || 0), 0);
  const totPct = totHeld ? Math.round((totPres * 100) / totHeld) : 100;

  const summary = `<div class="card" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <b>ملخص الحضور النظري (12 محاضرة لكل مادة)</b>
      ${chip(totHeld ? `${totPct}%` : 'لم يبدأ', totHeld ? 'ok' : '')}
    </div>
    <div class="meta" style="margin-top:6px">
      <span>حاضر: <b>${totPres}</b></span><span>· غائب: <b>${totAbs}</b></span><span>· المعقودة: <b>${totHeld}</b></span>
    </div>
    <div class="progress-bar"><i style="width:${totPct}%"></i></div>
  </div>`;

  const cards = rows.map(r => {
    const held = r.held || 0, pres = r.present || 0, abs = r.absent || 0;
    const pct = held ? Math.round((pres * 100) / held) : 0;
    const lecs = (r.lectures || []).map(l => {
      const lbl = l.state === 'present' ? 'حاضر' : l.state === 'absent' ? 'غائب' : l.state === 'open' ? 'مفتوحة' : '—';
      return `<div class="lec-cell ${l.state}"><span>م${l.n}</span><small>${lbl}</small></div>`;
    }).join('');
    return `<div class="card" style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>${esc(r.subject)}</b>
        ${held ? chip(`${pct}%`, 'ok') : chip('لم تُعقد جلسات')}
      </div>
      <div class="meta" style="margin-top:4px">
        ${chip(`حاضر ${pres}`, 'ok')}${chip(`غائب ${abs}`, abs ? 'bad' : '')}<span>من أصل ${held} محاضرات معقودة</span>
      </div>
      <div class="lec-grid">${lecs}</div>
    </div>`;
  }).join('');

  const staffBtn = (can('attendance.session') || can('attendance.scan') || can('attendance.admin'))
    ? `<button class="btn primary" style="margin-bottom:12px" data-act="go-att-admin">${ico('cal', 'sm')} إدارة جلسات الحضور والمسح</button>`
    : '';
  return staffBtn + summary + cards;
}
B.views.attendance = renderMyAttendanceList;


/* =====================================================================
   المرحلة 3 — 2) شؤوني:
   شاشة «شؤوني» تعرض البطاقات فقط، وعند الضغط على أي بطاقة تفتح الخدمة في شاشة كاملة مستقلة!
   ===================================================================== */
function loadAffairsSub(sub, force = false) {
  if (sub === 'grv') {
    swr('grievances', '/api/grievances', d => { if (d) { P.affairs.grv = d; rerender(); } }, force);
  } else if (sub === 'att') {
    loadMyAttendance(force);
  } else if (sub === 'sub') {
    swr('affairs-subs', '/api/affairs/submissions', d => { P.affairs.subs = d || []; rerender(); }, force);
  } else if (sub === 'bar') {
    if (!P.affairs.bar || force) {
      call('/api/barcode').then(r => { if (r.status === 200) { P.affairs.bar = r.data; rerender(); } });
    }
  } else if (sub === 'set') {
    swr('settings', '/api/settings', d => { if (d) { P.set = d; rerender(); } }, force);
  } else if (sub === 'sup') {
    loadAssistant(force);
  }
}

function renderAffairsGrievances() {
  const af = P.affairs, g = af.grv;
  if (!g) return skel(3);
  const st = g.student || S.boot.student || {};
  const cycles = g.cycles || [];

  const topNotice = `<div class="card" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
      <div><b>بياناتك المعتمدة للتظلمات</b><p class="hint" style="margin:2px 0 0">${esc(st.full_name)} · <bdi>${esc(st.academic_no)}</bdi> · ${esc(st.theory_group_id)} (${esc(st.practical_group_id)})</p></div>
      <button class="btn sm" style="width:auto" data-act="af-open" data-v="sup">${ico('chat', 'sm')} الدعم</button>
    </div>
  </div>`;

  if (!cycles.length) {
    return topNotice + empty('لا توجد دورات تظلمات حاليًا', 'عندما يفتح المندوب دورة تظلمات لمجموعتك ستظهر هنا تلقائيًا لتكتب سبب التظلم فقط.');
  }

  const list = cycles.map(c => {
    const mine = c.my_grievance;
    const isEditing = af.editId === c.id;
    const draftVal = isEditing ? af.reason : (B.ls.get(draftKey('grv_' + c.id)) ?? (mine ? mine.reason : ''));

    return `<div class="card" style="margin-bottom:10px">
      <div class="chips" style="margin-bottom:6px">
        ${chip(PART[c.part])}${chip(`مجموعتك: ${c.student_group}`)}
        ${c.is_open ? chip('مفتوح للتقديم والتعديل', 'ok') : chip('مغلق', 'bad')}
        ${mine ? chip('تم الإرسال ✅', 'ok') : ''}
      </div>
      <h3 style="margin:0 0 4px;font-size:17px">${esc(c.subject)}${c.title ? ' — ' + esc(c.title) : ''}</h3>
      <div class="meta"><span>آخر موعد: ${fmtDT(c.closes_at)}</span>${c.is_open ? `<span>· ${left(c.closes_at)}</span>` : ''}</div>
      ${mine && !isEditing ? `<div class="preview" style="margin-top:10px"><b>سبب تظلمك المسجّل:</b>\n${esc(mine.reason)}</div>` : ''}
      ${c.is_open ? (
        isEditing || !mine
          ? `<div style="margin-top:10px">
               <textarea class="field area" data-f="affairs.reason" maxlength="1000" rows="3" placeholder="اكتب سبب التظلم فقط (بياناتك ومجموعتك تُرفق تلقائيًا)…">${esc(draftVal)}</textarea>
               <div class="err" role="alert">${esc(isEditing ? af.err : '')}</div>
               <div class="row-actions">
                 <button class="btn primary sm" data-act="grv-save" data-id="${c.id}" ${af.busy ? 'disabled' : ''}>${mine ? 'حفظ تعديل التظلم' : 'إرسال التظلم'}</button>
                 ${mine ? `<button class="btn sm" data-act="grv-cancel">إلغاء</button>` : ''}
               </div>
             </div>`
          : `<div class="row-actions" style="margin-top:10px"><button class="btn sm" data-act="grv-edit" data-id="${c.id}">✏️ تعديل التظلم قبل الإغلاق</button></div>`
      ) : (!mine ? '<p class="hint" style="margin:8px 0 0">انتهت فترة التقديم لهذه الدورة.</p>' : '')}
    </div>`;
  }).join('');

  return topNotice + list;
}

function renderAffairsSubmissions() {
  const subs = P.affairs.subs;
  if (subs === null) return skel(3);
  if (!subs.length) return empty('لا توجد واجبات في سجلك', 'ستظهر هنا كل واجباتك وحالات تسليمها.');
  return subs.map(a => {
    const [lbl, cls] = STATE[a.state] || STATE.open;
    const canEdit = a.state === 'submitted' && isOpenForSubmit(a);
    return `<div class="item col">
      <div class="item-main">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <b>${esc(a.subject)} · واجب ${a.number}</b>
          ${chip(lbl, cls)}
        </div>
        <div class="meta">${chip(PART[a.part])}${chip(a.grp)}${a.is_late ? chip('سُلّم متأخرًا', 'warn') : ''}${a.received_at ? chip('استلمه الدكتور ✅', 'ok') : ''}</div>
        <div class="meta"><span>الموعد: ${fmtDT(a.due_at)}</span>${a.submitted_at ? `<span>· سُلّم: ${fmtD(a.submitted_at)}</span>` : ''}</div>
      </div>
      <div class="row-actions">
        <button class="btn sm" data-act="asg" data-id="${a.id}">تفاصيل الواجب</button>
        ${canEdit ? `<button class="btn sm primary" data-act="edit-asg-bot" data-id="${a.id}">✏️ تعديل إرسال الملف</button>` : ''}
        ${a.submitted_at ? `<button class="btn sm" data-act="restore-my-sub" data-id="${a.id}">📥 استرجاع ملفاتي</button>` : ''}
      </div>
    </div>`;
  }).join('');
}

function renderAffairsBarcode() {
  const b = P.affairs.bar;
  if (!b) return skel(2);
  return `<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <b>باركود البطاقة الجامعية (Code 128)</b>
    </div>
    <p class="hint" style="margin:6px 0 10px">إذا نسيت بطاقتك الجامعية، ولّد صورة الباركود لرقمك الأكاديمي لتصلك في البوت وتحمّلها ليمسحها جهاز التحضير.</p>
    <div class="barcode-wrap">${b.svg || ''}</div>
    <div class="row"><span>الطالب</span><b>${esc(b.full_name)}</b></div>
    <div class="row"><span>الرقم الأكاديمي</span><b><bdi>${esc(b.academic_no)}</bdi></b></div>
    <div class="row"><span>المجموعة</span><b>${esc(b.theory_group_id)} · عملي ${esc(b.practical_group_id)}</b></div>
    <button class="btn primary" style="margin-top:14px" data-act="bar-send" ${b.remaining <= 0 ? 'disabled' : ''}>
      ${ico('barcode', 'sm')} إرسال صورة الباركود في البوت
    </button>
  </div>`;
}

function renderAffairsSettings() {
  const segPref = (key, opts) => `<div class="seg" role="group">${opts.map(([v, l]) => `<button data-act="pref" data-k="${key}" data-v="${v}" aria-pressed="${B.prefs[key] === v}">${l}</button>`).join('')}</div>`;
  const swatchesList = B.ACCENT_SWATCHES || [['green', '#1D5A44'], ['teal', '#14625F'], ['blue', '#24558F'], ['violet', '#5B4496'], ['yellow', '#D4A017']];
  const sw = swatchesList
    .map(([v, c]) => `<button class="sw" style="background:${c}" data-act="pref" data-k="accent" data-v="${v}" aria-pressed="${B.prefs.accent === v}" aria-label="${v}">${ico('check')}</button>`).join('');
  return `<div class="sec" style="margin-top:4px">المظهر واللون الأساسي</div>
    <div class="card">${segPref('theme', [['auto', 'تلقائي'], ['light', 'نهاري'], ['dark', 'ليلي']])}
      <div class="sec" style="margin-top:16px">اللون الأساسي</div><div class="swatches">${sw}</div></div>
    ${renderNotificationSwitches()}`;
}

/* شاشة شؤوني الرئيسية: بطاقات فقط كما في الشاشة الرئيسية */
B.views.affairs = () => {
  const st = (S.boot && S.boot.student) || {};
  const tiles = [
    ['grv', 'تظلماتي', 'idcard'],
    ['sup', 'الدعم', 'chat'],
    ['att', 'حضوري', 'cal'],
    ['sub', 'تسليماتي', 'tasks'],
    ['bar', 'باركود بطاقتي', 'barcode'],
    ['set', 'الإعدادات', 'more'],
  ];
  return `<section class="hero" style="padding:18px 18px 34px;margin-bottom:14px">
      <small>خدمات شؤون الطالب</small>
      <h2 style="font-size:22px;margin:4px 0 10px">${esc(st.full_name || 'شؤوني')}</h2>
      <div class="chips">${st.academic_no ? `<span class="chip"><bdi>${esc(st.academic_no)}</bdi></span>` : ''}${st.theory_group_id ? `<span class="chip">${esc(st.theory_group_id)}</span>` : ''}${st.practical_group_id ? `<span class="chip">عملي ${esc(st.practical_group_id)}</span>` : ''}</div>
    </section>
    <div class="sec">اختر الخدمة المطلوبة</div>
    <div class="grid">${tiles.map(([k, l, icon]) =>
      `<button class="tile" data-act="af-open" data-v="${k}">${ico(icon)}<span>${esc(l)}</span></button>`
    ).join('')}</div>`;
};

/* الشاشة الكاملة المستقلة لكل خدمة من شؤوني */
B.views['af-view'] = sub => {
  if (sub === 'grv') return renderAffairsGrievances();
  if (sub === 'sup') return renderAssistantView();
  if (sub === 'att') return renderMyAttendanceList();
  if (sub === 'sub') return renderAffairsSubmissions();
  if (sub === 'bar') return renderAffairsBarcode();
  return renderAffairsSettings();
};

Object.assign(B.actions, {
  'af-open': d => { P.affairs.tab = d.v; P.affairs.err = ''; B.go('af-view:' + d.v); },
  'af-tab': d => { P.affairs.tab = d.v; P.affairs.err = ''; B.go('af-view:' + d.v); },
  /* من «يهمّني الآن»: نفتح شاشة تظلماتي ويبقى «شؤوني» خلفها ثم الرئيسية، دون وضع التعديل بنص فارغ */
  'go-notifs': () => { B.go('notifs'); },
  'go-grv': () => {
    P.affairs.tab = 'grv'; P.affairs.editId = null; P.affairs.reason = ''; P.affairs.err = '';
    B.go('af-view:grv');
  },
  'grv-edit': d => {
    const id = +d.id;
    const cyc = ((P.affairs.grv && P.affairs.grv.cycles) || []).find(x => x.id === id);
    P.affairs.editId = id;
    P.affairs.reason = B.ls.get(draftKey('grv_' + id)) || (cyc && cyc.my_grievance ? cyc.my_grievance.reason : '');
    P.affairs.err = '';
    rerender();
  },
  'grv-cancel': () => { P.affairs.editId = null; P.affairs.err = ''; rerender(); },
  'grv-save': async d => {
    const id = +d.id;
    const area = document.querySelector('[data-f="affairs.reason"]');
    const reason = (area ? area.value : P.affairs.reason || '').trim();
    P.affairs.editId = id; P.affairs.reason = reason; P.affairs.busy = true; P.affairs.err = ''; rerender();
    const r = await call(`/api/grievances/${id}`, 'POST', { reason });
    P.affairs.busy = false;
    if (r.status !== 200) { P.affairs.err = failMsg(r); return rerender(); }
    B.ls.set(draftKey('grv_' + id), null);
    B.ls.delPrefix('grievances');
    P.affairs.editId = null; P.affairs.reason = '';
    B.toast(r.data && r.data.updated ? 'تم تعديل تظلمك ✅' : 'تم تسجيل تظلمك ✅');
    loadAffairsSub('grv', true);
  },
  'bar-send': async () => {
    const r = await call('/api/barcode/send', 'POST');
    if (r.status === 200 && r.data) {
      P.affairs.bar = Object.assign({}, P.affairs.bar, r.data);
      B.toast('أُرسلت صورة الباركود Code 128 إلى محادثتك مع البوت ✅');
      rerender();
    } else {
      B.toast(failMsg(r));
    }
  },
});


/* =====================================================================
   المرحلة 3 — 3) المساعد الذكي والدعم والبلاغات (بالتصميم المعتمد 58918.jpg، شاشة كاملة بدون Navbar سفلي)
   ===================================================================== */
function scrollChatToBottom() {
  setTimeout(() => {
    const box = document.getElementById('support-chat-stream');
    if (box) box.scrollTop = box.scrollHeight;
  }, 30);
}

function loadAssistant(force = false) {
  if (!P.ai.info || force) {
    call('/api/assistant').then(r => { if (r.status === 200) { P.ai.info = r.data; rerender(); scrollChatToBottom(); } });
  }
  if (!P.att.mine && can('attendance.view_mine')) {
    swr('att-mine', '/api/attendance/mine', d => { P.att.mine = d || []; if (S.view === 'assistant' || S.view === 'af-view:sup') rerender(); });
  }
  swr('my-tickets', '/api/tickets', d => { P.ai.tickets = d || []; rerender(); scrollChatToBottom(); }, force);
  if (can('tickets.reply') && P.ai.staffTab === 'staff') {
    swr('staff-tickets', '/api/staff/tickets', d => { P.ai.staffTickets = d || []; rerender(); }, force);
  }
}

/* يبني بطاقة إحصائية الحضور البصرية المطابقة للصورة 58918.jpg */
function buildAttendanceVisualCard(attRows) {
  const rows = (attRows || []).filter(r => (r.held || 0) > 0);
  if (!rows.length) return '';
  const totHeld = rows.reduce((a, r) => a + (r.held || 0), 0);
  const totPres = rows.reduce((a, r) => a + (r.present || 0), 0);
  const totPct = totHeld ? Math.round((totPres * 100) / totHeld) : 100;

  const subjBars = rows.map(r => {
    const held = r.held || 0, pres = r.present || 0;
    const pct = held ? Math.round((pres * 100) / held) : 100;
    const isWarn = pct < 75;
    return `<div class="ai-att-row">
      <div class="ai-att-top">
        <span>${esc(r.subject)}</span>
        <span class="ai-att-stat ${isWarn ? 'warn' : ''}">${pres} من ${held} · %${pct}</span>
      </div>
      <div class="ai-prog ${isWarn ? 'warn' : ''}"><i style="width:${pct}%"></i></div>
    </div>`;
  }).join('');

  return `<div class="ai-bot-text" style="margin-bottom:6px">حضورك في المحاضرات النظرية، من أصل 12 محاضرة لكل مادة:</div>
    <div class="ai-att-hero">
      <span class="ai-att-pct">${totPct}%</span>
      <span class="ai-att-lbl">نسبة حضورك الإجمالية حتى الآن</span>
    </div>
    ${subjBars}`;
}

function renderBotCard(idx, questionText, answerText, srcLabel) {
  const attRows = (P.ai.info && P.ai.info.attendance && P.ai.info.attendance.length)
    ? P.ai.info.attendance
    : (P.att.mine || []);
  const qNorm = String(questionText || '');
  const isAttQuery = /حضور|غياب|محاضرات/.test(qNorm) && attRows && attRows.some(r => (r.held || 0) > 0);
  const attHTML = isAttQuery ? buildAttendanceVisualCard(attRows) : '';
  const contentHTML = attHTML || `<div class="ai-bot-text">${esc(answerText)}</div>`;
  const source = srcLabel || (isAttQuery ? 'من سجل حضورك' : 'من سجل دفعتك');
  const react = P.ai.reactions[idx] || '';

  return `<div class="ai-bot-card">
    ${contentHTML}
    <div class="ai-bot-foot">
      <span class="ai-foot-src">${F_ICONS.db}<span>${esc(source)}</span></span>
      <div class="ai-foot-acts">
        <button class="ai-act-btn" data-act="copy-text" data-text="${esc(answerText)}" aria-label="نسخ">${F_ICONS.copy}</button>
        <button class="ai-act-btn ${react === 'up' ? 'on' : ''}" data-act="ai-react" data-idx="${idx}" data-v="up" aria-label="مفيد">${F_ICONS.up}</button>
        <button class="ai-act-btn ${react === 'down' ? 'on' : ''}" data-act="ai-react" data-idx="${idx}" data-v="down" aria-label="غير مفيد">${F_ICONS.down}</button>
      </div>
    </div>
  </div>`;
}

/* يدمج أسئلة المساعد وبلاغات الدعم وردودها في خط زمني واحد (الأقدم أولًا).
   رد المطوّر يُوضع عند وقت الرد (replied_at) لا عند وقت البلاغ؛ وبلاغ بلا رد يُعرض مع «قيد المراجعة» فقط. */
function chatEvents(hist, tickets) {
  const ms = v => { const n = Date.parse(v || ''); return Number.isFinite(n) ? n : 0; };
  const ev = [];
  (hist || []).forEach((h, idx) => ev.push({ at: ms(h.created_at), type: 'q', h, idx }));
  (tickets || []).forEach(t => {
    const sent = ms(t.created_at);
    if (t.reply_text) {
      ev.push({ at: sent, type: 'tq', t });
      ev.push({ at: Math.max(ms(t.replied_at), sent), type: 'tr', t });
    } else {
      ev.push({ at: sent, type: 't', t });
    }
  });
  ev.forEach((e, i) => { e.seq = i; });
  ev.sort((a, b) => a.at - b.at || a.seq - b.seq);
  return ev;
}

function renderAssistantView() {
  const ai = P.ai, info = ai.info;
  const isDev = can('tickets.reply');
  const rem = info ? info.remaining : (B.kind() === 'guest' ? 5 : 10);

  const topHeader = `<header class="ai-top">
    <div class="ai-top-right">
      <button class="ai-back-btn" data-act="back" aria-label="رجوع">${F_ICONS.arrowRight}</button>
      <div class="ai-avatar">${F_ICONS.spark}</div>
      <div class="ai-title-wrap">
        <b>المساعد الذكي</b>
        <div class="ai-sub-status"><i class="ai-dot"></i><span>متصل</span></div>
      </div>
    </div>
    <div class="ai-top-left">
      ${isDev ? `<button class="ai-headset-btn ${ai.staffTab === 'staff' ? 'on' : ''}" data-act="ai-dev-toggle" title="بلاغات المستخدمين">📩</button>` : ''}
      <button class="ai-headset-btn ${ai.showTicket ? 'on' : ''}" data-act="tkt-toggle" aria-label="تذكرة للدعم" title="تواصل مع الدعم">${F_ICONS.headset}</button>
    </div>
  </header>`;

  if (isDev && ai.staffTab === 'staff') {
    const list = ai.staffTickets === null ? skel(3) : !ai.staffTickets.length ? empty('لا توجد بلاغات', 'ستظهر هنا بلاغات الطلاب والزوار للرد عليها.')
      : ai.staffTickets.map(t => {
          const catLbl = (TICKET_CATS.find(c => c[0] === t.category) || ['', 'بلاغ'])[1];
          const isReplying = ai.replyId === t.id;
          return `<div class="card" style="margin-bottom:10px">
            <div class="chips" style="margin-bottom:6px">
              ${chip(`${catLbl} #${t.id}`)}
              ${t.status === 'replied' ? chip('تم الرد ✅', 'ok') : chip('بانتظار الرد', 'warn')}
              ${t.theory_group_id ? chip(t.theory_group_id) : chip('زائر')}
            </div>
            <b>${esc(t.sender_name)}${t.academic_no ? ` · <bdi>${esc(t.academic_no)}</bdi>` : ''}</b>
            <p style="margin:6px 0">${esc(t.body)}</p>
            <div class="meta"><span>${fmtDT(t.created_at)}</span></div>
            ${t.reply_text ? `<div class="preview" style="margin-top:8px"><b>ردك:</b>\n${esc(t.reply_text)}</div>` : ''}
            ${isReplying ? `<div style="margin-top:10px">
                <textarea class="field area" data-f="ai.replyText" rows="2" placeholder="اكتب ردك ليصل للمستخدم في البوت وفي محادثة الدعم…">${esc(ai.replyText)}</textarea>
                <div class="row-actions">
                  <button class="btn sm primary" data-act="tkt-reply-send" data-id="${t.id}" ${ai.tBusy ? 'disabled' : ''}>إرسال الرد</button>
                  <button class="btn sm" data-act="tkt-reply-cancel">إلغاء</button>
                </div>
              </div>`
              : `<div class="row-actions" style="margin-top:10px"><button class="btn sm" data-act="tkt-reply-open" data-id="${t.id}">${t.reply_text ? 'تحديث الرد' : '💬 الرد على البلاغ'}</button></div>`}
          </div>`;
        }).join('');
    return `<div class="ai-screen">${topHeader}<div class="ai-stream">${list}</div></div>`;
  }

  const hist = (info && info.history) || [];
  const quickChips = B.kind() === 'guest'
    ? [
        ['كيف أربط رقمي الأكاديمي؟', 'tasks', 'ربط رقمي الأكاديمي'],
        ['كيف أتصفح الملفات والاختبارات؟', 'folder', 'الملفات والاختبارات'],
      ]
    : [
        ['ما هي واجباتي المفتوحة؟', 'tasks', 'واجباتي المفتوحة'],
        ['ما هي المواد المعرّضة للغياب؟', 'cal', 'المواد المعرّضة للغياب'],
        ['كم نسبة حضوري؟', 'cal', 'كم نسبة حضوري؟'],
        ['أين أجد الملخصات والنماذج؟', 'folder', 'الملخصات والنماذج'],
      ];

  const streamItems = [];
  if (!hist.length && !(ai.tickets && ai.tickets.length)) {
    streamItems.push(renderBotCard(
      'welcome',
      '',
      `أهلًا بك 👋 أنا المساعد الذكي ل${B.cohortTitle()}.\nاسألني عن نسبة حضورك، واجباتك المفتوحة، التظلمات، أو ابحث عن أي ملخص أو محاضرة، أو اضغط أيقونة السماعة 🎧 في الأعلى لمراسلة الدعم المباشر.`,
      'من دليل الدفعة'
    ));
  }

  /* كل الرسائل (أسئلة المساعد + بلاغات الدعم وردودها) في خط زمني واحد مرتَّب بالوقت،
     فلا تبقى البلاغات ثابتة في الأسفل: ما يصل لاحقًا يظهر لاحقًا. */
  chatEvents(hist, ai.tickets).forEach(ev => {
    if (ev.type === 'q') {
      streamItems.push(
        `<div class="ai-user-msg">
          <div class="ai-user-bubble">${esc(ev.h.question)}</div>
          <span class="ai-user-time">${esc(fmtTime(ev.h.created_at))}</span>
        </div>`,
        renderBotCard(`h_${ev.idx}`, ev.h.question, ev.h.answer)
      );
    } else if (ev.type === 't' || ev.type === 'tq') {
      const catLbl = (TICKET_CATS.find(c => c[0] === ev.t.category) || ['', 'بلاغ'])[1];
      streamItems.push(
        `<div class="ai-user-msg">
          <div class="ai-user-bubble">🎧 ${esc(catLbl)}: ${esc(ev.t.body)}</div>
          <span class="ai-user-time">${esc(fmtTime(ev.t.created_at))}</span>
        </div>`
      );
      if (ev.type === 't') {
        streamItems.push(renderBotCard(`tw_${ev.t.id}`, '', `تم استلام رسالتك (#${ev.t.id}) وهي قيد المراجعة، وسيظهر الرد هنا ويصلك إشعار في البوت فور الرد.`, 'نظام الدعم'));
      }
    } else {
      streamItems.push(renderBotCard(`t_${ev.t.id}`, '', `👨‍💻 رد الدعم على بلاغك #${ev.t.id}:\n${ev.t.reply_text}`, 'رد الدعم المباشر'));
    }
  });

  if (ai.busy && ai.pendingQ) {
    streamItems.push(
      `<div class="ai-user-msg"><div class="ai-user-bubble">${esc(ai.pendingQ)}</div><span class="ai-user-time">الآن</span></div>`,
      `<div class="ai-bot-card"><div class="ai-bot-text">••• جاري التحليل وإعداد الإجابة…</div></div>`
    );
  }

  if (ai.lastFiles && ai.lastFiles.length) {
    streamItems.push(`<div class="ai-bot-card">
      <b style="display:block;margin-bottom:8px">📚 ملفات مطابقة لسؤالك:</b>
      ${ai.lastFiles.map(f => `<div class="row" style="align-items:center">
        <span>${esc(f.title)} · <small class="muted">${esc(f.subject)}</small></span>
        <button class="f-send-circle" style="width:38px;height:38px" data-act="f-send" data-id="${f.id}">${F_ICONS.plane}</button>
      </div>`).join('')}
    </div>`);
  }

  const draftTBody = ai.tBody || B.ls.get(draftKey('ticket_body')) || '';
  if (ai.showTicket) {
    streamItems.push(`<div class="ai-bot-card" style="border-color:var(--brand)">
      <b>🎧 إرسال تذكرة للدعم / المطوّر</b>
      <p class="hint" style="margin:4px 0 10px">اختر نوع الرسالة واكتب التفاصيل، وسيظهر الرد هنا في المحادثة ويصلك إشعار في البوت.</p>
      ${seg('ai-tcat', TICKET_CATS, ai.tCat)}
      <textarea class="field area" style="margin-top:10px" data-f="ai.tBody" maxlength="1500" rows="3" placeholder="اكتب رسالتك أو استفسارك هنا…">${esc(draftTBody)}</textarea>
      <div class="err" role="alert">${esc(ai.err)}</div>
      <div class="row-actions">
        <button class="btn primary sm" data-act="tkt-submit" ${ai.tBusy ? 'disabled' : ''}>إرسال الرسالة</button>
        <button class="btn sm" data-act="tkt-toggle">إغلاق</button>
      </div>
    </div>`);
  }

  const chipsHTML = quickChips.map(([q, ic, lbl]) =>
    `<button class="ai-qchip" data-act="ai-quick" data-q="${esc(q)}">${ico(ic, 'sm')}<span>${esc(lbl)}</span></button>`
  ).join('');

  return `<div class="ai-screen">
    ${topHeader}
    <div class="ai-stream" id="support-chat-stream">${streamItems.join('')}</div>
    <div class="ai-bottom-dock">
      <div class="ai-chips-row">${chipsHTML}</div>
      <div class="ai-composer">
        <input class="ai-input" data-f="ai.q" placeholder="اكتب سؤالك هنا..." value="${esc(ai.q)}">
        <button class="ai-send-circle" data-act="ai-ask" aria-label="إرسال" ${ai.busy ? 'disabled' : ''}>${F_ICONS.plane}</button>
      </div>
    </div>
  </div>`;
}
B.views.assistant = renderAssistantView;

async function sendAssistantQuestion(qText) {
  const inp = document.querySelector('[data-f="ai.q"]');
  if (!qText && inp) P.ai.q = inp.value;
  const q = (qText || P.ai.q || '').trim();
  if (!q) return B.toast('اكتب سؤالك أولًا.');
  P.ai.busy = true; P.ai.err = ''; P.ai.pendingQ = q; P.ai.q = ''; rerender(); scrollChatToBottom();
  const r = await call('/api/assistant/ask', 'POST', { question: q });
  P.ai.busy = false; P.ai.pendingQ = '';
  if (r.status === 200 && r.data) {
    if (!P.ai.info) P.ai.info = { history: [] };
    P.ai.info.history = [...(P.ai.info.history || []), { question: q, answer: r.data.answer, created_at: new Date().toISOString() }];
    P.ai.info.remaining = r.data.remaining;
    P.ai.info.used = r.data.used;
    P.ai.info.limit = r.data.limit;
    if (r.data.attendance) P.ai.info.attendance = r.data.attendance;
    P.ai.lastFiles = r.data.files || [];
    if (r.data.needs_ticket) P.ai.showTicket = true;
  } else if (r.status === 429) {
    if (P.ai.info) P.ai.info.remaining = 0;
    P.ai.showTicket = true;
    B.toast('هذا السؤال يحتاج رصيد الذكاء الاصطناعي وقد انتهى اليوم. الأسئلة الجاهزة متاحة، أو راسل الدعم.');
  } else {
    B.toast(failMsg(r));
  }
  rerender();
  scrollChatToBottom();
}

Object.assign(B.actions, {
  'ai-quick': d => sendAssistantQuestion(d.q),
  'ai-ask': () => sendAssistantQuestion(P.ai.q),
  'ai-react': d => {
    const cur = P.ai.reactions[d.idx];
    P.ai.reactions[d.idx] = cur === d.v ? '' : d.v;
    B.toast(d.v === 'up' ? 'شكرًا لتقييمك الإيجابي 👍' : 'شكرًا لملاحظتك، سنحسّن الإجابات 🙏');
    rerender();
  },
  'tkt-toggle': () => { P.ai.staffTab = 'chat'; P.ai.showTicket = !P.ai.showTicket; P.ai.err = ''; rerender(); scrollChatToBottom(); },
  'ai-dev-toggle': () => {
    P.ai.staffTab = P.ai.staffTab === 'staff' ? 'chat' : 'staff';
    rerender();
    if (P.ai.staffTab === 'staff' && P.ai.staffTickets === null) {
      swr('staff-tickets', '/api/staff/tickets', x => { P.ai.staffTickets = x || []; rerender(); });
    }
  },
  'ai-tcat': d => { P.ai.tCat = d.v; rerender(); },
  'tkt-submit': async () => {
    const area = document.querySelector('[data-f="ai.tBody"]');
    if (area) P.ai.tBody = area.value;
    const body = (P.ai.tBody || B.ls.get(draftKey('ticket_body')) || '').trim();
    P.ai.tBody = body; P.ai.tBusy = true; P.ai.err = ''; rerender();
    const r = await call('/api/tickets', 'POST', { category: P.ai.tCat, body });
    P.ai.tBusy = false;
    if (r.status !== 200) { P.ai.err = failMsg(r); return rerender(); }
    B.ls.set(draftKey('ticket_body'), null);
    B.ls.delPrefix('my-tickets');
    P.ai.tBody = ''; P.ai.showTicket = false;
    B.toast('تم إرسال رسالتك للدعم وظهرت في المحادثة ✅');
    loadAssistant(true);
  },
  'ai-dev-tab': d => {
    P.ai.staffTab = d.v;
    rerender();
    if (d.v === 'staff' && P.ai.staffTickets === null) {
      swr('staff-tickets', '/api/staff/tickets', x => { P.ai.staffTickets = x || []; rerender(); });
    }
  },
  'tkt-reply-open': d => {
    const id = +d.id;
    const t = (P.ai.staffTickets || []).find(x => x.id === id);
    P.ai.replyId = id; P.ai.replyText = (t && t.reply_text) || ''; rerender();
  },
  'tkt-reply-cancel': () => { P.ai.replyId = null; P.ai.replyText = ''; rerender(); },
  'tkt-reply-send': async d => {
    const id = +d.id;
    P.ai.tBusy = true; rerender();
    const r = await call(`/api/staff/tickets/${id}/reply`, 'POST', { reply: P.ai.replyText });
    P.ai.tBusy = false;
    if (r.status !== 200) return B.toast(failMsg(r));
    P.ai.replyId = null; P.ai.replyText = '';
    B.ls.delPrefix('staff-tickets');
    B.toast('أُرسل الرد للمستخدم في البوت والتطبيق ✅');
    swr('staff-tickets', '/api/staff/tickets', x => { P.ai.staffTickets = x || []; rerender(); }, true);
  },
});


/* =====================================================================
   المرحلة 3 — 4) إدارة الحضور النظري
   ===================================================================== */
function allowedAttendanceGroups() {
  const all = B.theoryGroups();
  return all.filter(g => (S.boot.perms || []).some(p =>
    ['attendance.session', 'attendance.scan', 'attendance.admin'].includes(p.k) && (p.s === 'any' || p.g === g)
  ));
}

function loadAttendanceAdmin(force = false) {
  const adm = P.attAdm;
  const groups = allowedAttendanceGroups();
  if (!adm.group || !groups.includes(adm.group)) adm.group = groups[0] || B.firstTheory();
  const subs = S.boot.subjects || [];
  if (!adm.subject_id && subs.length) adm.subject_id = subs[0].id;
  if (!adm.cSubj && subs.length) adm.cSubj = subs[0].id;
  swr(`att-adm:${adm.group}`, `/api/staff/attendance?group=${adm.group}`, d => {
    if (d) {
      adm.data = d;
      if (d.open_session) {
        swr(`att-sess:${d.open_session.id}`, `/api/staff/attendance/sessions/${d.open_session.id}`, sd => {
          if (sd) { adm.sessDetail = sd; rerender(); }
        }, force);
      } else {
        adm.sessDetail = null;
      }
      rerender();
    }
  }, force);
}

async function submitAttendanceScan(sessionId, rawCode) {
  const code = String(rawCode || P.attAdm.scanCode || '').trim();
  if (!code) return B.toast('أدخل الرقم الأكاديمي أو امسح الباركود.');
  const r = await call(`/api/staff/attendance/sessions/${sessionId}/scan`, 'POST', { code });
  if (r.status === 200 && r.data) {
    P.attAdm.lastScan = r.data;
    P.attAdm.scanCode = '';
    loadAttendanceAdmin(true);
  } else {
    P.attAdm.lastScan = { status: 'error', msg: failMsg(r) };
    rerender();
  }
}

B.views['att-admin'] = () => {
  const adm = P.attAdm, d = adm.data, subs = S.boot.subjects || [], groups = allowedAttendanceGroups();
  const grpBar = groups.length > 1
    ? `<div class="scroll-x" style="margin-bottom:10px">${groups.map(g => pill(g, 'att-grp', `data-g="${g}"`, adm.group === g)).join('')}</div>`
    : `<div style="margin-bottom:10px">${chip(`المجموعة النظرية: ${adm.group}`)}</div>`;

  if (!d) return grpBar + skel(3);

  const subTabs = [['live', 'الجلسة والمسح']];
  if (d.can_admin) {
    subTabs.push(['devices', 'أجهزة المسح'], ['correct', 'تصحيح الحضور'], ['export', 'تصدير Excel']);
  }
  const tabNav = subTabs.length > 1 ? `<div style="margin-bottom:12px">${seg('att-tab', subTabs, adm.tab)}</div>` : '';

  if (adm.tab === 'devices' && d.can_admin) {
    const devs = d.devices || [];
    return `${grpBar}${tabNav}
      <div class="card">
        <b>إضافة جهاز مسح (Open Ticket Scanner)</b>
        <p class="hint" style="margin:4px 0 10px">أنشئ مفتاحًا خاصًا لكل جهاز مسح. يظهر المفتاح مرة واحدة فقط عند الإنشاء ويمكنك إلغاؤه في أي وقت، ويدعم المسح المتزامن من عدة أجهزة.</p>
        <div class="search-box" style="margin-bottom:0">
          <input class="field" data-f="attAdm.devLabel" maxlength="60" placeholder="اسم الجهاز (مثال: هاتف المندوب 1)" value="${esc(adm.devLabel)}">
          <button class="btn primary" data-act="att-dev-add" ${adm.busy ? 'disabled' : ''}>إنشاء مفتاح</button>
        </div>
        ${adm.newKey ? `<div class="key-box">
          <b>⚠️ انسخ الرابط/المفتاح الآن (يظهر مرة واحدة فقط):</b><br>
          <span>Key: ${esc(adm.newKey.token)}</span><br>
          <span>URL: ${esc(adm.newKey.scanner_url)}</span>
          <button class="btn sm primary" style="margin-top:8px" data-act="copy-text" data-text="${esc(adm.newKey.scanner_url)}">📋 نسخ رابط الماسح</button>
        </div>` : ''}
      </div>
      <div class="sec">الأجهزة الفعّالة (${devs.length})</div>
      ${!devs.length ? empty('لا توجد أجهزة مضافة', 'أضف جهازًا أعلاه للبدء بالمسح عبر Open Ticket Scanner.')
        : devs.map(dv => `<div class="item">
            <div class="item-main"><b>${esc(dv.label)}</b>
              <div class="meta"><code>${esc(dv.token_prefix)}…</code><span>· المسحات: ${dv.scans_count}</span></div></div>
            <button class="btn sm danger" style="width:auto" data-act="att-dev-rev" data-id="${dv.id}">إلغاء</button>
          </div>`).join('')}`;
  }

  if (adm.tab === 'correct' && d.can_admin) {
    const corrs = d.corrections || [];
    const lecs = Array.from({ length: 12 }, (_, i) => i + 1).map(n =>
      `<button class="num" data-act="att-clec" data-n="${n}" aria-pressed="${adm.cLec === n}">م${n}</button>`
    ).join('');
    return `${grpBar}${tabNav}
      <div class="card">
        <b>تصحيح حضور طالب (مع السبب والسجل)</b>
        <div class="sec">المادة</div>
        <div class="scroll-x wrap">${subs.map(s => pill(s.name, 'att-csubj', `data-id="${s.id}"`, adm.cSubj === s.id)).join('')}</div>
        <div class="sec">رقم المحاضرة النظرية</div>
        <div class="nums">${lecs}</div>
        <div class="sec">الرقم الأكاديمي للطالب</div>
        <input class="field" dir="ltr" inputmode="numeric" data-f="attAdm.cNo" placeholder="20260017" value="${esc(adm.cNo)}">
        <div class="sec">الحالة الجديدة</div>
        ${seg('att-cstat', [['present', 'حاضر ✅'], ['absent', 'غائب ❌']], adm.cStatus)}
        <div class="sec">سبب التصحيح <small class="muted">(إلزامي)</small></div>
        <input class="field" data-f="attAdm.cReason" maxlength="300" placeholder="مثال: عذر طبي معتمد / خطأ في المسح" value="${esc(adm.cReason)}">
        <div class="err" role="alert">${esc(adm.err)}</div>
        <button class="btn primary" style="margin-top:10px" data-act="att-correct-save" ${adm.busy ? 'disabled' : ''}>حفظ التصحيح في السجل</button>
      </div>
      <div class="sec">سجل آخر التصحيحات</div>
      ${!corrs.length ? empty('لا توجد تصحيحات مسجّلة', 'كل تصحيح يُوثّق هنا مع السبب والوقت.')
        : corrs.map(c => `<div class="item col">
            <div class="item-main"><b>${esc(c.student_name)} · <bdi>${esc(c.student_no)}</bdi></b>
              <div class="meta">${chip(c.subject)}${chip(`م${c.lecture_no}`)}${chip(c.new_status === 'present' ? 'حاضر' : 'غائب', c.new_status === 'present' ? 'ok' : 'bad')}</div>
              <p class="desc">السبب: ${esc(c.reason)}</p></div>
          </div>`).join('')}`;
  }

  if (adm.tab === 'export' && d.can_admin) {
    return `${grpBar}${tabNav}
      <div class="card">
        <b>تصدير كشف الحضور (Excel: م1–م12)</b>
        <p class="hint" style="margin:6px 0 12px">يحتوي الملف على: الرقم الأكاديمي، الاسم، وحالة كل محاضرة نظرية من م1 إلى م12 (1 حاضر، 0 غائب) مع الإجمالي والنسبة.</p>
        <div class="sec" style="margin-top:0">اختر المادة</div>
        <div class="scroll-x wrap">${subs.map(s => pill(s.name, 'att-csubj', `data-id="${s.id}"`, adm.cSubj === s.id)).join('')}</div>
        <button class="btn primary" style="margin-top:16px" data-act="att-exp-bot">📥 أرسل ملف Excel إلى محادثتي في البوت</button>
        <button class="btn" data-act="att-exp-dl">⬇️ تحميل مباشر من المتصفح</button>
      </div>`;
  }

  /* تبويب الجلسة والمسح المباشر */
  const open = d.open_session;
  const needAbsentFlag = !d.can_session_direct && d.can_session_absent;
  let liveBox = '';

  if (open) {
    const scanMsg = adm.lastScan
      ? (adm.lastScan.status === 'ok'
          ? `<div class="card" style="margin:10px 0;border-color:var(--ok)"><b>✅ تم تحضير: ${esc(adm.lastScan.student.name)}</b><div class="meta"><code>${esc(adm.lastScan.student.no)}</code> · عملي ${esc(adm.lastScan.student.practical)}</div></div>`
          : adm.lastScan.status === 'already_scanned'
          ? `<div class="card" style="margin:10px 0;border-color:var(--warn)"><b>⚠️ مسجّل مسبقًا: ${esc(adm.lastScan.student.name)}</b></div>`
          : adm.lastScan.status === 'wrong_group'
          ? `<div class="card" style="margin:10px 0;border-color:var(--bad)"><b>❌ مجموعة مختلفة (${esc(adm.lastScan.student_group)})</b></div>`
          : `<div class="card" style="margin:10px 0;border-color:var(--bad)"><b>❌ ${esc(adm.lastScan.msg || 'لم يُعثر على الطالب')}</b></div>`)
      : '';

    const stuRows = (adm.sessDetail && adm.sessDetail.students)
      ? `<div class="sec">الحاضرون في الجلسة (${open.present_count}/${d.total_students})</div>
         <div class="card" style="padding:4px 16px">
           ${adm.sessDetail.students.filter(s => s.status === 'present').map(s =>
             `<div class="row"><span>${esc(s.name)}</span><code>${esc(s.no)}</code></div>`
           ).join('') || '<p class="hint" style="text-align:center">لم يُمسح أحد بعد.</p>'}
         </div>`
      : '';

    liveBox = `<div class="card" style="border-color:var(--brand)">
      <div class="chips" style="margin-bottom:6px">
        ${chip('جلسة مفتوحة الآن', 'ok')}${chip(open.group)}${chip(`المحاضرة ${open.lecture_no}`)}
        ${open.delegate_absent ? chip('بالنيابة لغياب المندوب', 'warn') : ''}
      </div>
      <h2 style="margin:0 0 6px;font-size:20px">${esc(open.subject)} — محاضرة ${open.lecture_no}</h2>
      <div class="row"><span>الحضور المسجّل حتى الآن</span><b>${open.present_count} من ${d.total_students}</b></div>
      <div class="search-box" style="margin-top:12px;margin-bottom:0">
        <input class="field" dir="ltr" inputmode="numeric" data-f="attAdm.scanCode" placeholder="الرقم الأكاديمي للتحضير اليدوي…" value="${esc(adm.scanCode)}">
        <button class="btn primary" data-act="att-scan-go" data-id="${open.id}">تحضير</button>
      </div>
      <div class="row-actions" style="margin-top:10px">
        <button class="btn sm danger" data-act="att-close" data-id="${open.id}">🔒 إغلاق الجلسة واعتماد الغياب</button>
      </div>
      <div class="row-actions" style="margin-top:8px">
        <button class="btn sm" data-act="att-cancel" data-id="${open.id}" data-subj="${esc(open.subject)}" data-n="${open.lecture_no}" data-cnt="${open.present_count}">↩️ فتحتُها بالخطأ — تراجع بدون تسجيل غياب</button>
      </div>
    </div>${scanMsg}${stuRows}`;
  } else {
    const lecs = Array.from({ length: 12 }, (_, i) => i + 1).map(n =>
      `<button class="num" data-act="att-lec" data-n="${n}" aria-pressed="${adm.lecture_no === n}">م${n}</button>`
    ).join('');
    liveBox = `<div class="card">
      <b>فتح جلسة حضور نظري جديدة</b>
      <p class="hint" style="margin:4px 0 10px">تُفتح جلسة واحدة فقط لكل مجموعة نظرية في الوقت نفسه. عند إغلاقها يُعتمد الحضور ويُحسب غير المسجلين غائبين.</p>
      <div class="sec" style="margin-top:4px">المادة</div>
      <div class="scroll-x wrap">${subs.map(s => pill(s.name, 'att-subj', `data-id="${s.id}"`, adm.subject_id === s.id)).join('')}</div>
      <div class="sec">رقم المحاضرة النظرية (1–12)</div>
      <div class="nums">${lecs}</div>
      ${needAbsentFlag ? `<div class="row" style="margin-top:10px">
        <span style="color:var(--text)">فتح الجلسة بالنيابة لغياب المندوب</span>
        <button class="tg" role="switch" aria-checked="${!!adm.delegate_absent}" data-act="att-abs-toggle"><i></i></button>
      </div>` : ''}
      <div class="err" role="alert">${esc(adm.err)}</div>
      <button class="btn primary" style="margin-top:8px" data-act="att-open" ${adm.busy || (needAbsentFlag && !adm.delegate_absent) ? 'disabled' : ''}>فتح جلسة الحضور الآن</button>
    </div>`;
  }

  const prev = (d.sessions || []).filter(s => !open || s.id !== open.id);
  const prevHTML = prev.length
    ? `<div class="sec">الجلسات السابقة</div>` + prev.map(s => `<div class="item">
        <div class="item-main"><b>${esc(s.subject)} · المحاضرة ${s.lecture_no}</b>
          <div class="meta">${s.status === 'closed' ? chip('مغلقة', 'ok') : chip('مفتوحة', 'warn')}${s.delegate_absent ? chip('بالنيابة') : ''}<span>· ${fmtD(s.opened_at)}</span></div></div>
        <div class="item-side"><b>${s.present} حاضر</b><small>${s.absent} غائب</small></div>
      </div>`).join('')
    : '';

  return `${grpBar}${tabNav}${liveBox}${prevHTML}`;
};

Object.assign(B.actions, {
  'go-att-admin': () => { B.go('att-admin'); },
  'att-grp': d => { P.attAdm.group = d.g; P.attAdm.data = null; rerender(); loadAttendanceAdmin(); },
  'att-tab': d => { P.attAdm.tab = d.v; P.attAdm.err = ''; rerender(); },
  'att-subj': d => { P.attAdm.subject_id = +d.id; rerender(); },
  'att-lec': d => { P.attAdm.lecture_no = +d.n; rerender(); },
  'att-abs-toggle': () => { P.attAdm.delegate_absent = !P.attAdm.delegate_absent; rerender(); },
  'att-open': () => {
    const adm = P.attAdm;
    const subj = ((S.boot.subjects || []).find(s => s.id === adm.subject_id) || {}).name || '';
    ask(`فتح جلسة حضور:\n${subj} — المحاضرة ${adm.lecture_no}\nتأكد من المادة ورقم المحاضرة قبل المتابعة.`, async () => {
      adm.busy = true; adm.err = ''; rerender();
      const r = await call('/api/staff/attendance/sessions', 'POST', {
        subject_id: adm.subject_id, group: adm.group, lecture_no: adm.lecture_no, delegate_absent: adm.delegate_absent,
      });
      adm.busy = false;
      if (r.status !== 200) { adm.err = failMsg(r); return rerender(); }
      adm.lastScan = null;
      B.toast('تم فتح جلسة الحضور ✅');
      loadAttendanceAdmin(true);
    });
  },
  'att-cancel': d => {
    const cnt = +d.cnt || 0;
    const extra = cnt ? `\nسيُهمَل تحضير ${cnt} طالب مُسح في هذه الجلسة الخاطئة.` : '';
    ask(`التراجع عن جلسة «${d.subj} — المحاضرة ${d.n}»؟\nلن يُسجَّل أي طالب غائبًا، وتستطيع بعدها فتح الجلسة الصحيحة.${extra}`, async () => {
        const r = await call(`/api/staff/attendance/sessions/${d.id}/cancel`, 'POST', { delegate_absent: P.attAdm.delegate_absent });
      if (r.status !== 200) return B.toast(failMsg(r));
      P.attAdm.lastScan = null; P.attAdm.sessDetail = null;
      B.ls.delPrefix('att');
      B.toast('تم التراجع عن الجلسة ✅ لم يُسجَّل أي غياب');
      loadAttendanceAdmin(true);
    });
  },
  'att-scan-go': d => submitAttendanceScan(+d.id, P.attAdm.scanCode),
  'att-close': d => ask('إغلاق جلسة الحضور الآن؟ سيُعتمد الحضور ويُسجّل غير الممسوحين غائبين.', async () => {
    const r = await call(`/api/staff/attendance/sessions/${d.id}/close`, 'POST', { delegate_absent: P.attAdm.delegate_absent });
    if (r.status !== 200) return B.toast(failMsg(r));
    B.ls.delPrefix('att');
    B.toast(`أُغلقت الجلسة ✅ (حاضر: ${r.data.present} · غائب: ${r.data.absent})`);
    loadAttendanceAdmin(true);
  }),
  'att-dev-add': async () => {
    const adm = P.attAdm;
    if (!adm.devLabel || adm.devLabel.trim().length < 2) return B.toast('اكتب اسم الجهاز (حرفين على الأقل).');
    adm.busy = true; rerender();
    const r = await call('/api/staff/attendance/devices', 'POST', { group: adm.group, label: adm.devLabel.trim() });
    adm.busy = false;
    if (r.status !== 200) return B.toast(failMsg(r));
    adm.newKey = r.data; adm.devLabel = '';
    B.toast('تم إنشاء مفتاح الجهاز ✅ انسخه الآن');
    loadAttendanceAdmin(true);
  },
  'att-dev-rev': d => ask('إلغاء مفتاح هذا الجهاز؟ لن يستطيع المسح بعد الآن.', async () => {
    const r = await call(`/api/staff/attendance/devices/${d.id}`, 'DELETE');
    if (r.status === 200) { B.toast('تم إلغاء الجهاز'); loadAttendanceAdmin(true); } else B.toast(failMsg(r));
  }),
  'att-csubj': d => { P.attAdm.cSubj = +d.id; rerender(); },
  'att-clec': d => { P.attAdm.cLec = +d.n; rerender(); },
  'att-cstat': d => { P.attAdm.cStatus = d.v; rerender(); },
  'att-correct-save': async () => {
    const adm = P.attAdm; adm.busy = true; adm.err = ''; rerender();
    const r = await call('/api/staff/attendance/correct', 'POST', {
      subject_id: adm.cSubj, group: adm.group, lecture_no: adm.cLec,
      student_no: adm.cNo, status: adm.cStatus, reason: adm.cReason,
    });
    adm.busy = false;
    if (r.status !== 200) { adm.err = failMsg(r); return rerender(); }
    adm.cNo = ''; adm.cReason = '';
    B.ls.delPrefix('att');
    B.toast('تم حفظ تصحيح الحضور وتوثيقه في السجل ✅');
    loadAttendanceAdmin(true);
  },
  'att-exp-bot': async () => {
    const adm = P.attAdm;
    const r = await call('/api/staff/attendance/export-bot', 'POST', { subject_id: adm.cSubj, group: adm.group });
    B.toast(r.status === 200 ? 'أُرسل ملف Excel للحضور إلى محادثتك مع البوت ✅' : failMsg(r));
  },
  'att-exp-dl': () => {
    const adm = P.attAdm;
    if (B.DEMO) return B.toast('عرض تجريبي: ملف Excel يُرسل في البوت أو يُحمّل.');
    B.downloadAuth(`/api/staff/attendance/export?subject=${adm.cSubj}&group=${adm.group}`, 'attendance.xlsx');
  },
  'copy-text': d => {
    const text = d.text || '';
    const fallback = () => {
      try {
        const ta = document.createElement('textarea');
        ta.value = text; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
        document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
        const ok = document.execCommand('copy');
        ta.remove();
        B.toast(ok ? 'تم النسخ إلى الحافظة ✅' : 'اضغط مطولًا على الرابط في الأعلى وانسخه يدويًا.');
      } catch { B.toast('اضغط مطولًا على الرابط في الأعلى وانسخه يدويًا.'); }
    };
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => B.toast('تم النسخ إلى الحافظة ✅')).catch(fallback);
    } else fallback();
  },
});


/* =====================================================================
   المرحلة 3 — 5) فتح دورات التظلمات وتجميعها
   ===================================================================== */
function loadGrievanceAdmin(force = false) {
  swr('grv-adm', '/api/staff/grievances', d => { P.grvAdm.list = d || []; rerender(); }, force);
}
function newGrievanceForm() {
  const subs = S.boot.subjects || [];
  const isAny = (S.boot.perms || []).some(p => p.k === 'grievances.open' && p.s === 'any');
  const groups = groupsAllowed('theory', 'grievances.open');
  return {
    subject_id: subs.length ? subs[0].id : null,
    part: 'theory',
    theory_group: isAny ? 'ALL' : (groups[0] || B.firstTheory()),
    practical_groups: [],
    title: '',
    date: tomorrow(),
    time: '20:00',
  };
}

B.views['grv-admin'] = () => {
  const ga = P.grvAdm, subs = S.boot.subjects || [];
  const isAny = (S.boot.perms || []).some(p => p.k === 'grievances.open' && p.s === 'any');
  const tGroups = groupsAllowed('theory', 'grievances.open');

  if (ga.form) {
    const f = ga.form;
    const allPg = ((S.boot.groups && S.boot.groups.practical) || []).filter(p => f.theory_group === 'ALL' || p.theory === f.theory_group);
    return `<div class="card">
      <b>⚖️ فتح دورة تظلمات جديدة</b>
      <div class="sec">المادة</div>
      <div class="scroll-x wrap">${subs.map(s => pill(s.name, 'ga-subj', `data-id="${s.id}"`, f.subject_id === s.id)).join('')}</div>
      <div class="sec">النوع</div>
      ${seg('ga-part', [['theory', 'نظري'], ['practical', 'عملي']], f.part)}
      <div class="sec">المجموعة المستهدفة</div>
      <div class="scroll-x wrap">
        ${isAny ? pill('الدفعة كاملة', 'ga-grp', 'data-g="ALL"', f.theory_group === 'ALL') : ''}
        ${tGroups.map(g => pill(g, 'ga-grp', `data-g="${g}"`, f.theory_group === g)).join('')}
      </div>
      ${f.part === 'practical' ? `
        <div class="sec">المجموعات العملية <small class="muted">(اتركه فارغًا ليشمل كل عمليات المجموعة)</small></div>
        <div class="scroll-x wrap">${allPg.map(p => pill(p.id, 'ga-pg', `data-g="${p.id}"`, f.practical_groups.includes(p.id))).join('')}</div>
      ` : ''}
      <div class="sec">بيان / عنوان الدورة <small class="muted">(اختياري)</small></div>
      <input class="field" data-f="grvAdmForm.title" maxlength="140" placeholder="مثال: درجات الاختبار النصفي" value="${esc(f.title)}">
      <div class="sec">آخر موعد للتقديم والتعديل <small class="muted">(بتوقيت صنعاء)</small></div>
      ${dtPickerHTML('grvAdmForm.date', f.date, 'grvAdmForm.time', f.time)}
      <div class="err" role="alert">${esc(ga.err)}</div>
      <button class="btn primary" style="margin-top:12px" data-act="ga-open-save" ${ga.busy ? 'disabled' : ''}>🚀 فتح الدورة وإشعار الطلاب</button>
      <button class="btn" data-act="ga-form-cancel">إلغاء</button>
    </div>`;
  }

  const list = ga.list === null ? skel(3) : !ga.list.length ? empty('لا توجد دورات تظلمات', 'افتح دورة جديدة ليتمكن الطلاب من إرسال تظلماتهم وتُجمّع لك تلقائيًا.')
    : ga.list.map(c => `<button class="item" data-act="ga-detail" data-id="${c.id}">
        <div class="item-main"><b>${esc(c.subject)}${c.title ? ' · ' + esc(c.title) : ''}</b>
          <div class="meta">${chip(PART[c.part])}${chip(c.grp)}${c.status === 'closed' ? chip('مغلقة', 'bad') : chip('مفتوحة', 'ok')}</div></div>
        <div class="item-side"><b>${c.count}</b><small>تظلم</small></div>
      </button>`).join('');

  return `${list}<button class="fab" data-act="ga-new">${ico('idcard', 'sm')} دورة تظلمات جديدة</button>`;
};

B.views['grv-cycle'] = id => {
  const c = P.grvAdm.detail[id];
  if (!c) return skel(3);
  const items = c.items || [];
  return `<div class="card">
    <div class="chips" style="margin-bottom:6px">
      ${chip(PART[c.part])}${chip(c.theory_group_id || 'الدفعة كاملة')}
      ${c.status === 'closed' ? chip('مغلقة', 'bad') : chip('مفتوحة', 'ok')}
    </div>
    <h2 style="margin:0 0 4px;font-size:19px">${esc(c.subject)}${c.title ? ' — ' + esc(c.title) : ''}</h2>
    <div class="row"><span>آخر موعد</span><b>${fmtDT(c.closes_at)}</b></div>
    <div class="row"><span>إجمالي التظلمات</span><b>${items.length}</b></div>
  </div>
  <div class="sec">التجميع الموحّد لتسليمه للدكتور</div>
  <pre class="preview" dir="auto">${esc(c.compiled_text || '')}</pre>
  <div class="row-actions" style="margin-top:12px">
    <button class="btn sm primary" data-act="copy-text" data-text="${esc(c.compiled_text || '')}">📋 نسخ الكشف المجمّع</button>
    <button class="btn sm" data-act="ga-send-bot" data-id="${c.cycle_id}">📤 أرسل لي في البوت</button>
    ${c.status === 'open' ? `<button class="btn sm danger" data-act="ga-close" data-id="${c.cycle_id}">🔒 إغلاق الدورة الآن</button>` : ''}
  </div>`;
};

Object.assign(B.actions, {
  'ga-new': () => { P.grvAdm.form = newGrievanceForm(); P.grvAdm.err = ''; rerender(); window.scrollTo(0, 0); },
  'ga-form-cancel': () => { P.grvAdm.form = null; P.grvAdm.err = ''; rerender(); },
  'ga-subj': d => { P.grvAdm.form.subject_id = +d.id; rerender(); },
  'ga-part': d => { P.grvAdm.form.part = d.v; P.grvAdm.form.practical_groups = []; rerender(); },
  'ga-grp': d => { P.grvAdm.form.theory_group = d.g; P.grvAdm.form.practical_groups = []; rerender(); },
  'ga-pg': d => {
    const g = d.g, a = P.grvAdm.form.practical_groups;
    P.grvAdm.form.practical_groups = a.includes(g) ? a.filter(x => x !== g) : [...a, g];
    rerender();
  },
  'ga-open-save': async () => {
    const ga = P.grvAdm, f = ga.form;
    ga.busy = true; ga.err = ''; rerender();
    const r = await call('/api/staff/grievances', 'POST', {
      subject_id: f.subject_id,
      part: f.part,
      theory_group: f.theory_group === 'ALL' ? null : f.theory_group,
      practical_groups: f.part === 'practical' ? f.practical_groups : [],
      closes_local: `${f.date} ${f.time || '20:00'}`,
      title: f.title || null,
    });
    ga.busy = false;
    if (r.status !== 200) { ga.err = failMsg(r); return rerender(); }
    ga.form = null;
    B.ls.delPrefix('grv');
    B.toast('تم فتح دورة التظلمات وإشعار الطلاب ✅');
    loadGrievanceAdmin(true);
  },
  'ga-detail': d => { B.go('grv-cycle:' + d.id); },
  'ga-send-bot': async d => {
    const r = await call(`/api/staff/grievances/${d.id}/send-bot`, 'POST');
    B.toast(r.status === 200 ? 'أُرسل الكشف المجمّع إلى محادثتك مع البوت ✅' : failMsg(r));
  },
  'ga-close': d => ask('إغلاق دورة التظلمات الآن واعتماد الكشف المجمّع؟', async () => {
    const r = await call(`/api/staff/grievances/${d.id}/close`, 'POST');
    if (r.status === 200 && r.data) {
      P.grvAdm.detail[d.id] = r.data;
      B.ls.delPrefix('grv');
      B.toast('أُغلقت الدورة وأُرسل التجميع في البوت ✅');
      loadGrievanceAdmin(true);
      rerender();
    } else B.toast(failMsg(r));
  }),
});


/* ---------- ربط المسارات والعناوين وتحديث الشاشات عند الدخول ---------- */
Object.assign(B.routes, {
  assignments: 'assignments',
  files: 'files',
  'a-create': 'newasg',
  'a-upload': 'upload',
  attendance: 'attendance',
  affairs: 'affairs',
  assistant: 'assistant',
  'a-att': 'att-admin',
  'a-grv': 'grv-admin',
});
B.tabMap = Object.assign(B.tabMap, {
  asg: 'assignments', staff: 'assignments', newasg: 'assignments', 'newasg-done': 'assignments', 'newasg-wait': 'assignments',
  upload: 'files', 'upload-done': 'files',
  attendance: 'affairs', affairs: 'affairs', 'af-view': 'affairs',
});
Object.assign(B.titles, {
  files: 'الملفات', assignments: 'الواجبات', asg: 'الواجب', staff: 'إدارة الواجب',
  newasg: 'واجب جديد', 'newasg-done': 'تم النشر', 'newasg-wait': 'الإرفاق',
  upload: 'رفع ملف', 'upload-done': 'رفع ملف', notifs: 'الإشعارات',
  attendance: 'حضوري', affairs: 'شؤوني',
  'af-view:grv': 'تظلماتي', 'af-view:sup': 'المساعد الذكي', 'af-view:att': 'حضوري',
  'af-view:sub': 'تسليماتي', 'af-view:bar': 'باركود بطاقتي', 'af-view:set': 'الإعدادات',
  assistant: 'المساعد الذكي',
  'att-admin': 'جلسة الحضور', 'grv-admin': 'دورات التظلمات', 'grv-cycle': 'تجميع التظلمات',
});

B.enter = view => {
  const [v, id] = view.split(':');
  if (v === 'files') loadFiles();
  else if (v === 'assignments') loadAssignments();
  else if (v === 'asg') swr(`asg:${id}`, `/api/assignments/${id}`, d => { if (d) { P.d[id] = d; rerender(); } });
  else if (v === 'staff') { P.sd = { tab: 'sub', ext: false }; reloadStaff(id); }
  else if (v === 'notifs') {
    swr('notifs', '/api/notifications', d => {
      P.n = d || [];
      rerender();
      if ((S.home && S.home.unread > 0) || (P.n && P.n.some(x => !x.read_at))) {
        call('/api/notifications/read', 'POST').then(() => { if (S.home) S.home.unread = 0; });
      }
    });
  } else if (v === 'more' && can('affairs.use')) swr('settings', '/api/settings', d => { if (d) { P.set = d; rerender(); } });
  else if (v === 'attendance') loadMyAttendance();
  else if (v === 'af-view') loadAffairsSub(id);
  else if (v === 'assistant') loadAssistant();
  else if (v === 'att-admin') loadAttendanceAdmin();
  else if (v === 'grv-admin') loadGrievanceAdmin();
  else if (v === 'grv-cycle') swr(`grv-c:${id}`, `/api/staff/grievances/${id}`, d => { if (d) { P.grvAdm.detail[id] = d; rerender(); } });
};

/* إرسال بالسهم/Enter في حقول البحث والمساعد والمسح */
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const df = e.target.dataset && e.target.dataset.f;
  if (df === 'f.q') { e.preventDefault(); B.actions['f-smart-go'](); }
  else if (df === 'ai.q') { e.preventDefault(); B.actions['ai-ask'](); }
  else if (df === 'attAdm.scanCode' && P.attAdm.data && P.attAdm.data.open_session) {
    e.preventDefault();
    submitAttendanceScan(P.attAdm.data.open_session.id, e.target.value);
  }
});

/* ---------- بيانات المعاينة التجريبية (#demo=...) ---------- */
function demoRead(path) {
  const h = n => new Date(Date.now() + n * 3600e3).toISOString();
  if (path === '/api/home') return {
    items: [{ id: 11, number: 3, subject: 'شبكات الحاسوب ٢', part: 'theory', grp: 'CS2_G1', due_at: h(5) }],
    missed: [{ id: 9, number: 1, subject: 'قواعد البيانات', part: 'practical', grp: 'G2', due_at: h(-30) }],
    grievances: [{ id: 1, subject: 'شبكات الحاسوب ٢', part: 'theory', title: 'درجات النصفي', closes_at: h(18), submitted: false }],
    unread: 2,
  };
  if (path.startsWith('/api/files')) return [
    { id: 1, subject_id: 1, subject: 'نظم تشغيل', part: 'practical', content_type: 'lecture', label_kind: 'chapter', numbers: [2], title: 'محاضرة · Chapter 2', description: null, created_at: '2026-10-22T12:00:00Z', mine: true, can_delete: can('files.delete') },
    { id: 2, subject_id: 3, subject: 'قواعد البيانات', part: 'theory', content_type: 'exam', exam_kind: 'midterm', numbers: [], title: 'نموذج نصفي', description: null, created_at: '2026-10-22T10:00:00Z', mine: false, can_delete: can('files.delete') },
    { id: 3, subject_id: 4, subject: 'ثقافة وطنية', part: 'theory', content_type: 'lecture', label_kind: 'lecture', numbers: [1], title: 'محاضرة · المحاضرة 1', description: null, created_at: '2026-10-22T09:00:00Z', mine: false, can_delete: can('files.delete') },
    { id: 4, subject_id: 2, subject: 'شبكات الحاسوب ٢', part: 'theory', content_type: 'summary', label_kind: 'module', numbers: [9], title: 'ملخص · Module 9', description: null, created_at: '2026-10-21T14:00:00Z', mine: false, can_delete: can('files.delete') },
  ];
  if (path === '/api/assignments' || path === '/api/affairs/submissions') return [
    { id: 11, number: 3, subject: 'شبكات الحاسوب ٢', part: 'theory', grp: 'CS2_G1', due_at: h(5), closes_at: h(5), status: 'open', state: 'open', can_submit: true, bot_link: '#a_11', edit_bot_link: '#sub_11' },
    { id: 10, number: 2, subject: 'نظم تشغيل', part: 'practical', grp: 'G2', due_at: h(12), closes_at: h(14), status: 'open', submitted_at: h(-2), is_late: false, received_at: null, state: 'submitted', can_submit: true, bot_link: '#a_10', edit_bot_link: '#sub_10' },
    { id: 9, number: 1, subject: 'قواعد البيانات', part: 'practical', grp: 'G2', due_at: h(-30), closes_at: h(-6), status: 'closed', state: 'missed', can_submit: false },
  ];
  if (path.startsWith('/api/assignments/')) return {
    status: 'ok', id: 10, number: 2, subject: 'نظم تشغيل', part: 'practical', grp: 'G2',
    description: 'حل تمارين العملي بصيغة PDF أو ZIP.', due_at: h(12), closes_at: h(14), status_db: 'open', state: 'submitted', can_submit: true,
    attachments: [{ kind: 'document' }], allow_text: true, submission: { submitted_at: h(-2), is_late: false }, bot_link: 'https://t.me/bot?start=a_10', edit_bot_link: 'https://t.me/bot?start=sub_10',
  };
  if (path === '/api/staff/assignments') return [{ id: 11, number: 3, subject: 'شبكات الحاسوب ٢', part: 'theory', grp: 'CS2_G1', due_at: h(5), status: 'open', submitted: 34, total: 52 }];
  if (path.startsWith('/api/staff/assignments/')) return {
    status: 'ok', assignment: { id: 11, number: 3, subject: 'شبكات الحاسوب ٢', part: 'theory', grp: 'CS2_G1', due_at: h(5), status_db: 'open' },
    submitted: [{ no: '20260017', name: 'سارة محمد العلي', at: h(-2), late: false, received: true }],
    missing: [{ no: '20260021', name: 'خالد سالم' }],
  };
  if (path === '/api/notifications') return [
    { id: 1, kind: 'assignment_new', payload: { assignment_id: 11 }, title: 'واجب 3 — شبكات الحاسوب ٢ (نظري)', body: 'المجموعة: CS2_G1 · آخر موعد بعد 5 ساعات', read_at: null, created_at: h(-1) },
    { id: 2, kind: 'grievance_open', payload: { cycle_id: 1 }, title: '⚖️ فتح باب التظلمات — شبكات الحاسوب ٢ (نظري)', body: 'المجموعة: CS2_G1 · متاح للتقديم من شؤوني ← تظلماتي', read_at: null, created_at: h(-2) },
  ];
  if (path === '/api/settings') return { notify_assignment: true, notify_sharek: true, remind_24: true, remind_3: true, remind_1: false };
  if (path === '/api/attendance/mine') return [
    { subject_id: 1, subject: 'نظم تشغيل', held: 1, present: 1, absent: 0, lectures: Array.from({ length: 12 }, (_, i) => ({ n: i + 1, state: i < 1 ? 'present' : 'not_held' })) },
    { subject_id: 3, subject: 'قواعد البيانات', held: 1, present: 1, absent: 0, lectures: Array.from({ length: 12 }, (_, i) => ({ n: i + 1, state: i < 1 ? 'present' : 'not_held' })) },
    { subject_id: 2, subject: 'شبكات الحاسوب ٢', held: 3, present: 2, absent: 1, lectures: Array.from({ length: 12 }, (_, i) => ({ n: i + 1, state: i === 2 ? 'absent' : i < 3 ? 'present' : 'not_held' })) },
  ];
  if (path === '/api/grievances') return {
    status: 'ok',
    student: { academic_no: '20260017', full_name: 'سارة محمد العلي', theory_group_id: 'CS2_G1', practical_group_id: 'G2' },
    cycles: [{ id: 1, subject_id: 2, subject: 'شبكات الحاسوب ٢', part: 'theory', title: 'درجات النصفي', student_group: 'CS2_G1', closes_at: h(18), status: 'open', is_open: true, my_grievance: null }],
  };
  if (path === '/api/tickets' || path === '/api/staff/tickets') return [];
  if (path.startsWith('/api/staff/attendance/sessions/')) return {
    status: 'ok', session: { id: 1, subject: 'شبكات الحاسوب ٢', group: 'CS2_G1', lecture_no: 5, status: 'open' },
    students: [{ no: '20260017', name: 'سارة محمد العلي', practical: 'G2', status: 'present' }, { no: '20260021', name: 'خالد سالم', practical: 'G1', status: 'unmarked' }],
  };
  if (path.startsWith('/api/staff/attendance')) return {
    status: 'ok', group: 'CS2_G1', total_students: 52, can_admin: true, can_session_direct: true, can_session_absent: true, can_scan: true,
    open_session: null,
    sessions: [{ id: 1, subject_id: 2, subject: 'شبكات الحاسوب ٢', lecture_no: 4, status: 'closed', delegate_absent: false, opened_at: h(-48), present: 47, absent: 5 }],
    devices: [{ id: 1, label: 'هاتف المندوب', token_prefix: 'ots_a1b2c3', scans_count: 47 }],
    corrections: [],
  };
  if (path === '/api/staff/grievances') return [
    { id: 1, subject_id: 2, subject: 'شبكات الحاسوب ٢', part: 'theory', grp: 'CS2_G1', title: 'درجات النصفي', closes_at: h(18), status: 'open', count: 3 },
  ];
  if (path.startsWith('/api/staff/grievances/')) return {
    status: 'ok', cycle_id: 1, subject: 'شبكات الحاسوب ٢', part: 'theory', theory_group_id: 'CS2_G1', title: 'درجات النصفي', closes_at: h(18), status: 'open',
    items: [{ academic_no: '20260017', full_name: 'سارة محمد العلي', group: 'CS2_G1', reason: 'لم تُجمع درجة السؤال الثالث في الورقة.' }],
    compiled_text: '📋 كشف التظلمات المجمّع — شبكات الحاسوب ٢ (نظري)\n👥 المجموعة: CS2_G1\n📊 إجمالي التظلمات: 1\n─────────────\n1. سارة محمد العلي | 20260017 | CS2_G1\n   • السبب: لم تُجمع درجة السؤال الثالث في الورقة.',
  };
  return [];
}
function demoWrite(path) {
  if (path === '/api/barcode' || path === '/api/barcode/send') return {
    status: 'ok', academic_no: '20260017', full_name: 'سارة محمد العلي', theory_group_id: 'CS2_G1', practical_group_id: 'G2',
    used: 1, limit: 3, remaining: 2, svg: '<svg viewBox="0 0 240 80" width="240" height="80"><rect width="240" height="80" fill="#fff"/><text x="120" y="45" text-anchor="middle" font-family="monospace" font-size="18">|| ||| | |||| ||| || 20260017</text></svg>',
  };
  const demoAtt = [
    { subject_id: 1, subject: 'نظم تشغيل', held: 1, present: 1, absent: 0 },
    { subject_id: 3, subject: 'قواعد البيانات', held: 1, present: 1, absent: 0 },
    { subject_id: 2, subject: 'شبكات الحاسوب ٢', held: 3, present: 2, absent: 1 },
  ];
  if (path === '/api/assistant') return {
    status: 'ok', used: 1, limit: 10, remaining: 9,
    history: [{ question: 'كم نسبة حضوري؟', answer: 'نسبة حضورك الإجمالية حتى الآن 80%.', created_at: '2026-10-23T14:46:00Z' }],
    attendance: demoAtt,
  };
  if (path === '/api/assistant/ask') return {
    status: 'ok', answer: 'نسبة حضورك الإجمالية حتى الآن 80% في المحاضرات النظرية.', remaining: 8, used: 2, limit: 10, files: [], attendance: demoAtt,
  };
  return { status: 'ok', draft_id: 1, number: 4, bot_link: '#', notify_assignment: true, notify_sharek: true, remind_24: true, remind_3: true, remind_1: false };
}
})();
