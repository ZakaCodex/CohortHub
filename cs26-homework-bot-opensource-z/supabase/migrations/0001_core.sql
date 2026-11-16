-- =====================================================================
-- 0001_core.sql — المرحلة 1: الأساس (مجموعات، أدوار، صلاحيات، ربط، زوار، إصدارات)
-- شغّله مرة واحدة في Supabase → SQL Editor. آمن لإعادة التشغيل (idempotent).
-- كل الأوقات timestamptz (UTC). العرض بتوقيت صنعاء في الواجهة.
-- =====================================================================

-- ---------- الإعدادات والإصدارات ----------
create table if not exists app_settings (
  key        text primary key,
  value      jsonb       not null,
  is_public  boolean     not null default false,   -- يظهر للواجهة؟
  updated_at timestamptz not null default now()
);

create table if not exists data_versions (
  scope      text primary key,
  version    bigint      not null default 1,
  updated_at timestamptz not null default now()
);

create or replace function bump_scope(p_scope text) returns void
language sql security definer set search_path = public as $$
  insert into data_versions(scope, version) values (p_scope, 1)
  on conflict (scope) do update set version = data_versions.version + 1, updated_at = now();
$$;

create or replace function trg_bump() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform bump_scope(tg_argv[0]);
  return null;
end $$;

-- ---------- المجموعات ----------
create table if not exists theory_groups (
  id   text primary key,              -- CS2_G1
  sort smallint not null
);

create table if not exists practical_groups (
  code             text primary key,  -- G1..G9
  theory_group_id  text not null references theory_groups(id),
  sort             smallint not null,
  unique (code, theory_group_id)
);

-- ---------- المواد والدكاترة ----------
create table if not exists subjects (
  id         smallint generated always as identity primary key,
  name_ar    text not null unique,
  hashtag    text not null,            -- بدون #، مثل أمنية_معلومات
  sort       smallint not null default 0,
  is_active  boolean not null default true,
  deleted_at timestamptz
);

create table if not exists doctors (
  id          smallint generated always as identity primary key,
  name        text not null,
  telegram_id bigint unique,
  is_active   boolean not null default true,
  deleted_at  timestamptz
);

-- من يدرّس أي مادة/جزء/مجموعة
create table if not exists teaching (
  id                 integer generated always as identity primary key,
  subject_id         smallint not null references subjects(id),
  part               text not null check (part in ('theory','practical')),
  theory_group_id    text references theory_groups(id),
  practical_group_id text references practical_groups(code),
  doctor_id          smallint not null references doctors(id),
  check ((part = 'theory'    and theory_group_id    is not null and practical_group_id is null)
      or (part = 'practical' and practical_group_id is not null and theory_group_id    is null))
);
create unique index if not exists teaching_theory_uq
  on teaching (subject_id, theory_group_id) where part = 'theory';
create unique index if not exists teaching_practical_uq
  on teaching (subject_id, practical_group_id) where part = 'practical';

-- معرّفات المجتمعات والـ topics (تُضبط من البوت عند الإعداد)
-- مفاتيح: cohort:subject:<id> | cohort:theory:<CS2_G1> | cohort:notices:<CS2_G1>
--         doctor:<id>:theory:<CS2_G1> | doctor:<id>:practical:<G1>
create table if not exists tg_destinations (
  key        text primary key,
  chat_id    bigint not null,
  topic_id   integer,
  label      text,
  updated_at timestamptz not null default now()
);

-- ---------- الطلاب والمستخدمون ----------
create table if not exists students (
  academic_no        text primary key,
  full_name          text not null,
  theory_group_id    text not null references theory_groups(id),
  practical_group_id text not null,
  telegram_id        bigint unique,
  linked_at          timestamptz,
  created_at         timestamptz not null default now(),
  deleted_at         timestamptz,
  foreign key (practical_group_id, theory_group_id)
    references practical_groups (code, theory_group_id)
);
create index if not exists students_theory_idx on students (theory_group_id) where deleted_at is null;

create table if not exists users (
  telegram_id  bigint primary key,
  kind         text not null default 'new' check (kind in ('new','guest','student')),
  display_name text,
  username     text,
  is_banned    boolean not null default false,
  banned_at    timestamptz,
  banned_by    bigint,
  ban_reason   text,
  created_at   timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);
create index if not exists users_guest_idx on users (created_at desc) where kind = 'guest';

