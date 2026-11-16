/* المرحلة 4 (الأخيرة) في الواجهة:
   1) التقارير اليومية والقوالب والتقرير الأسبوعي الآلي.
   2) شارك (استفتاء باختيار واحد نهائي 2–10 خيارات، أو استقبال مشاركات متعددة مع النقل المباشر للبوت وإرسال الصور/الملفات للمشرف).
   3) الاختبارات MCQ (مطابقة للشاشات المرفقة في index.html تمامًا: بلا حروف عربية، مع حلقات اختيار ودعم كامل للأسئلة والإجابات الإنجليزية والعربية).
   4) شاشة الدكتور (واجبات مجموعاته فقط وتصدير Excel 1/0 لكل مجموعة منفردة).
   5) الإشعارات اليدوية ولوحة المطوّر الشاملة (تظهر البطاقات فقط وعند اختيار إحداها تفتح في شاشة كاملة مستقلة). */
(() => {
'use strict';
const B = window.BS, S = B.S, P = S.p2, esc = B.esc, ico = B.ico, can = B.can, swr = B.swr;

const PART = { theory: 'نظري', practical: 'عملي' };
const STUDY_DAYS = [[5, 'السبت'], [6, 'الأحد'], [0, 'الاثنين'], [1, 'الثلاثاء'], [2, 'الأربعاء'], [3, 'الخميس']];
const ORDINALS = ['الأولى', 'الثانية', 'الثالثة', 'الرابعة', 'الخامسة', 'السادسة'];
const ROLE_LABELS = { delegate: 'مندوب', deputy: 'نائب مندوب', committee: 'لجنة علمية', developer: 'مطوّر' };

const ICON_X = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M2 2l8 8M10 2l-8 8"/></svg>';
const ICON_TICK = '<svg viewBox="0 0 14 14" fill="none" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 7.5l3 3 6-7"/></svg>';
const ICON_CROSS = '<svg viewBox="0 0 14 14" fill="none" stroke-width="2.4" stroke-linecap="round"><path d="M3 3l8 8M11 3l-8 8"/></svg>';

const chip = (t, cls = '') => `<span class="chip ${cls}">${esc(t)}</span>`;
const pill = (label, act, data, on) => `<button class="pill" data-act="${act}" ${data} aria-pressed="${!!on}">${esc(label)}</button>`;
const seg = (act, opts, cur) => `<div class="seg" role="group">${opts.map(([v, l]) => `<button data-act="${act}" data-v="${v}" aria-pressed="${String(cur) === String(v)}">${esc(l)}</button>`).join('')}</div>`;
const skel = n => Array.from({ length: n }, () => '<div class="sk sk-row"></div>').join('');
const empty = (t, s) => `<div class="card empty"><b>${esc(t)}</b>${esc(s || '')}</div>`;
const ask = (msg, ok) => (B.tg && B.tg.showConfirm ? B.tg.showConfirm(msg, y => y && ok()) : window.confirm(msg) && ok());
const rerender = () => { if (!S.view.startsWith('flow')) B.render(); };
const fmtDT = iso => iso ? new Intl.DateTimeFormat('ar-u-nu-latn', { timeZone: 'Asia/Aden', weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', hour12: true }).format(new Date(iso)) : '';
const fmtD = iso => iso ? new Intl.DateTimeFormat('ar-u-nu-latn', { timeZone: 'Asia/Aden', day: 'numeric', month: 'short' }).format(new Date(iso)) : '';
const todaySanaa = () => new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10);
const jsDayToPyWeekday = isoDate => {
  const d = new Date(isoDate + 'T12:00:00Z');
  return (d.getUTCDay() + 6) % 7; // Monday=0 .. Sunday=6
};
const pad2 = n => String(n).padStart(2, '0');
const fmtTimer = s => `${pad2(Math.floor(s / 60))}:${pad2(Math.max(0, Math.floor(s % 60)))}`;
const shuffleArr = a => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const buzz = t => { try { B.tg && B.tg.HapticFeedback && (t === 'sel' ? B.tg.HapticFeedback.selectionChanged() : B.tg.HapticFeedback.notificationOccurred(t)); } catch { /* */ } };

async function call(path, method = 'GET', body) {
  if (B.DEMO) return { status: 200, data: demoWrite4(path, method, body) };
  try { return await B.api(path, { method, body }); } catch { return { status: 0, data: null }; }
}

/* تحميل ملف يتطلب ترويسة Authorization (window.open لا يرسلها) */
async function downloadAuth(path, fallbackName) {
  if (B.DEMO) return B.toast('عرض تجريبي');
  try {
    const r = await fetch(path, { headers: { Authorization: `tma ${B.INIT}` }, cache: 'no-store' });
    if (!r.ok) return B.toast(r.status === 404 ? 'لا توجد واجبات لهذه المجموعة.' : 'تعذّر تحميل الملف.');
    const m = /filename="?([^";]+)"?/.exec(r.headers.get('Content-Disposition') || '');
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url; a.download = (m && m[1]) || fallbackName;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch { B.toast('تعذّر التحميل. استخدم زر الإرسال في البوت.'); }
}

B.downloadAuth = downloadAuth;

function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || '');
      const idx = s.indexOf(',');
      resolve(idx >= 0 ? s.slice(idx + 1) : s);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/* ---------- حالة المرحلة 4 ---------- */
const P4 = S.p4 = {
  rep: { tab: 'write', group: '', date: todaySanaa(), ws: null, slots: [], note: '', tplDay: 5, tplSlots: [], weeklyText: '', busy: false, err: '' },
  sh: { txt: {}, list: null, staff: null, res: {}, resGroup: 'ALL', form: null, busy: false, err: '' },
  mcq: { step: 'home', subjectPick: null, examPick: null, timerOn: true, shuffleOn: false, instantOn: true, list: null, exam: null, run: null, up: null, busy: false, err: '' },
  doc: { dash: null, detail: {}, doctor_id: null, grp: null },
  ntf: { group: '', title: '', body: '', busy: false, err: '' },
  dev: {
    tab: 'students', ov: null, students: null, stuGroup: '', stuQ: '', stuForm: null, guests: null, cat: null,
    roleForm: { telegram_id: '', role: 'delegate', group: '' },
    subjForm: { name_ar: '', hashtag: '' },
    docForm: { name: '', telegram_id: '' },
    teachForm: { subject_id: '', part: 'theory', groups: [], doctor_id: '' },
    destUI: { kind: 'subject', subject_id: '', group: '', doctor_id: '', part: 'theory', chat_id: '', topic_id: '' },
    trash: null, activity: null, busy: false,
  },
};

const repDraftKey = (grp, dt) => `bs2:${B.UID}:draft:rep:${grp}:${dt}`;
const mcqQueueKey = () => `bs2:${B.UID}:mcq_queue`;

/* مزامنة طابور نتائج الاختبارات المحفوظة دون اتصال */
async function flushMcqQueue() {
  if (B.DEMO) return;
  const q = B.ls.get(mcqQueueKey()) || [];
  if (!q.length) return;
  const rem = [];
  for (const item of q) {
    const r = await call(`/api/mcq/${item.exam_id}/attempt`, 'POST', item);
    if (r.status !== 200 && r.status !== 404) rem.push(item);
  }
  B.ls.set(mcqQueueKey(), rem);
}


/* =====================================================================
   1) التقارير اليومية والقوالب والتقرير الأسبوعي
   ===================================================================== */
function allowedReportGroups() {
  const all = B.theoryGroups();
  return all.filter(g => (S.boot.perms || []).some(p => p.k === 'reports.write' && (p.s === 'any' || p.g === g)));
}
function resolveDoctorName(ws, subjectId, part, grp) {
  if (!ws || !ws.teaching || !subjectId) return '';
  const hit = ws.teaching.find(t => String(t.subject_id) === String(subjectId) && t.part === part && (!grp || t.group === grp))
    || ws.teaching.find(t => String(t.subject_id) === String(subjectId) && t.part === part);
  return (hit && hit.doctor) || '';
}
function defaultSlot(ws, mode = 'theory') {
  const subs = S.boot.subjects || [];
  const sid = subs.length ? subs[0].id : '';
  const sname = subs.length ? subs[0].name : '';
  const pgroups = (ws && ws.practical_groups) || B.practicalGroups();
  if (mode === 'practical_split') {
    return {
      mode: 'practical_split',
      items: pgroups.map(g => ({
        group: g,
        subject_id: sid,
        subject: sname,
        doctor: resolveDoctorName(ws, sid, 'practical', g),
        topics: '',
        assignment: '',
      })),
    };
  }
  const part = mode === 'practical_all' ? 'practical' : 'theory';
  return {
    mode,
    subject_id: sid,
    subject: sname,
    doctor: resolveDoctorName(ws, sid, part, ws && ws.group),
    topics: '',
    assignment: '',
  };
}
function applyTemplateToSlots(ws, weekday) {
  const tpls = (ws && ws.templates) || {};
  const raw = tpls[String(weekday)];
  if (!raw || !raw.length) return [defaultSlot(ws, 'theory'), defaultSlot(ws, 'theory')];
  const pgroups = (ws && ws.practical_groups) || B.practicalGroups();
  return raw.map(s => {
    if (s.mode === 'practical_split') {
      const byG = {};
      (s.items || []).forEach(it => { byG[it.group] = it; });
      return {
        mode: 'practical_split',
        items: pgroups.map(g => {
          const it = byG[g] || {};
          const sid = it.subject_id || ((S.boot.subjects || [])[0] || {}).id || '';
          const sname = it.subject || (((S.boot.subjects || []).find(x => x.id === +sid) || {}).name || '');
          return {
            group: g,
            subject_id: sid,
            subject: sname,
            doctor: it.doctor || resolveDoctorName(ws, sid, 'practical', g),
            topics: '',
            assignment: '',
          };
        }),
      };
    }
    const sid = s.subject_id || ((S.boot.subjects || [])[0] || {}).id || '';
    const sname = s.subject || (((S.boot.subjects || []).find(x => x.id === +sid) || {}).name || '');
    const part = s.mode === 'practical_all' ? 'practical' : 'theory';
    return {
      mode: s.mode || 'theory',
      subject_id: sid,
      subject: sname,
      doctor: s.doctor || resolveDoctorName(ws, sid, part, ws && ws.group),
      topics: '',
      assignment: '',
    };
  });
}

function loadReportsWorkspace(force = false) {
  const rep = P4.rep, groups = allowedReportGroups();
  if (!rep.group || !groups.includes(rep.group)) rep.group = groups[0] || B.firstTheory();
  swr(`rep:${rep.group}:${rep.date}`, `/api/reports/workspace?group=${rep.group}&date=${rep.date}`, d => {
    if (!d) return;
    rep.ws = d;
    const savedDraft = B.ls.get(repDraftKey(rep.group, rep.date));
    if (savedDraft && savedDraft.slots && savedDraft.slots.length) {
      rep.slots = savedDraft.slots;
      rep.note = savedDraft.note || '';
    } else if (d.current_report && d.current_report.slots) {
      rep.slots = d.current_report.slots;
      rep.note = d.current_report.note || '';
    } else {
      rep.slots = applyTemplateToSlots(d, jsDayToPyWeekday(rep.date));
      rep.note = '';
    }
    const tplRaw = (d.templates && d.templates[String(rep.tplDay)]) || [];
    rep.tplSlots = tplRaw.length ? JSON.parse(JSON.stringify(tplRaw)) : [defaultSlot(d, 'theory')];
    rep.weeklyText = (d.weekly_report && d.weekly_report.rendered_text) || d.compiled_weekly || '';
    rerender();
  }, force);
}

function saveReportLocalDraft() {
  const rep = P4.rep;
  if (rep.group && rep.date) {
    B.ls.set(repDraftKey(rep.group, rep.date), { slots: rep.slots, note: rep.note });
  }
}

function cleanBulletsJS(txt) {
  return String(txt || '').split('\n').map(l => l.replace(/^[\s•\-\*▪■]+/, '').trim()).filter(Boolean);
}

/* التكليف قد يكون عدة نقاط/أسئلة: سطر واحد «• التكليف: …» وأكثر من سطر كنقاط فرعية (مطابق لما يبنيه الخادم) */
function assignmentLinesJS(txt) {
  const items = cleanBulletsJS(txt);
  if (!items.length) return [];
  return items.length === 1 ? [`• التكليف: ${items[0]}`] : ['• التكليف:', ...items.map(i => `   ◦ ${i}`)];
}

function previewDailyReport(rep) {
  const d = new Date(rep.date + 'T12:00:00Z');
  const dayAr = new Intl.DateTimeFormat('ar-u-nu-latn', { weekday: 'long', day: 'numeric', month: 'long' }).format(d);
  const sep = '─────────────';
  const blocks = [];
  (rep.slots || []).forEach((s, idx) => {
    const ord = ORDINALS[idx] || String(idx + 1);
    if (s.mode === 'practical_split') {
      const sub = [];
      (s.items || []).forEach(it => {
        const bullets = cleanBulletsJS(it.topics);
        if (!it.subject || !bullets.length) return;
        const lines = [`■ ${it.subject} | ${it.group} عملي :`, ...bullets.map(b => `• ${b}`)];
        lines.push(...assignmentLinesJS(it.assignment));
        sub.push(lines.join('\n'));
      });
      if (sub.length) blocks.push(`🕗 المحاضرة ${ord}:\n\n` + sub.join('\n\n\n'));
    } else {
      const bullets = cleanBulletsJS(s.topics);
      if (!s.subject || !bullets.length) return;
      const stitle = s.mode === 'practical_all' ? `${s.subject} _ عملي` : s.subject;
      const head = `🕗 المحاضرة ${ord} | ${stitle}` + (s.doctor ? ` | ${s.doctor}` : '');
      const lines = [head, ...bullets.map(b => `• ${b}`)];
      lines.push(...assignmentLinesJS(s.assignment));
      blocks.push(lines.join('\n'));
    }
  });
  const out = [`📌 تقرير يوم ${dayAr} ${rep.group}`, sep];
  blocks.forEach(b => { out.push(b, sep); });
  const noteBullets = cleanBulletsJS(rep.note);
  if (noteBullets.length) {
    out.push('📌 ملاحظة:\n' + noteBullets.map(n => `• ${n}`).join('\n'));
  } else if (out.length > 2) {
    out.pop();
  }
  return out.join('\n\n');
}

B.views.reports = () => {
  const rep = P4.rep, ws = rep.ws, subs = S.boot.subjects || [], groups = allowedReportGroups();
  const grpBar = groups.length > 1
    ? `<div class="scroll-x" style="margin-bottom:10px">${groups.map(g => pill(g, 'rep-grp', `data-g="${g}"`, rep.group === g)).join('')}</div>`
    : `<div style="margin-bottom:10px">${chip(`المجموعة: ${rep.group}`)}</div>`;

  const topTabs = `<div style="margin-bottom:12px">${seg('rep-tab', [['write', 'تقرير اليوم'], ['tpl', 'القوالب'], ['weekly', 'الحصاد الأسبوعي']], rep.tab)}</div>`;
  if (!ws) return grpBar + topTabs + skel(3);

  if (rep.tab === 'tpl') {
    const dayBar = `<div class="scroll-x" style="margin-bottom:12px">${STUDY_DAYS.map(([w, l]) => pill(l, 'rep-tday', `data-w="${w}"`, rep.tplDay === w)).join('')}</div>`;
    const slotCards = (rep.tplSlots || []).map((s, idx) => {
      const ord = ORDINALS[idx] || (idx + 1);
      const body = s.mode === 'practical_split'
        ? (s.items || []).map((it, gIdx) => `<div class="row" style="align-items:center">
            <b>${esc(it.group)} عملي</b>
            <select class="field" style="width:auto;min-height:40px;font-size:14px" data-tpl-split="${idx}:${gIdx}">
              ${subs.map(sb => `<option value="${sb.id}" ${+it.subject_id === sb.id ? 'selected' : ''}>${esc(sb.name)}</option>`).join('')}
            </select>
          </div>`).join('')
        : `<select class="field" style="margin-top:8px;min-height:44px;font-size:15px" data-tpl-subj="${idx}">
            ${subs.map(sb => `<option value="${sb.id}" ${+s.subject_id === sb.id ? 'selected' : ''}>${esc(sb.name)}</option>`).join('')}
          </select>`;
      return `<div class="card" style="margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <b>المحاضرة ${ord}</b>
          <button class="btn sm danger" style="width:auto" data-act="rep-tpl-del-slot" data-i="${idx}">حذف الخانة</button>
        </div>
        ${seg(`rep-tpl-mode-${idx}`, [['theory', 'نظري'], ['practical_all', 'عملي موحّد'], ['practical_split', 'عملي منفصل']], s.mode)}
        ${body}
      </div>`;
    }).join('');
    return `${grpBar}${topTabs}${dayBar}
      <p class="hint" style="margin-top:0">اختر اليوم واضبط محاضراته المعتادة ليسهل عليك تعبئة التقرير اليومي تلقائيًا.</p>
      ${slotCards}
      <div class="row-actions">
        <button class="btn sm" data-act="rep-tpl-add-slot">+ إضافة محاضرة للقالب</button>
        <button class="btn sm primary" data-act="rep-tpl-save" ${rep.busy ? 'disabled' : ''}>حفظ قالب اليوم</button>
        <button class="btn sm danger" data-act="rep-tpl-clear">حذف القالب</button>
      </div>`;
  }

  if (rep.tab === 'weekly') {
    return `${grpBar}${topTabs}
      <div class="card">
        <b>📚 الحصاد الأسبوعي — ${esc(rep.group)}</b>
        <p class="hint" style="margin:4px 0 10px">يُجمّع آليًا من التقارير اليومية للمجموعة. راجعه وعدّله عند الحاجة قبل نشره يوم الخميس في topic المجموعة.</p>
        <textarea class="field area" data-f4="rep.weeklyText" rows="12" style="font-size:14px">${esc(rep.weeklyText)}</textarea>
        <div class="row-actions" style="margin-top:12px">
          <button class="btn primary sm" data-act="rep-weekly-save" data-pub="1" ${rep.busy ? 'disabled' : ''}>🚀 اعتماد ونشر في المجتمع</button>
          <button class="btn sm" data-act="rep-weekly-save" data-pub="0" ${rep.busy ? 'disabled' : ''}>💾 حفظ المراجعة</button>
          <button class="btn sm" data-act="copy-text" data-text="${esc(rep.weeklyText)}">📋 نسخ</button>
        </div>
      </div>`;
  }

  /* تبويب كتابة تقرير اليوم */
  const slotEditors = (rep.slots || []).map((s, idx) => {
    const ord = ORDINALS[idx] || (idx + 1);
    let inner = '';
    if (s.mode === 'practical_split') {
      inner = (s.items || []).map((it, gIdx) => `<div class="card" style="margin-top:8px;background:var(--surface-2);padding:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px">
          <b>■ ${esc(it.group)} عملي</b>
          <select class="field" style="width:auto;min-height:38px;font-size:14px" data-rep-split-subj="${idx}:${gIdx}">
            ${subs.map(sb => `<option value="${sb.id}" ${+it.subject_id === sb.id ? 'selected' : ''}>${esc(sb.name)}</option>`).join('')}
          </select>
        </div>
        <textarea class="field area" style="min-height:64px" data-rep-split-topics="${idx}:${gIdx}" rows="2" placeholder="ما تم أخذه في ${esc(it.group)}…">${esc(it.topics || '')}</textarea>
        <textarea class="field area" style="margin-top:6px;min-height:56px;font-size:14px" data-rep-split-hw="${idx}:${gIdx}" rows="2" placeholder="التكليف (اختياري) — كل سطر نقطة أو سؤال…">${esc(it.assignment || '')}</textarea>
      </div>`).join('');
    } else {
      inner = `<div style="margin-top:8px">
        <select class="field" style="min-height:44px;font-size:15px" data-rep-subj="${idx}">
          ${subs.map(sb => `<option value="${sb.id}" ${+s.subject_id === sb.id ? 'selected' : ''}>${esc(sb.name)}</option>`).join('')}
        </select>
        <div class="meta" style="margin:6px 2px">👨‍🏫 المدرس (تلقائي): <b>${esc(s.doctor || '—')}</b></div>
        <textarea class="field area" style="min-height:72px" data-rep-topics="${idx}" rows="2" placeholder="ما تم أخذه (كل سطر يتحول لنقطة • وتُمنع النقاط الفارغة)…">${esc(s.topics || '')}</textarea>
        <textarea class="field area" style="margin-top:8px;min-height:56px;font-size:14.5px" data-rep-hw="${idx}" rows="2" placeholder="التكليف (اختياري) — كل سطر نقطة أو سؤال…">${esc(s.assignment || '')}</textarea>
      </div>`;
    }
    return `<div class="card" style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <b>🕗 المحاضرة ${ord}</b>
        <button class="btn sm danger" style="width:auto" data-act="rep-del-slot" data-i="${idx}">حذف</button>
      </div>
      ${seg(`rep-mode-${idx}`, [['theory', 'نظري'], ['practical_all', 'عملي موحّد'], ['practical_split', 'عملي منفصل']], s.mode)}
      ${inner}
    </div>`;
  }).join('');

  return `${grpBar}${topTabs}
    <div class="card" style="margin-bottom:10px">
      <div class="dt-pick">
        <div class="dt-pick-head">📅 اختر تاريخ التقرير من التقويم</div>
        <input type="date" data-f4="rep.date" value="${esc(rep.date)}">
      </div>
      <div class="row-actions" style="margin-top:10px">
        <button class="btn sm" data-act="rep-load-tpl">تطبيق قالب هذا اليوم</button>
        <button class="btn sm" data-act="rep-custom">تقرير خاص بلا قالب</button>
        <button class="btn sm" data-act="rep-add-slot">+ محاضرة</button>
      </div>
    </div>
    ${slotEditors}
    <div class="card" style="margin-bottom:10px">
      <b>📌 ملاحظة ختامية <small class="muted">(اختيارية)</small></b>
      <textarea class="field area" style="margin-top:8px;min-height:68px" data-f4="rep.note" rows="2" placeholder="مثال: يوم الخميس اختبار عملي من البداية إلى موضوع subquery…">${esc(rep.note || '')}</textarea>
    </div>
    <div class="sec">معاينة التقرير بالصيغة المعتمدة</div>
    <pre class="preview" id="rep-live-preview" dir="auto">${esc(previewDailyReport(rep))}</pre>
    <div class="err" role="alert">${esc(rep.err)}</div>
    <button class="btn primary" data-act="rep-save" data-pub="1" ${rep.busy ? 'disabled' : ''}>🚀 حفظ ونشر في topic المجموعة</button>
    <button class="btn" data-act="rep-save" data-pub="0" ${rep.busy ? 'disabled' : ''}>💾 حفظ المسودة في القاعدة</button>`;
};


/* =====================================================================
   2) شارك (الطالب + الإدارة + النقل المباشر للبوت + إرسال الصور/الملفات للمشرف)
   ===================================================================== */
function loadSharek(force = false) {
  const ver = (S.boot && S.boot.versions && S.boot.versions.sharek) || 0;
  swr(`sharek:v${ver}`, '/api/sharek', d => { P4.sh.list = d || []; rerender(); focusSharekCard(); }, force);
}
/* عند القدوم من «يهمّني الآن» نمرّر الشاشة إلى الحملة المقصودة */
function focusSharekCard() {
  const id = P4.sh.focusId;
  if (!id || S.view !== 'sharek') return;
  const el = document.getElementById('sh-card-' + id);
  if (!el) return;
  P4.sh.focusId = null;
  setTimeout(() => { try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch { /* */ } }, 40);
}
function loadSharekStaff(force = false) {
  const ver = (S.boot && S.boot.versions && S.boot.versions.sharek) || 0;
  swr(`sharek-staff:v${ver}`, '/api/staff/sharek', d => { P4.sh.staff = d || []; rerender(); }, force);
}

B.views.sharek = () => {
  const sh = P4.sh, list = sh.list;
  const adminBtn = can('sharek.publish')
    ? `<button class="btn primary" style="margin-bottom:12px" data-act="go-sh-admin">${ico('poll', 'sm')} إدارة حملات شارك ونشر جديد</button>`
    : '';
  if (list === null) return adminBtn + skel(3);
  if (!list.length) return adminBtn + empty('لا توجد حملات نشطة في شارك', 'عند نشر استفتاء أو استقبال مشاركات لمجموعتك سيظهر هنا.');

  const cards = list.map(c => {
    if (c.kind === 'poll') {
      const voted = c.my_vote !== null && c.my_vote !== undefined;
      const totVotes = (c.tallies || []).reduce((a, x) => a + (x.votes || 0), 0);
      const optsHTML = (c.tallies || []).map(o => {
        const pct = totVotes ? Math.round((o.votes * 100) / totVotes) : 0;
        const isMine = c.my_vote === o.idx;
        if (voted) {
          return `<div style="margin-top:8px">
            <div style="display:flex;justify-content:space-between;font-size:14px">
              <span>${isMine ? '✅ ' : ''}${esc(o.label)}</span><b>${pct}% (${o.votes})</b>
            </div>
            <div class="progress-bar"><i style="width:${pct}%"></i></div>
          </div>`;
        }
        return `<button class="btn" style="margin-top:8px;justify-content:flex-start" data-act="sh-vote" data-id="${c.id}" data-idx="${o.idx}">${esc(o.label)}</button>`;
      }).join('');
      return `<div class="card" id="sh-card-${c.id}" style="margin-bottom:10px">
        <div class="chips" style="margin-bottom:6px">${chip('📊 استفتاء')}${chip(c.grp)}${voted ? chip('تم التصويت ✅', 'ok') : chip('اختيار واحد نهائي', 'warn')}</div>
        <b class="sh-title" style="font-size:16.5px">${esc(c.title)}</b>
        ${optsHTML}
      </div>`;
    }
    const txtVal = (sh.txt && sh.txt[c.id]) || '';
    return `<div class="card" id="sh-card-${c.id}" style="margin-bottom:10px">
      <div class="chips" style="margin-bottom:6px">${chip('📥 استقبال مشاركات')}${chip(c.grp)}${c.is_finished ? chip('أنهيت المشاركة ✅', 'ok') : chip(`أرسلت ${c.my_items_count || 0} عنصر`)}</div>
      <b class="sh-title" style="font-size:16.5px">${esc(c.title)}</b>
      ${c.is_finished ? '' : `
      <p class="hint" style="margin:6px 0 8px">اكتب مشاركتك هنا، ويمكنك إرسال عدة نصوص. الصور والملفات تُرسل عبر البوت فقط.</p>
      <textarea class="field area" dir="auto" rows="4" maxlength="3000" data-f4="shTxt.${c.id}" placeholder="اكتب مشاركتك…" ${sh.sendingId === c.id ? 'disabled' : ''}>${esc(txtVal)}</textarea>
      <button class="btn primary sm" style="margin-top:8px" data-act="sh-send-text" data-id="${c.id}" ${sh.sendingId === c.id ? 'disabled' : ''}>${sh.sendingId === c.id ? '⏳ جارٍ الإرسال…' : '✉️ إرسال النص'}</button>
      <button class="btn sm" data-act="sh-open-bot" data-id="${c.id}" data-url="${esc(c.bot_link || '')}">📎 إرسال صور/ملفات عبر البوت</button>
      <button class="btn sm" data-act="sh-finish" data-id="${c.id}" ${(c.my_items_count || 0) ? '' : 'disabled'}>✅ إنهاء المشاركة</button>`}
    </div>`;
  }).join('');

  return adminBtn + cards;
};

function allowedSharekGroups() {
  const all = B.theoryGroups();
  return all.filter(g => (S.boot.perms || []).some(p => p.k === 'sharek.publish' && (p.s === 'any' || p.g === g)));
}
function defaultSharekResGroup() {
  const isAny = (S.boot.perms || []).some(p => p.k === 'sharek.publish' && p.s === 'any');
  if (isAny) return 'ALL';
  const groups = allowedSharekGroups();
  return groups[0] || (S.boot.student && S.boot.student.theory_group_id) || B.firstTheory();
}
function compileSharekText(r) {
  if (!r || !r.campaign) return '';
  const c = r.campaign;
  const grpLabel = (!c.group || c.group === 'ALL') ? 'كل المجموعات' : c.group;
  const lines = [`🗳 تجميع شارك — ${c.title}`, `👥 المجموعة: ${grpLabel}`, '─────────────'];
  if (c.kind === 'poll') {
    const tallies = r.tallies || [];
    const tot = tallies.reduce((a, x) => a + (x.votes || 0), 0);
    lines.push(`📊 إجمالي المصوّتين: ${tot}`);
    tallies.forEach(o => {
      const pct = tot ? Math.round((o.votes * 100) / tot) : 0;
      lines.push(`• ${o.label}: ${o.votes} صوت (${pct}%)`);
    });
    const votedList = (r.votes || []).filter(v => v.option_label !== null && v.option_label !== undefined);
    if (votedList.length) {
      lines.push('─────────────', `📋 قائمة المشاركين (${votedList.length}):`);
      votedList.forEach((v, idx) => {
        lines.push(`${idx + 1}. ${v.name} (${v.no}) — ${v.option_label}`);
      });
    }
  } else {
    const subs = r.submissions || [];
    const byStudent = new Map();
    subs.forEach(s => {
      if (!byStudent.has(s.no)) byStudent.set(s.no, { name: s.name, no: s.no, theory: s.theory, practical: s.practical, items: [] });
      byStudent.get(s.no).items.push(s.text_content || s.file_name || (s.item_kind === 'photo' ? 'صورة مرفقة' : 'ملف مرفق'));
    });
    lines.push(`📥 عدد الطلاب المشاركين: ${byStudent.size} · إجمالي العناصر: ${subs.length}`);
    if (byStudent.size) {
      lines.push('─────────────');
      let idx = 1;
      byStudent.forEach(st => {
        lines.push(`${idx++}. ${st.name} (${st.no} · ${st.practical}):`);
        st.items.forEach(it => lines.push(`   • ${it}`));
      });
    }
  }
  return lines.join('\n');
}

B.views['sharek-admin'] = () => {
  const sh = P4.sh;
  const isAny = (S.boot.perms || []).some(p => p.k === 'sharek.publish' && p.s === 'any');
  const groups = allowedSharekGroups();

  if (sh.form) {
    const f = sh.form;
    return `<div class="card">
      <b>🗳 نشر حملة جديدة في شارك</b>
      <div class="sec">نوع الحملة</div>
      ${seg('sh-fkind', [['poll', '📊 استفتاء (2–10 خيارات)'], ['submission', '📥 استقبال مشاركات (نص/صورة/ملف)']], f.kind)}
      <div class="sec">المجموعة المستهدفة</div>
      <div class="scroll-x wrap">
        ${isAny ? pill('الجميع', 'sh-fgrp', 'data-g="ALL"', f.group === 'ALL') : ''}
        ${groups.map(g => pill(g, 'sh-fgrp', `data-g="${g}"`, f.group === g)).join('')}
      </div>
      <div class="sec">السؤال / عنوان الحملة</div>
      <textarea class="field area" data-f4="shForm.title" rows="3" placeholder="اكتب نص الاستفتاء أو المطلوب في المشاركة…">${esc(f.title)}</textarea>
      ${f.kind === 'poll' ? `
        <div class="sec">خيارات الاستفتاء <small class="muted">(كل خيار في سطر — من 2 إلى 10 خيارات)</small></div>
        <textarea class="field area" data-f4="shForm.options" rows="4" placeholder="الخيار الأول\nالخيار الثاني">${esc(f.options)}</textarea>
      ` : ''}
      <div class="err" role="alert">${esc(sh.err)}</div>
      <button class="btn primary" style="margin-top:12px" data-act="sh-create-save" ${sh.busy ? 'disabled' : ''}>🚀 نشر الحملة وإشعار الطلاب</button>
      <button class="btn" data-act="sh-form-cancel">إلغاء</button>
    </div>`;
  }

  const list = sh.staff === null ? skel(3) : !sh.staff.length ? empty('لا توجد حملات منشورة', 'اضغط الزر بالأسفل لنشر استفتاء أو استقبال مشاركات وتجميعها.')
    : sh.staff.map(c => `<button class="item" data-act="sh-detail" data-id="${c.id}">
        <div class="item-main"><b>${c.kind === 'poll' ? '📊' : '📥'} ${esc(c.title.slice(0, 60))}</b>
          <div class="meta">${chip(c.grp)}${c.status === 'hidden' ? chip('مخفية (نتائجها محفوظة)', 'warn') : chip('نشطة', 'ok')}</div></div>
        <div class="item-side"><b>${c.participants}</b><small>مشارك</small></div>
      </button>`).join('');

  return `${list}<button class="fab" data-act="sh-new">${ico('poll', 'sm')} حملة شارك جديدة</button>`;
};

B.views['sharek-res'] = id => {
  const r = P4.sh.res[id];
  if (!r) return skel(3);
  const c = r.campaign;
  const isAny = (S.boot.perms || []).some(p => p.k === 'sharek.publish' && p.s === 'any');
  const groups = allowedSharekGroups();
  const filterPool = isAny ? ['ALL', ...groups] : groups;
  const grpFilter = (!c.theory_group_id && filterPool.length > 1)
    ? `<div class="scroll-x" style="margin-bottom:10px">${filterPool.map(g => pill(g === 'ALL' ? 'كل المجموعات' : g, 'sh-rgrp', `data-id="${c.id}" data-g="${g}"`, P4.sh.resGroup === g)).join('')}</div>`
    : '';

  let body = '';
  if (c.kind === 'poll') {
    body = `<div class="card" style="margin-bottom:10px"><b>ملخص التصويت</b>${(r.tallies || []).map(o => `<div class="row"><span>${esc(o.label)}</span><b>${o.votes} صوت</b></div>`).join('')}</div>`;
  } else {
    const subs = r.submissions || [];
    body = `<div class="card" style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px">
        <b>📥 المشاركات والملفات المستلمة (${subs.length})</b>
        ${subs.length ? `<button class="btn sm primary" style="width:auto" data-act="sh-items-bot" data-id="${c.id}">📤 إرسال كل الصور والملفات للبوت</button>` : ''}
      </div>
      <p class="hint" style="margin:0 0 8px">اضغط «إرسال كل الصور والملفات للبوت» لتصلك الصور والمستندات والنصوص التي رفعها الطلاب مباشرة في محادثة البوت.</p>
      ${subs.slice(0, 30).map(s => {
        const icon = s.item_kind === 'photo' ? '🖼 صورة' : s.file_id ? '📎 ملف' : '💬 نص';
        return `<div class="row" style="align-items:center">
          <div>
            <b style="color:var(--text);display:block">${esc(s.name)} <small class="muted">(${esc(s.theory)})</small></b>
            <small class="muted">${icon}: ${esc(s.text_content || s.file_name || s.item_kind)}</small>
          </div>
          <button class="btn sm" style="width:auto" data-act="sh-items-bot" data-id="${c.id}" data-item="${s.id}">👁️ عرض في البوت</button>
        </div>`;
      }).join('') || '<p class="hint">لا مشاركات بعد.</p>'}
    </div>`;
  }

  const compiledText = compileSharekText(r);
  const compileBox = `<div class="card" style="margin-bottom:10px">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px">
      <b>📋 التجميع الموحّد للمشاركات والنتائج</b>
      <button class="btn sm" style="width:auto" data-act="sh-copy-compiled" data-id="${c.id}">📋 نسخ التجميع</button>
    </div>
    <pre class="preview" style="max-height:220px;overflow:auto;margin:0" dir="auto">${esc(compiledText)}</pre>
  </div>`;

  const canHide = c.status === 'open' && (c.theory_group_id || isAny);

  return `${grpFilter}
    <div class="card" style="margin-bottom:10px">
      <div class="chips" style="margin-bottom:6px">${chip(c.kind === 'poll' ? 'استفتاء' : 'مشاركات')}${chip(c.group === 'ALL' ? 'الجميع' : c.group)}${c.status === 'hidden' ? chip('مخفية', 'warn') : chip('نشطة', 'ok')}</div>
      <b class="sh-title">${esc(c.title)}</b>
    </div>
    ${body}
    ${compileBox}
    <div class="row-actions">
      ${c.kind === 'submission' ? `<button class="btn primary sm" data-act="sh-items-bot" data-id="${c.id}">🖼 إرسال الصور والملفات المستلمة للبوت</button>` : ''}
      <button class="btn ${c.kind === 'poll' ? 'primary' : ''} sm" data-act="sh-exp-bot" data-id="${c.id}">📊 إرسال كشف Excel في البوت</button>
      <button class="btn sm" data-act="sh-exp-dl" data-id="${c.id}">⬇️ تحميل Excel</button>
      ${canHide ? `<button class="btn sm danger" data-act="sh-hide" data-id="${c.id}">إخفاء الحملة</button>` : ''}
    </div>`;
};


/* =====================================================================
   3) الاختبارات MCQ — مطابقة للشاشات المرفقة في index.html تمامًا:
      (QUIZZES · 01 المادة ← QUIZZES · 02 الوحدة ← QUIZZES · 03 البدء ← QUESTION ← RESULT ← REVIEW)
      بلا حروف عربية، ودعم كامل للأسئلة والإجابات باللغة الإنجليزية والعربية (dir="auto")
   ===================================================================== */
let mcqTimer = null;
function stopMcqTimer() {
  if (mcqTimer) { clearInterval(mcqTimer); mcqTimer = null; }
}
function loadMcqList(force = false) {
  stopMcqTimer();
  flushMcqQueue();
  const ver = (S.boot && S.boot.versions && S.boot.versions.mcq) || 0;
  swr(`mcq:all:v${ver}`, '/api/mcq', d => { P4.mcq.list = Array.isArray(d) ? d : []; rerender(); }, force);
}

function qzSegsHTML(n, frac) {
  const items = Array.from({ length: n }, (_, i) => {
    const f = Math.max(0, Math.min(1, frac * n - i));
    return `<span class="qz-seg"><i style="width:${Math.round(f * 100)}%"></i></span>`;
  }).join('');
  return `<div class="qz-segs">${items}</div>`;
}
function qzTopHTML(n, frac, closeAct) {
  return `<div class="qz-top">${qzSegsHTML(n, frac)}<button class="qz-x" data-act="${closeAct}" aria-label="إغلاق">${ICON_X}</button></div>`;
}
function qzOptionHTML({ text, chipText, state, act, dataAttrs, disabled }) {
  const ringIcon = state === 'wrong' ? ICON_CROSS : ICON_TICK;
  return `<button class="qz-opt" data-s="${state || ''}" data-act="${act}" ${dataAttrs || ''} ${disabled ? 'disabled' : ''}>
    <span class="qz-ring">${ringIcon}</span>
    <span class="qz-lbl" dir="auto">${esc(text)}</span>
    ${chipText ? `<span class="qz-chip" dir="ltr">${esc(chipText)}</span>` : ''}
  </button>`;
}

B.views.quiz = () => {
  const m = P4.mcq, subs = S.boot.subjects || [], allExams = m.list;
  const uploadBtn = can('mcq.upload') ? `<div style="margin-top:12px;text-align:center"><button class="btn sm" style="width:auto;display:inline-flex" data-act="mcq-go-up">${ico('quiz', 'sm')} + رفع اختبار من Excel</button></div>` : '';

  if (allExams === null) {
    return `<div class="qz-stage"><div class="qz-wrap"><div class="qz-card">${qzTopHTML(3, 0, 'back')}${skel(4)}</div></div></div>`;
  }

  /* QUIZZES · 02: اختيار الوحدة / الاختبار داخل المادة */
  if (m.step === 'units' && m.subjectPick) {
    const exams = allExams.filter(e => String(e.subject_id) === String(m.subjectPick));
    const sObj = subs.find(s => String(s.id) === String(m.subjectPick));
    const subjTitle = (sObj && sObj.name) || (exams[0] && exams[0].subject) || 'المادة';
    const pickedExam = exams.find(e => String(e.id) === String(m.examPick));

    const opts = exams.length
      ? exams.map(e => {
          const b = e.last_attempt;
          const cText = b ? `${b.score}/${b.total}` : `${e.questions_count} Q`;
          return qzOptionHTML({
            text: e.title,
            chipText: cText,
            state: String(m.examPick) === String(e.id) ? 'sel' : '',
            act: 'qz-pick-exam',
            dataAttrs: `data-id="${e.id}"`,
          });
        }).join('')
      : `<div class="empty"><b>لا توجد اختبارات مرفوعة لهذه المادة بعد</b>اختر مادة أخرى أو ارفع اختبارًا جديدًا.</div>`;

    const delBtn = pickedExam && pickedExam.can_delete
      ? `<button class="btn sm danger" style="margin-top:8px" data-act="mcq-del" data-id="${pickedExam.id}">🗑 حذف الاختبار المحدد</button>`
      : '';

    return `<div class="qz-stage"><div class="qz-wrap"><div class="qz-card">
      ${qzTopHTML(3, 2 / 6, 'qz-to-home')}
      <div class="qz-eyebrow"><span>QUIZZES · 02</span></div>
      <h1 class="qz-h1" dir="auto">${esc(subjTitle)}</h1>
      <p class="qz-desc">اختر الوحدة أو الاختبار.</p>
      <div class="qz-hint">SELECT ONLY ONE</div>
      <div class="qz-list">${opts}</div>
      ${delBtn}
      <div class="qz-spacer"></div>
      <div class="qz-nav">
        <button class="qz-btn-back" data-act="qz-to-home">رجوع</button>
        <button class="qz-btn-next" data-act="qz-to-intro" ${pickedExam ? '' : 'disabled'}>التالي</button>
      </div>
    </div></div>${uploadBtn}</div>`;
  }

  /* QUIZZES · 03: تفاصيل الاختبار + خيارات العدّاد والخلط + بدء الاختبار */
  if (m.step === 'intro' && m.examPick) {
    const e = allExams.find(x => String(x.id) === String(m.examPick));
    if (!e) { m.step = 'units'; return B.views.quiz(); }
    const b = e.last_attempt;
    const secs = (e.duration_minutes || 15) * 60;
    const bestPct = b && b.total ? `${Math.round((b.score / b.total) * 100)}%` : '—';

    return `<div class="qz-stage"><div class="qz-wrap"><div class="qz-card">
      ${qzTopHTML(3, 3 / 6, 'qz-to-home')}
      <div class="qz-eyebrow"><span>QUIZZES · 03</span></div>
      <h1 class="qz-h1" dir="auto">${esc(e.title)}</h1>
      ${e.description ? `<p class="qz-desc" dir="auto">${esc(e.description)}</p>` : ''}
      <div class="qz-stats">
        <div class="qz-stat"><b>${e.questions_count}</b><span>QUESTIONS</span></div>
        <div class="qz-stat"><b>${m.timerOn ? fmtTimer(secs) : '∞'}</b><span>TIME</span></div>
        <div class="qz-stat"><b>${bestPct}</b><span>BEST</span></div>
      </div>
      <button class="qz-toggle" data-act="qz-toggle-timer">
        <span>تفعيل العدّاد الزمني</span>
        <span class="qz-sw ${m.timerOn ? 'on' : ''}"></span>
      </button>
      <button class="qz-toggle" data-act="qz-toggle-shuffle">
        <span>خلط الأسئلة والخيارات</span>
        <span class="qz-sw ${m.shuffleOn ? 'on' : ''}"></span>
      </button>
      <button class="qz-toggle" data-act="qz-toggle-instant">
        <span>تصحيح فوري</span>
        <span class="qz-sw ${m.instantOn ? 'on' : ''}"></span>
      </button>
      <div class="qz-spacer"></div>
      <div class="qz-nav">
        <button class="qz-btn-back" data-act="qz-to-units">رجوع</button>
        <button class="qz-btn-next" data-act="mcq-open" data-id="${e.id}" ${m.busy ? 'disabled' : ''}>${m.busy ? '…' : 'ابدأ الاختبار'}</button>
      </div>
    </div></div></div>`;
  }

  /* QUIZZES · 01: اختيار المادة */
  const subjRows = subs.map(s => {
    const c = allExams.filter(e => +e.subject_id === +s.id).length;
    return qzOptionHTML({
      text: s.name,
      chipText: `${c} اختبار`,
      state: String(m.subjectPick) === String(s.id) ? 'sel' : '',
      act: 'qz-pick-subj',
      dataAttrs: `data-id="${s.id}"`,
    });
  }).join('');

  return `<div class="qz-stage"><div class="qz-wrap"><div class="qz-card">
    ${qzTopHTML(3, 1 / 6, 'back')}
    <div class="qz-eyebrow"><span>QUIZZES · 01</span></div>
    <h1 class="qz-h1">اختر المادة</h1>
    <p class="qz-desc">اختبارات MCQ تفاعلية مع عدّاد وتصحيح فوري.</p>
    <div class="qz-hint">SELECT ONLY ONE</div>
    <div class="qz-list">${subjRows}</div>
    <div class="qz-spacer"></div>
    <div class="qz-nav">
      <button class="qz-btn-back" data-act="back">رجوع</button>
      <button class="qz-btn-next" data-act="qz-to-units" ${m.subjectPick ? '' : 'disabled'}>التالي</button>
    </div>
  </div></div>${uploadBtn}</div>`;
};

function startMcqRun(examData) {
  stopMcqTimer();
  const m = P4.mcq, info = examData.exam;
  const secs = (info.duration_minutes || 15) * 60;
  let qs = (examData.questions || []).map(q => {
    let opts = (q.options || []).map((t, orig) => ({ t, orig }));
    if (m.shuffleOn) opts = shuffleArr(opts);
    return { id: q.id, q: q.question, opts, correctOrig: q.correct_index };
  });
  if (m.shuffleOn) qs = shuffleArr(qs);

  m.run = {
    exam: info,
    qs,
    answers: {},
    idx: 0,
    secs,
    timerOn: m.timerOn,
    instant: m.instantOn,
    startAt: Date.now(),
    deadline: Date.now() + secs * 1000,
    leftSec: secs,
    elapsed: 0,
    done: false,
    reviewMode: false,
  };
  if (m.timerOn) {
    mcqTimer = setInterval(() => {
      const run = P4.mcq.run;
      if (!run || run.done) return stopMcqTimer();
      run.leftSec = Math.max(0, Math.ceil((run.deadline - Date.now()) / 1000));
      const el = document.getElementById('qz-timer-el');
      if (el) {
        el.textContent = fmtTimer(run.leftSec);
        el.classList.toggle('low', run.leftSec <= 60);
      }
      if (run.leftSec <= 0) {
        B.toast('انتهى الوقت ⏰');
        finishMcqAttempt();
      }
    }, 500);
  }
}

B.views['mcq-run'] = () => {
  const m = P4.mcq, ex = m.exam;
  if (!ex) return `<div class="qz-stage"><div class="qz-wrap"><div class="qz-card">${qzTopHTML(6, 0, 'back')}${skel(4)}</div></div></div>`;
  if (!m.run) startMcqRun(ex);
  const run = m.run, qs = run.qs;

  /* شاشة مراجعة الأخطاء (REVIEW) كما في الملف المرفق */
  if (run.done && run.reviewMode) {
    const wrong = qs.filter(q => !(run.answers[q.id] && run.answers[q.id].ok));
    const cards = wrong.map(q => {
      const a = run.answers[q.id];
      const right = q.opts.find(o => o.orig === q.correctOrig);
      return `<div class="qz-rev">
        <p dir="auto">${esc(q.q)}</p>
        <div class="qz-pill r"><em>YOU</em><span dir="auto">${a ? esc(q.opts[a.pos].t) : 'لم تُجب'}</span></div>
        <div class="qz-pill g"><em>OK</em><span dir="auto">${esc(right ? right.t : '')}</span></div>
      </div>`;
    }).join('');
    return `<div class="qz-stage"><div class="qz-wrap"><div class="qz-card">
      ${qzTopHTML(6, 1, 'mcq-rev-close')}
      <div class="qz-eyebrow"><span>REVIEW</span></div>
      <h1 class="qz-h1">مراجعة الأخطاء</h1>
      <div style="height:10px"></div>
      <div class="qz-rev-list">${cards}</div>
      <button class="qz-btn-next qz-btn-wide" data-act="mcq-rev-close">رجوع للنتيجة</button>
    </div></div></div>`;
  }

  /* شاشة النتيجة النهائية (RESULT) مع الحلقة الدائرية المتحركة كما في الملف المرفق */
  if (run.done) {
    const total = qs.length || 1;
    const score = run.score || 0;
    const pct = Math.round((score / total) * 100);
    const R = 80, C = +(2 * Math.PI * R).toFixed(2);
    const offset = +(C * (1 - pct / 100)).toFixed(2);
    const msg = pct >= 90 ? 'ممتاز! 🌟' : pct >= 75 ? 'عمل رائع 👏' : pct >= 50 ? 'جيد، يمكنك أفضل' : 'راجع المادة وأعد المحاولة 💪';
    const wrong = qs.filter(q => !(run.answers[q.id] && run.answers[q.id].ok));

    return `<div class="qz-stage"><div class="qz-wrap"><div class="qz-card">
      ${qzTopHTML(6, 1, 'qz-exit-home')}
      <div class="qz-eyebrow"><span>RESULT</span><span class="qz-timer" dir="ltr">${fmtTimer(run.elapsed)}</span></div>
      <div class="qz-score">
        <div class="qz-ringwrap">
          <svg viewBox="0 0 190 190">
            <circle cx="95" cy="95" r="${R}" fill="none" stroke="var(--line)" stroke-width="12"/>
            <circle cx="95" cy="95" r="${R}" fill="none" stroke="var(--action)" stroke-width="12" stroke-linecap="round"
              stroke-dasharray="${C}" stroke-dashoffset="${offset}" transform="rotate(-90 95 95)"
              style="transition:stroke-dashoffset .9s cubic-bezier(.2,.8,.2,1)"/>
          </svg>
          <div class="num"><b>${pct}%</b><span dir="ltr">${score} / ${total}</span></div>
        </div>
      </div>
      <div class="qz-msg">${msg}</div>
      <p class="qz-sub" dir="auto">${esc(run.exam.title)}</p>
      <div class="qz-spacer"></div>
      ${wrong.length ? `<button class="qz-btn-next qz-btn-wide ghost" data-act="mcq-rev-open">مراجعة الأخطاء (${wrong.length})</button>` : ''}
      <button class="qz-btn-next qz-btn-wide" data-act="mcq-retry">إعادة الاختبار</button>
      <button class="qz-btn-back qz-btn-wide" style="text-align:center" data-act="qz-exit-home">القائمة الرئيسية</button>
    </div></div></div>`;
  }

  /* شاشة السؤال (QUESTION) بلا حروف عربية وبدعم كامل للإنجليزية والعربية */
  const q = qs[run.idx];
  const ans = run.answers[q.id];
  const isLast = run.idx === qs.length - 1;
  const frac = (run.idx + (ans ? 1 : 0)) / qs.length;

  const instant = run.instant !== false;
  const reveal = instant && !!ans;
  const hintClass = reveal ? (ans.ok ? 'qz-hint ok' : 'qz-hint no') : 'qz-hint';
  const hintText = reveal ? (ans.ok ? '✓ إجابة صحيحة' : '✕ إجابة خاطئة — الصحيحة مظلّلة') : 'SELECT ONLY ONE';

  const optsHTML = q.opts.map((o, pos) => {
    let st = '';
    if (reveal) {
      const isCorrect = o.orig === q.correctOrig;
      st = isCorrect ? 'correct' : (pos === ans.pos ? 'wrong' : 'dim');
    } else if (ans && pos === ans.pos) {
      st = 'sel';
    }
    return qzOptionHTML({
      text: o.t,
      state: st,
      act: 'mcq-pick',
      dataAttrs: `data-pos="${pos}"`,
      disabled: reveal,
    });
  }).join('');

  const timerText = run.timerOn ? fmtTimer(run.leftSec) : '∞';
  const timerLow = run.timerOn && run.leftSec <= 60 ? 'low' : '';
  const nextLabel = ans ? (isLast ? 'إنهاء' : 'التالي') : (isLast ? 'إنهاء' : 'تخطي');

  return `<div class="qz-stage"><div class="qz-wrap"><div class="qz-card">
    ${qzTopHTML(6, frac, 'mcq-ask-exit')}
    <div class="qz-eyebrow">
      <span>QUESTION ${pad2(run.idx + 1)}/${pad2(qs.length)}</span>
      <span class="qz-timer ${timerLow}" id="qz-timer-el" dir="ltr">${timerText}</span>
    </div>
    <h1 class="qz-h1 q" dir="auto">${esc(q.q)}</h1>
    <div class="${hintClass}">${hintText}</div>
    <div class="qz-list">${optsHTML}</div>
    <div class="qz-spacer"></div>
    <div class="qz-nav">
      <button class="qz-btn-back" data-act="mcq-prev" ${run.idx === 0 ? 'disabled' : ''}>رجوع</button>
      <button class="qz-btn-next ${ans ? '' : 'ghost'}" data-act="mcq-next">${nextLabel}</button>
    </div>
  </div></div></div>`;
};

function finishMcqAttempt() {
  stopMcqTimer();
  const m = P4.mcq, run = m.run;
  if (!run || run.done) return;
  const total = run.qs.length;
  const score = run.qs.filter(q => run.answers[q.id] && run.answers[q.id].ok).length;
  run.done = true;
  run.score = score;
  run.elapsed = Math.max(1, Math.min(run.secs, Math.round((Date.now() - run.startAt) / 1000)));

  /* تحديث أفضل نتيجة محليًا في القائمة */
  const exItem = (m.list || []).find(x => String(x.id) === String(run.exam.id));
  if (exItem) exItem.last_attempt = { score, total, elapsed_seconds: run.elapsed };

  rerender();
  const payload = { exam_id: run.exam.id, score, total, elapsed_seconds: run.elapsed, timer_enabled: run.timerOn };
  call(`/api/mcq/${run.exam.id}/attempt`, 'POST', payload).then(r => {
    if (r.status === 0) {
      const q = B.ls.get(mcqQueueKey()) || [];
      q.push(payload);
      B.ls.set(mcqQueueKey(), q);
      B.toast('حُفظت نتيجتك محليًا وستُرسل تلقائيًا عند عودة الاتصال.');
    }
  });
}

B.views['mcq-upload'] = () => {
  const subs = S.boot.subjects || [];
  const up = P4.mcq.up || (P4.mcq.up = { subject_id: subs.length ? subs[0].id : null, title: '', description: '', duration_minutes: 15, fileName: '', b64: '' });
  return `<div class="card">
    <b>📤 رفع اختبار MCQ من ملف Excel</b>
    <div class="sec">المادة</div>
    <div class="scroll-x wrap">${subs.map(s => pill(s.name, 'mcqu-subj', `data-id="${s.id}"`, up.subject_id === s.id)).join('')}</div>
    <div class="sec">عنوان الاختبار</div>
    <input class="field" dir="auto" data-f4="mcqUp.title" maxlength="140" placeholder="مثال: Module 9 & 13 Quiz" value="${esc(up.title)}">
    <div class="sec">وصف الاختبار <small class="muted">(اختياري)</small></div>
    <input class="field" dir="auto" data-f4="mcqUp.description" maxlength="400" placeholder="مثال: 20 Multiple Choice Questions" value="${esc(up.description)}">
    <div class="sec">المدة المقترحة بالدقائق</div>
    <input class="field" type="number" min="1" max="180" data-f4="mcqUp.duration_minutes" value="${esc(up.duration_minutes)}">
    <div class="sec">ملف الأسئلة (.xlsx)</div>
    <p class="hint" style="margin:2px 0 8px">الأعمدة بالترتيب: السؤال | الخيار 1 | الخيار 2 | الخيار 3 | الخيار 4 | الإجابة الصحيحة (يدعم العربية والإنجليزية)</p>
    <input type="file" id="mcq-xlsx-input" accept=".xlsx" class="field" style="padding:10px">
    <button class="btn sm" style="margin-top:8px" data-act="mcq-dl-tpl">⬇️ تحميل قالب Excel الجاهز</button>
    <div class="err" role="alert">${esc(P4.mcq.err)}</div>
    <button class="btn primary" style="margin-top:12px" data-act="mcq-upload-save" ${P4.mcq.busy ? 'disabled' : ''}>رفع وحفظ الاختبار</button>
  </div>`;
};


/* =====================================================================
   4) شاشة الدكتور
   ===================================================================== */
function loadDoctorView(force = false) {
  const doc = P4.doc;
  const q = doc.doctor_id ? `?doctor_id=${doc.doctor_id}` : '';
  swr(`doc-dash:${doc.doctor_id || 'me'}`, `/api/doctor/assignments${q}`, d => { if (d) { doc.dash = d; rerender(); } }, force);
}

const docsBarHTML = d => (d.doctors && d.doctors.length)
  ? `<div class="scroll-x" style="margin-bottom:10px">${d.doctors.map(x => pill(x.name, 'doc-pick', `data-id="${x.id}"`, (d.doctor && d.doctor.id) === x.id)).join('')}</div>`
  : '';

/* الشاشة الرئيسية للدكتور: خياران بنفس شكل بلاطات «الأقسام» في الرئيسية */
B.views['doc-assign'] = () => `<div class="sec">واجبات الطلاب</div>
  <div class="grid">
    <button class="tile" data-act="doc-go" data-v="results">${ico('tasks')}<span>نتائج الواجبات</span></button>
    <button class="tile" data-act="doc-go" data-v="export">${ico('report')}<span>تصدير Excel</span></button>
  </div>`;

/* الخيار 1: نتائج الواجبات (نفس القائمة السابقة) */
B.views['doc-results'] = () => {
  const doc = P4.doc, d = doc.dash;
  if (!d) return skel(3);
  const docsBar = docsBarHTML(d);
  const asgs = d.assignments || [];
  if (!asgs.length) return docsBar + empty('لا توجد واجبات مسجّلة', 'ستظهر هنا واجبات مجموعاتك لتصدير كشف التسليم (1 سلّم، 0 لم يسلّم).');

  return docsBar + asgs.map(a => `<div class="item col">
    <div class="item-main">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>${esc(a.subject)} · واجب ${a.number}</b>
        ${chip(`${a.submitted}/${a.total} سلّموا`, 'ok')}
      </div>
      <div class="meta">${chip(PART[a.part])}${chip(`المجموعة: ${a.grp}`)}<span>· الموعد: ${fmtD(a.due_at)}</span></div>
    </div>
    <div class="row-actions">
      <button class="btn sm primary" data-act="doc-exp-bot" data-id="${a.id}">📥 إرسال Excel (1/0) في البوت</button>
      <button class="btn sm" data-act="doc-exp-dl" data-id="${a.id}">⬇️ تحميل Excel (1/0)</button>
    </div>
  </div>`).join('');
};



/* الخيار 2: ملف Excel لكل مجموعة (الرقم الأكاديمي | اسم الطالب | واجب 1 | واجب 2 ...؛ 1 سلّم، 0 لم يسلّم) */
function doctorGroups(d) {
  const m = new Map();
  (d.assignments || []).forEach(a => {
    const g = m.get(a.grp) || { grp: a.grp, part: a.part, count: 0 };
    g.count += 1; m.set(a.grp, g);
  });
  return [...m.values()].sort((x, y) => String(x.grp).localeCompare(String(y.grp)));
}
B.views['doc-export'] = () => {
  const doc = P4.doc, d = doc.dash;
  if (!d) return skel(3);
  const groups = doctorGroups(d);
  if (!groups.length) return docsBarHTML(d) + empty('لا توجد واجبات مسجّلة', 'ستظهر هنا مجموعاتك لتصدير كشف التسليم.');
  if (!groups.some(g => g.grp === doc.grp)) doc.grp = groups.length === 1 ? groups[0].grp : null;
  const cur = groups.find(g => g.grp === doc.grp);
  return `${docsBarHTML(d)}<div class="sec">اختر المجموعة</div>
    <div class="scroll-x" style="margin-bottom:12px">${groups.map(g => pill(g.grp, 'doc-grp-pick', `data-g="${esc(g.grp)}"`, g.grp === doc.grp)).join('')}</div>
    ${cur ? `<div class="item col"><div class="item-main">
        <b>المجموعة ${esc(cur.grp)}</b>
        <div class="meta">${chip(PART[cur.part])}${chip(`${cur.count} واجب`)}</div>
        <p class="hint" style="margin:6px 0 0">الأعمدة: الرقم الأكاديمي | اسم الطالب | ثم عمود لكل واجب (1 سلّم، 0 لم يسلّم).</p>
      </div>
      <div class="row-actions">
        <button class="btn sm primary" data-act="doc-grp-bot">📥 إرسال Excel في البوت</button>
        <button class="btn sm" data-act="doc-grp-dl">⬇️ تحميل Excel</button>
      </div></div>` : empty('اختر مجموعة', 'ثم أرسل الملف إلى البوت أو حمّله.')}`;
};


/* =====================================================================
   5) الإشعارات اليدوية ولوحة المطوّر الشاملة:
      شاشة «لوحة المطوّر» تعرض البطاقات فقط، وعند اختيار أي بطاقة تفتح في شاشة كاملة مستقلة!
   ===================================================================== */
function allowedNotifyGroups() {
  const all = B.theoryGroups();
  return all.filter(g => (S.boot.perms || []).some(p => p.k === 'notify.send' && (p.s === 'any' || p.g === g)));
}
B.views['notify-send'] = () => {
  const nf = P4.ntf;
  const isAny = (S.boot.perms || []).some(p => p.k === 'notify.send' && p.s === 'any');
  const groups = allowedNotifyGroups();
  if (!nf.group) nf.group = isAny ? 'ALL' : (groups[0] || B.firstTheory());
  return `<div class="card">
    <b>📣 إرسال إعلان / إشعار يدوي</b>
    <p class="hint" style="margin:4px 0 10px">يصل الإعلان في البوت وفي مركز إشعارات التطبيق فورًا، ويتجاوز كتم الإشعارات لضمان تنبيه الطلاب.</p>
    <div class="sec">المجموعة المستهدفة</div>
    <div class="scroll-x wrap">
      ${isAny ? pill('الجميع (الدفعة كاملة)', 'ntf-grp', 'data-g="ALL"', nf.group === 'ALL') : ''}
      ${groups.map(g => pill(`${g} وعملياتها`, 'ntf-grp', `data-g="${g}"`, nf.group === g)).join('')}
    </div>
    <div class="sec">عنوان الإعلان <small class="muted">(اختياري)</small></div>
    <input class="field" data-f4="ntf.title" maxlength="120" placeholder="مثال: تنبيه بخصوص محاضرة الغد" value="${esc(nf.title)}">
    <div class="sec">نص الإشعار</div>
    <textarea class="field area" data-f4="ntf.body" maxlength="1500" rows="4" placeholder="اكتب نص الإعلان…">${esc(nf.body)}</textarea>
    <div class="err" role="alert">${esc(nf.err)}</div>
    <button class="btn primary" style="margin-top:12px" data-act="ntf-send" ${nf.busy ? 'disabled' : ''}>🚀 إرسال الإشعار الآن</button>
  </div>`;
};

function loadDevSub(sub, force = false) {
  const dv = P4.dev;
  if (sub === 'students') {
    swr('dev-ov', '/api/dev/overview', d => { if (d) { dv.ov = d; rerender(); } }, force);
    const q = `group=${dv.stuGroup}&q=${encodeURIComponent(dv.stuQ || '')}`;
    swr(`dev-stu:${q}`, `/api/dev/students?${q}`, d => { dv.students = d || []; rerender(); }, force);
  } else if (sub === 'guests') {
    swr('dev-guests', '/api/dev/guests', d => { dv.guests = d || []; rerender(); }, force);
  } else if (sub === 'roles' || sub === 'catalog') {
    swr('dev-cat', '/api/dev/catalog', d => {
      if (d) {
        dv.cat = d;
        const subs = d.subjects || [], docs = d.doctors || [];
        if (!dv.teachForm.subject_id && subs.length) dv.teachForm.subject_id = subs[0].id;
        if (!dv.teachForm.doctor_id && docs.length) dv.teachForm.doctor_id = docs[0].id;
        if (!dv.destUI.subject_id && subs.length) dv.destUI.subject_id = subs[0].id;
        if (!dv.destUI.doctor_id && docs.length) dv.destUI.doctor_id = docs[0].id;
        rerender();
      }
    }, force);
  } else if (sub === 'trash') {
    swr('dev-trash', '/api/dev/trash', d => { if (d) { dv.trash = d; rerender(); } }, force);
  } else if (sub === 'activity') {
    swr('dev-act', '/api/dev/activity', d => { dv.activity = d || []; rerender(); }, force);
  } else if (sub === 'health') {
    swr('dev-ov', '/api/dev/overview', d => { if (d) { dv.ov = d; rerender(); } }, force);
  }
}

function computeDestKeyAndLabel(ui, cat) {
  const subs = (cat && cat.subjects) || [];
  const docs = (cat && cat.doctors) || [];
  if (ui.kind === 'subject') {
    const s = subs.find(x => String(x.id) === String(ui.subject_id)) || subs[0];
    if (!s) return { key: '', label: 'اختر المادة' };
    return { key: `cohort:subject:${s.id}`, label: `ملفات ومحاضرات ${s.name_ar}` };
  }
  if (ui.kind === 'notices') {
    const g = ui.group || B.firstTheory();
    return { key: `cohort:notices:${g}`, label: `إعلانات وواجبات ${g}` };
  }
  if (ui.kind === 'reports') {
    const g = ui.group || B.firstTheory();
    return { key: `cohort:theory:${g}`, label: `تقارير ${g}` };
  }
  const d = docs.find(x => String(x.id) === String(ui.doctor_id)) || docs[0];
  const g = ui.group || (ui.part === 'practical' ? B.firstPractical() : B.firstTheory());
  if (!d) return { key: '', label: 'اختر الدكتور' };
  return { key: `doctor:${d.id}:${ui.part}:${g}`, label: `تسليمات ${d.name} (${PART[ui.part]} ${g})` };
}

/* شاشة لوحة المطوّر الرئيسية: تعرض البطاقات فقط كما في الشاشة الرئيسية */
B.views.dev = () => {
  const tiles = [
    ['students', 'الطلاب', 'idcard'],
    ['guests', 'الزوار والحظر', 'lock'],
    ['roles', 'الأدوار', 'shield'],
    ['catalog', 'المواد والمجتمعات', 'folder'],
    ['trash', 'السلة', 'tasks'],
    ['activity', 'سجل النشاط', 'report'],
    ['health', 'صحة النظام', 'bell'],
  ];
  return `<section class="hero" style="padding:18px 18px 34px;margin-bottom:14px">
      <small>إدارة النظام الشاملة</small>
      <h2 style="font-size:22px;margin:4px 0 8px">لوحة المطوّر</h2>
      <div class="chips"><span class="chip">صلاحيات كاملة</span><span class="chip">${esc(B.cohortTitle())}</span></div>
    </section>
    <div class="sec">اختر القسم المطلوب</div>
    <div class="grid">${tiles.map(([k, l, icon]) =>
      `<button class="tile" data-act="dev-open" data-v="${k}">${ico(icon)}<span>${esc(l)}</span></button>`
    ).join('')}</div>`;
};

/* الشاشة الكاملة المستقلة لكل قسم في لوحة المطوّر */
B.views['dev-view'] = sub => {
  const dv = P4.dev, ov = dv.ov;

  if (sub === 'students') {
    const stus = dv.students;
    return `<div class="card" style="margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <b>إدارة الطلاب (${ov && ov.counts ? `${ov.counts.students_linked}/${ov.counts.students_total} مربوط` : '…'})</b>
          <button class="btn sm primary" style="width:auto" data-act="dev-stu-form-toggle">${dv.stuForm ? 'إغلاق' : '+ إضافة طالب يدويًا'}</button>
        </div>
        ${dv.stuForm ? `<div style="margin-top:10px">
          <div class="two">
            <input class="field" dir="ltr" data-f4="devStu.no" placeholder="الرقم الأكاديمي" value="${esc(dv.stuForm.no)}">
            <input class="field" data-f4="devStu.name" placeholder="اسم الطالب الكامل" value="${esc(dv.stuForm.name)}">
          </div>
          <div class="two">
            <input class="field" dir="ltr" data-f4="devStu.theory" placeholder="${esc(B.firstTheory())}" value="${esc(dv.stuForm.theory)}">
            <input class="field" dir="ltr" data-f4="devStu.practical" placeholder="${esc(B.firstPractical())}" value="${esc(dv.stuForm.practical)}">
          </div>
          <button class="btn primary sm" style="margin-top:10px" data-act="dev-stu-save">حفظ الطالب</button>
        </div>` : ''}
        <div style="margin-top:10px;border-top:1px solid var(--line);padding-top:10px">
          <small class="muted" style="display:block;margin-bottom:6px">استيراد قائمة الطلاب من ملف Excel (.xlsx):</small>
          <input type="file" id="dev-stu-xlsx" accept=".xlsx" class="field" style="min-height:42px;padding:6px;font-size:14px">
          <button class="btn sm" style="margin-top:8px" data-act="dev-stu-import">📥 استيراد ملف Excel الآن</button>
        </div>
      </div>
      <div class="search-box">
        <input class="field" data-f4="dev.stuQ" placeholder="بحث بالاسم أو الرقم الأكاديمي أو Telegram ID…" value="${esc(dv.stuQ)}">
        <button class="btn sm primary" data-act="dev-stu-search">بحث</button>
      </div>
      ${stus === null ? skel(3) : !stus.length ? empty('لا نتائج', 'جرّب بحثًا آخر.')
        : stus.map(s => `<div class="item">
            <div class="item-main"><b>${esc(s.full_name)}</b>
              <div class="meta"><code>${esc(s.academic_no)}</code>${chip(s.theory_group_id)}${chip(s.practical_group_id)}
                ${s.telegram_id ? chip(`TG: ${s.telegram_id}`, 'ok') : chip('غير مربوط')}</div></div>
            ${s.telegram_id ? `<button class="btn sm danger" style="width:auto" data-act="dev-unlink" data-no="${esc(s.academic_no)}">فك الارتباط</button>` : ''}
          </div>`).join('')}`;
  }

  if (sub === 'guests') {
    const gs = dv.guests;
    return gs === null ? skel(3) : !gs.length ? empty('لا يوجد زوار أو محظورون', 'ستظهر هنا قائمة الزوار والحسابات المحظورة.')
      : gs.map(g => `<div class="item">
          <div class="item-main"><b>${esc(g.display_name || 'بدون اسم')}</b>
            <div class="meta"><code>TG: ${g.telegram_id}</code>${g.username ? `<span>@${esc(g.username)}</span>` : ''}
              ${g.is_banned ? chip('محظور 🚫', 'bad') : chip('زائر')}</div></div>
          <button class="btn sm ${g.is_banned ? 'primary' : 'danger'}" style="width:auto" data-act="dev-ban" data-id="${g.telegram_id}" data-ban="${g.is_banned ? 0 : 1}">
            ${g.is_banned ? 'فك الحظر' : 'حظر'}
          </button>
        </div>`).join('');
  }

  if (sub === 'roles') {
    const cat = dv.cat;
    if (!cat) return skel(3);
    const rf = dv.roleForm;
    return `<div class="card" style="margin-bottom:12px">
        <b>منح صلاحية لمستخدم (تُفعّل خلال ثوانٍ)</b>
        <div class="two" style="margin-top:8px">
          <input class="field" dir="ltr" inputmode="numeric" data-f4="devRole.telegram_id" placeholder="Telegram ID" value="${esc(rf.telegram_id)}">
          <select class="field" data-f4="devRole.role">
            <option value="delegate" ${rf.role === 'delegate' ? 'selected' : ''}>مندوب</option>
            <option value="deputy" ${rf.role === 'deputy' ? 'selected' : ''}>نائب مندوب</option>
            <option value="committee" ${rf.role === 'committee' ? 'selected' : ''}>لجنة علمية</option>
            <option value="developer" ${rf.role === 'developer' ? 'selected' : ''}>مطوّر</option>
          </select>
        </div>
        ${rf.role !== 'developer' ? `<div style="margin-top:8px">${seg('dev-rgrp', B.theoryGroups().map(g => [g, g]), rf.group)}</div>` : ''}
        <button class="btn primary sm" style="margin-top:10px" data-act="dev-role-grant">منح الصلاحية</button>
      </div>
      <div class="sec">المشرفون الحاليون (${(cat.roles || []).length})</div>
      ${(cat.roles || []).map(r => `<div class="item">
        <div class="item-main"><b>${esc(r.name)}</b>
          <div class="meta">${chip(ROLE_LABELS[r.role] || r.role)}${r.group ? chip(r.group) : chip('عام')}<code>TG: ${r.telegram_id}</code></div></div>
        <button class="btn sm danger" style="width:auto" data-act="dev-role-rev" data-id="${r.id}">سحب</button>
      </div>`).join('')}`;
  }

  if (sub === 'catalog') {
    const cat = dv.cat;
    if (!cat) return skel(3);
    const subs = cat.subjects || [], docs = cat.doctors || [];
    const tf = dv.teachForm;
    const poolGroups = tf.part === 'practical' ? B.practicalGroups() : B.theoryGroups();
    const selGroups = tf.groups || [];
    const isAllSelected = poolGroups.every(g => selGroups.includes(g));

    const ui = dv.destUI;
    const destResolved = computeDestKeyAndLabel(ui, cat);
    const destGroupsPool = ui.kind === 'doctor' && ui.part === 'practical' ? B.practicalGroups() : B.theoryGroups();

    return `<div class="card" style="margin-bottom:12px">
        <b>1) إضافة / تحديث مادة</b>
        <div class="two">
          <input class="field" data-f4="devSubj.name_ar" placeholder="اسم المادة (مثال: شبكات الحاسوب)" value="${esc(dv.subjForm.name_ar)}">
          <input class="field" data-f4="devSubj.hashtag" placeholder="الوسم (اختياري)" value="${esc(dv.subjForm.hashtag)}">
        </div>
        <button class="btn primary sm" style="margin-top:8px" data-act="dev-subj-save">حفظ المادة</button>
      </div>

      <div class="card" style="margin-bottom:12px">
        <b>2) إضافة / تحديث دكتور أو معيد</b>
        <div class="two">
          <input class="field" data-f4="devDoc.name" placeholder="الاسم (مثال: د. أحمد المنصوري)" value="${esc(dv.docForm.name)}">
          <input class="field" dir="ltr" inputmode="numeric" data-f4="devDoc.telegram_id" placeholder="Telegram ID (اختياري)" value="${esc(dv.docForm.telegram_id)}">
        </div>
        <button class="btn primary sm" style="margin-top:8px" data-act="dev-doc-save">حفظ الدكتور</button>
      </div>

      <div class="card" style="margin-bottom:12px">
        <b>3) ربط مادة بدكتور ومجموعات (اختر الكل أو عدة مجموعات بضغطة زر)</b>
        <div class="sec">اختر المادة</div>
        <div class="scroll-x wrap">${subs.map(s => pill(s.name_ar, 'dev-t-subj', `data-id="${s.id}"`, String(tf.subject_id) === String(s.id))).join('')}</div>
        <div class="sec">اختر الدكتور / المعيد</div>
        <div class="scroll-x wrap">${docs.map(d => pill(d.name, 'dev-t-doc', `data-id="${d.id}"`, String(tf.doctor_id) === String(d.id))).join('') || '<span class="muted">أضف دكتورًا أولًا من الأعلى.</span>'}</div>
        <div class="sec">القسم</div>
        ${seg('dev-t-part', [['theory', `📚 نظري (${B.firstTheory()}..${B.theoryGroups().slice(-1)[0] || ''})`], ['practical', `💻 عملي (${B.firstPractical()}..${B.practicalGroups().slice(-1)[0] || ''})`]], tf.part)}
        <div class="sec">المجموعات <small class="muted">(يمكنك تحديد «الكل» أو أكثر من مجموعة)</small></div>
        <div class="scroll-x wrap">
          ${pill('الكل ✅', 'dev-t-grp-all', '', isAllSelected)}
          ${poolGroups.map(g => pill(g, 'dev-t-grp', `data-g="${g}"`, selGroups.includes(g))).join('')}
        </div>
        <button class="btn primary sm" style="margin-top:12px" data-act="dev-teach-save">🔗 حفظ ربط الدكتور بالمجموعات المحددة (${selGroups.length})</button>
        ${(cat.teaching || []).length ? `<div class="sec">الروابط الحالية (${cat.teaching.length})</div>
          <div>${cat.teaching.map(t => `<div class="row" style="align-items:center">
            <span><b>${esc(t.subject)}</b> · ${PART[t.part]} (${esc(t.group)}) ← ${esc(t.doctor)}</span>
            <button class="btn sm danger" style="width:auto;min-height:32px;padding:0 10px" data-act="dev-teach-del" data-id="${t.id}">حذف</button>
          </div>`).join('')}</div>` : ''}
      </div>

      <div class="card" style="margin-bottom:12px">
        <b>4) ربط المجتمعات والأقسام (Topics) بسهولة</b>
        <div class="stale" style="margin:8px 0;background:var(--brand-soft);color:var(--brand-deep)">
          💡 <b>طريقة سريعة جدًا:</b> اكتب الأمر <code>/where</code> داخل أي Topic في مجموعة تيليجرام، وستظهر لك أزرار تحت الرسالة لربط الـ Topic مباشرة بضغطة زر دون نسخ أي رقم!
        </div>
        <div class="sec">أو اختر من هنا ما تريد ربطه:</div>
        <div class="scroll-x wrap">
          ${pill('📚 ملفات مادة', 'dev-d-kind', 'data-k="subject"', ui.kind === 'subject')}
          ${pill('📣 إعلانات وواجبات مجموعة', 'dev-d-kind', 'data-k="notices"', ui.kind === 'notices')}
          ${pill('📌 تقارير مجموعة', 'dev-d-kind', 'data-k="reports"', ui.kind === 'reports')}
          ${pill('👨‍🏫 تسليمات دكتور', 'dev-d-kind', 'data-k="doctor"', ui.kind === 'doctor')}
        </div>

        ${ui.kind === 'subject' ? `
          <div class="sec">اختر المادة</div>
          <div class="scroll-x wrap">${subs.map(s => pill(s.name_ar, 'dev-d-subj', `data-id="${s.id}"`, String(ui.subject_id) === String(s.id))).join('')}</div>
        ` : ''}

        ${ui.kind === 'notices' || ui.kind === 'reports' ? `
          <div class="sec">اختر المجموعة النظرية</div>
          <div class="scroll-x wrap">${B.theoryGroups().map(g => pill(g, 'dev-d-grp', `data-g="${g}"`, ui.group === g)).join('')}</div>
        ` : ''}

        ${ui.kind === 'doctor' ? `
          <div class="sec">اختر الدكتور</div>
          <div class="scroll-x wrap">${docs.map(d => pill(d.name, 'dev-d-doc', `data-id="${d.id}"`, String(ui.doctor_id) === String(d.id))).join('')}</div>
          <div class="sec">نوع التسليم والمجموعة</div>
          ${seg('dev-d-part', [['theory', 'نظري'], ['practical', 'عملي']], ui.part)}
          <div class="scroll-x wrap" style="margin-top:8px">${destGroupsPool.map(g => pill(g, 'dev-d-grp', `data-g="${g}"`, ui.group === g)).join('')}</div>
        ` : ''}

        <div class="card" style="margin-top:10px;background:var(--surface-2);padding:10px 14px">
          <small class="muted">الوجهة المختارة تلقائيًا:</small>
          <b style="display:block">${esc(destResolved.label)}</b>
        </div>

        <div class="two">
          <input class="field" dir="ltr" data-f4="devDestUI.chat_id" placeholder="chat_id (مثال: -100123456)" value="${esc(ui.chat_id)}">
          <input class="field" dir="ltr" data-f4="devDestUI.topic_id" placeholder="topic_id (اتركه فارغًا للعام)" value="${esc(ui.topic_id)}">
        </div>
        <button class="btn primary sm" style="margin-top:10px" data-act="dev-dest-save">💾 حفظ ربط الوجهة</button>

        ${(cat.destinations || []).length ? `<div class="sec">الأقسام المربوطة حاليًا (${cat.destinations.length})</div>
          ${(cat.destinations || []).map(d => `<div class="row" style="align-items:center">
            <div>
              <b style="display:block;color:var(--text)">${esc(d.label || d.key)}</b>
              <small class="muted"><code>${d.chat_id}</code> · Topic: <code>${d.topic_id || 'عام'}</code></small>
            </div>
            <button class="btn sm danger" style="width:auto;min-height:32px;padding:0 10px" data-act="dev-dest-del" data-k="${esc(d.key)}">حذف</button>
          </div>`).join('')}` : ''}
      </div>`;
  }

  if (sub === 'trash') {
    const tr = dv.trash;
    if (!tr) return skel(3);
    const files = tr.files || [], asgs = tr.assignments || [];
    return `<div class="sec" style="margin-top:4px">الملفات المحذوفة (${files.length}) — تبقى 30 يومًا</div>
      ${!files.length ? empty('سلة الملفات فارغة', '') : files.map(f => `<div class="item">
        <div class="item-main"><b>${esc(f.subject)} · ${esc(f.title)}</b><div class="meta"><span>حُذف: ${fmtD(f.deleted_at)}</span></div></div>
        <button class="btn sm primary" style="width:auto" data-act="dev-restore-file" data-id="${f.id}">استرجاع</button>
      </div>`).join('')}
      <div class="sec">الواجبات المحذوفة (${asgs.length})</div>
      ${!asgs.length ? empty('سلة الواجبات فارغة', '') : asgs.map(a => `<div class="item">
        <div class="item-main"><b>${esc(a.subject)} · واجب ${a.number} (${esc(a.group)})</b><div class="meta"><span>حُذف: ${fmtD(a.deleted_at)}</span></div></div>
        <button class="btn sm primary" style="width:auto" data-act="dev-restore-asg" data-id="${a.id}">استرجاع</button>
      </div>`).join('')}`;
  }

  if (sub === 'activity') {
    const act = dv.activity;
    return act === null ? skel(4) : !act.length ? empty('لا يوجد نشاط مسجّل', '')
      : act.map(a => `<div class="item col">
          <div class="item-main"><b>${esc(a.action)} — ${esc(a.actor_name)}</b>
            <div class="meta"><span>${esc(a.entity || '')} #${esc(a.entity_id || '')}</span><span>· ${fmtDT(a.at)}</span></div></div>
        </div>`).join('');
  }

  /* تبويب صحة النظام والبلاغات */
  if (!ov) return skel(3);
  const hl = ov.health || {}, cnt = ov.counts || {};
  return `<div class="card" style="margin-bottom:12px">
      <b>💚 حالة طابور الإشعارات والرسائل (Outbox)</b>
      <div class="row"><span>في الانتظار (Pending)</span><b>${hl.pending || 0}</b></div>
      <div class="row"><span>قيد الإرسال (Sending)</span><b>${hl.sending || 0}</b></div>
      <div class="row"><span>أُرسلت خلال 24 ساعة</span><b>${hl.sent_24h || 0}</b></div>
      <div class="row"><span>فشلت بعد المحاولات (Failed)</span><b style="color:var(--bad)">${hl.failed || 0}</b></div>
      <div class="row"><span>أوقفوا البوت (Blocked)</span><b>${hl.blocked || 0}</b></div>
      <div class="row-actions" style="margin-top:12px">
        <button class="btn sm primary" data-act="dev-outbox-retry">🔄 إعادة محاولة الفاشل (${hl.failed || 0})</button>
        <button class="btn sm" data-act="dev-go-tickets">📩 البلاغات المفتوحة (${cnt.open_tickets || 0})</button>
      </div>
    </div>`;
};


/* ---------- ربط المدخلات والأحداث للمرحلة 4 ---------- */
document.addEventListener('input', e => {
  const t = e.target;
  const f4 = t.dataset && t.dataset.f4;
  if (f4) {
    const [o, k] = f4.split('.');
    if (o === 'rep') { P4.rep[k] = t.value; if (k === 'date') loadReportsWorkspace(true); else saveReportLocalDraft(); }
    else if (o === 'shForm' && P4.sh.form) P4.sh.form[k] = t.value;
    else if (o === 'shTxt') P4.sh.txt[k] = t.value;
    else if (o === 'mcqUp' && P4.mcq.up) P4.mcq.up[k] = t.value;
    else if (o === 'ntf') P4.ntf[k] = t.value;
    else if (o === 'dev') P4.dev[k] = t.value;
    else if (o === 'devStu' && P4.dev.stuForm) P4.dev.stuForm[k] = t.value;
    else if (o === 'devRole') P4.dev.roleForm[k] = t.value;
    else if (o === 'devSubj') P4.dev.subjForm[k] = t.value;
    else if (o === 'devDoc') P4.dev.docForm[k] = t.value;
    else if (o === 'devTeach') P4.dev.teachForm[k] = t.value;
    else if (o === 'devDestUI') P4.dev.destUI[k] = t.value;
  }
  if (t.dataset.repTopics !== undefined) {
    P4.rep.slots[+t.dataset.repTopics].topics = t.value; saveReportLocalDraft();
  } else if (t.dataset.repHw !== undefined) {
    P4.rep.slots[+t.dataset.repHw].assignment = t.value; saveReportLocalDraft();
  } else if (t.dataset.repSplitTopics !== undefined) {
    const [si, gi] = t.dataset.repSplitTopics.split(':').map(Number);
    P4.rep.slots[si].items[gi].topics = t.value; saveReportLocalDraft();
  } else if (t.dataset.repSplitHw !== undefined) {
    const [si, gi] = t.dataset.repSplitHw.split(':').map(Number);
    P4.rep.slots[si].items[gi].assignment = t.value; saveReportLocalDraft();
  }
  const pv = document.getElementById('rep-live-preview');
  if (pv) pv.textContent = previewDailyReport(P4.rep);
});

document.addEventListener('change', e => {
  const t = e.target;
  if (t.dataset.repSubj !== undefined) {
    const idx = +t.dataset.repSubj, sid = +t.value;
    const s = P4.rep.slots[idx];
    const subj = (S.boot.subjects || []).find(x => x.id === sid);
    s.subject_id = sid; s.subject = subj ? subj.name : '';
    s.doctor = resolveDoctorName(P4.rep.ws, sid, s.mode === 'practical_all' ? 'practical' : 'theory', P4.rep.group);
    saveReportLocalDraft(); rerender();
  } else if (t.dataset.repSplitSubj !== undefined) {
    const [si, gi] = t.dataset.repSplitSubj.split(':').map(Number), sid = +t.value;
    const it = P4.rep.slots[si].items[gi];
    const subj = (S.boot.subjects || []).find(x => x.id === sid);
    it.subject_id = sid; it.subject = subj ? subj.name : '';
    it.doctor = resolveDoctorName(P4.rep.ws, sid, 'practical', it.group);
    saveReportLocalDraft(); rerender();
  } else if (t.dataset.tplSubj !== undefined) {
    const idx = +t.dataset.tplSubj, sid = +t.value;
    const s = P4.rep.tplSlots[idx];
    const subj = (S.boot.subjects || []).find(x => x.id === sid);
    s.subject_id = sid; s.subject = subj ? subj.name : '';
    s.doctor = resolveDoctorName(P4.rep.ws, sid, s.mode === 'practical_all' ? 'practical' : 'theory', P4.rep.group);
    rerender();
  } else if (t.dataset.tplSplit !== undefined) {
    const [si, gi] = t.dataset.tplSplit.split(':').map(Number), sid = +t.value;
    const it = P4.rep.tplSlots[si].items[gi];
    const subj = (S.boot.subjects || []).find(x => x.id === sid);
    it.subject_id = sid; it.subject = subj ? subj.name : '';
    it.doctor = resolveDoctorName(P4.rep.ws, sid, 'practical', it.group);
    rerender();
  }
});

/* معالجة أزرار تغيير نوع المحاضرة في التقرير والقالب */
document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act || '';
  if (act.startsWith('rep-mode-')) {
    const idx = +act.slice(9);
    P4.rep.slots[idx] = defaultSlot(P4.rep.ws, el.dataset.v);
    saveReportLocalDraft(); rerender();
  } else if (act.startsWith('rep-tpl-mode-')) {
    const idx = +act.slice(13);
    P4.rep.tplSlots[idx] = defaultSlot(P4.rep.ws, el.dataset.v);
    rerender();
  }
});

