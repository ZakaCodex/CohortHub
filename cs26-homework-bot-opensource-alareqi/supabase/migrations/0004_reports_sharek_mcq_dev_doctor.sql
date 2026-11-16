-- =====================================================================
-- 0004_reports_sharek_mcq_dev_doctor.sql — المرحلة 4 (الأخيرة):
-- 1) التقارير اليومية والقوالب (السبت–الخميس) والتقرير الأسبوعي الآلي.
-- 2) شارك (استفتاء 2–10 خيارات، أو استقبال مشاركات متعددة، مع تصدير Excel).
-- 3) الاختبارات MCQ (رفع من Excel، زر العدّاد، التصحيح الفوري، حفظ النتيجة).
-- 4) شاشة الدكتور (واجبات مجموعاته وتصدير Excel 1/0 لكل مجموعة منفردة).
-- 5) الإشعارات اليدوية ولوحة المطوّر الشاملة (الطلاب، الزوار، الأدوار، المواد والدكاترة والمجتمعات، السلة، سجل النشاط، وصحة النظام).
-- شغّله بعد 0003. آمن لإعادة التشغيل (idempotent).
-- =====================================================================

-- توسيع أنواع المسودات لتشمل رفع اختبارات MCQ واستيراد الطلاب من البوت إن لزم
alter table drafts drop constraint if exists drafts_kind_check;
alter table drafts add constraint drafts_kind_check
  check (kind in ('file','assignment','mcq','students_import'));


-- =====================================================================
-- 1) التقارير اليومية والقوالب (السبت إلى الخميس) والتقرير الأسبوعي
-- =====================================================================
create table if not exists report_templates (
  theory_group_id text     not null references theory_groups(id),
  weekday         smallint not null check (weekday between 0 and 6), -- 5=السبت، 6=الأحد، 0=الاثنين، 1=الثلاثاء، 2=الأربعاء، 3=الخميس
  slots           jsonb    not null default '[]',
  updated_by      bigint,
  updated_at      timestamptz not null default now(),
  primary key (theory_group_id, weekday)
);

create table if not exists daily_reports (
  id              bigint generated always as identity primary key,
  theory_group_id text        not null references theory_groups(id),
  report_date     date        not null,
  slots           jsonb       not null default '[]',
  note            text,
  rendered_text   text        not null,
  published_at    timestamptz,
  chat_id         bigint,
  topic_id        integer,
  message_id      integer,
  created_by      bigint      not null,
  updated_at      timestamptz not null default now(),
  unique (theory_group_id, report_date)
);
create index if not exists daily_reports_group_date_idx
  on daily_reports (theory_group_id, report_date desc);

create table if not exists weekly_reports (
  id              bigint generated always as identity primary key,
  theory_group_id text        not null references theory_groups(id),
  week_start      date        not null,  -- تاريخ السبت لبداية الأسبوع الدراسي
  rendered_text   text        not null,
  status          text        not null default 'draft' check (status in ('draft','reviewed','published')),
  reviewed_by     bigint,
  reviewed_at     timestamptz,
  published_at    timestamptz,
  chat_id         bigint,
  topic_id        integer,
  message_id      integer,
  updated_at      timestamptz not null default now(),
  unique (theory_group_id, week_start)
);

create table if not exists report_reminders_sent (
  theory_group_id text not null references theory_groups(id),
  report_date     date not null,
  sent_at         timestamptz not null default now(),
  primary key (theory_group_id, report_date)
);

-- يحسب تاريخ يوم السبت لبداية الأسبوع الدراسي لأي تاريخ معطى
create or replace function study_week_start(p_date date) returns date
language sql immutable as $$
  -- extract(dow): 0=Sunday..6=Saturday -> المسافة منذ السبت الماضي: (dow + 1) % 7
  select p_date - (((extract(dow from p_date)::int + 1) % 7));
$$;

create or replace function reports_workspace(
  p_actor bigint,
  p_group text default null,
  p_date date default null
) returns jsonb language plpgsql stable security definer set search_path = public as $$
declare
  v_group text := p_group;
  v_date  date := coalesce(p_date, (now() at time zone 'Asia/Aden')::date);
  v_wstart date := study_week_start(v_date);
begin
  if v_group is null then
    select id into v_group from theory_groups where has_perm(p_actor, 'reports.write', id) order by sort limit 1;
  end if;
  if v_group is null then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  perform require_perm(p_actor, 'reports.write', v_group);

  return jsonb_build_object(
    'status', 'ok',
    'group', v_group,
    'date', v_date,
    'week_start', v_wstart,
    'practical_groups', coalesce((
      select jsonb_agg(code order by sort) from practical_groups where theory_group_id = v_group
    ), '[]'::jsonb),
    'teaching', coalesce((
      select jsonb_agg(jsonb_build_object(
        'subject_id', t.subject_id,
        'subject', sj.name_ar,
        'part', t.part,
        'group', coalesce(t.theory_group_id, t.practical_group_id),
        'doctor', d.name
      ))
      from teaching t
      join subjects sj on sj.id = t.subject_id and sj.deleted_at is null
      join doctors d on d.id = t.doctor_id and d.deleted_at is null
      where (t.part = 'theory' and t.theory_group_id = v_group)
         or (t.part = 'practical' and t.practical_group_id in (select code from practical_groups where theory_group_id = v_group))
    ), '[]'::jsonb),
    'templates', coalesce((
      select jsonb_object_agg(weekday::text, slots)
        from report_templates where theory_group_id = v_group
    ), '{}'::jsonb),
    'current_report', (
      select to_jsonb(dr) from daily_reports dr where dr.theory_group_id = v_group and dr.report_date = v_date
    ),
    'week_reports', coalesce((
      select jsonb_agg(to_jsonb(r) order by r.report_date asc) from (
        select id, report_date, slots, note, rendered_text, published_at, updated_at
          from daily_reports
         where theory_group_id = v_group
           and report_date between v_wstart and (v_wstart + 5)
         order by report_date asc
      ) r
    ), '[]'::jsonb),
    'weekly_report', (
      select to_jsonb(wr) from weekly_reports wr where wr.theory_group_id = v_group and wr.week_start = v_wstart
    )
  );
end $$;