-- ---------- الأدوار والصلاحيات (بيانات لا كود) ----------
create table if not exists role_grants (
  id              bigint generated always as identity primary key,
  telegram_id     bigint not null,
  role            text not null check (role in ('deputy','delegate','committee','developer')),
  theory_group_id text references theory_groups(id),
  granted_by      bigint,
  granted_at      timestamptz not null default now(),
  revoked_at      timestamptz,
  revoked_by      bigint,
  check ((role = 'developer' and theory_group_id is null)
      or (role <> 'developer' and theory_group_id is not null))
);
create unique index if not exists role_grants_active_uq
  on role_grants (telegram_id, role, coalesce(theory_group_id, '')) where revoked_at is null;
create index if not exists role_grants_tg_idx on role_grants (telegram_id) where revoked_at is null;

create table if not exists role_permissions (
  role       text not null check (role in ('guest','student','deputy','delegate','committee','doctor','developer')),
  permission text not null,
  scope      text not null check (scope in ('own','group','any')),
  condition  text,                                  -- مثل delegate_absent
  primary key (role, permission)
);

-- ---------- سجل النشاط ----------
create table if not exists activity_log (
  id        bigint generated always as identity primary key,
  at        timestamptz not null default now(),
  actor_id  bigint,
  action    text not null,
  entity    text,
  entity_id text,
  meta      jsonb
);
create index if not exists activity_log_at_idx on activity_log (at desc);

create or replace function log_activity(p_actor bigint, p_action text, p_entity text, p_id text, p_meta jsonb default null)
returns void language sql security definer set search_path = public as $$
  insert into activity_log(actor_id, action, entity, entity_id, meta) values (p_actor, p_action, p_entity, p_id, p_meta);
$$;

-- ---------- محفّزات الإصدارات (إبطال الذاكرة المؤقتة فورًا) ----------
do $$
declare r record;
begin
  for r in select * from (values
      ('role_grants','roles'), ('role_permissions','roles'), ('students','roles'),
      ('app_settings','settings'),
      ('subjects','subjects'), ('doctors','subjects'), ('teaching','subjects'),
      ('theory_groups','groups'), ('practical_groups','groups'),
      ('tg_destinations','destinations')
  ) as t(tbl, scope)
  loop
    execute format('drop trigger if exists bump_%1$s on %1$I', r.tbl);
    execute format('create trigger bump_%1$s after insert or update or delete on %1$I
                    for each statement execute function trg_bump(%2$L)', r.tbl, r.scope);
  end loop;
end $$;

-- ---------- دوال مساعدة ----------
create or replace function is_developer(p_tg bigint) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from role_grants where telegram_id = p_tg and role = 'developer' and revoked_at is null);
$$;

create or replace function require_developer(p_tg bigint) returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not is_developer(p_tg) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
end $$;

-- ---------- bootstrap: استدعاء واحد يجلب كل ما تحتاجه الرئيسية ----------
create or replace function app_bootstrap(p_tg bigint, p_name text default null, p_username text default null)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  u      users%rowtype;
  s      students%rowtype;
  d      doctors%rowtype;
  v_kind text;
  v_base text[] := '{}';
  v_roles jsonb;
  v_perms jsonb;
  v_versions jsonb;