Object.assign(B.actions, {
  /* التقارير */
  'rep-grp': d => { P4.rep.group = d.g; P4.rep.ws = null; rerender(); loadReportsWorkspace(); },
  'rep-tab': d => { P4.rep.tab = d.v; P4.rep.err = ''; rerender(); },
  'rep-tday': d => {
    P4.rep.tplDay = +d.w;
    const raw = (P4.rep.ws && P4.rep.ws.templates && P4.rep.ws.templates[String(d.w)]) || [];
    P4.rep.tplSlots = raw.length ? JSON.parse(JSON.stringify(raw)) : [defaultSlot(P4.rep.ws, 'theory')];
    rerender();
  },
  'rep-load-tpl': () => {
    P4.rep.slots = applyTemplateToSlots(P4.rep.ws, jsDayToPyWeekday(P4.rep.date));
    saveReportLocalDraft(); rerender(); B.toast('تم تحميل قالب اليوم ✅');
  },
  'rep-custom': () => { P4.rep.slots = [defaultSlot(P4.rep.ws, 'theory')]; P4.rep.note = ''; saveReportLocalDraft(); rerender(); },
  'rep-add-slot': () => {
    if (P4.rep.slots.length >= 6) return B.toast('الحد الأقصى 6 محاضرات في اليوم.');
    P4.rep.slots.push(defaultSlot(P4.rep.ws, 'theory')); saveReportLocalDraft(); rerender();
  },
  'rep-del-slot': d => { P4.rep.slots.splice(+d.i, 1); saveReportLocalDraft(); rerender(); },
  'rep-save': async d => {
    const rep = P4.rep, pub = d.pub === '1';
    rep.busy = true; rep.err = ''; rerender();
    const r = await call('/api/reports/daily', 'POST', {
      group: rep.group, report_date: rep.date, slots: rep.slots, note: rep.note || null, publish: pub,
    });
    rep.busy = false;
    if (r.status !== 200) { rep.err = 'تأكد من تعبئة ما تم أخذه في محاضرة واحدة على الأقل.'; return rerender(); }
    B.ls.set(repDraftKey(rep.group, rep.date), null);
    B.ls.delPrefix('rep');
    B.toast(pub ? 'تم حفظ التقرير ونشره في المجتمع ✅' : 'تم حفظ التقرير ✅');
    loadReportsWorkspace(true);
  },
  'rep-tpl-add-slot': () => {
    if (P4.rep.tplSlots.length >= 6) return B.toast('الحد الأقصى 6 محاضرات.');
    P4.rep.tplSlots.push(defaultSlot(P4.rep.ws, 'theory')); rerender();
  },
  'rep-tpl-del-slot': d => { P4.rep.tplSlots.splice(+d.i, 1); rerender(); },
  'rep-tpl-save': async () => {
    const rep = P4.rep; rep.busy = true; rerender();
    const r = await call('/api/reports/templates', 'POST', { group: rep.group, weekday: rep.tplDay, slots: rep.tplSlots });
    rep.busy = false;
    B.ls.delPrefix('rep');
    B.toast(r.status === 200 ? 'تم حفظ قالب اليوم ✅' : 'تعذّر حفظ القالب.');
    loadReportsWorkspace(true);
  },
  'rep-tpl-clear': () => ask('حذف قالب هذا اليوم؟', async () => {
    await call(`/api/reports/templates/${P4.rep.group}/${P4.rep.tplDay}`, 'DELETE');
    B.ls.delPrefix('rep');
    B.toast('تم حذف القالب'); loadReportsWorkspace(true);
  }),
  'rep-weekly-save': async d => {
    const rep = P4.rep, pub = d.pub === '1';
    rep.busy = true; rerender();
    const r = await call('/api/reports/weekly', 'POST', {
      group: rep.group, week_start: rep.ws.week_start, rendered_text: rep.weeklyText, publish: pub,
    });
    rep.busy = false;
    B.ls.delPrefix('rep');
    B.toast(r.status === 200 ? (pub ? 'نُشر الحصاد الأسبوعي في المجتمع ✅' : 'حُفظت مراجعة التقرير الأسبوعي ✅') : 'تعذّر الحفظ.');
    rerender();
  },

  /* شارك */
  'go-sh-admin': () => { B.go('sharek-admin'); },
  /* بطاقة «مشاركة/استفتاء مفتوح» في يهمّني الآن → شاشة شارك (كان الإجراء غير معرَّف فلا يحدث شيء عند الضغط) */
  'go-sharek': d => { P4.sh.focusId = +d.id || null; B.go('sharek'); },
  'sh-vote': d => ask('تأكيد اختيارك؟ التصويت نهائي ولا يمكن تغييره بعد الإرسال.', async () => {
    const r = await call(`/api/sharek/${d.id}/vote`, 'POST', { option_idx: +d.idx });
    if (r.status === 200) { B.ls.delPrefix('sharek'); B.ls.delPrefix('home'); B.toast('تم تسجيل صوتك ✅'); loadSharek(true); }
    else B.toast('سبق لك التصويت أو انتهت الحملة.');
  }),
  'sh-send-text': async d => {
    if (P4.sh.sendingId) return;
    const cid = +d.id;
    const txt = ((P4.sh.txt && P4.sh.txt[cid]) || '').trim();
    if (!txt) return B.toast('اكتب نص المشاركة أولًا.');
    P4.sh.sendingId = cid;
    rerender();
    const r = await call(`/api/sharek/${cid}/text`, 'POST', { text: txt });
    P4.sh.sendingId = null;
    if (r.status === 200) {
      P4.sh.txt[cid] = '';
      const hit = (P4.sh.list || []).find(x => +x.id === cid);
      if (hit && r.data && typeof r.data.count === 'number') hit.my_items_count = r.data.count;
      else if (hit) hit.my_items_count = (hit.my_items_count || 0) + 1;
      rerender();
      B.ls.delPrefix('sharek'); B.ls.delPrefix('home');
      B.toast('تم إرسال النص ✅ يمكنك إضافة نص أو ملف آخر أو إنهاء المشاركة.');
      loadSharek(true);
    } else {
      rerender();
      const m = { already_finished: 'أنهيت مشاركتك في هذه الحملة مسبقًا.', too_many: 'بلغت الحد الأقصى من العناصر.', not_found: 'هذه الحملة لم تعد متاحة.', not_target: 'هذه الحملة ليست لمجموعتك.' };
      B.toast(m[r.data && r.data.detail] || 'تعذّر إرسال النص، حاول مرة أخرى.');
    }
  },
  'sh-finish': d => ask('إنهاء المشاركة واعتمادها؟ لن تستطيع إضافة عناصر أخرى بعد ذلك.', async () => {
    const r = await call(`/api/sharek/${d.id}/finish`, 'POST');
    if (r.status === 200) {
      B.ls.delPrefix('sharek'); B.ls.delPrefix('home');
      B.toast('تم إنهاء واعتماد مشاركتك ✅'); loadSharek(true);
    } else {
      B.toast((r.data && r.data.detail) === 'no_items' ? 'أرسل نصًا أو ملفًا واحدًا على الأقل قبل الإنهاء.' : 'تعذّر إنهاء المشاركة.');
    }
  }),
  'sh-open-bot': async d => {
    if (d.id && !B.DEMO) {
      const r = await call(`/api/sharek/${d.id}/start-bot`, 'POST');
      const link = (r.data && r.data.bot_link) || d.url;
      return B.openBotDirect(link);
    }
    B.openBotDirect(d.url);
  },
  'sh-new': () => {
    const isAny = (S.boot.perms || []).some(p => p.k === 'sharek.publish' && p.s === 'any');
    const groups = allowedSharekGroups();
    P4.sh.form = { kind: 'poll', group: isAny ? 'ALL' : (groups[0] || B.firstTheory()), title: '', options: '' };
    P4.sh.err = ''; rerender(); window.scrollTo(0, 0);
  },
  'sh-form-cancel': () => { P4.sh.form = null; P4.sh.err = ''; rerender(); },
  'sh-fkind': d => { P4.sh.form.kind = d.v; rerender(); },
  'sh-fgrp': d => { P4.sh.form.group = d.g; rerender(); },
  'sh-create-save': async () => {
    const sh = P4.sh, f = sh.form;
    sh.busy = true; sh.err = ''; rerender();
    const r = await call('/api/staff/sharek', 'POST', {
      kind: f.kind, group: f.group, title: f.title, options: f.options,
    });
    sh.busy = false;
    if (r.status !== 200) { sh.err = 'تأكد من كتابة العنوان وإدخال 2 إلى 10 خيارات للاستفتاء.'; return rerender(); }
    sh.form = null; B.ls.delPrefix('sharek'); B.toast('تم نشر الحملة وإشعار الطلاب ✅'); loadSharekStaff(true);
  },
  'sh-detail': d => { P4.sh.resGroup = defaultSharekResGroup(); B.go('sharek-res:' + d.id); },
  'sh-rgrp': d => {
    P4.sh.resGroup = d.g;
    swr(`sh-res:${d.id}:${d.g}`, `/api/staff/sharek/${d.id}?group=${d.g}`, x => { if (x) { P4.sh.res[d.id] = x; rerender(); } });
  },
  'sh-copy-compiled': async d => {
    const r = P4.sh.res[d.id];
    const text = compileSharekText(r);
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      B.toast('تم نسخ التجميع إلى الحافظة ✅');
    } catch {
      B.toast('انسخ النص مباشرة من المعاينة أعلاه.');
    }
  },
  'sh-items-bot': async d => {
    const r = await call(`/api/staff/sharek/${d.id}/send-items-bot`, 'POST', {
      group: P4.sh.resGroup,
      item_id: d.item ? +d.item : null,
    });
    if (r.status === 200) B.toast('أُرسلت الصور والملفات المستلمة إلى محادثتك مع البوت ✅');
    else if (r.status === 404) B.toast('لا توجد صور أو مشاركات مستلمة في هذه الحملة بعد.');
    else B.toast('تعذّر الإرسال إلى البوت.');
  },
  'sh-exp-bot': async d => {
    const r = await call(`/api/staff/sharek/${d.id}/export-bot`, 'POST', { group: P4.sh.resGroup });
    B.toast(r.status === 200 ? 'أُرسل ملف Excel إلى محادثتك مع البوت ✅' : 'تعذّر الإرسال.');
  },
  'sh-exp-dl': d => {
    if (B.DEMO) return B.toast('عرض تجريبي');
    downloadAuth(`/api/staff/sharek/${d.id}/export?group=${P4.sh.resGroup}`, `sharek_${d.id}.xlsx`);
  },
  'sh-hide': d => ask('إخفاء الحملة عن الطلاب؟ ستبقى نتائجها محفوظة للتصدير.', async () => {
    const r = await call(`/api/staff/sharek/${d.id}`, 'DELETE');
    if (r.status === 200) { B.ls.delPrefix('sharek'); B.toast('تم إخفاء الحملة'); loadSharekStaff(true); B.back(); }
  }),

  /* الاختبارات MCQ (مطابقة للشاشات المرفقة في index.html) */
  'qz-pick-subj': d => { P4.mcq.subjectPick = +d.id; buzz('sel'); rerender(); },
  'qz-to-units': () => { if (!P4.mcq.subjectPick) return; P4.mcq.step = 'units'; P4.mcq.examPick = null; rerender(); window.scrollTo(0, 0); },
  'qz-to-home': () => { stopMcqTimer(); P4.mcq.step = 'home'; P4.mcq.examPick = null; rerender(); window.scrollTo(0, 0); },
  'qz-pick-exam': d => { P4.mcq.examPick = +d.id; buzz('sel'); rerender(); },
  'qz-to-intro': () => { if (!P4.mcq.examPick) return; P4.mcq.step = 'intro'; rerender(); window.scrollTo(0, 0); },
  'qz-toggle-timer': () => { P4.mcq.timerOn = !P4.mcq.timerOn; buzz('sel'); rerender(); },
  'qz-toggle-instant': () => { P4.mcq.instantOn = !P4.mcq.instantOn; buzz('sel'); rerender(); },
  'qz-toggle-shuffle': () => { P4.mcq.shuffleOn = !P4.mcq.shuffleOn; buzz('sel'); rerender(); },
  'mcq-open': d => {
    stopMcqTimer();
    P4.mcq.exam = null;
    P4.mcq.run = null;
    B.go('mcq-run:' + d.id);
  },
  'mcq-pick': d => {
    const run = P4.mcq.run;
    if (!run || run.done) return;
    const q = run.qs[run.idx];
    if (run.instant !== false && run.answers[q.id]) return; /* التصحيح الفوري: الإجابة نهائية */
    const pos = +d.pos;
    const o = q.opts[pos];
    const ok = o.orig === q.correctOrig;
    run.answers[q.id] = { pos, orig: o.orig, ok };
    buzz(run.instant !== false ? (ok ? 'success' : 'error') : 'sel');
    rerender();
  },
  'mcq-prev': () => {
    const run = P4.mcq.run;
    if (!run || run.idx <= 0) return;
    run.idx--;
    rerender();
    window.scrollTo(0, 0);
  },
  'mcq-next': () => {
    const run = P4.mcq.run;
    if (!run) return;
    if (run.idx + 1 >= run.qs.length) finishMcqAttempt();
    else { run.idx++; rerender(); window.scrollTo(0, 0); }
  },
  'mcq-ask-exit': () => ask('الخروج من الاختبار؟ سيُفقد تقدّمك الحالي.', () => {
    stopMcqTimer();
    P4.mcq.run = null;
    B.back();
  }),
  'qz-exit-home': () => {
    stopMcqTimer();
    P4.mcq.run = null;
    P4.mcq.step = 'home';
    B.back();
  },
  'mcq-rev-open': () => { if (P4.mcq.run) { P4.mcq.run.reviewMode = true; rerender(); window.scrollTo(0, 0); } },
  'mcq-rev-close': () => { if (P4.mcq.run) { P4.mcq.run.reviewMode = false; rerender(); window.scrollTo(0, 0); } },
  'mcq-retry': () => {
    if (P4.mcq.exam) {
      startMcqRun(P4.mcq.exam);
      rerender();
      window.scrollTo(0, 0);
    }
  },
  'mcq-del': d => ask('حذف هذا الاختبار؟', async () => {
    const r = await call(`/api/mcq/${d.id}`, 'DELETE');
    if (r.status === 200) {
      P4.mcq.examPick = null;
      B.ls.delPrefix('mcq');
      B.toast('تم حذف الاختبار');
      loadMcqList(true);
    }
  }),
  'mcq-go-up': () => { P4.mcq.up = null; P4.mcq.err = ''; B.go('mcq-upload'); },
  'mcqu-subj': d => { P4.mcq.up.subject_id = +d.id; rerender(); },
  'mcq-dl-tpl': () => { downloadAuth('/api/mcq/template', 'mcq_template.xlsx'); },
  'mcq-upload-save': async () => {
    const up = P4.mcq.up, inp = document.getElementById('mcq-xlsx-input');
    const file = inp && inp.files && inp.files[0];
    if (!up.subject_id || !up.title.trim()) { P4.mcq.err = 'اختر المادة واكتب عنوان الاختبار.'; return rerender(); }
    if (!file && !B.DEMO) { P4.mcq.err = 'اختر ملف Excel (.xlsx) للأسئلة.'; return rerender(); }
    P4.mcq.busy = true; P4.mcq.err = ''; rerender();
    const b64 = file ? await readFileBase64(file) : '';
    const r = await call('/api/mcq/upload', 'POST', {
      subject_id: up.subject_id, title: up.title, description: up.description || null,
      duration_minutes: +up.duration_minutes || 15, excel_base64: b64 || null,
    });
    P4.mcq.busy = false;
    if (r.status !== 200) {
      P4.mcq.err = (r.data && r.data.errors && r.data.errors.join('\n')) || 'تعذّر رفع الاختبار. راجع أعمدة ملف Excel.';
      return rerender();
    }
    B.ls.delPrefix('mcq');
    B.toast(`تم رفع الاختبار بنجاح (${r.data.questions_count || ''} سؤال) ✅`);
    P4.mcq.up = null; B.back(); loadMcqList(true);
  },

  /* شاشة الدكتور */
  'doc-pick': d => { P4.doc.doctor_id = +d.id; P4.doc.dash = null; rerender(); loadDoctorView(); },
  'doc-go': d => B.go(d.v === 'export' ? 'doc-export' : 'doc-results'),
  'doc-grp-pick': d => { P4.doc.grp = d.g; rerender(); },
  'doc-grp-bot': async () => {
    const doc = P4.doc; if (!doc.grp) return;
    const q = doc.doctor_id ? `?doctor_id=${doc.doctor_id}` : '';
    const r = await call(`/api/doctor/groups/${encodeURIComponent(doc.grp)}/export-bot${q}`, 'POST');
    B.toast(r.status === 200 ? 'أُرسل ملف Excel إلى محادثتك مع البوت ✅' : (r.status === 404 ? 'لا توجد واجبات لهذه المجموعة.' : 'تعذّر الإرسال.'));
  },
  'doc-grp-dl': () => {
    const doc = P4.doc; if (!doc.grp) return;
    const q = doc.doctor_id ? `?doctor_id=${doc.doctor_id}` : '';
    downloadAuth(`/api/doctor/groups/${encodeURIComponent(doc.grp)}/export${q}`, `assignments_${doc.grp}.xlsx`);
  },
  'doc-exp-bot': async d => {
    const r = await call(`/api/doctor/assignments/${d.id}/export-bot`, 'POST');
    B.toast(r.status === 200 ? 'أُرسل ملف Excel (1/0) إلى محادثتك مع البوت ✅' : 'تعذّر الإرسال.');
  },
  'doc-exp-dl': d => {
    if (B.DEMO) return B.toast('عرض تجريبي');
    downloadAuth(`/api/doctor/assignments/${d.id}/export`, `assignment_${d.id}.xlsx`);
  },

  /* الإشعارات اليدوية */
  'ntf-grp': d => { P4.ntf.group = d.g; rerender(); },
  'ntf-send': async () => {
    const nf = P4.ntf;
    if (!nf.body || nf.body.trim().length < 2) { nf.err = 'اكتب نص الإشعار.'; return rerender(); }
    nf.busy = true; nf.err = ''; rerender();
    const r = await call('/api/notify/manual', 'POST', { group: nf.group, title: nf.title || null, body: nf.body });
    nf.busy = false;
    if (r.status !== 200) { nf.err = 'تعذّر إرسال الإشعار.'; return rerender(); }
    nf.title = ''; nf.body = '';
    B.toast(`أُرسل الإعلان إلى ${r.data.recipients || 0} طالب ✅`);
    rerender();
  },

  /* لوحة المطوّر: كل بطاقة تفتح في شاشة كاملة مستقلة */
  'dev-open': d => { P4.dev.tab = d.v; B.go('dev-view:' + d.v); },
  'dev-stu-form-toggle': () => { P4.dev.stuForm = P4.dev.stuForm ? null : { no: '', name: '', theory: B.firstTheory(), practical: B.firstPractical() }; rerender(); },
  'dev-stu-save': async () => {
    const f = P4.dev.stuForm;
    const r = await call('/api/dev/students', 'POST', { academic_no: f.no, full_name: f.name, theory_group_id: f.theory, practical_group_id: f.practical });
    if (r.status !== 200) return B.toast('بيانات غير صالحة أو المجموعة العملية لا تتبع النظرية.');
    P4.dev.stuForm = null; B.ls.delPrefix('dev-stu'); B.toast('تم حفظ الطالب ✅'); loadDevSub('students', true);
  },
  'dev-stu-import': async () => {
    const inp = document.getElementById('dev-stu-xlsx');
    const file = inp && inp.files && inp.files[0];
    if (!file && !B.DEMO) return B.toast('اختر ملف Excel أولًا.');
    const b64 = file ? await readFileBase64(file) : '';
    const r = await call('/api/dev/students/import', 'POST', { excel_base64: b64 });
    if (r.status !== 200) return B.toast('تعذّر قراءة ملف Excel.');
    B.ls.delPrefix('dev-stu');
    B.toast(`تم الاستيراد ✅ (جديد: ${r.data.inserted || 0} · محدّث: ${r.data.updated || 0})`);
    loadDevSub('students', true);
  },
  'dev-stu-search': () => loadDevSub('students', true),
  'dev-unlink': d => ask(`فك ارتباط الحساب عن الرقم الأكاديمي ${d.no}؟`, async () => {
    const r = await call('/api/dev/students/unlink', 'POST', { academic_no: d.no });
    if (r.status === 200) { B.ls.delPrefix('dev-stu'); B.toast('تم فك الارتباط ✅'); loadDevSub('students', true); }
  }),
  'dev-ban': d => ask(d.ban === '1' ? 'حظر هذا المستخدم؟' : 'فك الحظر عن هذا المستخدم؟', async () => {
    const r = await call('/api/dev/users/ban', 'POST', { telegram_id: +d.id, banned: d.ban === '1' });
    if (r.status === 200) { B.ls.delPrefix('dev-guests'); B.toast(d.ban === '1' ? 'تم الحظر' : 'تم فك الحظر'); loadDevSub('guests', true); }
  }),
  'dev-rgrp': d => { P4.dev.roleForm.group = d.v; rerender(); },
  'dev-role-grant': async () => {
    const rf = P4.dev.roleForm;
    if (!rf.telegram_id) return B.toast('أدخل Telegram ID.');
    const r = await call('/api/dev/roles', 'POST', { telegram_id: +rf.telegram_id, role: rf.role, group: rf.role === 'developer' ? null : rf.group });
    if (r.status !== 200) return B.toast('تعذّر منح الصلاحية.');
    rf.telegram_id = ''; B.ls.delPrefix('dev-cat'); B.toast('تم منح الصلاحية ✅'); loadDevSub('roles', true);
  },
  'dev-role-rev': d => ask('سحب هذه الصلاحية؟', async () => {
    const r = await call(`/api/dev/roles/${d.id}`, 'DELETE');
    if (r.status === 200) { B.ls.delPrefix('dev-cat'); B.toast('تم سحب الصلاحية'); loadDevSub('roles', true); }
  }),
  'dev-subj-save': async () => {
    const sf = P4.dev.subjForm;
    if (!sf.name_ar.trim()) return B.toast('اكتب اسم المادة.');
    const r = await call('/api/dev/subjects', 'POST', sf);
    if (r.status === 200) { sf.name_ar = ''; sf.hashtag = ''; B.ls.delPrefix('dev-cat'); B.toast('تم حفظ المادة ✅'); loadDevSub('catalog', true); }
  },
  'dev-doc-save': async () => {
    const df = P4.dev.docForm;
    if (!df.name.trim()) return B.toast('اكتب اسم الدكتور.');
    const r = await call('/api/dev/doctors', 'POST', { name: df.name, telegram_id: df.telegram_id ? +df.telegram_id : null });
    if (r.status === 200) { df.name = ''; df.telegram_id = ''; B.ls.delPrefix('dev-cat'); B.toast('تم حفظ الدكتور ✅'); loadDevSub('catalog', true); }
  },

  /* تحديد الدكتور والمجموعات (اختيار الكل أو عدة مجموعات) */
  'dev-t-subj': d => { P4.dev.teachForm.subject_id = +d.id; rerender(); },
  'dev-t-doc': d => { P4.dev.teachForm.doctor_id = +d.id; rerender(); },
  'dev-t-part': d => {
    P4.dev.teachForm.part = d.v;
    P4.dev.teachForm.groups = d.v === 'practical' ? [B.firstPractical()] : [B.firstTheory()];
    rerender();
  },
  'dev-t-grp': d => {
    const g = d.g, cur = P4.dev.teachForm.groups || [];
    P4.dev.teachForm.groups = cur.includes(g) ? cur.filter(x => x !== g) : [...cur, g];
    rerender();
  },
  'dev-t-grp-all': () => {
    const tf = P4.dev.teachForm;
    const pool = tf.part === 'practical' ? B.practicalGroups() : B.theoryGroups();
    const isAll = pool.every(g => (tf.groups || []).includes(g));
    tf.groups = isAll ? [] : [...pool];
    rerender();
  },
  'dev-teach-save': async () => {
    const tf = P4.dev.teachForm;
    if (!tf.subject_id || !tf.doctor_id || !(tf.groups && tf.groups.length)) return B.toast('اختر المادة والدكتور ومجموعة واحدة على الأقل.');
    const r = await call('/api/dev/teaching', 'POST', {
      subject_id: +tf.subject_id,
      doctor_id: +tf.doctor_id,
      part: tf.part,
      groups: tf.groups,
    });
    if (r.status === 200) { B.ls.delPrefix('dev-cat'); B.toast(`تم ربط التدريس لـ ${tf.groups.length} مجموعة ✅`); loadDevSub('catalog', true); }
    else B.toast('تعذّر حفظ الربط.');
  },
  'dev-teach-del': async d => {
    const r = await call(`/api/dev/teaching/${d.id}`, 'DELETE');
    if (r.status === 200) { B.ls.delPrefix('dev-cat'); B.toast('تم حذف الرابط'); loadDevSub('catalog', true); }
  },

  /* واجهة ربط المجتمعات المبسّطة */
  'dev-d-kind': d => {
    const ui = P4.dev.destUI;
    ui.kind = d.k;
    if (d.k === 'notices' || d.k === 'reports') ui.group = B.theoryGroups().includes(ui.group) ? ui.group : B.firstTheory();
    if (d.k === 'doctor') ui.group = ui.part === 'practical' ? B.firstPractical() : B.firstTheory();
    rerender();
  },
  'dev-d-subj': d => { P4.dev.destUI.subject_id = +d.id; rerender(); },
  'dev-d-doc': d => { P4.dev.destUI.doctor_id = +d.id; rerender(); },
  'dev-d-part': d => {
    P4.dev.destUI.part = d.v;
    P4.dev.destUI.group = d.v === 'practical' ? B.firstPractical() : B.firstTheory();
    rerender();
  },
  'dev-d-grp': d => { P4.dev.destUI.group = d.g; rerender(); },
  'dev-dest-save': async () => {
    const ui = P4.dev.destUI;
    const resolved = computeDestKeyAndLabel(ui, P4.dev.cat);
    if (!resolved.key || !ui.chat_id) return B.toast('أدخل رقم chat_id الخاص بالمجتمع.');
    const r = await call('/api/dev/destinations', 'POST', {
      key: resolved.key,
      chat_id: +ui.chat_id,
      topic_id: ui.topic_id ? +ui.topic_id : null,
      label: resolved.label,
    });
    if (r.status === 200) {
      B.ls.delPrefix('dev-cat');
      B.toast(`تم ربط «${resolved.label}» بنجاح ✅`);
      loadDevSub('catalog', true);
    } else B.toast('تعذّر حفظ الوجهة.');
  },
  'dev-dest-del': d => ask('حذف ربط هذا القسم؟', async () => {
    const r = await call('/api/dev/destinations/delete', 'POST', { key: d.k });
    if (r.status === 200) { B.ls.delPrefix('dev-cat'); B.toast('تم الحذف'); loadDevSub('catalog', true); }
  }),
  'dev-restore-file': async d => {
    const r = await call(`/api/files/${d.id}/restore`, 'POST');
    if (r.status === 200) { B.ls.delPrefix('dev-trash'); B.ls.delPrefix('files'); B.toast('تم استرجاع الملف وإعادة نشره ✅'); loadDevSub('trash', true); }
  },
  'dev-restore-asg': async d => {
    const r = await call(`/api/assignments/${d.id}/restore`, 'POST');
    if (r.status === 200) { B.ls.delPrefix('dev-trash'); B.ls.delPrefix('asg'); B.toast('تم استرجاع الواجب ✅'); loadDevSub('trash', true); }
  },
  'dev-outbox-retry': async () => {
    const r = await call('/api/dev/outbox/retry', 'POST');
    if (r.status === 200) { B.ls.delPrefix('dev-ov'); B.toast(`تمت إعادة جدولة ${r.data.retried || 0} رسالة ✅`); loadDevSub('health', true); }
  },
  'dev-go-tickets': () => { P.ai.staffTab = 'staff'; B.go('assistant'); },
});