create or replace function save_report_template(
  p_actor bigint,
  p_group text,
  p_weekday int,
  p_slots jsonb
) returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform require_perm(p_actor, 'reports.write', p_group);
  if p_weekday < 0 or p_weekday > 6 then
    return jsonb_build_object('status', 'invalid_weekday');
  end if;
  insert into report_templates (theory_group_id, weekday, slots, updated_by, updated_at)
  values (p_group, p_weekday, coalesce(p_slots, '[]'::jsonb), p_actor, now())
  on conflict (theory_group_id, weekday) do update
     set slots = excluded.slots, updated_by = p_actor, updated_at = now();
  perform log_activity(p_actor, 'report_template.save', 'report_template', p_group || ':' || p_weekday, null);
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function delete_report_template(p_actor bigint, p_group text, p_weekday int) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform require_perm(p_actor, 'reports.write', p_group);
  delete from report_templates where theory_group_id = p_group and weekday = p_weekday;
  perform log_activity(p_actor, 'report_template.delete', 'report_template', p_group || ':' || p_weekday, null);
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function save_daily_report(
  p_actor bigint,
  p_group text,
  p_date date,
  p_slots jsonb,
  p_note text,
  p_rendered text,
  p_publish boolean default false
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_id bigint;
  v_dest tg_destinations%rowtype;
begin
  perform require_perm(p_actor, 'reports.write', p_group);
  if trim(coalesce(p_rendered, '')) = '' then
    return jsonb_build_object('status', 'empty_report');
  end if;

  insert into daily_reports (theory_group_id, report_date, slots, note, rendered_text, published_at, created_by, updated_at)
  values (p_group, p_date, coalesce(p_slots, '[]'::jsonb), nullif(trim(coalesce(p_note, '')), ''), p_rendered,
          case when p_publish then now() end, p_actor, now())
  on conflict (theory_group_id, report_date) do update
     set slots = excluded.slots,
         note = excluded.note,
         rendered_text = excluded.rendered_text,
         published_at = coalesce(excluded.published_at, daily_reports.published_at),
         updated_at = now()
  returning id into v_id;

  if p_publish then
    select * into v_dest from tg_destinations where key in ('cohort:theory:' || p_group, 'cohort:notices:' || p_group) order by key desc limit 1;
    if found then
      insert into outbox (kind, chat_id, topic_id, payload, dedupe_key)
      values ('report_post', v_dest.chat_id, v_dest.topic_id,
              jsonb_build_object('report_id', v_id, 'group', p_group, 'date', p_date, 'text', p_rendered),
              'rep_post:' || v_id || ':' || extract(epoch from now())::bigint);
    end if;
  end if;

  perform log_activity(p_actor, case when p_publish then 'daily_report.publish' else 'daily_report.save' end,
    'daily_report', v_id::text, jsonb_build_object('group', p_group, 'date', p_date));
  return jsonb_build_object('status', 'ok', 'id', v_id, 'published', p_publish);
end $$;

create or replace function save_weekly_report(
  p_actor bigint,
  p_group text,
  p_week_start date,
  p_rendered text,
  p_publish boolean default false
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_id bigint;
  v_dest tg_destinations%rowtype;
begin
  perform require_perm(p_actor, 'reports.write', p_group);
  if trim(coalesce(p_rendered, '')) = '' then
    return jsonb_build_object('status', 'empty_report');
  end if;

  insert into weekly_reports (theory_group_id, week_start, rendered_text, status, reviewed_by, reviewed_at, published_at, updated_at)
  values (
    p_group, p_week_start, p_rendered,
    case when p_publish then 'published' else 'reviewed' end,
    p_actor, now(), case when p_publish then now() end, now()
  )
  on conflict (theory_group_id, week_start) do update
     set rendered_text = excluded.rendered_text,
         status = case when p_publish then 'published' else 'reviewed' end,
         reviewed_by = p_actor,
         reviewed_at = now(),
         published_at = coalesce(excluded.published_at, weekly_reports.published_at),
         updated_at = now()
  returning id into v_id;

  if p_publish then
    select * into v_dest from tg_destinations where key in ('cohort:theory:' || p_group, 'cohort:notices:' || p_group) order by key desc limit 1;
    if found then
      insert into outbox (kind, chat_id, topic_id, payload, dedupe_key)
      values ('weekly_report_post', v_dest.chat_id, v_dest.topic_id,
              jsonb_build_object('weekly_id', v_id, 'group', p_group, 'week_start', p_week_start, 'text', p_rendered),
              'wrep_post:' || v_id || ':' || extract(epoch from now())::bigint);
    end if;
  end if;

  perform log_activity(p_actor, case when p_publish then 'weekly_report.publish' else 'weekly_report.review' end,
    'weekly_report', v_id::text, jsonb_build_object('group', p_group, 'week_start', p_week_start));
  return jsonb_build_object('status', 'ok', 'id', v_id, 'published', p_publish);
end $$;


-- =====================================================================
-- 2) شارك (مدمجة — استفتاء باختيار واحد نهائي 2–10 خيارات، أو مشاركات متعددة)
-- =====================================================================
create table if not exists sharek_campaigns (
  id              bigint generated always as identity primary key,
  kind            text        not null check (kind in ('poll','submission')),
  theory_group_id text        references theory_groups(id), -- null = الجميع (للمطوّر)
  title           text        not null,
  options         jsonb       not null default '[]',
  status          text        not null default 'open' check (status in ('open','hidden')),
  created_by      bigint      not null,
  created_at      timestamptz not null default now(),
  hidden_at       timestamptz,
  hidden_by       bigint
);
create index if not exists sharek_campaigns_open_idx
  on sharek_campaigns (id desc) where status = 'open';

create table if not exists sharek_votes (
  campaign_id bigint   not null references sharek_campaigns(id) on delete cascade,
  student_no  text     not null references students(academic_no),
  option_idx  smallint not null,
  voted_at    timestamptz not null default now(),
  primary key (campaign_id, student_no)
);

create table if not exists sharek_items (
  id           bigint generated always as identity primary key,
  campaign_id  bigint not null references sharek_campaigns(id) on delete cascade,
  student_no   text   not null references students(academic_no),
  item_kind    text   not null check (item_kind in ('text','photo','document','video','audio','voice')),
  text_content text,
  tg_file_id   text,
  file_name    text,
  created_at   timestamptz not null default now()
);
create index if not exists sharek_items_camp_idx on sharek_items (campaign_id, student_no, id);

create table if not exists sharek_sessions (
  campaign_id bigint not null references sharek_campaigns(id) on delete cascade,
  student_no  text   not null references students(academic_no),
  finished_at timestamptz not null default now(),
  primary key (campaign_id, student_no)
);

-- ضمان منح كامل صلاحيات شارك (إنشاء وتجميع وتصدير) للجنة العلمية والمندوب ضمن مجموعتهم
insert into role_permissions (role, permission, scope, condition) values
  ('delegate',  'sharek.publish', 'group', null),
  ('committee', 'sharek.publish', 'group', null),
  ('developer', 'sharek.publish', 'any',   null)
on conflict (role, permission) do update
   set scope = excluded.scope, condition = excluded.condition;

create or replace function create_sharek_campaign(
  p_actor bigint,
  p_kind text,
  p_group text,
  p_title text,
  p_options jsonb default '[]'::jsonb
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_id bigint;
  v_title text := trim(coalesce(p_title, ''));
  v_opts jsonb := coalesce(p_options, '[]'::jsonb);
  v_grp text := case when p_group is null or upper(trim(p_group)) in ('', 'ALL', 'الجميع') then null else upper(trim(p_group)) end;
  v_payload jsonb;
  r record;
begin
  if p_kind not in ('poll', 'submission') then
    return jsonb_build_object('status', 'invalid_kind');
  end if;
  if v_grp is null and perm_scope(p_actor, 'sharek.publish') = 'group' then
    select gr.theory_group_id into v_grp
      from role_grants gr join role_permissions rp on rp.role = gr.role
     where gr.telegram_id = p_actor and gr.revoked_at is null
       and rp.permission = 'sharek.publish' and gr.theory_group_id is not null
     order by gr.id limit 1;
  end if;
  p_group := v_grp;
  if p_group is null then
    if perm_scope(p_actor, 'sharek.publish') <> 'any' then
      raise exception 'forbidden' using errcode = '42501';
    end if;
  else
    if not exists (select 1 from theory_groups where id = p_group) then
      return jsonb_build_object('status', 'invalid_group');
    end if;
    perform require_perm(p_actor, 'sharek.publish', p_group);
  end if;

  if char_length(v_title) < 3 or char_length(v_title) > 1000 then
    return jsonb_build_object('status', 'invalid_title');
  end if;

  if p_kind = 'poll' then
    if jsonb_typeof(v_opts) <> 'array' or jsonb_array_length(v_opts) < 2 or jsonb_array_length(v_opts) > 10 then
      return jsonb_build_object('status', 'invalid_options_count');
    end if;
  else
    v_opts := '[]'::jsonb;
  end if;

  insert into sharek_campaigns (kind, theory_group_id, title, options, created_by)
  values (p_kind, p_group, v_title, v_opts, p_actor)
  returning id into v_id;

  v_payload := jsonb_build_object(
    'campaign_id', v_id,
    'kind', p_kind,
    'group', coalesce(p_group, 'الجميع'),
    'title', v_title,
    'options', v_opts
  );

  for r in
    select s.telegram_id as tg, coalesce(us.notify_sharek, true) as push
      from students s left join user_settings us on us.telegram_id = s.telegram_id
     where s.deleted_at is null and s.telegram_id is not null
       and (p_group is null or s.theory_group_id = p_group)
  loop
    perform notify_user(r.tg, 'sharek_new', v_payload, r.push, 'sh_new:' || v_id || ':' || r.tg);
  end loop;

  perform bump_scope('sharek');
  perform log_activity(p_actor, 'sharek.publish', 'sharek_campaign', v_id::text,
    jsonb_build_object('kind', p_kind, 'group', p_group));
  return jsonb_build_object('status', 'ok', 'id', v_id);
end $$;

create or replace function my_sharek(p_tg bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s students%rowtype;
begin
  perform require_perm(p_tg, 'sharek.participate');
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return '[]'::jsonb; end if;

  return coalesce((
    select jsonb_agg(to_jsonb(r) order by r.id desc) from (
      select
        c.id,
        c.kind,
        coalesce(c.theory_group_id, 'الجميع') as grp,
        c.title,
        c.options,
        c.created_at,
        (select option_idx from sharek_votes v where v.campaign_id = c.id and v.student_no = s.academic_no) as my_vote,
        (select count(*) from sharek_items i where i.campaign_id = c.id and i.student_no = s.academic_no)::int as my_items_count,
        exists (select 1 from sharek_sessions ss where ss.campaign_id = c.id and ss.student_no = s.academic_no) as is_finished,
        case when c.kind = 'poll' then (
          select coalesce(jsonb_agg(
            jsonb_build_object(
              'idx', ord - 1,
              'label', opt,
              'votes', (select count(*) from sharek_votes vx where vx.campaign_id = c.id and vx.option_idx = (ord - 1))::int
            ) order by ord
          ), '[]'::jsonb)
          from jsonb_array_elements_text(c.options) with ordinality t(opt, ord)
        ) else '[]'::jsonb end as tallies
      from sharek_campaigns c
      where c.status = 'open'
        and (c.theory_group_id is null or c.theory_group_id = s.theory_group_id or is_developer(p_tg))
      order by c.id desc limit 40
    ) r
  ), '[]'::jsonb);
end $$;

-- التصويت في استفتاء شارك (اختيار واحد نهائي لا يتغيّر)
create or replace function vote_sharek(p_tg bigint, p_campaign bigint, p_option_idx int) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  s students%rowtype;
  c sharek_campaigns%rowtype;
begin
  perform require_perm(p_tg, 'sharek.participate');
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_student'); end if;

  select * into c from sharek_campaigns where id = p_campaign and status = 'open';
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if c.kind <> 'poll' then return jsonb_build_object('status', 'not_poll'); end if;
  if c.theory_group_id is not null and c.theory_group_id <> s.theory_group_id and not is_developer(p_tg) then
    return jsonb_build_object('status', 'not_target');
  end if;
  if p_option_idx is null or p_option_idx < 0 or p_option_idx >= jsonb_array_length(c.options) then
    return jsonb_build_object('status', 'invalid_option');
  end if;

  insert into sharek_votes (campaign_id, student_no, option_idx)
  values (c.id, s.academic_no, p_option_idx)
  on conflict (campaign_id, student_no) do nothing;

  if not found then
    return jsonb_build_object('status', 'already_voted');
  end if;

  return jsonb_build_object('status', 'ok', 'option_idx', p_option_idx);
end $$;

-- إضافة عنصر مشاركة في حملة استقبال مشاركات متعددة (قبل الإنهاء)
create or replace function add_sharek_item(p_tg bigint, p_campaign bigint, p_item jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  s students%rowtype;
  c sharek_campaigns%rowtype;
  v_count int;
  v_kind text := coalesce(p_item->>'item_kind', 'text');
  v_text text := nullif(trim(coalesce(p_item->>'text_content', '')), '');
  v_fid  text := nullif(p_item->>'file_id', '');
  v_name text := nullif(p_item->>'file_name', '');
begin
  perform require_perm(p_tg, 'sharek.participate');
  -- قفل صف الطالب لمنع التوازي عند النقر المزدوج أو إعادة المحاولة الآلية
  select * into s from students where telegram_id = p_tg and deleted_at is null for update;
  if not found then return jsonb_build_object('status', 'not_student'); end if;

  select * into c from sharek_campaigns where id = p_campaign and status = 'open';
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if c.kind <> 'submission' then return jsonb_build_object('status', 'not_submission'); end if;
  if c.theory_group_id is not null and c.theory_group_id <> s.theory_group_id and not is_developer(p_tg) then
    return jsonb_build_object('status', 'not_target');
  end if;
  if exists (select 1 from sharek_sessions where campaign_id = c.id and student_no = s.academic_no) then
    return jsonb_build_object('status', 'already_finished');
  end if;

  select count(*) into v_count from sharek_items where campaign_id = c.id and student_no = s.academic_no;

  -- منع تكرار نفس النص أو نفس الملف بالضبط عند إرساله مرتين بالخطأ، مع السماح بإرسال نصوص أو ملفات أخرى مختلفة
  if exists (
    select 1 from sharek_items
     where campaign_id = c.id and student_no = s.academic_no
       and item_kind = v_kind
       and coalesce(text_content, '') = coalesce(v_text, '')
       and coalesce(tg_file_id, '') = coalesce(v_fid, '')
  ) then
    return jsonb_build_object('status', 'ok', 'count', v_count, 'deduped', true);
  end if;

  if v_count >= 20 then
    return jsonb_build_object('status', 'too_many');
  end if;

  insert into sharek_items (campaign_id, student_no, item_kind, text_content, tg_file_id, file_name)
  values (c.id, s.academic_no, v_kind, v_text, v_fid, v_name);

  return jsonb_build_object('status', 'ok', 'count', v_count + 1);
end $$;

create or replace function finish_sharek_submission(p_tg bigint, p_campaign bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  s students%rowtype;
  c sharek_campaigns%rowtype;
  v_count int;
begin
  perform require_perm(p_tg, 'sharek.participate');
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_student'); end if;

  select * into c from sharek_campaigns where id = p_campaign;
  if not found then return jsonb_build_object('status', 'not_found'); end if;

  select count(*) into v_count from sharek_items where campaign_id = c.id and student_no = s.academic_no;
  if v_count = 0 then
    return jsonb_build_object('status', 'no_items');
  end if;

  insert into sharek_sessions (campaign_id, student_no)
  values (c.id, s.academic_no)
  on conflict (campaign_id, student_no) do nothing;

  return jsonb_build_object('status', 'ok', 'count', v_count);
end $$;

create or replace function staff_sharek_campaigns(p_actor bigint) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(to_jsonb(r) order by r.id desc), '[]'::jsonb) from (
    select
      c.id, c.kind, coalesce(c.theory_group_id, 'الجميع') as grp, c.theory_group_id,
      c.title, c.options, c.status, c.created_at,
      case when c.kind = 'poll'
           then (
             select count(*) from sharek_votes v join students sx on sx.academic_no = v.student_no
              where v.campaign_id = c.id
                and has_perm(p_actor, 'sharek.publish', sx.theory_group_id)
           )::int
           else (
             select count(distinct i.student_no) from sharek_items i join students sx on sx.academic_no = i.student_no
              where i.campaign_id = c.id
                and has_perm(p_actor, 'sharek.publish', sx.theory_group_id)
           )::int
      end as participants
    from sharek_campaigns c
    where has_perm(p_actor, 'sharek.publish', c.theory_group_id)
    order by c.id desc limit 60
  ) r;
$$;

-- الحذف يُخفي الحملة عن الطلاب وتبقى نتائجها محفوظة
create or replace function hide_sharek_campaign(p_actor bigint, p_campaign bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare c sharek_campaigns%rowtype;
begin
  select * into c from sharek_campaigns where id = p_campaign;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if c.theory_group_id is null then
    if perm_scope(p_actor, 'sharek.publish') <> 'any' then
      raise exception 'forbidden' using errcode = '42501';
    end if;
  else
    perform require_perm(p_actor, 'sharek.publish', c.theory_group_id);
  end if;
  update sharek_campaigns set status = 'hidden', hidden_at = now(), hidden_by = p_actor where id = p_campaign;
  perform bump_scope('sharek');
  perform log_activity(p_actor, 'sharek.hide', 'sharek_campaign', p_campaign::text, null);
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function sharek_campaign_results(
  p_actor bigint,
  p_campaign bigint,
  p_group text default null
) returns jsonb language plpgsql stable security definer set search_path = public as $$
declare
  c sharek_campaigns%rowtype;
  v_req_grp text := case when p_group is null or upper(trim(p_group)) in ('', 'ALL', 'الجميع') then null else upper(trim(p_group)) end;
  v_grp text;
begin
  select * into c from sharek_campaigns where id = p_campaign;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  v_grp := coalesce(c.theory_group_id, v_req_grp);
  if v_grp is null and perm_scope(p_actor, 'sharek.publish') = 'group' then
    select gr.theory_group_id into v_grp
      from role_grants gr join role_permissions rp on rp.role = gr.role
     where gr.telegram_id = p_actor and gr.revoked_at is null
       and rp.permission = 'sharek.publish' and gr.theory_group_id is not null
     order by gr.id limit 1;
  end if;
  if v_grp is null then
    if perm_scope(p_actor, 'sharek.publish') <> 'any' then
      raise exception 'forbidden' using errcode = '42501';
    end if;
  else
    perform require_perm(p_actor, 'sharek.publish', v_grp);
  end if;

  return jsonb_build_object(
    'status', 'ok',
    'campaign', jsonb_build_object(
      'id', c.id, 'kind', c.kind, 'group', coalesce(v_grp, 'ALL'),
      'theory_group_id', c.theory_group_id, 'title', c.title,
      'options', c.options, 'status', c.status, 'created_at', c.created_at
    ),
    'tallies', case when c.kind = 'poll' then (
      select coalesce(jsonb_agg(
        jsonb_build_object(
          'idx', ord - 1,
          'label', opt,
          'votes', (
            select count(*) from sharek_votes vx join students sx on sx.academic_no = vx.student_no
             where vx.campaign_id = c.id and vx.option_idx = (ord - 1)
               and (v_grp is null or sx.theory_group_id = v_grp)
          )::int
        ) order by ord
      ), '[]'::jsonb)
      from jsonb_array_elements_text(c.options) with ordinality t(opt, ord)
    ) else '[]'::jsonb end,
    'votes', case when c.kind = 'poll' then coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'no', st.academic_no,
          'name', st.full_name,
          'theory', st.theory_group_id,
          'practical', st.practical_group_id,
          'option_idx', v.option_idx,
          'option_label', case when v.option_idx is not null then (c.options->>v.option_idx) end,
          'voted_at', v.voted_at
        ) order by st.theory_group_id, st.full_name
      )
      from students st
      left join sharek_votes v on v.campaign_id = c.id and v.student_no = st.academic_no
      where st.deleted_at is null and (v_grp is null or st.theory_group_id = v_grp)
    ), '[]'::jsonb) else '[]'::jsonb end,
    'submissions', case when c.kind = 'submission' then coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'id', i.id,
          'no', st.academic_no,
          'name', st.full_name,
          'theory', st.theory_group_id,
          'practical', st.practical_group_id,
          'item_kind', i.item_kind,
          'text_content', i.text_content,
          'file_id', i.tg_file_id,
          'file_name', i.file_name,
          'created_at', i.created_at,
          'finished', exists (select 1 from sharek_sessions ss where ss.campaign_id = c.id and ss.student_no = st.academic_no)
        ) order by i.id asc
      )
      from sharek_items i
      join students st on st.academic_no = i.student_no
      where i.campaign_id = c.id and (v_grp is null or c.theory_group_id is not null or st.theory_group_id = v_grp)
    ), '[]'::jsonb) else '[]'::jsonb end
  );