begin
  insert into users (telegram_id, display_name, username, last_seen_at)
  values (p_tg, nullif(trim(coalesce(p_name, '')), ''), nullif(p_username, ''), now())
  on conflict (telegram_id) do update
     set last_seen_at = now(),
         username     = coalesce(excluded.username, users.username)
  returning * into u;

  select coalesce(jsonb_object_agg(scope, version), '{}') into v_versions from data_versions;

  if u.is_banned then
    return jsonb_build_object(
      'user', jsonb_build_object('telegram_id', u.telegram_id, 'kind', 'banned'),
      'versions', v_versions);
  end if;

  select * into s from students where telegram_id = p_tg and deleted_at is null;
  select * into d from doctors  where telegram_id = p_tg and is_active and deleted_at is null;

  v_kind := case
    when d.id is not null           then 'doctor'
    when s.academic_no is not null  then 'student'
    when u.kind = 'guest'           then 'guest'
    else 'new' end;

  v_base := case v_kind
    when 'doctor'  then array['doctor']
    when 'student' then array['student']
    when 'guest'   then array['guest']
    else '{}'::text[] end;

  select coalesce(jsonb_agg(jsonb_build_object('role', role, 'group', theory_group_id) order by role), '[]')
    into v_roles
    from role_grants where telegram_id = p_tg and revoked_at is null;

  select coalesce(jsonb_agg(distinct p), '[]') into v_perms from (
    select jsonb_build_object('k', rp.permission, 's', rp.scope, 'g', null::text, 'c', rp.condition) as p
      from role_permissions rp where rp.role = any (v_base)
    union all
    select jsonb_build_object('k', rp.permission, 's', rp.scope, 'g', g.theory_group_id, 'c', rp.condition)
      from role_grants g join role_permissions rp on rp.role = g.role
     where g.telegram_id = p_tg and g.revoked_at is null
  ) x;

  return jsonb_build_object(
    'user', jsonb_build_object(
      'telegram_id', u.telegram_id, 'kind', v_kind,
      'name', coalesce(s.full_name, d.name, u.display_name), 'username', u.username),
    'student', case when s.academic_no is null then null else jsonb_build_object(
      'academic_no', s.academic_no, 'full_name', s.full_name,
      'theory_group_id', s.theory_group_id, 'practical_group_id', s.practical_group_id) end,
    'doctor_id', d.id,
    'roles', v_roles,
    'perms', v_perms,
    'groups', jsonb_build_object(
      'theory',    (select coalesce(jsonb_agg(id order by sort), '[]') from theory_groups),
      'practical', (select coalesce(jsonb_agg(jsonb_build_object('id', code, 'theory', theory_group_id) order by sort), '[]') from practical_groups)),
    'subjects', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'id', s_row.id, 'name', s_row.name_ar, 'tag', s_row.hashtag,
        'doctors', jsonb_build_object(
          'theory', coalesce((
            select jsonb_agg(jsonb_build_object('id', d_row.id, 'name', d_row.name) order by tc.cnt desc, d_row.id)
              from (select t.doctor_id, count(*) as cnt from teaching t where t.subject_id = s_row.id and t.part = 'theory' group by t.doctor_id) tc
              join doctors d_row on d_row.id = tc.doctor_id and d_row.is_active and d_row.deleted_at is null
          ), '[]'::jsonb),
          'practical', coalesce((
            select jsonb_agg(jsonb_build_object('id', d_row.id, 'name', d_row.name) order by tc.cnt desc, d_row.id)
              from (select t.doctor_id, count(*) as cnt from teaching t where t.subject_id = s_row.id and t.part = 'practical' group by t.doctor_id) tc
              join doctors d_row on d_row.id = tc.doctor_id and d_row.is_active and d_row.deleted_at is null
          ), '[]'::jsonb)
        )
      ) order by s_row.sort, s_row.id), '[]')
      from subjects s_row where s_row.is_active and s_row.deleted_at is null
    ),
    'versions', v_versions,
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from app_settings where is_public)
  );
end $$;

-- ---------- الربط بالرقم الأكاديمي ----------
-- حدّ للمحاولات يمنع تخمين الأرقام الأكاديمية لكشف أسماء الطلاب (12 محاولة / 10 دقائق لكل مستخدم)
create table if not exists link_attempts (
  id          bigint generated always as identity primary key,
  telegram_id bigint not null,
  at          timestamptz not null default now()
);
create index if not exists link_attempts_idx on link_attempts (telegram_id, at desc);

create or replace function link_rate_ok(p_tg bigint) returns boolean
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  delete from link_attempts where telegram_id = p_tg and at < now() - interval '1 day';
  select count(*) into n from link_attempts where telegram_id = p_tg and at > now() - interval '10 minutes';
  if n >= 12 then return false; end if;
  insert into link_attempts (telegram_id) values (p_tg);
  return true;
end $$;