/* ---------- ربط المسارات والعناوين ودخول الشاشات للمرحلة 4 ---------- */
Object.assign(B.routes, {
  quiz: 'quiz',
  sharek: 'sharek',
  'doc-assign': 'doc-assign',
  'a-report': 'reports',
  'a-sharek': 'sharek-admin',
  'a-mcq': 'mcq-upload',
  'a-notify': 'notify-send',
  'a-dev': 'dev',
});
Object.assign(B.titles, {
  reports: 'التقارير اليومية والأسبوعية',
  sharek: 'شارك',
  'sharek-admin': 'إدارة شارك',
  'sharek-res': 'نتائج شارك',
  quiz: 'الاختبارات (MCQ)',
  'mcq-run': 'الاختبار',
  'mcq-upload': 'رفع اختبار MCQ',
  'doc-assign': 'واجبات الطلاب (الدكتور)',
  'doc-results': 'نتائج الواجبات',
  'doc-export': 'تصدير Excel',
  'notify-send': 'إرسال إشعار يدوي',
  dev: 'لوحة المطوّر',
  'dev-view:students': 'إدارة الطلاب',
  'dev-view:guests': 'الزوار والحظر',
  'dev-view:roles': 'إدارة الأدوار والصلاحيات',
  'dev-view:catalog': 'المواد والدكاترة والمجتمعات',
  'dev-view:trash': 'سلة المحذوفات',
  'dev-view:activity': 'سجل النشاط',
  'dev-view:health': 'صحة النظام',
});