end $$;


-- =====================================================================
-- 3) الاختبارات MCQ (بلا دفتر أخطاء، مع زر العدّاد ومتاحة للضيف)
-- =====================================================================
create table if not exists mcq_exams (
  id               bigint generated always as identity primary key,
  subject_id       smallint not null references subjects(id),
  title            text     not null,
  description      text,
  duration_minutes smallint not null default 15 check (duration_minutes between 1 and 180),
  questions_count  smallint not null default 0,
  created_by       bigint   not null,
  created_at       timestamptz not null default now(),
  deleted_at       timestamptz,
  deleted_by       bigint
);
create index if not exists mcq_exams_subj_idx
  on mcq_exams (subject_id, id desc) where deleted_at is null;

create table if not exists mcq_questions (
  id            bigint generated always as identity primary key,
  exam_id       bigint   not null references mcq_exams(id) on delete cascade,
  position      smallint not null,
  question      text     not null,
  options       jsonb    not null,
  correct_index smallint not null check (correct_index between 0 and 3)
);
create index if not exists mcq_questions_exam_idx on mcq_questions (exam_id, position);

create table if not exists mcq_attempts (
  id              bigint generated always as identity primary key,
  exam_id         bigint   not null references mcq_exams(id) on delete cascade,
  telegram_id     bigint   not null,
  score           smallint not null,
  total           smallint not null,
  elapsed_seconds int      not null default 0,
  timer_enabled   boolean  not null default true,
  created_at      timestamptz not null default now()
);
create index if not exists mcq_attempts_user_idx on mcq_attempts (telegram_id, exam_id, id desc);

