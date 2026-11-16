-- =====================================================================
-- 0003_attendance_affairs_support.sql — المرحلة 3:
-- الحضور (النظري فقط، 12 محاضرة، أجهزة المسح، التصحيح، التصدير)،
-- شؤوني (حضوري، تسليماتي، تظلماتي، باركود بطاقتي Code 128)،
-- المساعد الذكي والبحث الذكي في الملفات، والدعم والبلاغات.
-- شغّله بعد 0002. آمن لإعادة التشغيل (idempotent).
-- =====================================================================

-- ---------- تحديث نوع المسودات وأولويات الإشعارات ----------
-- ---------- 1) الحضور (النظري فقط، 12 محاضرة لكل مادة) ----------
create table if not exists attendance_sessions (
  id                bigint generated always as identity primary key,
  subject_id        smallint not null references subjects(id),
  theory_group_id   text not null references theory_groups(id),
  lecture_no        smallint not null check (lecture_no between 1 and 12),
  status            text not null default 'open' check (status in ('open','closed')),
  delegate_absent   boolean not null default false,
  opened_by         bigint not null,
  opened_at         timestamptz not null default now(),
  closed_by         bigint,
  closed_at         timestamptz,
  unique (subject_id, theory_group_id, lecture_no)
);

-- جلسة مفتوحة واحدة فقط لكل مجموعة نظرية في الوقت نفسه (تفرضها القاعدة)
create unique index if not exists attendance_one_open_per_group_uq
  on attendance_sessions (theory_group_id) where status = 'open';

create index if not exists attendance_sessions_group_idx
  on attendance_sessions (theory_group_id, subject_id, lecture_no);

create table if not exists scanner_devices (
  id              bigint generated always as identity primary key,
  theory_group_id text not null references theory_groups(id),
  label           text not null,
  token_hash      text not null unique,
  token_prefix    text not null,
  created_by      bigint not null,
  created_at      timestamptz not null default now(),
  last_used_at    timestamptz,
  scans_count     int not null default 0,
  revoked_at      timestamptz,
  revoked_by      bigint
);
create index if not exists scanner_devices_group_idx
  on scanner_devices (theory_group_id, id desc) where revoked_at is null;

create table if not exists attendance_records (
  session_id  bigint not null references attendance_sessions(id) on delete cascade,
  student_no  text not null references students(academic_no),
  status      text not null check (status in ('present','absent')),
  scanned_by  bigint,
  device_id   bigint references scanner_devices(id),
  recorded_at timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (session_id, student_no)
);
create index if not exists attendance_records_student_idx
  on attendance_records (student_no, session_id);

create table if not exists attendance_corrections (
  id         bigint generated always as identity primary key,
  session_id bigint not null references attendance_sessions(id) on delete cascade,
  student_no text not null references students(academic_no),
  old_status text,
  new_status text not null check (new_status in ('present','absent')),
  reason     text not null,
  actor_id   bigint not null,
  created_at timestamptz not null default now()
);
create index if not exists attendance_corrections_sess_idx
  on attendance_corrections (session_id, created_at desc);