create or replace function lookup_student(p_tg bigint, p_no text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  ex students%rowtype;
  s  students%rowtype;
begin
  -- إذا كان حساب تيليجرام هذا مرتبطًا بطالب بالفعل (مثلًا سجّل عبر الـ Mini App ثم عاد للبوت)
  select * into ex from students where telegram_id = p_tg and deleted_at is null;
  if ex.academic_no is not null then
    return jsonb_build_object(
      'status', 'already_linked',
      'academic_no', ex.academic_no,
      'full_name', ex.full_name,
      'theory_group_id', ex.theory_group_id,
      'practical_group_id', ex.practical_group_id
    );
  end if;

  if not link_rate_ok(p_tg) then
    return jsonb_build_object('status', 'rate_limited');
  end if;
  select * into s from students where academic_no = p_no and deleted_at is null;
  if s.academic_no is null then return jsonb_build_object('status', 'not_found'); end if;
  if s.telegram_id is not null then
    if s.telegram_id = p_tg then
      return jsonb_build_object(
        'status', 'already_linked',
        'academic_no', s.academic_no,
        'full_name', s.full_name,
        'theory_group_id', s.theory_group_id,
        'practical_group_id', s.practical_group_id
      );
    end if;
    return jsonb_build_object('status', 'taken');
  end if;
  return jsonb_build_object('status', 'ok', 'full_name', s.full_name,
           'theory_group_id', s.theory_group_id, 'practical_group_id', s.practical_group_id);
end $$;

create or replace function link_student(p_tg bigint, p_no text, p_username text default null)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  ex students%rowtype;
  s  students%rowtype;
begin
  select * into ex from students where telegram_id = p_tg and deleted_at is null;
  if ex.academic_no is not null then
    return jsonb_build_object('status', 'already_linked', 'academic_no', ex.academic_no,
             'full_name', ex.full_name, 'theory_group_id', ex.theory_group_id, 'practical_group_id', ex.practical_group_id);
  end if;
  if not link_rate_ok(p_tg) then
    return jsonb_build_object('status', 'rate_limited');
  end if;

  update students set telegram_id = p_tg, linked_at = now()
   where academic_no = p_no and telegram_id is null and deleted_at is null
  returning * into s;

  if s.academic_no is null then
    return jsonb_build_object('status',
      case when exists (select 1 from students where academic_no = p_no and deleted_at is null)
           then 'taken' else 'not_found' end);
  end if;

  insert into users (telegram_id, kind, display_name, username)
  values (p_tg, 'student', s.full_name, nullif(p_username, ''))
  on conflict (telegram_id) do update
     set kind = 'student', display_name = excluded.display_name,
         username = coalesce(excluded.username, users.username);

  perform bump_scope('roles');
  perform log_activity(p_tg, 'student.link', 'student', p_no, null);
  return jsonb_build_object('status', 'ok', 'academic_no', s.academic_no, 'full_name', s.full_name,
           'theory_group_id', s.theory_group_id, 'practical_group_id', s.practical_group_id);
end $$;

create or replace function register_guest(p_tg bigint, p_name text, p_username text default null)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_name text := regexp_replace(trim(coalesce(p_name, '')), '\s+', ' ', 'g');
  v_kind text;
begin
  if char_length(v_name) < 2 or char_length(v_name) > 40 then
    return jsonb_build_object('status', 'invalid_name');
  end if;
  select kind into v_kind from users where telegram_id = p_tg;
  if v_kind = 'student' then
    return jsonb_build_object('status', 'is_student');
  end if;
  insert into users (telegram_id, kind, display_name, username)
  values (p_tg, 'guest', v_name, nullif(p_username, ''))
  on conflict (telegram_id) do update
     set kind = 'guest', display_name = excluded.display_name,
         username = coalesce(excluded.username, users.username);
  perform bump_scope('roles');
  return jsonb_build_object('status', 'ok', 'name', v_name);
end $$;

-- ---------- عمليات المطوّر ----------
create or replace function unlink_student(p_actor bigint, p_no text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_tg bigint;
begin
  perform require_developer(p_actor);
  select telegram_id into v_tg from students where academic_no = p_no and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  update students set telegram_id = null, linked_at = null where academic_no = p_no;
  if v_tg is not null then update users set kind = 'new' where telegram_id = v_tg; end if;
  perform bump_scope('roles');
  perform log_activity(p_actor, 'student.unlink', 'student', p_no, jsonb_build_object('telegram_id', v_tg));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function set_ban(p_actor bigint, p_target bigint, p_banned boolean, p_reason text default null)
returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  update users set is_banned = p_banned,
         banned_at = case when p_banned then now() end,
         banned_by = case when p_banned then p_actor end,
         ban_reason = case when p_banned then p_reason end
   where telegram_id = p_target;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform bump_scope('roles');
  perform log_activity(p_actor, case when p_banned then 'user.ban' else 'user.unban' end, 'user', p_target::text, jsonb_build_object('reason', p_reason));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function grant_role(p_actor bigint, p_target bigint, p_role text, p_group text default null)
returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform require_developer(p_actor);
  if p_role not in ('deputy','delegate','committee','developer') then
    return jsonb_build_object('status', 'invalid_role');
  end if;
  if (p_role = 'developer') <> (p_group is null) then
    return jsonb_build_object('status', 'invalid_group');
  end if;
  if p_group is not null and not exists (select 1 from theory_groups where id = p_group) then
    return jsonb_build_object('status', 'invalid_group');
  end if;
  if exists (select 1 from role_grants where telegram_id = p_target and role = p_role
              and coalesce(theory_group_id, '') = coalesce(p_group, '') and revoked_at is null) then
    return jsonb_build_object('status', 'exists');
  end if;
  insert into role_grants (telegram_id, role, theory_group_id, granted_by)
  values (p_target, p_role, p_group, p_actor);
  perform log_activity(p_actor, 'role.grant', 'user', p_target::text, jsonb_build_object('role', p_role, 'group', p_group));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function revoke_role(p_actor bigint, p_grant_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare g role_grants%rowtype;
begin
  perform require_developer(p_actor);
  select * into g from role_grants where id = p_grant_id and revoked_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if g.role = 'developer' and (select count(*) from role_grants where role = 'developer' and revoked_at is null) <= 1 then
    return jsonb_build_object('status', 'last_developer');
  end if;
  update role_grants set revoked_at = now(), revoked_by = p_actor where id = p_grant_id;
  perform log_activity(p_actor, 'role.revoke', 'user', g.telegram_id::text, jsonb_build_object('role', g.role, 'group', g.theory_group_id));
  return jsonb_build_object('status', 'ok');
end $$;

-- استيراد الطلاب: p_rows = [{"academic_no","full_name","theory","practical"}, ...]
create or replace function import_students(p_actor bigint, p_rows jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  r record; v_ins int := 0; v_upd int := 0; v_errors jsonb := '[]';
  v_no text; v_name text; v_t text; v_p text; v_existed boolean;
begin
  perform require_developer(p_actor);
  for r in select value as item, ordinality as n from jsonb_array_elements(p_rows) with ordinality loop
    v_no   := trim(r.item->>'academic_no');
    v_name := regexp_replace(trim(coalesce(r.item->>'full_name', '')), '\s+', ' ', 'g');
    v_t    := upper(trim(r.item->>'theory'));
    v_p    := upper(trim(r.item->>'practical'));
    if v_no is null or v_no = '' or v_name = '' then
      v_errors := v_errors || jsonb_build_object('row', r.n, 'reason', 'بيانات ناقصة'); continue;
    end if;
    if not exists (select 1 from practical_groups where code = v_p and theory_group_id = v_t) then
      v_errors := v_errors || jsonb_build_object('row', r.n, 'reason', 'مجموعة غير صحيحة أو العملية لا تتبع النظرية'); continue;
    end if;
    select exists (select 1 from students where academic_no = v_no) into v_existed;
    insert into students (academic_no, full_name, theory_group_id, practical_group_id)
    values (v_no, v_name, v_t, v_p)
    on conflict (academic_no) do update
       set full_name = excluded.full_name, theory_group_id = excluded.theory_group_id,
           practical_group_id = excluded.practical_group_id, deleted_at = null;
    if v_existed then v_upd := v_upd + 1; else v_ins := v_ins + 1; end if;
  end loop;
  perform bump_scope('roles');
  perform log_activity(p_actor, 'students.import', 'students', null,
          jsonb_build_object('inserted', v_ins, 'updated', v_upd, 'errors', jsonb_array_length(v_errors)));
  return jsonb_build_object('inserted', v_ins, 'updated', v_upd, 'errors', v_errors);
end $$;

-- ---------- بيانات أولية ----------
-- بيانات المجموعات صارت في supabase/seed_groups.sql (يولّدها scripts/setup_cohort.py من cohort.json).
-- نسخة CS26 العاملة نُفّذ فيها هذا الجزء سابقًا؛ لا حاجة لإعادة أي شيء.

insert into app_settings (key, value, is_public) values
  ('timezone',               '"Asia/Aden"',       true),   -- UTC+3 ثابت
  ('reminder_hours',         '[24,3]',            true),   -- قبل الموعد
  ('assistant_daily_student','10',                true),
  ('assistant_daily_guest',  '5',                 true),
  ('assignment_grace_hours', '12',                false),  -- داخلي فقط، لا يظهر في أي مكان
  ('barcode_daily_limit',    '3',                 false)
  on conflict do nothing;

-- مصفوفة الصلاحيات (الأعمدة: guest, student, deputy, delegate, committee, doctor, developer)
insert into role_permissions (role, permission, scope, condition) values
  -- الملفات والاختبارات والمساعد (الضيف مسموح، الدكتور لا)
  ('guest','files.view','any',null), ('student','files.view','any',null), ('deputy','files.view','any',null),
  ('delegate','files.view','any',null), ('committee','files.view','any',null), ('developer','files.view','any',null),
  ('guest','mcq.take','any',null), ('student','mcq.take','any',null), ('deputy','mcq.take','any',null),
  ('delegate','mcq.take','any',null), ('committee','mcq.take','any',null), ('developer','mcq.take','any',null),
  ('guest','assistant.use','own',null), ('student','assistant.use','own',null), ('deputy','assistant.use','own',null),
  ('delegate','assistant.use','own',null), ('committee','assistant.use','own',null), ('developer','assistant.use','own',null),
  ('guest','ui.locked_preview','any',null),
  -- ميزات الطالب (واجباتي، شارك، حضوري، شؤوني)
  ('student','assignments.submit','own',null), ('deputy','assignments.submit','own',null),
  ('delegate','assignments.submit','own',null), ('committee','assignments.submit','own',null), ('developer','assignments.submit','own',null),
  ('student','sharek.participate','own',null), ('deputy','sharek.participate','own',null),
  ('delegate','sharek.participate','own',null), ('committee','sharek.participate','own',null), ('developer','sharek.participate','own',null),
  ('student','attendance.view_mine','own',null), ('deputy','attendance.view_mine','own',null),
  ('delegate','attendance.view_mine','own',null), ('committee','attendance.view_mine','own',null), ('developer','attendance.view_mine','own',null),
  ('student','affairs.use','own',null), ('deputy','affairs.use','own',null),
  ('delegate','affairs.use','own',null), ('committee','affairs.use','own',null), ('developer','affairs.use','own',null),
  -- التظلمات
  ('delegate','grievances.open','group',null), ('developer','grievances.open','any',null),
  -- الإشعارات اليدوية
  ('delegate','notify.send','group',null), ('committee','notify.send','group',null), ('developer','notify.send','any',null),
  -- الواجبات
  ('delegate','assignments.create','group',null), ('committee','assignments.create','group',null), ('developer','assignments.create','any',null),
  ('delegate','assignments.manage','group',null), ('committee','assignments.manage','group',null), ('developer','assignments.manage','any',null),
  -- الملفات والاختبارات
  ('committee','files.upload','any',null), ('developer','files.upload','any',null),
  ('committee','files.delete','own',null), ('developer','files.delete','any',null),
  ('committee','mcq.upload','any',null), ('developer','mcq.upload','any',null),
  -- التقارير
  ('committee','reports.write','group',null), ('developer','reports.write','any',null),
  -- شارك (كامل الصلاحيات للمندوب واللجنة العلمية ضمن مجموعتهم: إنشاء وتجميع وتصدير)
  ('delegate','sharek.publish','group',null), ('committee','sharek.publish','group',null), ('developer','sharek.publish','any',null),
  -- الحضور
  ('delegate','attendance.session','group',null),
  ('deputy','attendance.session','group','delegate_absent'),
  ('committee','attendance.session','group','delegate_absent'),
  ('developer','attendance.session','any',null),
  ('deputy','attendance.scan','group',null), ('delegate','attendance.scan','group',null),
  ('committee','attendance.scan','group',null), ('developer','attendance.scan','any',null),
  ('delegate','attendance.admin','group',null), ('developer','attendance.admin','any',null),
  -- الدكتور
  ('doctor','doctor.assignments','own',null), ('developer','doctor.assignments','any',null),
  -- المطوّر وحده
  ('developer','students.view','any',null), ('developer','students.manage','any',null),
  ('developer','guests.manage','any',null), ('developer','roles.manage','any',null),
  ('developer','activity.view','any',null), ('developer','communities.manage','any',null),
  ('developer','tickets.reply','any',null)
  on conflict (role, permission) do update
     set scope = excluded.scope, condition = excluded.condition;

-- ---------- الأمان: RLS مغلق بلا سياسات، والوصول بمفتاح الخدمة من الخادم فقط ----------
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