create or replace function create_mcq_exam(
  p_actor bigint,
  p_subject int,
  p_title text,
  p_description text,
  p_duration int,
  p_questions jsonb
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_id bigint;
  v_title text := trim(coalesce(p_title, ''));
  v_count int;
begin
  perform require_perm(p_actor, 'mcq.upload');
  if not exists (select 1 from subjects where id = p_subject and is_active and deleted_at is null) then
    return jsonb_build_object('status', 'invalid_subject');
  end if;
  if char_length(v_title) < 2 or char_length(v_title) > 160 then
    return jsonb_build_object('status', 'invalid_title');
  end if;
  if jsonb_typeof(p_questions) <> 'array' or jsonb_array_length(p_questions) < 1 or jsonb_array_length(p_questions) > 500 then
    return jsonb_build_object('status', 'invalid_questions');
  end if;
  v_count := jsonb_array_length(p_questions);

  insert into mcq_exams (subject_id, title, description, duration_minutes, questions_count, created_by)
  values (p_subject, v_title, nullif(trim(coalesce(p_description, '')), ''), greatest(1, least(coalesce(p_duration, 15), 180)), v_count, p_actor)
  returning id into v_id;

  insert into mcq_questions (exam_id, position, question, options, correct_index)
  select
    v_id,
    ord::smallint,
    q->>'question',
    q->'options',
    (q->>'correct_index')::smallint
  from jsonb_array_elements(p_questions) with ordinality t(q, ord);

  perform bump_scope('mcq');
  perform log_activity(p_actor, 'mcq.upload', 'mcq_exam', v_id::text, jsonb_build_object('subject', p_subject, 'questions', v_count));
  return jsonb_build_object('status', 'ok', 'id', v_id, 'questions_count', v_count);
end $$;

create or replace function delete_mcq_exam(p_actor bigint, p_exam bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform require_perm(p_actor, 'mcq.upload');
  update mcq_exams set deleted_at = now(), deleted_by = p_actor where id = p_exam and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform bump_scope('mcq');
  perform log_activity(p_actor, 'mcq.delete', 'mcq_exam', p_exam::text, null);
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function list_mcq_exams(p_tg bigint, p_subject int default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_can_manage boolean;
begin
  perform require_perm(p_tg, 'mcq.take');
  v_can_manage := has_perm(p_tg, 'mcq.upload');
  return coalesce((
    select jsonb_agg(to_jsonb(r) order by r.id desc) from (
      select
        e.id, e.subject_id, sj.name_ar as subject, e.title, e.description,
        e.duration_minutes, e.questions_count, e.created_at,
        v_can_manage as can_delete,
        (
          select jsonb_build_object('score', a.score, 'total', a.total, 'elapsed_seconds', a.elapsed_seconds, 'at', a.created_at)
            from mcq_attempts a where a.exam_id = e.id and a.telegram_id = p_tg
           order by a.id desc limit 1
        ) as last_attempt
      from mcq_exams e
      join subjects sj on sj.id = e.subject_id
      where e.deleted_at is null
        and (p_subject is null or e.subject_id = p_subject)
      order by e.id desc limit 80
    ) r
  ), '[]'::jsonb);
end $$;

create or replace function get_mcq_exam(p_tg bigint, p_exam bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare e mcq_exams%rowtype;
begin
  perform require_perm(p_tg, 'mcq.take');
  select * into e from mcq_exams where id = p_exam and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;

  return jsonb_build_object(
    'status', 'ok',
    'exam', jsonb_build_object(
      'id', e.id,
      'subject_id', e.subject_id,
      'subject', (select name_ar from subjects where id = e.subject_id),
      'title', e.title,
      'description', e.description,
      'duration_minutes', e.duration_minutes,
      'questions_count', e.questions_count
    ),
    'questions', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'id', q.id,
          'position', q.position,
          'question', q.question,
          'options', q.options,
          'correct_index', q.correct_index
        ) order by q.position
      )
      from mcq_questions q where q.exam_id = e.id
    ), '[]'::jsonb)
  );