-- فتح جلسة حضور نظري (المندوب، أو النائب/اللجنة عند غياب المندوب، أو المطوّر)
create or replace function open_attendance_session(
  p_actor bigint,
  p_subject int,
  p_group text,
  p_lecture int,
  p_delegate_absent boolean default false
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_open attendance_sessions%rowtype;
  v_existing attendance_sessions%rowtype;
  v_id bigint;
begin
  if not exists (select 1 from theory_groups where id = p_group) then
    return jsonb_build_object('status', 'invalid_group');
  end if;
  if p_lecture is null or p_lecture < 1 or p_lecture > 12 then
    return jsonb_build_object('status', 'invalid_lecture');
  end if;
  if not exists (select 1 from subjects where id = p_subject and is_active and deleted_at is null) then
    return jsonb_build_object('status', 'invalid_subject');
  end if;
  if not has_perm(p_actor, 'attendance.session', p_group, p_delegate_absent) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  perform pg_advisory_xact_lock(hashtext('att_sess:' || p_group));

  select * into v_open from attendance_sessions where theory_group_id = p_group and status = 'open';
  if found then
    if v_open.subject_id = p_subject and v_open.lecture_no = p_lecture then
      return jsonb_build_object('status', 'ok', 'session_id', v_open.id, 'already_open', true);
    end if;
    return jsonb_build_object(
      'status', 'group_has_open_session',
      'open_session_id', v_open.id,
      'subject_id', v_open.subject_id,
      'lecture_no', v_open.lecture_no
    );
  end if;

  select * into v_existing
    from attendance_sessions
   where subject_id = p_subject and theory_group_id = p_group and lecture_no = p_lecture;

  if found then
    -- إعادة فتح جلسة مغلقة متاح لمن يملك صلاحية إدارة الحضور (المندوب/المطوّر)
    if not has_perm(p_actor, 'attendance.admin', p_group) then
      return jsonb_build_object('status', 'lecture_already_closed');
    end if;
    update attendance_sessions
       set status = 'open', closed_at = null, closed_by = null, delegate_absent = p_delegate_absent
     where id = v_existing.id
     returning id into v_id;
  else
    insert into attendance_sessions (subject_id, theory_group_id, lecture_no, delegate_absent, opened_by)
    values (p_subject, p_group, p_lecture, p_delegate_absent, p_actor)
    returning id into v_id;
  end if;

  perform bump_scope('attendance');
  perform log_activity(p_actor, 'attendance.open', 'attendance_session', v_id::text,
    jsonb_build_object('subject', p_subject, 'group', p_group, 'lecture', p_lecture, 'delegate_absent', p_delegate_absent));
  return jsonb_build_object('status', 'ok', 'session_id', v_id);
end $$;

-- إغلاق الجلسة: يعتمد الحضور ويحسب غير المسجلين غائبين تلقائيًا
create or replace function close_attendance_session(
  p_actor bigint,
  p_session bigint,
  p_delegate_absent boolean default false
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  sess attendance_sessions%rowtype;
  v_present int;
  v_absent int;
begin
  select * into sess from attendance_sessions where id = p_session for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if not has_perm(p_actor, 'attendance.session', sess.theory_group_id, p_delegate_absent or sess.delegate_absent) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  insert into attendance_records (session_id, student_no, status)
  select sess.id, s.academic_no, 'absent'
    from students s
   where s.theory_group_id = sess.theory_group_id and s.deleted_at is null
   on conflict (session_id, student_no) do nothing;

  update attendance_sessions
     set status = 'closed', closed_at = coalesce(closed_at, now()), closed_by = coalesce(closed_by, p_actor)
   where id = sess.id;

  select count(*) filter (where status = 'present'),
         count(*) filter (where status = 'absent')
    into v_present, v_absent
    from attendance_records where session_id = sess.id;

  perform bump_scope('attendance');
  perform log_activity(p_actor, 'attendance.close', 'attendance_session', sess.id::text,
    jsonb_build_object('present', v_present, 'absent', v_absent));
  return jsonb_build_object('status', 'ok', 'present', v_present, 'absent', v_absent, 'total', v_present + v_absent);
end $$;

-- مسح حضور طالب داخل جلسة مفتوحة (من التطبيق أو بإدخال الرقم الأكاديمي)
create or replace function scan_attendance(
  p_actor bigint,
  p_session bigint,
  p_academic_no text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  sess attendance_sessions%rowtype;
  st   students%rowtype;
  rec  attendance_records%rowtype;
  v_present int;
begin
  select * into sess from attendance_sessions where id = p_session;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'attendance.scan', sess.theory_group_id);
  if sess.status <> 'open' then
    return jsonb_build_object('status', 'session_closed');
  end if;

  select * into st from students where academic_no = trim(p_academic_no) and deleted_at is null;
  if not found then
    return jsonb_build_object('status', 'student_not_found');
  end if;
  if st.theory_group_id <> sess.theory_group_id then
    return jsonb_build_object(
      'status', 'wrong_group',
      'student_group', st.theory_group_id,
      'student', jsonb_build_object('no', st.academic_no, 'name', st.full_name, 'theory', st.theory_group_id, 'practical', st.practical_group_id)
    );
  end if;

  select * into rec from attendance_records where session_id = sess.id and student_no = st.academic_no;
  if found and rec.status = 'present' then
    select count(*) into v_present from attendance_records where session_id = sess.id and status = 'present';
    return jsonb_build_object(
      'status', 'already_scanned',
      'present_count', v_present,
      'student', jsonb_build_object('no', st.academic_no, 'name', st.full_name, 'practical', st.practical_group_id)
    );
  end if;

  insert into attendance_records (session_id, student_no, status, scanned_by, recorded_at, updated_at)
  values (sess.id, st.academic_no, 'present', p_actor, now(), now())
  on conflict (session_id, student_no) do update
     set status = 'present', scanned_by = p_actor, updated_at = now();

  select count(*) into v_present from attendance_records where session_id = sess.id and status = 'present';
  return jsonb_build_object(
    'status', 'ok',
    'present_count', v_present,
    'student', jsonb_build_object('no', st.academic_no, 'name', st.full_name, 'practical', st.practical_group_id)
  );
end $$;

-- المسح المتزامن عبر جهاز Open Ticket Scanner بمفتاح الجهاز
create or replace function scan_by_device_token(
  p_token_hash text,
  p_academic_no text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  dev  scanner_devices%rowtype;
  sess attendance_sessions%rowtype;
  st   students%rowtype;
  rec  attendance_records%rowtype;
  v_subj text;
  v_present int;
begin
  select * into dev from scanner_devices where token_hash = p_token_hash and revoked_at is null;
  if not found then
    return jsonb_build_object('status', 'invalid_device');
  end if;

  select * into sess from attendance_sessions where theory_group_id = dev.theory_group_id and status = 'open';
  if not found then
    return jsonb_build_object('status', 'no_open_session', 'group', dev.theory_group_id);
  end if;

  select name_ar into v_subj from subjects where id = sess.subject_id;
  select * into st from students where academic_no = trim(p_academic_no) and deleted_at is null;
  if not found then
    return jsonb_build_object('status', 'student_not_found');
  end if;
  if st.theory_group_id <> sess.theory_group_id then
    return jsonb_build_object(
      'status', 'wrong_group',
      'student_group', st.theory_group_id,
      'expected_group', sess.theory_group_id,
      'student', jsonb_build_object('no', st.academic_no, 'name', st.full_name)
    );
  end if;

  update scanner_devices set last_used_at = now(), scans_count = scans_count + 1 where id = dev.id;

  select * into rec from attendance_records where session_id = sess.id and student_no = st.academic_no;
  if found and rec.status = 'present' then
    select count(*) into v_present from attendance_records where session_id = sess.id and status = 'present';
    return jsonb_build_object(
      'status', 'already_scanned',
      'subject', v_subj,
      'lecture_no', sess.lecture_no,
      'present_count', v_present,
      'student', jsonb_build_object('no', st.academic_no, 'name', st.full_name, 'practical', st.practical_group_id)
    );
  end if;

  insert into attendance_records (session_id, student_no, status, device_id, recorded_at, updated_at)
  values (sess.id, st.academic_no, 'present', dev.id, now(), now())
  on conflict (session_id, student_no) do update
     set status = 'present', device_id = dev.id, updated_at = now();

  select count(*) into v_present from attendance_records where session_id = sess.id and status = 'present';
  return jsonb_build_object(
    'status', 'ok',
    'subject', v_subj,
    'lecture_no', sess.lecture_no,
    'present_count', v_present,
    'student', jsonb_build_object('no', st.academic_no, 'name', st.full_name, 'practical', st.practical_group_id)
  );
end $$;

-- تصحيح الحضور (فوق fn_correct_attendance) مع السبب والسجل
create or replace function fn_correct_attendance(
  p_actor bigint,
  p_subject int,
  p_group text,
  p_lecture int,
  p_student_no text,
  p_status text,
  p_reason text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  sess attendance_sessions%rowtype;
  st   students%rowtype;
  v_old text;
  v_reason text := trim(coalesce(p_reason, ''));
begin
  perform require_perm(p_actor, 'attendance.admin', p_group);
  if p_status not in ('present', 'absent') then
    return jsonb_build_object('status', 'invalid_status');
  end if;
  if p_lecture is null or p_lecture < 1 or p_lecture > 12 then
    return jsonb_build_object('status', 'invalid_lecture');
  end if;
  if char_length(v_reason) < 2 or char_length(v_reason) > 300 then
    return jsonb_build_object('status', 'reason_required');
  end if;

  select * into st from students where academic_no = trim(p_student_no) and deleted_at is null;
  if not found then return jsonb_build_object('status', 'student_not_found'); end if;
  if st.theory_group_id <> p_group then return jsonb_build_object('status', 'wrong_group'); end if;

  select * into sess
    from attendance_sessions
   where subject_id = p_subject and theory_group_id = p_group and lecture_no = p_lecture;

  if not found then
    return jsonb_build_object('status', 'session_not_found');
  end if;

  select status into v_old from attendance_records where session_id = sess.id and student_no = st.academic_no;

  insert into attendance_records (session_id, student_no, status, scanned_by, recorded_at, updated_at)
  values (sess.id, st.academic_no, p_status, p_actor, now(), now())
  on conflict (session_id, student_no) do update
     set status = excluded.status, scanned_by = p_actor, updated_at = now();

  insert into attendance_corrections (session_id, student_no, old_status, new_status, reason, actor_id)
  values (sess.id, st.academic_no, v_old, p_status, v_reason, p_actor);

  perform bump_scope('attendance');
  perform log_activity(p_actor, 'attendance.correct', 'attendance_session', sess.id::text,
    jsonb_build_object('student_no', st.academic_no, 'old', v_old, 'new', p_status, 'reason', v_reason));
  return jsonb_build_object('status', 'ok', 'old_status', v_old, 'new_status', p_status);
end $$;

-- إدارة أجهزة المسح (Open Ticket Scanner)
create or replace function create_scanner_device(
  p_actor bigint,
  p_group text,
  p_label text,
  p_token_hash text,
  p_token_prefix text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_id bigint;
  v_label text := trim(coalesce(p_label, ''));
begin
  if not exists (select 1 from theory_groups where id = p_group) then
    return jsonb_build_object('status', 'invalid_group');
  end if;
  perform require_perm(p_actor, 'attendance.admin', p_group);
  if char_length(v_label) < 2 or char_length(v_label) > 60 then
    return jsonb_build_object('status', 'invalid_label');
  end if;
  insert into scanner_devices (theory_group_id, label, token_hash, token_prefix, created_by)
  values (p_group, v_label, p_token_hash, p_token_prefix, p_actor)
  returning id into v_id;

  perform log_activity(p_actor, 'scanner.create', 'scanner_device', v_id::text,
    jsonb_build_object('group', p_group, 'label', v_label));
  return jsonb_build_object('status', 'ok', 'id', v_id, 'group', p_group, 'label', v_label, 'token_prefix', p_token_prefix);
end $$;

create or replace function revoke_scanner_device(p_actor bigint, p_device_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare d scanner_devices%rowtype;
begin
  select * into d from scanner_devices where id = p_device_id and revoked_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'attendance.admin', d.theory_group_id);
  update scanner_devices set revoked_at = now(), revoked_by = p_actor where id = p_device_id;
  perform log_activity(p_actor, 'scanner.revoke', 'scanner_device', p_device_id::text, jsonb_build_object('group', d.theory_group_id));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function list_scanner_devices(p_actor bigint, p_group text default null) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(to_jsonb(r) order by r.id desc), '[]'::jsonb) from (
    select id, theory_group_id, label, token_prefix, created_at, last_used_at, scans_count
      from scanner_devices
     where revoked_at is null
       and (p_group is null or theory_group_id = p_group)
       and has_perm(p_actor, 'attendance.admin', theory_group_id)
     order by id desc
  ) r;
$$;

-- حضوري (للطلاب): لكل مادة، حضور المحاضرات النظرية (12 محاضرة) حاضر/غائب والنسبة، بلا أي تنبيه غياب
create or replace function my_attendance(p_tg bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s students%rowtype;
begin
  perform require_perm(p_tg, 'attendance.view_mine');
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return '[]'::jsonb; end if;

  return coalesce((
    select jsonb_agg(to_jsonb(row_data) order by row_data.sort, row_data.subject_id)
      from (
        select
          sj.id as subject_id,
          sj.name_ar as subject,
          sj.sort,
          coalesce(sum(case when ar.status = 'present' then 1 else 0 end), 0)::int as present,
          coalesce(sum(case when sess.status = 'closed' and coalesce(ar.status, 'absent') = 'absent' then 1 else 0 end), 0)::int as absent,
          coalesce(count(sess.id) filter (where sess.status = 'closed' or ar.status = 'present'), 0)::int as held,
          (
            select jsonb_agg(
              jsonb_build_object(
                'n', n,
                'state', case
                  when sx.id is null then 'none'
                  when rx.status = 'present' then 'present'
                  when sx.status = 'open' then 'open'
                  else 'absent'
                end
              ) order by n
            )
            from generate_series(1, 12) n
            left join attendance_sessions sx
              on sx.subject_id = sj.id and sx.theory_group_id = s.theory_group_id and sx.lecture_no = n
            left join attendance_records rx
              on rx.session_id = sx.id and rx.student_no = s.academic_no
          ) as lectures
        from subjects sj
        left join attendance_sessions sess
          on sess.subject_id = sj.id and sess.theory_group_id = s.theory_group_id
        left join attendance_records ar
          on ar.session_id = sess.id and ar.student_no = s.academic_no
        where sj.is_active and sj.deleted_at is null
        group by sj.id, sj.name_ar, sj.sort
      ) row_data
  ), '[]'::jsonb);
end $$;

-- نظرة عامة لإدارة الحضور (الجلسة المفتوحة، جلسات المادة/المجموعة، الأجهزة، آخر التصحيحات)
create or replace function attendance_overview(p_actor bigint, p_group text default null, p_subject int default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_group text := p_group;
  v_can_admin boolean;
begin
  if v_group is null then
    select id into v_group from theory_groups
     where has_perm(p_actor, 'attendance.scan', id)
        or has_perm(p_actor, 'attendance.session', id, true)
        or has_perm(p_actor, 'attendance.admin', id)
     order by sort limit 1;
  end if;
  if v_group is null then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if not (has_perm(p_actor, 'attendance.scan', v_group)
       or has_perm(p_actor, 'attendance.session', v_group, true)
       or has_perm(p_actor, 'attendance.admin', v_group)) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  v_can_admin := has_perm(p_actor, 'attendance.admin', v_group);

  return jsonb_build_object(
    'status', 'ok',
    'group', v_group,
    'can_admin', v_can_admin,
    'can_session_direct', has_perm(p_actor, 'attendance.session', v_group, false),
    'can_session_absent', has_perm(p_actor, 'attendance.session', v_group, true),
    'total_students', (select count(*) from students where theory_group_id = v_group and deleted_at is null),
    'open_session', (
      select jsonb_build_object(
        'id', s.id, 'subject_id', s.subject_id, 'subject', sj.name_ar, 'group', s.theory_group_id,
        'lecture_no', s.lecture_no, 'opened_at', s.opened_at, 'delegate_absent', s.delegate_absent,
        'present_count', (select count(*) from attendance_records ar where ar.session_id = s.id and ar.status = 'present')
      )
      from attendance_sessions s join subjects sj on sj.id = s.subject_id
      where s.theory_group_id = v_group and s.status = 'open'
      limit 1
    ),
    'sessions', coalesce((
      select jsonb_agg(to_jsonb(x) order by x.lecture_no desc) from (
        select s.id, s.subject_id, sj.name_ar as subject, s.lecture_no, s.status, s.opened_at, s.closed_at, s.delegate_absent,
               (select count(*) from attendance_records ar where ar.session_id = s.id and ar.status = 'present') as present,
               (select count(*) from attendance_records ar where ar.session_id = s.id and ar.status = 'absent') as absent
          from attendance_sessions s join subjects sj on sj.id = s.subject_id
         where s.theory_group_id = v_group and (p_subject is null or s.subject_id = p_subject)
         order by s.lecture_no desc limit 30
      ) x
    ), '[]'::jsonb),
    'devices', case when v_can_admin then list_scanner_devices(p_actor, v_group) else '[]'::jsonb end,
    'corrections', case when v_can_admin then coalesce((
      select jsonb_agg(to_jsonb(c) order by c.id desc) from (
        select ac.id, ac.student_no, st.full_name as student_name, sj.name_ar as subject, s.lecture_no,
               ac.old_status, ac.new_status, ac.reason, ac.created_at
          from attendance_corrections ac
          join attendance_sessions s on s.id = ac.session_id
          join subjects sj on sj.id = s.subject_id
          join students st on st.academic_no = ac.student_no
         where s.theory_group_id = v_group and (p_subject is null or s.subject_id = p_subject)
         order by ac.id desc limit 20
      ) c
    ), '[]'::jsonb) else '[]'::jsonb end
  );
end $$;

-- تفاصيل جلسة حضور محددة مع قائمة طلاب المجموعة وحالتهم
create or replace function attendance_session_detail(p_actor bigint, p_session bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare sess attendance_sessions%rowtype;
begin
  select * into sess from attendance_sessions where id = p_session;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if not (has_perm(p_actor, 'attendance.scan', sess.theory_group_id)
       or has_perm(p_actor, 'attendance.session', sess.theory_group_id, true)
       or has_perm(p_actor, 'attendance.admin', sess.theory_group_id)) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  return jsonb_build_object(
    'status', 'ok',
    'session', jsonb_build_object(
      'id', sess.id, 'subject_id', sess.subject_id,
      'subject', (select name_ar from subjects where id = sess.subject_id),
      'group', sess.theory_group_id, 'lecture_no', sess.lecture_no,
      'status', sess.status, 'opened_at', sess.opened_at, 'closed_at', sess.closed_at,
      'delegate_absent', sess.delegate_absent
    ),
    'students', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'no', st.academic_no,
          'name', st.full_name,
          'practical', st.practical_group_id,
          'status', coalesce(ar.status, case when sess.status = 'closed' then 'absent' else 'none' end),
          'recorded_at', ar.recorded_at
        ) order by st.full_name
      )
      from students st
      left join attendance_records ar on ar.session_id = sess.id and ar.student_no = st.academic_no
      where st.theory_group_id = sess.theory_group_id and st.deleted_at is null
    ), '[]'::jsonb)
  );
end $$;

-- مصفوفة الحضور الكاملة لمادة ومجموعة نظرية (م1–م12) لتصدير Excel والتصحيح
create or replace function attendance_matrix(p_actor bigint, p_subject int, p_group text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_subj subjects%rowtype;
begin
  perform require_perm(p_actor, 'attendance.admin', p_group);
  select * into v_subj from subjects where id = p_subject and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;

  return jsonb_build_object(
    'status', 'ok',
    'subject', jsonb_build_object('id', v_subj.id, 'name', v_subj.name_ar),
    'group', p_group,
    'held_lectures', coalesce((
      select jsonb_agg(lecture_no order by lecture_no)
        from attendance_sessions
       where subject_id = p_subject and theory_group_id = p_group
    ), '[]'::jsonb),
    'students', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'academic_no', st.academic_no,
          'full_name', st.full_name,
          'practical', st.practical_group_id,
          'lectures', (
            select jsonb_agg(
              case
                when sx.id is null then null
                when rx.status = 'present' then 1
                when sx.status = 'closed' then 0
                else null
              end
              order by n
            )
            from generate_series(1, 12) n
            left join attendance_sessions sx
              on sx.subject_id = p_subject and sx.theory_group_id = p_group and sx.lecture_no = n
            left join attendance_records rx
              on rx.session_id = sx.id and rx.student_no = st.academic_no
          )
        ) order by st.academic_no
      )
      from students st
      where st.theory_group_id = p_group and st.deleted_at is null
    ), '[]'::jsonb)
  );