Object.assign(B.tabMap, { 'doc-assign': 'f:doc-assign', 'doc-results': 'f:doc-assign', 'doc-export': 'f:doc-assign' });

const prevEnter = B.enter;
B.enter = view => {
  stopMcqTimer();
  if (prevEnter) prevEnter(view);
  const [v, id] = view.split(':');
  if (v === 'reports') loadReportsWorkspace();
  else if (v === 'sharek') loadSharek();
  else if (v === 'sharek-admin') loadSharekStaff();
  else if (v === 'sharek-res') {
    const grp = P4.sh.resGroup || defaultSharekResGroup();
    P4.sh.resGroup = grp;
    swr(`sh-res:${id}:${grp}`, `/api/staff/sharek/${id}?group=${grp}`, d => { if (d) { P4.sh.res[id] = d; rerender(); } });
  }
  else if (v === 'quiz') loadMcqList();
  else if (v === 'mcq-run') swr(`mcq-exam:${id}`, `/api/mcq/${id}`, d => { if (d) { P4.mcq.exam = d; rerender(); } });
  else if (v === 'doc-assign' || v === 'doc-results' || v === 'doc-export' || (v === 'f' && id === 'doc-assign')) loadDoctorView();
  else if (v === 'dev-view') loadDevSub(id);
};