end $$;

create or replace function submit_mcq_attempt(
  p_tg bigint,
  p_exam bigint,
  p_score int,
  p_total int,
  p_elapsed int,
  p_timer_enabled boolean
) returns jsonb language plpgsql security definer set search_path = public as $$
declare e mcq_exams%rowtype;
begin
  perform require_perm(p_tg, 'mcq.take');
  select * into e from mcq_exams where id = p_exam and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;

  insert into mcq_attempts (exam_id, telegram_id, score, total, elapsed_seconds, timer_enabled)
  values (
    e.id, p_tg,
    greatest(0, least(p_score, e.questions_count)),
    greatest(1, e.questions_count),
    greatest(0, coalesce(p_elapsed, 0)),
    coalesce(p_timer_enabled, true)
  );
  return jsonb_build_object('status', 'ok');
end $$;


-- =====================================================================
-- 4) شاشة الدكتور (يرى واجبات مجموعاته فقط ويصدّر Excel 1/0 لكل مجموعة منفردة)
-- =====================================================================
create or replace function doctor_dashboard(p_tg bigint, p_doctor_id int default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_doc doctors%rowtype;
begin
  perform require_perm(p_tg, 'doctor.assignments');
  if is_developer(p_tg) and p_doctor_id is not null then
    select * into v_doc from doctors where id = p_doctor_id and deleted_at is null;
  else
    select * into v_doc from doctors where telegram_id = p_tg and is_active and deleted_at is null;
    if not found and is_developer(p_tg) then
      select * into v_doc from doctors where is_active and deleted_at is null order by id limit 1;
    end if;
  end if;

  return jsonb_build_object(
    'status', 'ok',
    'doctor', case when v_doc.id is not null then jsonb_build_object('id', v_doc.id, 'name', v_doc.name) end,
    'doctors', case when is_developer(p_tg) then coalesce((
      select jsonb_agg(jsonb_build_object('id', id, 'name', name) order by id) from doctors where is_active and deleted_at is null
    ), '[]'::jsonb) else '[]'::jsonb end,
    'assignments', coalesce((
      select jsonb_agg(to_jsonb(r) order by r.id desc) from (
        select
          a.id, a.number, sj.name_ar as subject, a.part,
          coalesce(a.theory_group_id, a.practical_group_id) as grp,
          a.due_at, a.status,
          (select count(*) from submissions sb where sb.assignment_id = a.id and sb.submitted_at is not null)::int as submitted,
          (select count(*) from students st where st.deleted_at is null and targets_student(a, st))::int as total
        from assignments a
        join subjects sj on sj.id = a.subject_id
        where a.deleted_at is null
          and (
            (v_doc.id is not null and a.doctor_id = v_doc.id)
            or (v_doc.id is null and is_developer(p_tg))
          )
        order by a.id desc limit 100
      ) r
    ), '[]'::jsonb)
  );
end $$;

create or replace function doctor_assignment_export(p_tg bigint, p_assignment bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  a assignments%rowtype;
  v_doc doctors%rowtype;
begin
  perform require_perm(p_tg, 'doctor.assignments');
  select * into a from assignments where id = p_assignment and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;

  if not is_developer(p_tg) then
    select * into v_doc from doctors where telegram_id = p_tg and is_active and deleted_at is null;
    if not found or a.doctor_id is distinct from v_doc.id then
      raise exception 'forbidden' using errcode = '42501';
    end if;
  end if;

  return jsonb_build_object(
    'status', 'ok',
    'assignment', jsonb_build_object(
      'id', a.id,
      'number', a.number,
      'subject', (select name_ar from subjects where id = a.subject_id),
      'part', a.part,
      'group', coalesce(a.theory_group_id, a.practical_group_id),
      'due_at', a.due_at
    ),
    'students', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'academic_no', st.academic_no,
          'full_name', st.full_name,
          'theory_group_id', st.theory_group_id,
          'practical_group_id', st.practical_group_id,
          'submitted', case when sb.submitted_at is not null then 1 else 0 end,
          'is_late', coalesce(sb.is_late, false),
          'received', sb.received_at is not null,
          'submitted_at', sb.submitted_at
        ) order by st.academic_no
      )
      from students st
      left join submissions sb on sb.assignment_id = a.id and sb.student_no = st.academic_no
      where st.deleted_at is null and targets_student(a, st)
    ), '[]'::jsonb)
  );
end $$;