end $$;


-- ---------- 2) الباركود («باركود بطاقتي» Code 128) ----------
create table if not exists barcode_logs (
  id          bigint generated always as identity primary key,
  telegram_id bigint not null,
  academic_no text not null references students(academic_no),
  day_sanaa   date not null default (now() at time zone 'Asia/Aden')::date,
  created_at  timestamptz not null default now()
);
create index if not exists barcode_logs_daily_idx
  on barcode_logs (academic_no, day_sanaa);

create or replace function barcode_status(p_tg bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  s students%rowtype;
  v_limit int;
  v_used int;
begin
  perform require_perm(p_tg, 'affairs.use');
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_student'); end if;
  v_limit := coalesce((select (value #>> '{}')::int from app_settings where key = 'barcode_daily_limit'), 3);
  select count(*) into v_used
    from barcode_logs
   where academic_no = s.academic_no and day_sanaa = (now() at time zone 'Asia/Aden')::date;

  return jsonb_build_object(
    'status', 'ok',
    'academic_no', s.academic_no,
    'full_name', s.full_name,
    'theory_group_id', s.theory_group_id,
    'practical_group_id', s.practical_group_id,
    'used', v_used,
    'limit', v_limit,
    'remaining', greatest(0, v_limit - v_used)
  );
end $$;

create or replace function request_barcode(p_tg bigint, p_enqueue boolean default true) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  s students%rowtype;
  v_limit int;
  v_used int;
  v_payload jsonb;
begin
  perform require_perm(p_tg, 'affairs.use');
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_student'); end if;

  v_limit := coalesce((select (value #>> '{}')::int from app_settings where key = 'barcode_daily_limit'), 3);
  select count(*) into v_used
    from barcode_logs
   where academic_no = s.academic_no and day_sanaa = (now() at time zone 'Asia/Aden')::date;

  if v_used >= v_limit then
    return jsonb_build_object('status', 'daily_limit_reached', 'used', v_used, 'limit', v_limit, 'remaining', 0);
  end if;

  insert into barcode_logs (telegram_id, academic_no, day_sanaa)
  values (p_tg, s.academic_no, (now() at time zone 'Asia/Aden')::date);

  v_payload := jsonb_build_object(
    'academic_no', s.academic_no,
    'full_name', s.full_name,
    'theory_group_id', s.theory_group_id,
    'practical_group_id', s.practical_group_id
  );

  if p_enqueue then
    insert into outbox (kind, chat_id, payload)
    values ('barcode_send', p_tg, v_payload);
  end if;

  perform log_activity(p_tg, 'barcode.generate', 'student', s.academic_no, jsonb_build_object('used_today', v_used + 1));
  return v_payload || jsonb_build_object(
    'status', 'ok',
    'used', v_used + 1,
    'limit', v_limit,
    'remaining', greatest(0, v_limit - (v_used + 1))
  );
end $$;


-- ---------- 3) التظلمات (فتح دورة، تقديم وتعديل قبل الإغلاق، وتجميع) ----------
create table if not exists grievance_cycles (
  id               bigint generated always as identity primary key,
  subject_id       smallint not null references subjects(id),
  part             text not null check (part in ('theory','practical')),
  theory_group_id  text references theory_groups(id),       -- null = الدفعة كاملة (للمطوّر)
  practical_groups text[] not null default '{}',            -- للعملي: مجموعات محددة أو فارغ = كل عمليات المجموعة
  title            text,
  closes_at        timestamptz not null,
  status           text not null default 'open' check (status in ('open','closed')),
  closed_at        timestamptz,
  closed_by        bigint,
  summary_sent_at  timestamptz,
  created_by       bigint not null,
  created_at       timestamptz not null default now()
);
create index if not exists grievance_cycles_open_idx
  on grievance_cycles (closes_at) where status = 'open';

create table if not exists grievances (
  id         bigint generated always as identity primary key,
  cycle_id   bigint not null references grievance_cycles(id) on delete cascade,
  student_no text not null references students(academic_no),
  group_code text not null,
  reason     text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (cycle_id, student_no)
);
create index if not exists grievances_cycle_idx on grievances (cycle_id, id);

create or replace function cycle_targets_student(c grievance_cycles, s students) returns boolean
language sql immutable as $$
  select (c.theory_group_id is null or c.theory_group_id = s.theory_group_id)
     and (c.part = 'theory' or cardinality(c.practical_groups) = 0 or s.practical_group_id = any(c.practical_groups));
$$;

create or replace function open_grievance_cycle(
  p_actor bigint,
  p_subject int,
  p_part text,
  p_theory_group text,
  p_practical_groups jsonb,
  p_closes_at timestamptz,
  p_title text default null
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_id bigint;
  v_subj text;
  v_pg text[];
  v_payload jsonb;
  r record;
begin
  if p_part not in ('theory', 'practical') then
    return jsonb_build_object('status', 'invalid_part');
  end if;
  if p_theory_group is null then
    if perm_scope(p_actor, 'grievances.open') <> 'any' then
      raise exception 'forbidden' using errcode = '42501';
    end if;
  else
    if not exists (select 1 from theory_groups where id = p_theory_group) then
      return jsonb_build_object('status', 'invalid_group');
    end if;
    perform require_perm(p_actor, 'grievances.open', p_theory_group);
  end if;

  select name_ar into v_subj from subjects where id = p_subject and is_active and deleted_at is null;
  if not found then return jsonb_build_object('status', 'invalid_subject'); end if;
  if p_closes_at <= now() + interval '1 minute' then
    return jsonb_build_object('status', 'closes_in_past');
  end if;

  v_pg := case
    when p_part = 'practical' and jsonb_typeof(p_practical_groups) = 'array' then
      coalesce(array(
        select pg.code from practical_groups pg
         where pg.code in (select jsonb_array_elements_text(p_practical_groups))
           and (p_theory_group is null or pg.theory_group_id = p_theory_group)
         order by pg.sort
      ), '{}')
    else '{}'
  end;

  insert into grievance_cycles (subject_id, part, theory_group_id, practical_groups, title, closes_at, created_by)
  values (p_subject, p_part, p_theory_group, v_pg, nullif(trim(coalesce(p_title, '')), ''), p_closes_at, p_actor)
  returning id into v_id;

  v_payload := jsonb_build_object(
    'cycle_id', v_id,
    'subject', v_subj,
    'part', p_part,
    'group', coalesce(p_theory_group, 'الدفعة كاملة'),
    'practical_groups', to_jsonb(v_pg),
    'title', nullif(trim(coalesce(p_title, '')), ''),
    'closes_at', p_closes_at
  );

  for r in
    select s.telegram_id as tg
      from students s
     where s.deleted_at is null and s.telegram_id is not null
       and (p_theory_group is null or s.theory_group_id = p_theory_group)
       and (p_part = 'theory' or cardinality(v_pg) = 0 or s.practical_group_id = any(v_pg))
  loop
    perform notify_user(r.tg, 'grievance_open', v_payload, true, 'grv_open:' || v_id || ':' || r.tg);
  end loop;

  perform bump_scope('grievances');
  perform log_activity(p_actor, 'grievance_cycle.open', 'grievance_cycle', v_id::text,
    jsonb_build_object('subject', p_subject, 'part', p_part, 'group', p_theory_group));
  return jsonb_build_object('status', 'ok', 'id', v_id);
end $$;

create or replace function my_grievances(p_tg bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s students%rowtype;
begin
  perform require_perm(p_tg, 'affairs.use');
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_student', 'cycles', '[]'::jsonb); end if;

  return jsonb_build_object(
    'status', 'ok',
    'student', jsonb_build_object(
      'academic_no', s.academic_no,
      'full_name', s.full_name,
      'theory_group_id', s.theory_group_id,
      'practical_group_id', s.practical_group_id
    ),
    'cycles', coalesce((
      select jsonb_agg(to_jsonb(r) order by r.is_open desc, r.closes_at desc, r.id desc) from (
        select
          c.id,
          c.subject_id,
          sj.name_ar as subject,
          c.part,
          c.title,
          c.closes_at,
          c.status,
          (c.status = 'open' and now() <= c.closes_at) as is_open,
          case when c.part = 'theory' then s.theory_group_id else s.practical_group_id end as student_group,
          case when g.id is not null then jsonb_build_object(
            'id', g.id, 'reason', g.reason, 'group_code', g.group_code,
            'created_at', g.created_at, 'updated_at', g.updated_at
          ) end as my_grievance
        from grievance_cycles c
        join subjects sj on sj.id = c.subject_id
        left join grievances g on g.cycle_id = c.id and g.student_no = s.academic_no
        where cycle_targets_student(c, s) or g.id is not null
        order by (c.status = 'open' and now() <= c.closes_at) desc, c.closes_at desc
        limit 40
      ) r
    ), '[]'::jsonb)
  );
end $$;

-- إرسال أو تعديل التظلم قبل الإغلاق (الطالب يكتب سبب التظلم فقط، وبياناته تُؤخذ تلقائيًا)
create or replace function upsert_grievance(p_tg bigint, p_cycle bigint, p_reason text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  s students%rowtype;
  c grievance_cycles%rowtype;
  v_reason text := trim(coalesce(p_reason, ''));
  v_grp text;
  v_existed boolean;
  v_id bigint;
begin
  perform require_perm(p_tg, 'affairs.use');
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_student'); end if;

  select * into c from grievance_cycles where id = p_cycle;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if not cycle_targets_student(c, s) then return jsonb_build_object('status', 'not_target'); end if;
  if c.status <> 'open' or now() > c.closes_at then
    return jsonb_build_object('status', 'closed');
  end if;
  if char_length(v_reason) < 3 or char_length(v_reason) > 1000 then
    return jsonb_build_object('status', 'invalid_reason');
  end if;

  v_grp := case when c.part = 'theory' then s.theory_group_id else s.practical_group_id end;
  select exists (select 1 from grievances where cycle_id = c.id and student_no = s.academic_no) into v_existed;

  insert into grievances (cycle_id, student_no, group_code, reason)
  values (c.id, s.academic_no, v_grp, v_reason)
  on conflict (cycle_id, student_no) do update
     set reason = excluded.reason, group_code = excluded.group_code, updated_at = now()
  returning id into v_id;

  perform log_activity(p_tg, case when v_existed then 'grievance.edit' else 'grievance.submit' end,
    'grievance_cycle', c.id::text, jsonb_build_object('student_no', s.academic_no));
  return jsonb_build_object('status', 'ok', 'id', v_id, 'updated', v_existed);
end $$;

-- حزمة تجميع التظلمات لدورة معينة (للمندوب/المطوّر)
create or replace function grievance_cycle_package(p_cycle bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  c grievance_cycles%rowtype;
  v_subj text;
begin
  select * into c from grievance_cycles where id = p_cycle;
  if not found then return null; end if;
  select name_ar into v_subj from subjects where id = c.subject_id;
  return jsonb_build_object(
    'cycle_id', c.id,
    'subject_id', c.subject_id,
    'subject', v_subj,
    'part', c.part,
    'theory_group_id', c.theory_group_id,
    'practical_groups', to_jsonb(c.practical_groups),
    'title', c.title,
    'closes_at', c.closes_at,
    'status', c.status,
    'created_by', c.created_by,
    'items', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'no', st.academic_no,
          'name', st.full_name,
          'group', g.group_code,
          'reason', g.reason,
          'updated_at', g.updated_at
        ) order by g.group_code, st.full_name
      )
      from grievances g
      join students st on st.academic_no = g.student_no
      where g.cycle_id = c.id
    ), '[]'::jsonb)
  );
end $$;

create or replace function staff_grievance_cycles(p_actor bigint) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(to_jsonb(r) order by r.id desc), '[]'::jsonb) from (
    select c.id, c.subject_id, sj.name_ar as subject, c.part,
           coalesce(c.theory_group_id, 'الدفعة كاملة') as grp,
           c.practical_groups, c.title, c.closes_at, c.status, c.created_at,
           (select count(*) from grievances g where g.cycle_id = c.id) as count
      from grievance_cycles c join subjects sj on sj.id = c.subject_id
     where has_perm(p_actor, 'grievances.open', c.theory_group_id)
     order by c.id desc limit 50
  ) r;
$$;

create or replace function grievance_cycle_detail(p_actor bigint, p_cycle bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  c grievance_cycles%rowtype;
  pkg jsonb;
begin
  select * into c from grievance_cycles where id = p_cycle;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'grievances.open', c.theory_group_id);
  pkg := grievance_cycle_package(p_cycle);
  return jsonb_build_object('status', 'ok', 'cycle', pkg);
end $$;

create or replace function close_grievance_cycle(p_actor bigint, p_cycle bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  c grievance_cycles%rowtype;
  pkg jsonb;
begin
  select * into c from grievance_cycles where id = p_cycle for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'grievances.open', c.theory_group_id);
  update grievance_cycles
     set status = 'closed', closed_at = coalesce(closed_at, now()), closed_by = coalesce(closed_by, p_actor),
         summary_sent_at = coalesce(summary_sent_at, now())
   where id = p_cycle;

  pkg := grievance_cycle_package(p_cycle);
  perform notify_user(p_actor, 'grievance_compiled', pkg, true, 'grv_comp:' || p_cycle || ':' || p_actor);
  perform bump_scope('grievances');
  perform log_activity(p_actor, 'grievance_cycle.close', 'grievance_cycle', p_cycle::text, null);
  return jsonb_build_object('status', 'ok', 'cycle', pkg);
end $$;


-- ---------- 4) الدعم والمساعد الذكي والبلاغات ----------
create table if not exists assistant_logs (
  id          bigint generated always as identity primary key,
  telegram_id bigint not null,
  day_sanaa   date not null default (now() at time zone 'Asia/Aden')::date,
  question    text not null,
  answer      text not null,
  created_at  timestamptz not null default now()
);
create index if not exists assistant_logs_user_day_idx
  on assistant_logs (telegram_id, day_sanaa, id desc);

create table if not exists support_tickets (
  id          bigint generated always as identity primary key,
  telegram_id bigint not null,
  category    text not null check (category in ('bug','complaint','inquiry')),
  body        text not null,
  status      text not null default 'open' check (status in ('open','replied','closed')),
  reply_text  text,
  replied_by  bigint,
  replied_at  timestamptz,
  created_at  timestamptz not null default now()
);
create index if not exists support_tickets_user_idx on support_tickets (telegram_id, id desc);
create index if not exists support_tickets_status_idx on support_tickets (status, id desc);

create or replace function assistant_quota(p_tg bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_role text := base_role(p_tg);
  v_limit int;
  v_used int;
begin
  perform require_perm(p_tg, 'assistant.use');
  if is_developer(p_tg) then
    v_limit := 50;
  elsif v_role = 'student' then
    v_limit := coalesce((select (value #>> '{}')::int from app_settings where key = 'assistant_daily_student'), 10);
  else
    v_limit := coalesce((select (value #>> '{}')::int from app_settings where key = 'assistant_daily_guest'), 5);
  end if;

  select count(*) into v_used
    from assistant_logs
   where telegram_id = p_tg and day_sanaa = (now() at time zone 'Asia/Aden')::date;

  return jsonb_build_object(
    'role', coalesce(v_role, 'guest'),
    'used', v_used,
    'limit', v_limit,
    'remaining', greatest(0, v_limit - v_used)
  );
end $$;

-- يجمع سياق المساعد الذكي (حصة الرسائل + بيانات الطالب نفسه + سجل محادثة اليوم)
create or replace function assistant_context(p_tg bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  q jsonb;
  s students%rowtype;
begin
  q := assistant_quota(p_tg);
  select * into s from students where telegram_id = p_tg and deleted_at is null;

  return q || jsonb_build_object(
    'student', case when s.academic_no is not null then jsonb_build_object(
      'academic_no', s.academic_no,
      'full_name', s.full_name,
      'theory_group_id', s.theory_group_id,
      'practical_group_id', s.practical_group_id
    ) end,
    'assignments', case when s.academic_no is not null then my_assignments(p_tg, 15) else '[]'::jsonb end,
    'attendance', case when s.academic_no is not null then my_attendance(p_tg) else '[]'::jsonb end,
    'grievances', case when s.academic_no is not null then (my_grievances(p_tg)->'cycles') else '[]'::jsonb end,
    'subjects', coalesce((
      select jsonb_agg(jsonb_build_object('id', id, 'name', name_ar, 'tag', hashtag) order by sort, id)
        from subjects where is_active and deleted_at is null
    ), '[]'::jsonb),
    'history', coalesce((
      select jsonb_agg(to_jsonb(h) order by h.id asc) from (
        select id, question, answer, created_at
          from assistant_logs
         where telegram_id = p_tg
         order by id desc limit 8
      ) h
    ), '[]'::jsonb)
  );
end $$;

create or replace function record_assistant_exchange(p_tg bigint, p_question text, p_answer text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  q jsonb;
begin
  q := assistant_quota(p_tg);
  if (q->>'remaining')::int <= 0 then
    return q || jsonb_build_object('status', 'limit_reached');
  end if;
  insert into assistant_logs (telegram_id, day_sanaa, question, answer)
  values (p_tg, (now() at time zone 'Asia/Aden')::date, left(trim(p_question), 600), left(trim(p_answer), 2000));
  return assistant_quota(p_tg) || jsonb_build_object('status', 'ok');
end $$;

-- بحث الملفات الذكي: يجلب الملفات المرشّحة ليُرتّبها محرك البحث الذكي بالكلمات المفتاحية
create or replace function search_files_smart(
  p_tg bigint,
  p_subject int default null,
  p_part text default null,
  p_type text default null,
  p_exam_kind text default null,
  p_numbers smallint[] default null
) returns jsonb language plpgsql stable security definer set search_path = public as $$
begin
  perform require_perm(p_tg, 'files.view');
  return coalesce((
    select jsonb_agg(to_jsonb(r) order by r.created_at desc, r.id desc) from (
      select f.id, f.subject_id, sj.name_ar as subject, f.part, f.content_type, f.exam_kind,
             f.label_kind, f.numbers, f.description, f.file_name, f.created_at,
             d.name as doctor_name
        from files f
        join subjects sj on sj.id = f.subject_id
        left join doctors d on d.id = f.doctor_id
       where f.deleted_at is null
         and (p_subject is null or f.subject_id = p_subject)
         and (p_part is null or f.part = p_part)
         and (p_type is null or f.content_type = p_type)
         and (p_exam_kind is null or f.exam_kind = p_exam_kind)
         and (p_numbers is null or cardinality(p_numbers) = 0 or f.numbers && p_numbers)
       order by f.created_at desc, f.id desc limit 60
    ) r
  ), '[]'::jsonb);
end $$;

-- البلاغات والاستفسارات والشكاوى
create or replace function create_ticket(p_tg bigint, p_category text, p_body text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_body text := trim(coalesce(p_body, ''));
  v_id bigint;
  u users%rowtype;
  s students%rowtype;
  v_payload jsonb;
  dev record;
begin
  perform require_perm(p_tg, 'assistant.use');
  if p_category not in ('bug', 'complaint', 'inquiry') then
    return jsonb_build_object('status', 'invalid_category');
  end if;
  if char_length(v_body) < 4 or char_length(v_body) > 1500 then
    return jsonb_build_object('status', 'invalid_body');
  end if;

  insert into support_tickets (telegram_id, category, body)
  values (p_tg, p_category, v_body)
  returning id into v_id;

  select * into u from users where telegram_id = p_tg;
  select * into s from students where telegram_id = p_tg and deleted_at is null;

  v_payload := jsonb_build_object(
    'ticket_id', v_id,
    'category', p_category,
    'body', v_body,
    'sender_id', p_tg,
    'sender_name', coalesce(s.full_name, u.display_name, 'مستخدم'),
    'academic_no', s.academic_no,
    'group', s.theory_group_id
  );

  for dev in
    select distinct telegram_id as tg from role_grants where role = 'developer' and revoked_at is null
  loop
    perform notify_user(dev.tg, 'ticket_new', v_payload, true, 'tkt_new:' || v_id || ':' || dev.tg);
  end loop;

  perform bump_scope('tickets');
  perform log_activity(p_tg, 'ticket.create', 'ticket', v_id::text, jsonb_build_object('category', p_category));
  return jsonb_build_object('status', 'ok', 'id', v_id);
end $$;

create or replace function my_tickets(p_tg bigint) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(to_jsonb(r) order by r.id desc), '[]'::jsonb) from (
    select id, category, body, status, reply_text, created_at, replied_at
      from support_tickets
     where telegram_id = p_tg
     order by id desc limit 30
  ) r;
$$;

create or replace function list_tickets(p_actor bigint, p_status text default null, p_limit int default 50) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform require_perm(p_actor, 'tickets.reply');
  return coalesce((
    select jsonb_agg(to_jsonb(r) order by (r.status = 'open') desc, r.id desc) from (
      select t.id, t.telegram_id, t.category, t.body, t.status, t.reply_text, t.created_at, t.replied_at,
             coalesce(s.full_name, u.display_name, 'مستخدم') as sender_name,
             u.username,
             coalesce(u.kind, 'guest') as sender_kind,
             s.academic_no,
             s.theory_group_id,
             s.practical_group_id
        from support_tickets t
        left join users u on u.telegram_id = t.telegram_id
        left join students s on s.telegram_id = t.telegram_id and s.deleted_at is null
       where p_status is null or t.status = p_status
       order by (t.status = 'open') desc, t.id desc
       limit least(p_limit, 100)
    ) r
  ), '[]'::jsonb);
end $$;

create or replace function reply_ticket(p_actor bigint, p_ticket_id bigint, p_reply text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  t support_tickets%rowtype;
  v_reply text := trim(coalesce(p_reply, ''));
begin
  perform require_perm(p_actor, 'tickets.reply');
  if char_length(v_reply) < 2 or char_length(v_reply) > 1500 then
    return jsonb_build_object('status', 'invalid_reply');
  end if;

  select * into t from support_tickets where id = p_ticket_id for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;

  update support_tickets
     set status = 'replied', reply_text = v_reply, replied_by = p_actor, replied_at = now()
   where id = p_ticket_id;

  perform notify_user(
    t.telegram_id,
    'ticket_reply',
    jsonb_build_object('ticket_id', t.id, 'category', t.category, 'body', left(t.body, 120), 'reply', v_reply),
    true,
    'tkt_rep:' || t.id || ':' || extract(epoch from now())::bigint
  );

  perform bump_scope('tickets');
  perform log_activity(p_actor, 'ticket.reply', 'ticket', p_ticket_id::text, null);
  return jsonb_build_object('status', 'ok', 'id', t.id, 'telegram_id', t.telegram_id);
end $$;


-- ---------- 5) تحديث ملخص الرئيسية والمهام الدورية ----------
create or replace function home_summary(p_tg bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  s students%rowtype;
  v_asg jsonb;
begin
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  v_asg := case when s.academic_no is not null then my_assignments(p_tg, 40) else '[]'::jsonb end;

  return jsonb_build_object(
    'unread', (select count(*) from notifications where telegram_id = p_tg and read_at is null),
    'items', coalesce((
      select jsonb_agg(x) from (
        select r from jsonb_array_elements(v_asg) r
         where r->>'status' = 'open' and (r->>'submitted_at') is null and (r->>'due_at')::timestamptz > now()
         order by (r->>'due_at')::timestamptz limit 3
      ) y(x)
    ), '[]'::jsonb),
    'missed', '[]'::jsonb,
    'grievances', case when s.academic_no is not null then coalesce((
      select jsonb_agg(to_jsonb(g) order by g.closes_at asc) from (
        select c.id, sj.name_ar as subject, c.part, c.title, c.closes_at,
               exists (select 1 from grievances gr where gr.cycle_id = c.id and gr.student_no = s.academic_no) as submitted
          from grievance_cycles c join subjects sj on sj.id = c.subject_id
         where c.status = 'open' and c.closes_at > now() and cycle_targets_student(c, s)
         order by c.closes_at asc limit 2
      ) g
    ), '[]'::jsonb) else '[]'::jsonb end
  );
end $$;

create or replace function run_due_work() returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_closed int;
  v_rem int := 0;
  v_grv_closed int := 0;
  r record;
  s record;
  gc record;
  v_a assignments%rowtype;
  v_new boolean;
  v_payload jsonb;
  v_pkg jsonb;
begin
  with c as (update assignments set status = 'closed', closed_at = now()
              where status = 'open' and deleted_at is null and closes_at <= now() returning 1)
  select count(*) into v_closed from c;

  -- لكل واجب مفتوح: أضيق نافذة تذكير (24/3/1 ساعة) نحن داخلها الآن؛ نرسل واحدًا فقط ونُعلّم الأوسع كمُرسَل
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

  -- إغلاق دورات التظلمات المنتهية آليًا وإرسال التجميع للمندوب/المطوّر الذي فتحها
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

  return jsonb_build_object('closed', v_closed, 'reminders', v_rem, 'grievances_closed', v_grv_closed);
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