/* ---------- بيانات المعاينة التجريبية (#demo=...) للمرحلة 4 ---------- */
function demoRead4(path) {
  const h = n => new Date(Date.now() + n * 3600e3).toISOString();
  if (path.startsWith('/api/reports/workspace')) return {
    status: 'ok', group: 'CS2_G1', date: todaySanaa(), week_start: '2026-10-17',
    practical_groups: ['G1', 'G2', 'G3'],
    teaching: [
      { subject_id: 1, subject: 'شبكات الحاسوب', part: 'theory', group: 'CS2_G1', doctor: 'د. خالد الهاشمي' },
      { subject_id: 2, subject: 'مبادئ قواعد البيانات', part: 'practical', group: 'G3', doctor: 'أ. منى فرج' },
      { subject_id: 3, subject: 'البرمجة الهدفية', part: 'theory', group: 'CS2_G1', doctor: 'د. أحمد المنصوري' },
    ],
    templates: { '1': [{ slot: 1, mode: 'theory', subject_id: 3, subject: 'البرمجة الهدفية', doctor: 'د. أحمد المنصوري' }, { slot: 2, mode: 'theory', subject_id: 1, subject: 'شبكات الحاسوب', doctor: 'د. خالد الهاشمي' }] },
    current_report: null, week_reports: [], weekly_report: null,
    compiled_weekly: '📚 الأسبوع 1 — CS2_G1\n─────────────\n📌 تقرير يوم الثلاثاء 8 سبتمبر CS2_G1\n• تم أخذ موضوع ال Inheritance',
  };
  if (path.startsWith('/api/sharek')) return [
    { id: 1, kind: 'poll', grp: 'CS2_G1', title: 'ما اليوم الأنسب للمحاضرة التعويضية في مادة شبكات الحاسوب؟', my_vote: null, tallies: [{ idx: 0, label: 'الاثنين 10 صباحًا', votes: 18 }, { idx: 1, label: 'الأربعاء 12 ظهرًا', votes: 24 }] },
    { id: 2, kind: 'submission', grp: 'CS2_G1', title: 'شاركنا ملخصاتك أو ملاحظاتك لمادة قواعد البيانات', my_items_count: 1, is_finished: false, bot_link: 'https://t.me/bot?start=sh_2' },
  ];
  if (path.startsWith('/api/staff/sharek/')) return {
    status: 'ok', campaign: { id: 1, kind: 'submission', group: 'CS2_G1', title: 'شاركنا ملخصاتك أو ملاحظاتك لمادة قواعد البيانات', status: 'open' },
    tallies: [],
    votes: [],
    submissions: [
      { id: 1, no: '20260017', name: 'سارة محمد العلي', theory: 'CS2_G1', practical: 'G2', item_kind: 'document', file_id: 'doc1', file_name: 'ملخص_قواعد_بيانات.pdf' },
    ],
  };
  if (path.startsWith('/api/staff/sharek')) return [
    { id: 1, kind: 'submission', grp: 'CS2_G1', title: 'شاركنا ملخصاتك أو ملاحظاتك لمادة قواعد البيانات', status: 'open', participants: 12 },
  ];
  if (/^\/api\/mcq\/\d+/.test(path)) return {
    status: 'ok',
    exam: { id: 1, subject_id: 1, subject: 'شبكات الحاسوب', title: 'Module 9 & 13: Network Layer', description: 'Interactive quiz on IPv4, IPv6, and ICMP protocols.', duration_minutes: 10, questions_count: 2 },
    questions: [
      { id: 1, position: 1, question: 'Which protocol is responsible for logical addressing and routing at the Network Layer?', options: ['IP (Internet Protocol)', 'TCP (Transmission Control Protocol)', 'HTTP', 'ARP'], correct_index: 0 },
      { id: 2, position: 2, question: 'Which OSI layer handles Frames and MAC addresses?', options: ['Physical Layer', 'Data Link Layer', 'Transport Layer', 'Session Layer'], correct_index: 1 },
    ],
  };
  if (path.startsWith('/api/mcq')) return [
    { id: 1, subject_id: 1, subject: 'شبكات الحاسوب', title: 'Module 9 & 13: Network Layer', description: 'Interactive quiz on IPv4, IPv6, and ICMP protocols.', duration_minutes: 10, questions_count: 2, can_delete: true, last_attempt: { score: 2, total: 2 } },
  ];
  if (path.startsWith('/api/doctor/assignments')) return {
    status: 'ok', doctor: { id: 1, name: 'د. أحمد المنصوري' }, doctors: [{ id: 1, name: 'د. أحمد المنصوري' }],
    assignments: [
      { id: 11, number: 3, subject: 'البرمجة الهدفية', part: 'theory', grp: 'CS2_G1', due_at: h(5), status: 'open', submitted: 38, total: 52 },
      { id: 10, number: 2, subject: 'البرمجة الهدفية', part: 'theory', grp: 'CS2_G2', due_at: h(-48), status: 'closed', submitted: 49, total: 50 },
    ],
  };
  if (path.startsWith('/api/dev/overview')) return {
    status: 'ok',
    counts: { students_total: 156, students_linked: 142, guests: 9, banned: 1, open_tickets: 1, trash_files: 1, trash_assignments: 0 },
    health: { pending: 0, sending: 0, sent_24h: 312, failed: 0, blocked: 2, recent_failures: [] },
  };
  if (path.startsWith('/api/dev/students')) return [
    { academic_no: '20260017', full_name: 'سارة محمد العلي', theory_group_id: 'CS2_G1', practical_group_id: 'G2', telegram_id: 777001 },
    { academic_no: '20260021', full_name: 'خالد سالم', theory_group_id: 'CS2_G1', practical_group_id: 'G1', telegram_id: null },
  ];
  if (path.startsWith('/api/dev/guests')) return [
    { telegram_id: 888001, display_name: 'زائر تجريبي', username: 'guest_user', is_banned: false, last_seen_at: h(-1) },
  ];
  if (path.startsWith('/api/dev/catalog')) return {
    status: 'ok',
    roles: [{ id: 1, telegram_id: 777001, role: 'delegate', group: 'CS2_G1', name: 'سارة محمد العلي' }],
    subjects: S.boot.subjects.map(s => ({ id: s.id, name_ar: s.name, hashtag: s.tag, sort: 1, is_active: true })),
    doctors: [{ id: 1, name: 'د. أحمد المنصوري', telegram_id: 111111 }],
    teaching: [{ id: 1, subject_id: 1, subject: 'شبكات الحاسوب', part: 'theory', group: 'CS2_G1', doctor_id: 1, doctor: 'د. أحمد المنصوري' }],
    destinations: [{ key: 'cohort:subject:1', chat_id: -100123456, topic_id: 11, label: 'ملفات ومحاضرات شبكات الحاسوب' }],
  };
  if (path.startsWith('/api/dev/trash')) return {
    status: 'ok', files: [{ id: 99, subject: 'شبكات الحاسوب', title: 'ملخص · Module 5', deleted_at: h(-12) }], assignments: [],
  };
  if (path.startsWith('/api/dev/activity')) return [
    { id: 1, at: h(-1), actor_name: 'سارة محمد العلي', action: 'assignment.publish', entity: 'assignment', entity_id: '11' },
  ];
  return null;
}
function demoWrite4() {
  return { status: 'ok', ok: true, id: 1, questions_count: 10, recipients: 52, inserted: 10, updated: 2, retried: 0, count: 3 };
}
})();