-- =====================================================================
-- 5) الإشعارات اليدوية (تتجاوز الكتم) ولوحة المطوّر الشاملة
-- =====================================================================
create or replace function send_manual_notification(
  p_actor bigint,
  p_group text,
  p_title text,
  p_body text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_title text := nullif(trim(coalesce(p_title, '')), '');
  v_body  text := trim(coalesce(p_body, ''));
  v_payload jsonb;
  v_sent int := 0;
  r record;
begin
  if p_group is null then
    if perm_scope(p_actor, 'notify.send') <> 'any' then
      raise exception 'forbidden' using errcode = '42501';
    end if;
  else
    if not exists (select 1 from theory_groups where id = p_group) then
      return jsonb_build_object('status', 'invalid_group');
    end if;
    perform require_perm(p_actor, 'notify.send', p_group);
  end if;

  if char_length(v_body) < 2 or char_length(v_body) > 1500 then
    return jsonb_build_object('status', 'invalid_body');
  end if;

  v_payload := jsonb_build_object('title', v_title, 'body', v_body, 'group', coalesce(p_group, 'الجميع'));

  -- الإعلان اليدوي يتجاوز الكتم دائمًا (p_push = true)
  for r in
    select s.telegram_id as tg
      from students s
     where s.deleted_at is null and s.telegram_id is not null
       and (p_group is null or s.theory_group_id = p_group)
  loop
    perform notify_user(r.tg, 'manual', v_payload, true, null);
    v_sent := v_sent + 1;
  end loop;

  perform log_activity(p_actor, 'notify.manual', 'notification', coalesce(p_group, 'ALL'), jsonb_build_object('recipients', v_sent));
  return jsonb_build_object('status', 'ok', 'recipients', v_sent);
end $$;

-- نظرة عامة وصحة النظام للوحة المطوّر
create or replace function dev_overview(p_actor bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  return jsonb_build_object(
    'status', 'ok',
    'counts', jsonb_build_object(
      'students_total', (select count(*) from students where deleted_at is null),
      'students_linked', (select count(*) from students where deleted_at is null and telegram_id is not null),
      'guests', (select count(*) from users where kind = 'guest' and not is_banned),
      'banned', (select count(*) from users where is_banned),
      'open_tickets', (select count(*) from support_tickets where status = 'open'),
      'trash_files', (select count(*) from files where deleted_at is not null),
      'trash_assignments', (select count(*) from assignments where deleted_at is not null)
    ),
    'health', jsonb_build_object(
      'pending', (select count(*) from outbox where status = 'pending'),
      'sending', (select count(*) from outbox where status = 'sending'),
      'sent_24h', (select count(*) from outbox where status = 'sent' and sent_at > now() - interval '24 hours'),
      'failed', (select count(*) from outbox where status = 'failed'),
      'blocked', (select count(*) from outbox where status = 'blocked'),
      'recent_failures', coalesce((
        select jsonb_agg(jsonb_build_object('id', id, 'kind', kind, 'chat_id', chat_id, 'attempts', attempts, 'error', last_error, 'at', created_at) order by id desc)
          from (select * from outbox where status = 'failed' order by id desc limit 15) f
      ), '[]'::jsonb)
    )
  );
end $$;

create or replace function dev_retry_failed_outbox(p_actor bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  perform require_developer(p_actor);
  with u as (
    update outbox set status = 'pending', attempts = 0, next_attempt_at = now(), last_error = null
     where status = 'failed' returning 1
  )
  select count(*) into n from u;
  perform log_activity(p_actor, 'outbox.retry_failed', 'outbox', null, jsonb_build_object('count', n));
  return jsonb_build_object('status', 'ok', 'retried', n);
end $$;

create or replace function dev_list_students(
  p_actor bigint,
  p_group text default null,
  p_q text default null,
  p_limit int default 120
) returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_q text := trim(coalesce(p_q, ''));
begin
  perform require_developer(p_actor);
  return coalesce((
    select jsonb_agg(to_jsonb(r) order by r.theory_group_id, r.academic_no) from (
      select s.academic_no, s.full_name, s.theory_group_id, s.practical_group_id,
             s.telegram_id, s.linked_at, u.username
        from students s
        left join users u on u.telegram_id = s.telegram_id
       where s.deleted_at is null
         and (p_group is null or s.theory_group_id = p_group)
         and (v_q = '' or s.academic_no ilike '%' || v_q || '%' or s.full_name ilike '%' || v_q || '%' or coalesce(s.telegram_id::text, '') = v_q)
       order by s.theory_group_id, s.academic_no
       limit least(p_limit, 300)
    ) r
  ), '[]'::jsonb);
end $$;

create or replace function dev_upsert_student(
  p_actor bigint,
  p_no text,
  p_name text,
  p_theory text,
  p_practical text
) returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  if not exists (select 1 from practical_groups where code = p_practical and theory_group_id = p_theory) then
    return jsonb_build_object('status', 'invalid_group');
  end if;
  insert into students (academic_no, full_name, theory_group_id, practical_group_id)
  values (trim(p_no), trim(p_name), p_theory, p_practical)
  on conflict (academic_no) do update
     set full_name = excluded.full_name,
         theory_group_id = excluded.theory_group_id,
         practical_group_id = excluded.practical_group_id,
         deleted_at = null;
  perform bump_scope('roles');
  perform log_activity(p_actor, 'student.upsert', 'student', trim(p_no), jsonb_build_object('theory', p_theory, 'practical', p_practical));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function dev_list_guests(p_actor bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  return coalesce((
    select jsonb_agg(to_jsonb(r) order by r.is_banned desc, r.last_seen_at desc) from (
      select telegram_id, kind, display_name, username, is_banned, banned_at, ban_reason, created_at, last_seen_at
        from users
       where kind = 'guest' or is_banned
       order by is_banned desc, last_seen_at desc limit 150
    ) r
  ), '[]'::jsonb);
end $$;

create or replace function dev_catalog_and_roles(p_actor bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  return jsonb_build_object(
    'status', 'ok',
    'roles', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', rg.id,
        'telegram_id', rg.telegram_id,
        'role', rg.role,
        'group', rg.theory_group_id,
        'granted_at', rg.granted_at,
        'name', coalesce(st.full_name, u.display_name, 'مستخدم'),
        'academic_no', st.academic_no
      ) order by rg.role, rg.theory_group_id, rg.id)
      from role_grants rg
      left join students st on st.telegram_id = rg.telegram_id and st.deleted_at is null
      left join users u on u.telegram_id = rg.telegram_id
      where rg.revoked_at is null
    ), '[]'::jsonb),
    'subjects', coalesce((
      select jsonb_agg(jsonb_build_object('id', id, 'name_ar', name_ar, 'hashtag', hashtag, 'sort', sort, 'is_active', is_active) order by sort, id)
        from subjects where deleted_at is null
    ), '[]'::jsonb),
    'doctors', coalesce((
      select jsonb_agg(jsonb_build_object('id', id, 'name', name, 'telegram_id', telegram_id, 'is_active', is_active) order by id)
        from doctors where deleted_at is null
    ), '[]'::jsonb),
    'teaching', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', t.id, 'subject_id', t.subject_id, 'subject', sj.name_ar,
        'part', t.part, 'group', coalesce(t.theory_group_id, t.practical_group_id),
        'doctor_id', t.doctor_id, 'doctor', d.name
      ) order by t.subject_id, t.part, coalesce(t.theory_group_id, t.practical_group_id))
      from teaching t
      join subjects sj on sj.id = t.subject_id
      join doctors d on d.id = t.doctor_id
    ), '[]'::jsonb),
    'destinations', coalesce((
      select jsonb_agg(jsonb_build_object('key', key, 'chat_id', chat_id, 'topic_id', topic_id, 'label', label, 'updated_at', updated_at) order by key)
        from tg_destinations
    ), '[]'::jsonb)
  );
end $$;

create or replace function dev_upsert_subject(p_actor bigint, p_name text, p_hashtag text, p_sort int default 0) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_id smallint;
begin
  perform require_developer(p_actor);
  insert into subjects (name_ar, hashtag, sort, is_active, deleted_at)
  values (trim(p_name), trim(p_hashtag), coalesce(p_sort, 0), true, null)
  on conflict (name_ar) do update
     set hashtag = excluded.hashtag, sort = excluded.sort, is_active = true, deleted_at = null
  returning id into v_id;
  perform log_activity(p_actor, 'subject.upsert', 'subject', v_id::text, jsonb_build_object('name', p_name));
  return jsonb_build_object('status', 'ok', 'id', v_id);
end $$;

create or replace function dev_upsert_doctor(p_actor bigint, p_id int default null, p_name text default '', p_tg bigint default null) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_id smallint;
begin
  perform require_developer(p_actor);
  if p_id is not null then
    update doctors set name = trim(p_name), telegram_id = p_tg, is_active = true, deleted_at = null
     where id = p_id returning id into v_id;
  else
    insert into doctors (name, telegram_id) values (trim(p_name), p_tg)
    on conflict (telegram_id) do update set name = excluded.name, is_active = true, deleted_at = null
    returning id into v_id;
  end if;
  if p_tg is not null then
    insert into users (telegram_id, kind, display_name) values (p_tg, 'new', trim(p_name))
    on conflict (telegram_id) do update set display_name = excluded.display_name;
  end if;
  perform bump_scope('roles');
  perform log_activity(p_actor, 'doctor.upsert', 'doctor', v_id::text, jsonb_build_object('name', p_name, 'telegram_id', p_tg));
  return jsonb_build_object('status', 'ok', 'id', v_id);
end $$;

create or replace function dev_upsert_teaching(p_actor bigint, p_subject int, p_part text, p_group text, p_doctor int) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  if p_part = 'theory' then
    delete from teaching where subject_id = p_subject and part = 'theory' and theory_group_id = p_group;
    insert into teaching (subject_id, part, theory_group_id, doctor_id) values (p_subject, 'theory', p_group, p_doctor);
  elsif p_part = 'practical' then
    delete from teaching where subject_id = p_subject and part = 'practical' and practical_group_id = p_group;
    insert into teaching (subject_id, part, practical_group_id, doctor_id) values (p_subject, 'practical', p_group, p_doctor);
  else
    return jsonb_build_object('status', 'invalid_part');
  end if;
  perform log_activity(p_actor, 'teaching.upsert', 'teaching', p_subject || ':' || p_part || ':' || p_group, jsonb_build_object('doctor_id', p_doctor));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function dev_delete_teaching(p_actor bigint, p_id int) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  delete from teaching where id = p_id;
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function dev_upsert_destination(p_actor bigint, p_key text, p_chat bigint, p_topic int default null, p_label text default null) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  insert into tg_destinations (key, chat_id, topic_id, label, updated_at)
  values (trim(p_key), p_chat, p_topic, nullif(trim(coalesce(p_label, '')), ''), now())
  on conflict (key) do update
     set chat_id = excluded.chat_id, topic_id = excluded.topic_id, label = coalesce(excluded.label, tg_destinations.label), updated_at = now();
  perform log_activity(p_actor, 'destination.upsert', 'tg_destination', trim(p_key), jsonb_build_object('chat_id', p_chat, 'topic_id', p_topic));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function dev_delete_destination(p_actor bigint, p_key text) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  delete from tg_destinations where key = p_key;
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function dev_list_trash(p_actor bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  return jsonb_build_object(
    'status', 'ok',
    'files', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', f.id, 'subject', sj.name_ar, 'part', f.part, 'content_type', f.content_type,
        'exam_kind', f.exam_kind, 'label_kind', f.label_kind, 'numbers', f.numbers,
        'deleted_at', f.deleted_at
      ) order by f.deleted_at desc)
      from files f join subjects sj on sj.id = f.subject_id
      where f.deleted_at is not null
    ), '[]'::jsonb),
    'assignments', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', a.id, 'number', a.number, 'subject', sj.name_ar, 'part', a.part,
        'group', coalesce(a.theory_group_id, a.practical_group_id), 'deleted_at', a.deleted_at
      ) order by a.deleted_at desc)
      from assignments a join subjects sj on sj.id = a.subject_id
      where a.deleted_at is not null
    ), '[]'::jsonb)
  );
end $$;

create or replace function dev_list_activity(p_actor bigint, p_limit int default 60) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  return coalesce((
    select jsonb_agg(to_jsonb(r) order by r.id desc) from (
      select a.id, a.at, a.actor_id, coalesce(s.full_name, u.display_name, a.actor_id::text) as actor_name,
             a.action, a.entity, a.entity_id, a.meta
        from activity_log a
        left join students s on s.telegram_id = a.actor_id and s.deleted_at is null
        left join users u on u.telegram_id = a.actor_id
       order by a.id desc limit least(p_limit, 150)
    ) r
  ), '[]'::jsonb);
end $$;


-- =====================================================================
-- 6) تحديث المهام الدورية (تذكير المساء بالتقرير اليومي + التقرير الأسبوعي يوم الخميس)
-- =====================================================================
create or replace function run_due_work() returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_closed int;
  v_rem int := 0;
  v_grv_closed int := 0;
  v_rep_rem int := 0;
  r record;
  s record;
  gc record;
  grp record;
  cm record;
  v_a assignments%rowtype;
  v_new boolean;
  v_payload jsonb;
  v_pkg jsonb;
  v_now_sanaa timestamp := (now() at time zone 'Asia/Aden');
  v_today date := v_now_sanaa::date;
  v_dow int := extract(dow from v_today)::int; -- 0=الأحد..4=الخميس، 5=الجمعة، 6=السبت
begin
  -- 1) إغلاق الواجبات المنتهية
  with c as (update assignments set status = 'closed', closed_at = now()
              where status = 'open' and deleted_at is null and closes_at <= now() returning 1)
  select count(*) into v_closed from c;

  -- 2) تذكيرات الواجبات (24/3/1 ساعة) لمن لم يسلّم فقط
  for r in
    select a.id, min(v.h) as h
      from assignments a join (values (24), (3), (1)) v(h) on now() >= a.due_at - make_interval(hours => v.h)
     where a.status = 'open' and a.deleted_at is null and now() < a.due_at
     group by a.id
  loop
    with ins as (
      insert into reminders_sent (assignment_id, hours) select r.id, x from unnest(array[24, 3, 1]) x where x >= r.h
      on conflict do nothing returning hours)
    select coalesce(bool_or(hours = r.h), false) into v_new from ins;
    if not v_new then continue; end if;

    select * into v_a from assignments where id = r.id;
    v_payload := jsonb_build_object('assignment_id', v_a.id, 'number', v_a.number,
                   'subject', (select name_ar from subjects where id = v_a.subject_id), 'part', v_a.part,
                   'group', coalesce(v_a.theory_group_id, v_a.practical_group_id), 'due_at', v_a.due_at, 'hours', r.h);
    for s in
      select st.telegram_id as tg
        from students st left join user_settings us on us.telegram_id = st.telegram_id
       where st.deleted_at is null and st.telegram_id is not null and targets_student(v_a, st)
         and not exists (select 1 from submissions sb where sb.assignment_id = v_a.id and sb.student_no = st.academic_no and sb.submitted_at is not null)
         and case r.h when 24 then coalesce(us.remind_24, true) when 3 then coalesce(us.remind_3, true) else coalesce(us.remind_1, false) end
    loop
      perform notify_user(s.tg, 'assignment_reminder', v_payload, true, 'asg_rem:' || v_a.id || ':' || r.h || ':' || s.tg);
      v_rem := v_rem + 1;
    end loop;
  end loop;

  -- 3) إغلاق دورات التظلمات المنتهية آليًا وإرسال التجميع للمندوب/المطوّر
  for gc in
    update grievance_cycles
       set status = 'closed', closed_at = now(), summary_sent_at = now()
     where status = 'open' and closes_at <= now()
     returning id, created_by
  loop
    v_pkg := grievance_cycle_package(gc.id);
    perform notify_user(gc.created_by, 'grievance_compiled', v_pkg, true, 'grv_comp:' || gc.id || ':' || gc.created_by);
    v_grv_closed := v_grv_closed + 1;
  end loop;
  if v_grv_closed > 0 then
    perform bump_scope('grievances');
  end if;

  -- 4) تذكير مسائي (الساعة 8 مساءً بتوقيت صنعاء في أيام الدراسة السبت–الخميس) لأعضاء اللجنة للمجموعة التي لم يُكتب تقريرها اليومي
  if v_dow <> 5 and extract(hour from v_now_sanaa)::int >= 20 then
    for grp in
      select tg.id as g
        from theory_groups tg
       where not exists (select 1 from daily_reports dr where dr.theory_group_id = tg.id and dr.report_date = v_today)
         and not exists (select 1 from report_reminders_sent rr where rr.theory_group_id = tg.id and rr.report_date = v_today)
    loop
      insert into report_reminders_sent (theory_group_id, report_date) values (grp.g, v_today) on conflict do nothing;
      for cm in
        select distinct telegram_id as tg from role_grants where role = 'committee' and theory_group_id = grp.g and revoked_at is null
      loop
        perform notify_user(
          cm.tg,
          'report_reminder',
          jsonb_build_object('group', grp.g, 'date', v_today),
          true,
          'rep_rem:' || grp.g || ':' || v_today || ':' || cm.tg
        );
        v_rep_rem := v_rep_rem + 1;
      end loop;
    end loop;
  end if;

  return jsonb_build_object(
    'closed', v_closed,
    'reminders', v_rem,
    'grievances_closed', v_grv_closed,
    'report_reminders', v_rep_rem
  );
end $$;


-- ---------- الأمان لكل ما أُضيف ----------
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke execute on all functions in schema public from public, anon, authenticated;
grant  execute on all functions in schema public to service_role;

-- =====================================================================
-- 7) تحديث إصدارات الكاش تلقائيًا عند رفع أو تعديل أو حذف الملفات والواجبات
-- =====================================================================
create or replace function trg_bump_files_fn() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform bump_scope('files');
  return null;
end $$;

drop trigger if exists trg_bump_files on files;
create trigger trg_bump_files
after insert or update or delete on files
for each statement execute function trg_bump_files_fn();

create or replace function trg_bump_assignments_fn() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform bump_scope('assignments');
  return null;
end $$;

drop trigger if exists trg_bump_assignments on assignments;
create trigger trg_bump_assignments
after insert or update or delete on assignments
for each statement execute function trg_bump_assignments_fn();


-- =====================================================================
-- 8) ضمان وصول تسليمات الواجبات لمجموعة الدكتور دائمًا (حتى لو رُبط الدكتور بعد إنشاء الواجب أو رُبط بمجموعة عامة)
-- =====================================================================
create or replace function dev_upsert_teaching(p_actor bigint, p_subject int, p_part text, p_group text, p_doctor int) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  if p_part = 'theory' then
    delete from teaching where subject_id = p_subject and part = 'theory' and theory_group_id = p_group;
    insert into teaching (subject_id, part, theory_group_id, doctor_id) values (p_subject, 'theory', p_group, p_doctor);
    -- تحديث الواجبات الحالية لنفس المادة والمجموعة لتأخذ معرّف الدكتور فورًا
    update assignments set doctor_id = p_doctor
     where subject_id = p_subject and part = 'theory' and theory_group_id = p_group;
  elsif p_part = 'practical' then
    delete from teaching where subject_id = p_subject and part = 'practical' and practical_group_id = p_group;
    insert into teaching (subject_id, part, practical_group_id, doctor_id) values (p_subject, 'practical', p_group, p_doctor);
    update assignments set doctor_id = p_doctor
     where subject_id = p_subject and part = 'practical' and practical_group_id = p_group;
  else
    return jsonb_build_object('status', 'invalid_part');
  end if;
  perform bump_scope('subjects');
  perform log_activity(p_actor, 'teaching.upsert', 'teaching', p_subject || ':' || p_part || ':' || p_group, jsonb_build_object('doctor_id', p_doctor));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function dev_upsert_destination(p_actor bigint, p_key text, p_chat bigint, p_topic int default null, p_label text default null) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  insert into tg_destinations (key, chat_id, topic_id, label, updated_at)
  values (trim(p_key), p_chat, p_topic, nullif(trim(coalesce(p_label, '')), ''), now())
  on conflict (key) do update
     set chat_id = excluded.chat_id, topic_id = excluded.topic_id, label = coalesce(excluded.label, tg_destinations.label), updated_at = now();
  perform bump_scope('destinations');
  perform log_activity(p_actor, 'destination.upsert', 'tg_destination', trim(p_key), jsonb_build_object('chat_id', p_chat, 'topic_id', p_topic));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function submission_package(p_sub bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  sb submissions%rowtype;
  a  assignments%rowtype;
  st students%rowtype;
  v_grp text;
  v_theory text;
  v_doc smallint;
  v_dest tg_destinations%rowtype;
  v_key text;
begin
  select * into sb from submissions where id = p_sub;
  select * into a from assignments where id = sb.assignment_id;
  select * into st from students where academic_no = sb.student_no;
  v_grp := coalesce(a.theory_group_id, a.practical_group_id);
  v_theory := theory_of(v_grp);

  -- 1) تحديد الدكتور: من الواجب أولًا، أو من جدول التدريس (نفس القسم والمجموعة، ثم نظري المجموعة، ثم أي ربط للمادة)
  v_doc := a.doctor_id;
  if v_doc is null then
    select doctor_id into v_doc from teaching
     where subject_id = a.subject_id and part = a.part and coalesce(theory_group_id, practical_group_id) = v_grp
     limit 1;
  end if;
  if v_doc is null then
    select doctor_id into v_doc from teaching
     where subject_id = a.subject_id and part = 'theory' and theory_group_id = v_theory
     limit 1;
  end if;
  if v_doc is null then
    select doctor_id into v_doc from teaching
     where subject_id = a.subject_id
     order by id limit 1;
  end if;

  -- 2) البحث عن وجهة مجتمع الدكتور المرنة:
  -- أ) المطابقة التامة (doctor:<id>:<part>:<group>)
  -- ب) مطابقة المجموعة النظرية الأم لنفس الدكتور (doctor:<id>:theory:<CS2_G*>)
  -- ج) أي وجهة مسجّلة لهذا الدكتور (doctor:<id>:%، مثل مجموعة دكتور عامة بلا تقسيم)
  -- د) إن لم يُربط جدول التدريس بعد ولكن رُبطت وجهة دكتور وحيدة لهذه المجموعة في tg_destinations
  if v_doc is not null then
    select * into v_dest from tg_destinations
     where key in (
       'doctor:' || v_doc || ':' || a.part || ':' || v_grp,
       'doctor:' || v_doc || ':theory:' || v_theory
     )
     order by case when key = 'doctor:' || v_doc || ':' || a.part || ':' || v_grp then 0 else 1 end
     limit 1;

    if not found then
      select * into v_dest from tg_destinations
       where key like 'doctor:' || v_doc || ':%'
       order by updated_at desc limit 1;
    end if;
  else
    select * into v_dest from tg_destinations
     where key like 'doctor:%:' || a.part || ':' || v_grp
        or key like 'doctor:%:theory:' || v_theory
     order by updated_at desc limit 1;
  end if;

  v_key := coalesce(
    v_dest.key,
    case when v_doc is not null then 'doctor:' || v_doc || ':' || a.part || ':' || v_grp end
  );

  return jsonb_build_object(
    'submission_id', sb.id,
    'is_late', sb.is_late,
    'revision', sb.revision,
    'student', jsonb_build_object(
      'no', st.academic_no, 'name', st.full_name, 'tg', st.telegram_id,
      'theory', st.theory_group_id, 'practical', st.practical_group_id
    ),
    'assignment', jsonb_build_object(
      'id', a.id, 'number', a.number, 'part', a.part, 'grp', v_grp,
      'subject', (select name_ar from subjects where id = a.subject_id)
    ),
    'dest_key', v_key,
    'dest_chat_id', v_dest.chat_id,
    'dest_topic_id', v_dest.topic_id,
    'files', coalesce((
      select jsonb_agg(jsonb_build_object('media_kind', media_kind, 'file_id', tg_file_id, 'name', file_name) order by position)
        from submission_files where submission_id = sb.id and finalized_at is not null and superseded_at is null
    ), '[]'::jsonb),
    'old_deliveries', coalesce((
      select jsonb_agg(jsonb_build_object('chat_id', chat_id, 'message_id', message_id))
        from submission_deliveries where submission_id = sb.id
    ), '[]'::jsonb)
  );
end $$;

-- تحديث الواجبات المنشأة سابقًا التي كانت بدون doctor_id بسبب ربط التدريس لاحقًا
update assignments a
   set doctor_id = coalesce(
     (select t.doctor_id from teaching t
       where t.subject_id = a.subject_id and t.part = a.part
         and coalesce(t.theory_group_id, t.practical_group_id) = coalesce(a.theory_group_id, a.practical_group_id)
       limit 1),
     (select t.doctor_id from teaching t
       where t.subject_id = a.subject_id and t.part = 'theory'
         and t.theory_group_id = theory_of(coalesce(a.theory_group_id, a.practical_group_id))
       limit 1),
     (select t.doctor_id from teaching t
       where t.subject_id = a.subject_id
       order by t.id limit 1)
   )
 where a.doctor_id is null;

-- تقليص مهلة إعادة محاولة التسليمات المعلّقة إلى 5 ثوانٍ ليصل الواجب فور ربط مجموعة الدكتور
create or replace function pending_deliveries(p_limit int default 10) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(id), '[]'::jsonb) from (
    select id from submissions where delivery_state = 'pending' and updated_at < now() - interval '5 seconds'
     order by updated_at limit p_limit) x;
$$;
