-- =====================================================================
-- 0002_files_assignments.sql — المرحلة 2: الملفات والأرشفة، الواجبات، الإشعارات
-- شغّله بعد 0001. آمن لإعادة التشغيل. الصلاحيات تُفحص هنا (المصدر: role_permissions).
-- =====================================================================

alter table users add column if not exists bot_blocked_at timestamptz;

-- ---------- إعدادات الطالب ----------
create table if not exists user_settings (
  telegram_id       bigint primary key,
  notify_assignment boolean not null default true,
  notify_sharek     boolean not null default true,
  remind_24         boolean not null default true,
  remind_3          boolean not null default true,
  remind_1          boolean not null default false,
  updated_at        timestamptz not null default now()
);

-- ---------- دوال الصلاحيات ----------
create or replace function base_role(p_tg bigint) returns text
language sql stable security definer set search_path = public as $$
  select case
    when exists (select 1 from doctors  where telegram_id = p_tg and is_active and deleted_at is null) then 'doctor'
    when exists (select 1 from students where telegram_id = p_tg and deleted_at is null)               then 'student'
    when exists (select 1 from users    where telegram_id = p_tg and kind = 'guest')                   then 'guest'
    else null end;
$$;

create or replace function theory_of(p_group text) returns text
language sql stable security definer set search_path = public as $$
  select coalesce((select theory_group_id from practical_groups where code = p_group),
                  (select id from theory_groups where id = p_group));
$$;

create or replace function has_perm(p_tg bigint, p_key text, p_group text default null, p_delegate_absent boolean default false)
returns boolean language sql stable security definer set search_path = public as $$
  select not exists (select 1 from users where telegram_id = p_tg and is_banned)
     and exists (
    select 1 from (
      select rp.scope, null::text as g, rp.condition
        from role_permissions rp where rp.permission = p_key and rp.role = base_role(p_tg)
      union all
      select rp.scope, gr.theory_group_id, rp.condition
        from role_grants gr join role_permissions rp on rp.role = gr.role
       where gr.telegram_id = p_tg and gr.revoked_at is null and rp.permission = p_key
    ) e
    where (e.condition is null or (e.condition = 'delegate_absent' and p_delegate_absent))
      and (e.scope in ('any','own') or (e.scope = 'group' and (p_group is null or e.g = p_group)))
  );
$$;

-- أوسع نطاق يملكه المستخدم لهذه الصلاحية: any > group > own
create or replace function perm_scope(p_tg bigint, p_key text) returns text
language sql stable security definer set search_path = public as $$
  select (array_agg(s order by case s when 'any' then 1 when 'group' then 2 else 3 end))[1] from (
    select rp.scope as s from role_permissions rp
     where rp.permission = p_key and rp.role = base_role(p_tg) and rp.condition is null
    union all
    select rp.scope from role_grants gr join role_permissions rp on rp.role = gr.role
     where gr.telegram_id = p_tg and gr.revoked_at is null and rp.permission = p_key and rp.condition is null
  ) x;
$$;

create or replace function require_perm(p_tg bigint, p_key text, p_group text default null) returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not has_perm(p_tg, p_key, p_group) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
end $$;

create or replace function grace_interval() returns interval
language sql stable security definer set search_path = public as $$
  select make_interval(hours => coalesce((select (value #>> '{}')::int from app_settings where key = 'assignment_grace_hours'), 12));
$$;

-- ---------- الإشعارات وطابور الإرسال ----------
create table if not exists notifications (
  id          bigint generated always as identity primary key,
  telegram_id bigint not null,
  kind        text not null,
  payload     jsonb not null default '{}',
  created_at  timestamptz not null default now(),
  read_at     timestamptz
);
create index if not exists notifications_user_idx on notifications (telegram_id, id desc);

create table if not exists outbox (
  id              bigint generated always as identity primary key,
  kind            text not null,
  chat_id         bigint not null,
  topic_id        integer,
  payload         jsonb not null default '{}',
  status          text not null default 'pending' check (status in ('pending','sending','sent','failed','blocked')),
  attempts        smallint not null default 0,
  next_attempt_at timestamptz not null default now(),
  claimed_at      timestamptz,
  last_error      text,
  dedupe_key      text unique,
  created_at      timestamptz not null default now(),
  sent_at         timestamptz,
  message_id      integer
);
create index if not exists outbox_work_idx on outbox (next_attempt_at, id) where status in ('pending','sending');

-- إشعار داخل التطبيق دائمًا، ودفع في البوت عند p_push (ما لم يكن المستخدم أوقف البوت)
create or replace function notify_user(p_tg bigint, p_kind text, p_payload jsonb, p_push boolean, p_dedupe text default null)
returns void language plpgsql security definer set search_path = public as $$
begin
  insert into notifications (telegram_id, kind, payload) values (p_tg, p_kind, p_payload);
  if p_push and exists (select 1 from users where telegram_id = p_tg and not is_banned and bot_blocked_at is null) then
    insert into outbox (kind, chat_id, payload, dedupe_key) values (p_kind, p_tg, p_payload, p_dedupe)
    on conflict (dedupe_key) do nothing;
  end if;
end $$;

create or replace function claim_outbox(p_limit int default 25) returns jsonb
language sql security definer set search_path = public as $$
  with picked as (
    select id from outbox
     where (status = 'pending' and next_attempt_at <= now())
        or (status = 'sending' and claimed_at < now() - interval '2 minutes')
     order by id limit p_limit for update skip locked),
  upd as (
    update outbox o set status = 'sending', attempts = o.attempts + 1, claimed_at = now()
      from picked where o.id = picked.id returning o.*)
  select coalesce(jsonb_agg(to_jsonb(upd) order by upd.id), '[]'::jsonb) from upd;
$$;

create or replace function finish_outbox_batch(p_results jsonb) returns void
language plpgsql security definer set search_path = public as $$
declare r jsonb; v_status text; v_row outbox%rowtype;
begin
  for r in select * from jsonb_array_elements(p_results) loop
    select * into v_row from outbox where id = (r->>'id')::bigint;
    if not found then continue; end if;
    v_status := r->>'status';
    if v_status = 'sent' then
      update outbox set status = 'sent', sent_at = now(), message_id = nullif(r->>'mid', '')::int, last_error = null where id = v_row.id;
    elsif v_status = 'retry' and v_row.attempts < 6 then
      update outbox set status = 'pending', next_attempt_at = now() + make_interval(secs => coalesce((r->>'delay')::int, 5)),
             last_error = r->>'error' where id = v_row.id;
    elsif v_status = 'blocked' then
      update outbox set status = 'blocked', last_error = r->>'error' where id = v_row.id;
      if v_row.chat_id > 0 then update users set bot_blocked_at = now() where telegram_id = v_row.chat_id; end if;
    else
      update outbox set status = 'failed', last_error = coalesce(r->>'error', 'failed') where id = v_row.id;
    end if;
  end loop;
end $$;

create or replace function unblock_user(p_tg bigint) returns void
language sql security definer set search_path = public as $$
  update users set bot_blocked_at = null where telegram_id = p_tg and bot_blocked_at is not null;
$$;

create or replace function list_notifications(p_tg bigint, p_limit int default 30, p_before bigint default null) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(to_jsonb(r) order by r.id desc), '[]'::jsonb) from (
    select id, kind, payload, created_at, read_at from notifications
     where telegram_id = p_tg and (p_before is null or id < p_before)
     order by id desc limit least(p_limit, 50)) r;
$$;

create or replace function mark_notifications_read(p_tg bigint) returns void
language sql security definer set search_path = public as $$
  update notifications set read_at = now() where telegram_id = p_tg and read_at is null;
$$;

create or replace function get_user_settings(p_tg bigint) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce((select to_jsonb(u) - 'telegram_id' - 'updated_at' from user_settings u where telegram_id = p_tg),
    jsonb_build_object('notify_assignment', true, 'notify_sharek', true, 'remind_24', true, 'remind_3', true, 'remind_1', false));
$$;

create or replace function set_user_settings(p_tg bigint, p_patch jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  insert into user_settings (telegram_id) values (p_tg) on conflict do nothing;
  update user_settings set
    notify_assignment = coalesce((p_patch->>'notify_assignment')::boolean, notify_assignment),
    notify_sharek     = coalesce((p_patch->>'notify_sharek')::boolean, notify_sharek),
    remind_24         = coalesce((p_patch->>'remind_24')::boolean, remind_24),
    remind_3          = coalesce((p_patch->>'remind_3')::boolean, remind_3),
    remind_1          = coalesce((p_patch->>'remind_1')::boolean, remind_1),
    updated_at = now()
  where telegram_id = p_tg;
  return get_user_settings(p_tg);
end $$;

-- ---------- المسودات (الخطوات التي تبدأ في التطبيق وتنتهي برفع الملف في البوت) ----------
create table if not exists drafts (
  id          bigint generated always as identity primary key,
  kind        text not null check (kind in ('file','assignment')),
  owner_id    bigint not null,
  payload     jsonb not null,
  attachments jsonb not null default '[]',
  state       text not null default 'open' check (state in ('open','done','cancelled')),
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null default now() + interval '1 day'
);
create index if not exists drafts_owner_idx on drafts (owner_id, kind, id desc) where state = 'open';

-- ---------- الملفات ----------
create table if not exists files (
  id            bigint generated always as identity primary key,
  subject_id    smallint not null references subjects(id),
  part          text not null check (part in ('theory','practical')),
  doctor_id     smallint references doctors(id),
  content_type  text not null check (content_type in ('lecture','summary','exam','exercises','translation','reference','benefit')),
  exam_kind     text check (exam_kind in ('midterm','final')),
  label_kind    text check (label_kind in ('lecture','lab','module','chapter')),
  numbers       smallint[] not null default '{}',
  description   text,
  caption       text,
  media_kind    text not null,
  tg_file_id    text not null,
  tg_unique_id  text,
  file_name     text,
  mime          text,
  size          bigint,
  chat_id       bigint,
  topic_id      integer,
  message_id    integer,
  uploaded_by   bigint not null,
  created_at    timestamptz not null default now(),
  deleted_at    timestamptz,
  deleted_by    bigint,
  check (content_type <> 'exam' or exam_kind is not null),
  check (content_type not in ('exercises','summary','translation') or cardinality(numbers) > 0),
  check (cardinality(numbers) = 0 or label_kind is not null),
  check (numbers <@ '{1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20}'::smallint[])
);
create index if not exists files_browse_idx on files (subject_id, part, content_type, created_at desc) where deleted_at is null;
create index if not exists files_unique_idx on files (tg_unique_id) where deleted_at is null;

-- الدكتور بحسب اختيار الرافع (عند تعدد المدرسين) أو تلقائيًا بحسب المادة والجزء (الأكثر تدريسًا ثم الأقل رقمًا)
create or replace function resolve_doctor(p_subject int, p_part text, p_doctor int default null) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(
    (select to_jsonb(x) from (
       select d.id, d.name from teaching t join doctors d on d.id = t.doctor_id
        where p_doctor is not null and d.id = p_doctor and t.subject_id = p_subject and t.part = p_part
          and d.is_active and d.deleted_at is null
        limit 1) x),
    (select to_jsonb(x) from (
       select d.id, d.name from teaching t join doctors d on d.id = t.doctor_id
        where t.subject_id = p_subject and t.part = p_part and d.is_active and d.deleted_at is null
        group by d.id, d.name order by count(*) desc, d.id limit 1) x)
  );
$$;

create or replace function create_draft(p_actor bigint, p_kind text, p_payload jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_id bigint; v_theory text; v_old files%rowtype;
begin
  if p_kind = 'file' then
    perform require_perm(p_actor, 'files.upload');
    if not exists (select 1 from subjects where id = (p_payload->>'subject_id')::smallint and is_active and deleted_at is null) then
      return jsonb_build_object('status', 'invalid_subject');
    end if;
    if p_payload ? 'replaces_id' then
      select * into v_old from files where id = (p_payload->>'replaces_id')::bigint and deleted_at is null;
      if not found then return jsonb_build_object('status', 'not_found'); end if;
      if perm_scope(p_actor, 'files.delete') = 'own' and v_old.uploaded_by <> p_actor then
        raise exception 'forbidden' using errcode = '42501';
      end if;
    end if;
  elsif p_kind = 'assignment' then
    v_theory := theory_of(p_payload->>'group');
    if v_theory is null then return jsonb_build_object('status', 'invalid_group'); end if;
    perform require_perm(p_actor, 'assignments.create', v_theory);
    if not exists (select 1 from subjects where id = (p_payload->>'subject_id')::smallint and is_active and deleted_at is null) then
      return jsonb_build_object('status', 'invalid_subject');
    end if;
    if (p_payload->>'due_at')::timestamptz <= now() + interval '1 minute' then
      return jsonb_build_object('status', 'due_in_past');
    end if;
  else
    return jsonb_build_object('status', 'invalid_kind');
  end if;
  update drafts set state = 'cancelled' where owner_id = p_actor and kind = p_kind and state = 'open';
  insert into drafts (kind, owner_id, payload) values (p_kind, p_actor, p_payload) returning id into v_id;
  return jsonb_build_object('status', 'ok', 'draft_id', v_id);
end $$;

-- يجمع كل ما يحتاجه البوت لبناء الرسالة قبل نشر الملف في المجتمع
create or replace function prepare_file(p_actor bigint, p_unique text, p_draft bigint default null) returns jsonb
language plpgsql security definer set search_path = public as $$
declare d drafts%rowtype; v_s subjects%rowtype; v_dup bigint;
begin
  perform require_perm(p_actor, 'files.upload');
  select * into d from drafts
   where owner_id = p_actor and kind = 'file' and state = 'open' and expires_at > now()
     and (p_draft is null or id = p_draft)
   order by id desc limit 1;
  if not found then return jsonb_build_object('status', 'no_draft'); end if;
  select * into v_s from subjects where id = (d.payload->>'subject_id')::smallint;
  select id into v_dup from files where tg_unique_id = p_unique and deleted_at is null and subject_id = v_s.id limit 1;
  return jsonb_build_object('status', 'ok', 'draft_id', d.id, 'payload', d.payload,
    'subject', jsonb_build_object('id', v_s.id, 'name', v_s.name_ar, 'tag', v_s.hashtag),
    'doctor', resolve_doctor(v_s.id, d.payload->>'part', nullif(d.payload->>'doctor_id', '')::int), 'duplicate', v_dup);
end $$;

create or replace function register_file(p_actor bigint, p_draft bigint, p_media jsonb, p_post jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare d drafts%rowtype; pl jsonb; v_id bigint; v_doc smallint; v_old files%rowtype;
begin
  perform require_perm(p_actor, 'files.upload');
  select * into d from drafts where id = p_draft and owner_id = p_actor and kind = 'file' and state = 'open' for update;
  if not found then return jsonb_build_object('status', 'no_draft'); end if;
  pl := d.payload;
  v_doc := (resolve_doctor((pl->>'subject_id')::int, pl->>'part', nullif(pl->>'doctor_id', '')::int)->>'id')::smallint;
  insert into files (subject_id, part, doctor_id, content_type, exam_kind, label_kind, numbers, description, caption,
                     media_kind, tg_file_id, tg_unique_id, file_name, mime, size, chat_id, topic_id, message_id, uploaded_by)
  values ((pl->>'subject_id')::smallint, pl->>'part', v_doc, pl->>'content_type', nullif(pl->>'exam_kind', ''), nullif(pl->>'label_kind', ''),
          coalesce(array(select x::smallint from jsonb_array_elements_text(coalesce(pl->'numbers', '[]'::jsonb)) x order by 1), '{}'),
          nullif(pl->>'description', ''), p_post->>'caption',
          p_media->>'media_kind', p_media->>'file_id', p_media->>'unique_id', p_media->>'name', p_media->>'mime', (p_media->>'size')::bigint,
          (p_post->>'chat_id')::bigint, (p_post->>'topic_id')::int, (p_post->>'message_id')::int, p_actor)
  returning id into v_id;
  update drafts set state = 'done' where id = d.id;
  if pl ? 'replaces_id' then
    update files set deleted_at = now(), deleted_by = p_actor
     where id = (pl->>'replaces_id')::bigint and deleted_at is null returning * into v_old;
  end if;
  perform log_activity(p_actor, 'file.upload', 'file', v_id::text, jsonb_build_object('subject', pl->>'subject_id', 'type', pl->>'content_type'));
  return jsonb_build_object('status', 'ok', 'file_id', v_id,
    'old', case when v_old.id is not null and v_old.message_id is not null
           then jsonb_build_object('chat_id', v_old.chat_id, 'message_id', v_old.message_id) end);
end $$;

create or replace function list_files(p_tg bigint, p_subject int default null, p_part text default null,
                                      p_type text default null, p_limit int default 20, p_offset int default 0) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_scope text;
begin
  perform require_perm(p_tg, 'files.view');
  v_scope := perm_scope(p_tg, 'files.delete');
  return coalesce((select jsonb_agg(to_jsonb(r) order by r.created_at desc, r.id desc) from (
    select f.id, f.subject_id, f.part, f.content_type, f.exam_kind, f.label_kind, f.numbers, f.description, f.created_at,
           (v_scope = 'any' or (v_scope = 'own' and f.uploaded_by = p_tg)) as can_delete,
           (f.uploaded_by = p_tg) as mine
      from files f
     where f.deleted_at is null
       and (p_subject is null or f.subject_id = p_subject)
       and (p_part is null or f.part = p_part)
       and (p_type is null or f.content_type = p_type)
     order by f.created_at desc, f.id desc limit least(p_limit, 50) offset greatest(p_offset, 0)) r), '[]'::jsonb);
end $$;

create or replace function file_facets(p_tg bigint, p_subject int) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform require_perm(p_tg, 'files.view');
  return coalesce((select jsonb_agg(jsonb_build_object('part', part, 'content_type', content_type, 'n', n) order by part desc, content_type) from (
    select part, content_type, count(*) as n from files where deleted_at is null and subject_id = p_subject group by 1, 2) x), '[]'::jsonb);
end $$;

create or replace function get_file(p_tg bigint, p_id bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare f files%rowtype;
begin
  perform require_perm(p_tg, 'files.view');
  select * into f from files where id = p_id and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  return jsonb_build_object('status', 'ok', 'src_chat', f.chat_id, 'src_msg', f.message_id,
                            'media_kind', f.media_kind, 'file_id', f.tg_file_id);
end $$;

-- إرسال الملف للطالب في البوت (من التطبيق): يدخل طابور الإرسال
create or replace function request_file_send(p_tg bigint, p_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v jsonb;
begin
  v := get_file(p_tg, p_id);
  if v->>'status' <> 'ok' then return v; end if;
  insert into outbox (kind, chat_id, payload) values ('file_send', p_tg, v - 'status');
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function delete_file(p_actor bigint, p_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare f files%rowtype; v_scope text;
begin
  perform require_perm(p_actor, 'files.delete');
  v_scope := perm_scope(p_actor, 'files.delete');
  select * into f from files where id = p_id and deleted_at is null for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if v_scope = 'own' and f.uploaded_by <> p_actor then raise exception 'forbidden' using errcode = '42501'; end if;
  update files set deleted_at = now(), deleted_by = p_actor where id = p_id;
  if f.chat_id is not null and f.message_id is not null then
    insert into outbox (kind, chat_id, payload) values ('tg_delete', f.chat_id, jsonb_build_object('message_id', f.message_id));
  end if;
  perform log_activity(p_actor, 'file.delete', 'file', p_id::text, null);
  return jsonb_build_object('status', 'ok');
end $$;

-- استرجاع ملف من السلة: يُعاد نشره في موضوعه عبر الطابور
create or replace function restore_file(p_actor bigint, p_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare f files%rowtype; d tg_destinations%rowtype;
begin
  perform require_perm(p_actor, 'files.delete');
  select * into f from files where id = p_id and deleted_at is not null for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if perm_scope(p_actor, 'files.delete') = 'own' and f.uploaded_by <> p_actor then raise exception 'forbidden' using errcode = '42501'; end if;
  update files set deleted_at = null, deleted_by = null, message_id = null where id = p_id;
  select * into d from tg_destinations where key = 'cohort:subject:' || f.subject_id;
  if found then
    insert into outbox (kind, chat_id, topic_id, payload, dedupe_key)
    values ('file_repost', d.chat_id, d.topic_id,
            jsonb_build_object('file_row', p_id, 'media_kind', f.media_kind, 'file_id', f.tg_file_id, 'caption', f.caption),
            'repost:' || p_id || ':' || extract(epoch from now())::bigint);
  end if;
  perform log_activity(p_actor, 'file.restore', 'file', p_id::text, null);
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function set_file_message(p_id bigint, p_chat bigint, p_topic int, p_msg int) returns void
language sql security definer set search_path = public as $$
  update files set chat_id = p_chat, topic_id = p_topic, message_id = p_msg where id = p_id;
$$;

-- ---------- الواجبات ----------
create table if not exists assignments (
  id                 bigint generated always as identity primary key,
  subject_id         smallint not null references subjects(id),
  part               text not null check (part in ('theory','practical')),
  theory_group_id    text references theory_groups(id),
  practical_group_id text references practical_groups(code),
  number             int not null,
  description        text,
  due_at             timestamptz not null,
  closes_at          timestamptz not null,           -- الموعد + المهلة الاحتياطية (داخلية، لا تظهر لأحد)
  status             text not null default 'open' check (status in ('open','closed')),
  closed_at          timestamptz,
  closed_by          bigint,
  doctor_id          smallint references doctors(id),
  created_by         bigint not null,
  created_at         timestamptz not null default now(),
  deleted_at         timestamptz,
  deleted_by         bigint,
  check ((part = 'theory'    and theory_group_id    is not null and practical_group_id is null)
      or (part = 'practical' and practical_group_id is not null and theory_group_id    is null))
);
create unique index if not exists assignments_number_uq
  on assignments (subject_id, part, coalesce(theory_group_id, practical_group_id), number);
create index if not exists assignments_open_idx on assignments (closes_at) where status = 'open' and deleted_at is null;

create table if not exists assignment_files (
  id            bigint generated always as identity primary key,
  assignment_id bigint not null references assignments(id) on delete cascade,
  media_kind    text not null,
  tg_file_id    text not null,
  file_name     text,
  position      int not null default 1
);

create table if not exists submissions (
  id             bigint generated always as identity primary key,
  assignment_id  bigint not null references assignments(id),
  student_no     text not null references students(academic_no),
  submitted_at   timestamptz,
  updated_at     timestamptz not null default now(),
  is_late        boolean not null default false,
  revision       int not null default 0,
  received_at    timestamptz,
  received_by    bigint,
  delivery_state text not null default 'none' check (delivery_state in ('none','pending','done')),
  unique (assignment_id, student_no)
);
create index if not exists submissions_pending_idx on submissions (updated_at) where delivery_state = 'pending';

create table if not exists submission_files (
  id            bigint generated always as identity primary key,
  submission_id bigint not null references submissions(id) on delete cascade,
  media_kind    text not null,
  tg_file_id    text not null,
  tg_unique_id  text,
  file_name     text,
  mime          text,
  size          bigint,
  position      int not null default 1,
  created_at    timestamptz not null default now(),
  finalized_at  timestamptz,                 -- null = لم يُنهَ التسليم بعد
  superseded_at timestamptz                  -- استُبدل بتسليم أحدث (نحتفظ به للتاريخ)
);
create index if not exists submission_files_sub_idx on submission_files (submission_id);

create table if not exists submission_deliveries (
  submission_id bigint not null references submissions(id) on delete cascade,
  chat_id       bigint not null,
  message_id    integer not null,
  primary key (chat_id, message_id)
);

create table if not exists reminders_sent (
  assignment_id bigint not null references assignments(id) on delete cascade,
  hours         smallint not null,
  sent_at       timestamptz not null default now(),
  primary key (assignment_id, hours)
);

create or replace function targets_student(a assignments, s students) returns boolean
language sql immutable as $$
  select (a.part = 'theory' and a.theory_group_id = s.theory_group_id)
      or (a.part = 'practical' and a.practical_group_id = s.practical_group_id);
$$;

create or replace function add_draft_attachment(p_actor bigint, p_draft bigint, p_file jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  update drafts set attachments = attachments || jsonb_build_array(p_file)
   where id = p_draft and owner_id = p_actor and kind = 'assignment' and state = 'open' and jsonb_array_length(attachments) < 10
  returning jsonb_array_length(attachments) into n;
  if n is null then return jsonb_build_object('status', 'no_draft'); end if;
  return jsonb_build_object('status', 'ok', 'count', n);
end $$;

create or replace function publish_assignment(p_actor bigint, p_draft bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  d drafts%rowtype; pl jsonb; v_part text; v_group text; v_theory text; v_n int; v_id bigint;
  v_due timestamptz; v_doc smallint; v_subject text; r record; v_payload jsonb; v_dest tg_destinations%rowtype;
begin
  select * into d from drafts where id = p_draft and owner_id = p_actor and kind = 'assignment' and state = 'open' for update;
  if not found then return jsonb_build_object('status', 'no_draft'); end if;
  pl := d.payload; v_part := pl->>'part'; v_group := pl->>'group'; v_theory := theory_of(v_group);
  perform require_perm(p_actor, 'assignments.create', v_theory);
  v_due := (pl->>'due_at')::timestamptz;
  if v_due <= now() then return jsonb_build_object('status', 'due_in_past'); end if;

  perform pg_advisory_xact_lock(hashtext('asg:' || (pl->>'subject_id') || v_part || v_group));
  select coalesce(max(number), 0) + 1 into v_n from assignments
   where subject_id = (pl->>'subject_id')::smallint and part = v_part and coalesce(theory_group_id, practical_group_id) = v_group;
  select doctor_id into v_doc from teaching
   where subject_id = (pl->>'subject_id')::smallint and part = v_part and coalesce(theory_group_id, practical_group_id) = v_group;
  select name_ar into v_subject from subjects where id = (pl->>'subject_id')::smallint;

  insert into assignments (subject_id, part, theory_group_id, practical_group_id, number, description, due_at, closes_at, doctor_id, created_by)
  values ((pl->>'subject_id')::smallint, v_part,
          case when v_part = 'theory' then v_group end, case when v_part = 'practical' then v_group end,
          v_n, nullif(pl->>'description', ''), v_due, v_due + grace_interval(), v_doc, p_actor)
  returning id into v_id;

  insert into assignment_files (assignment_id, media_kind, tg_file_id, file_name, position)
  select v_id, a->>'media_kind', a->>'file_id', a->>'name', ord::int
    from jsonb_array_elements(d.attachments) with ordinality t(a, ord);
  update drafts set state = 'done' where id = d.id;

  v_payload := jsonb_build_object('assignment_id', v_id, 'number', v_n, 'subject', v_subject, 'part', v_part, 'group', v_group,
                                  'description', nullif(pl->>'description', ''), 'due_at', v_due,
                                  'has_attachment', jsonb_array_length(d.attachments) > 0);
  for r in
    select s.telegram_id as tg, coalesce(us.notify_assignment, true) as push
      from students s left join user_settings us on us.telegram_id = s.telegram_id
     where s.deleted_at is null and s.telegram_id is not null
       and ((v_part = 'theory' and s.theory_group_id = v_group) or (v_part = 'practical' and s.practical_group_id = v_group))
  loop
    perform notify_user(r.tg, 'assignment_new', v_payload, r.push, 'asg_new:' || v_id || ':' || r.tg);
  end loop;

  select * into v_dest from tg_destinations where key = 'cohort:notices:' || v_theory;
  if found then
    insert into outbox (kind, chat_id, topic_id, payload, dedupe_key)
    values ('assignment_post', v_dest.chat_id, v_dest.topic_id, v_payload, 'asg_post:' || v_id) on conflict (dedupe_key) do nothing;
  end if;
  perform log_activity(p_actor, 'assignment.publish', 'assignment', v_id::text, jsonb_build_object('group', v_group, 'number', v_n));
  return jsonb_build_object('status', 'ok', 'id', v_id, 'number', v_n);
end $$;

create or replace function my_assignments(p_tg bigint, p_limit int default 60) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s students%rowtype;
begin
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return '[]'::jsonb; end if;
  return coalesce((select jsonb_agg(to_jsonb(r) order by r.due_at desc) from (
    select a.id, a.number, sj.name_ar as subject, a.part, coalesce(a.theory_group_id, a.practical_group_id) as grp,
           a.description, a.due_at, a.closes_at, a.status,
           exists (select 1 from assignment_files af where af.assignment_id = a.id) as has_attachment,
           sub.submitted_at, coalesce(sub.is_late, false) as is_late, sub.received_at
      from assignments a
      join subjects sj on sj.id = a.subject_id
      left join submissions sub on sub.assignment_id = a.id and sub.student_no = s.academic_no
     where a.deleted_at is null and targets_student(a, s)
     order by a.due_at desc limit least(p_limit, 100)) r), '[]'::jsonb);
end $$;

create or replace function assignment_detail(p_tg bigint, p_id bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare a assignments%rowtype; s students%rowtype; v_theory text; v_ok boolean;
begin
  select * into a from assignments where id = p_id and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  v_theory := theory_of(coalesce(a.theory_group_id, a.practical_group_id));
  v_ok := (s.academic_no is not null and targets_student(a, s)) or has_perm(p_tg, 'assignments.manage', v_theory);
  if not v_ok then raise exception 'forbidden' using errcode = '42501'; end if;
  return jsonb_build_object('status', 'ok',
    'id', a.id, 'number', a.number, 'subject', (select name_ar from subjects where id = a.subject_id), 'part', a.part,
    'grp', coalesce(a.theory_group_id, a.practical_group_id), 'description', a.description,
    'due_at', a.due_at, 'closes_at', a.closes_at, 'status_db', a.status,
    'attachments', coalesce((select jsonb_agg(jsonb_build_object('id', af.id, 'name', af.file_name) order by af.position)
                               from assignment_files af where af.assignment_id = a.id), '[]'::jsonb),
    'submission', (select jsonb_build_object('submitted_at', sb.submitted_at, 'is_late', sb.is_late, 'received_at', sb.received_at, 'revision', sb.revision)
                     from submissions sb where sb.assignment_id = a.id and sb.student_no = s.academic_no and sb.submitted_at is not null));
end $$;

create or replace function assignment_file_list(p_tg bigint, p_id bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  v := assignment_detail(p_tg, p_id);   -- يتحقق من الصلاحية
  if v->>'status' <> 'ok' then return '[]'::jsonb; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('media_kind', media_kind, 'file_id', tg_file_id, 'name', file_name) order by position)
                     from assignment_files where assignment_id = p_id), '[]'::jsonb);
end $$;

create or replace function staff_assignments(p_actor bigint, p_limit int default 60) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(to_jsonb(r) order by r.created_at desc), '[]'::jsonb) from (
    select a.id, a.number, sj.name_ar as subject, a.part, coalesce(a.theory_group_id, a.practical_group_id) as grp,
           a.due_at, a.closes_at, a.status, a.created_at,
           (select count(*) from submissions x where x.assignment_id = a.id and x.submitted_at is not null) as submitted,
           (select count(*) from students st where st.deleted_at is null and targets_student(a, st)) as total
      from assignments a join subjects sj on sj.id = a.subject_id
     where a.deleted_at is null
       and has_perm(p_actor, 'assignments.manage', theory_of(coalesce(a.theory_group_id, a.practical_group_id)))
     order by a.created_at desc limit least(p_limit, 100)) r;
$$;

create or replace function assignment_roster(p_actor bigint, p_id bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare a assignments%rowtype;
begin
  select * into a from assignments where id = p_id and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'assignments.manage', theory_of(coalesce(a.theory_group_id, a.practical_group_id)));
  return jsonb_build_object('status', 'ok',
    'assignment', jsonb_build_object('id', a.id, 'number', a.number, 'subject', (select name_ar from subjects where id = a.subject_id),
                    'part', a.part, 'grp', coalesce(a.theory_group_id, a.practical_group_id), 'description', a.description,
                    'due_at', a.due_at, 'closes_at', a.closes_at, 'status_db', a.status),
    'submitted', coalesce((select jsonb_agg(jsonb_build_object('no', st.academic_no, 'name', st.full_name, 'at', sb.submitted_at,
                                            'late', sb.is_late, 'received', sb.received_at is not null) order by sb.submitted_at)
                   from students st join submissions sb on sb.student_no = st.academic_no and sb.assignment_id = a.id and sb.submitted_at is not null
                  where st.deleted_at is null and targets_student(a, st)), '[]'::jsonb),
    'missing', coalesce((select jsonb_agg(jsonb_build_object('no', st.academic_no, 'name', st.full_name) order by st.full_name)
                   from students st
                  where st.deleted_at is null and targets_student(a, st)
                    and not exists (select 1 from submissions sb where sb.assignment_id = a.id and sb.student_no = st.academic_no and sb.submitted_at is not null)), '[]'::jsonb));
end $$;

-- التمديد للمجموعة كاملة (لا لطالب بعينه)، وتُحسب المهلة الاحتياطية بعد الموعد الجديد
create or replace function extend_assignment(p_actor bigint, p_id bigint, p_new_due timestamptz) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a assignments%rowtype;
begin
  select * into a from assignments where id = p_id and deleted_at is null for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'assignments.manage', theory_of(coalesce(a.theory_group_id, a.practical_group_id)));
  if p_new_due <= now() or p_new_due <= a.due_at then return jsonb_build_object('status', 'invalid_due'); end if;
  update assignments set due_at = p_new_due, closes_at = p_new_due + grace_interval(), status = 'open', closed_at = null, closed_by = null
   where id = p_id;
  delete from reminders_sent where assignment_id = p_id;   -- لتُعاد التذكيرات بحسب الموعد الجديد
  perform log_activity(p_actor, 'assignment.extend', 'assignment', p_id::text, jsonb_build_object('due', p_new_due));
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function close_assignment(p_actor bigint, p_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a assignments%rowtype;
begin
  select * into a from assignments where id = p_id and deleted_at is null for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'assignments.manage', theory_of(coalesce(a.theory_group_id, a.practical_group_id)));
  update assignments set status = 'closed', closed_at = now(), closed_by = p_actor where id = p_id;
  perform log_activity(p_actor, 'assignment.close', 'assignment', p_id::text, null);
  return jsonb_build_object('status', 'ok');
end $$;

-- الحذف ناعم (سلة 30 يومًا) والتسليمات تبقى
create or replace function delete_assignment(p_actor bigint, p_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a assignments%rowtype;
begin
  select * into a from assignments where id = p_id and deleted_at is null for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'assignments.manage', theory_of(coalesce(a.theory_group_id, a.practical_group_id)));
  update assignments set deleted_at = now(), deleted_by = p_actor where id = p_id;
  perform log_activity(p_actor, 'assignment.delete', 'assignment', p_id::text, null);
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function restore_assignment(p_actor bigint, p_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a assignments%rowtype;
begin
  select * into a from assignments where id = p_id and deleted_at is not null for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  perform require_perm(p_actor, 'assignments.manage', theory_of(coalesce(a.theory_group_id, a.practical_group_id)));
  update assignments set deleted_at = null, deleted_by = null where id = p_id;
  perform log_activity(p_actor, 'assignment.restore', 'assignment', p_id::text, null);
  return jsonb_build_object('status', 'ok');
end $$;

-- ---------- التسليم ----------
create or replace function add_submission_file(p_tg bigint, p_assignment bigint, p_file jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare s students%rowtype; a assignments%rowtype; v_sub bigint; n int;
begin
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_student'); end if;
  perform require_perm(p_tg, 'assignments.submit');
  select * into a from assignments where id = p_assignment and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if not targets_student(a, s) then return jsonb_build_object('status', 'not_target'); end if;
  if a.status <> 'open' or now() > a.closes_at then return jsonb_build_object('status', 'closed'); end if;

  insert into submissions (assignment_id, student_no) values (a.id, s.academic_no) on conflict (assignment_id, student_no) do nothing;
  select id into v_sub from submissions where assignment_id = a.id and student_no = s.academic_no;
  select count(*) into n from submission_files where submission_id = v_sub and finalized_at is null;
  if n >= 20 then return jsonb_build_object('status', 'too_many'); end if;
  insert into submission_files (submission_id, media_kind, tg_file_id, tg_unique_id, file_name, mime, size, position)
  values (v_sub, p_file->>'media_kind', p_file->>'file_id', p_file->>'unique_id', p_file->>'name', p_file->>'mime', (p_file->>'size')::bigint,
          (select coalesce(max(position), 0) + 1 from submission_files where submission_id = v_sub));
  return jsonb_build_object('status', 'ok', 'pending', n + 1);
end $$;

create or replace function cancel_submission_pending(p_tg bigint, p_assignment bigint) returns void
language sql security definer set search_path = public as $$
  delete from submission_files sf using submissions sb, students st
   where sf.submission_id = sb.id and sb.assignment_id = p_assignment and sb.student_no = st.academic_no
     and st.telegram_id = p_tg and sf.finalized_at is null;
$$;

-- كل ما يلزم لتسليم الملفات للدكتور
create or replace function submission_package(p_sub bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare sb submissions%rowtype; a assignments%rowtype; st students%rowtype; v_key text; v_grp text;
begin
  select * into sb from submissions where id = p_sub;
  select * into a from assignments where id = sb.assignment_id;
  select * into st from students where academic_no = sb.student_no;
  v_grp := coalesce(a.theory_group_id, a.practical_group_id);
  v_key := case when a.doctor_id is null then null
                else 'doctor:' || a.doctor_id || ':' || a.part || ':' || v_grp end;
  return jsonb_build_object('submission_id', sb.id, 'is_late', sb.is_late, 'revision', sb.revision,
    'student', jsonb_build_object('no', st.academic_no, 'name', st.full_name, 'tg', st.telegram_id,
                                  'theory', st.theory_group_id, 'practical', st.practical_group_id),
    'assignment', jsonb_build_object('id', a.id, 'number', a.number, 'part', a.part, 'grp', v_grp,
                                     'subject', (select name_ar from subjects where id = a.subject_id)),
    'dest_key', v_key,
    'files', coalesce((select jsonb_agg(jsonb_build_object('media_kind', media_kind, 'file_id', tg_file_id, 'name', file_name) order by position)
                         from submission_files where submission_id = sb.id and finalized_at is not null and superseded_at is null), '[]'::jsonb),
    'old_deliveries', coalesce((select jsonb_agg(jsonb_build_object('chat_id', chat_id, 'message_id', message_id))
                                  from submission_deliveries where submission_id = sb.id), '[]'::jsonb));
end $$;

create or replace function finalize_submission(p_tg bigint, p_assignment bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare s students%rowtype; a assignments%rowtype; sb submissions%rowtype; n int;
begin
  select * into s from students where telegram_id = p_tg and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_student'); end if;
  perform require_perm(p_tg, 'assignments.submit');
  select * into a from assignments where id = p_assignment and deleted_at is null;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if a.status <> 'open' or now() > a.closes_at then return jsonb_build_object('status', 'closed'); end if;
  select * into sb from submissions where assignment_id = a.id and student_no = s.academic_no for update;
  if not found then return jsonb_build_object('status', 'no_files'); end if;
  select count(*) into n from submission_files where submission_id = sb.id and finalized_at is null;
  if n = 0 then return jsonb_build_object('status', 'no_files'); end if;

  update submission_files set superseded_at = now() where submission_id = sb.id and finalized_at is not null and superseded_at is null;
  update submission_files set finalized_at = now() where submission_id = sb.id and finalized_at is null;
  update submissions set submitted_at = coalesce(submitted_at, now()), updated_at = now(), is_late = (now() > a.due_at),
         revision = revision + 1, delivery_state = 'pending', received_at = null, received_by = null
   where id = sb.id;
  perform log_activity(p_tg, 'submission.finalize', 'assignment', a.id::text, jsonb_build_object('files', n));
  return jsonb_build_object('status', 'ok', 'package', submission_package(sb.id));
end $$;

create or replace function record_delivery(p_sub bigint, p_msgs jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  delete from submission_deliveries where submission_id = p_sub;
  insert into submission_deliveries (submission_id, chat_id, message_id)
  select p_sub, (m->>'chat_id')::bigint, (m->>'message_id')::int from jsonb_array_elements(p_msgs) m on conflict do nothing;
  update submissions set delivery_state = 'done' where id = p_sub;
end $$;

create or replace function pending_deliveries(p_limit int default 10) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(id), '[]'::jsonb) from (
    select id from submissions where delivery_state = 'pending' and updated_at < now() - interval '60 seconds'
     order by updated_at limit p_limit) x;
$$;

create or replace function mark_received(p_tg bigint, p_sub bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare sb submissions%rowtype; a assignments%rowtype;
begin
  select * into sb from submissions where id = p_sub;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  select * into a from assignments where id = sb.assignment_id;
  if not (is_developer(p_tg) or exists (select 1 from doctors where telegram_id = p_tg and id = a.doctor_id and is_active)) then
    return jsonb_build_object('status', 'forbidden');
  end if;
  update submissions set received_at = now(), received_by = p_tg where id = p_sub;
  return jsonb_build_object('status', 'ok');
end $$;

create or replace function my_submission_files(p_tg bigint, p_assignment bigint) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('media_kind', sf.media_kind, 'file_id', sf.tg_file_id, 'name', sf.file_name) order by sf.position), '[]'::jsonb)
    from submission_files sf
    join submissions sb on sb.id = sf.submission_id and sb.assignment_id = p_assignment
    join students st on st.academic_no = sb.student_no and st.telegram_id = p_tg
   where sf.finalized_at is not null and sf.superseded_at is null;
$$;

-- ---------- ملخص الرئيسية: «يهمّني الآن» + عدّاد الإشعارات ----------
create or replace function home_summary(p_tg bigint) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'unread', (select count(*) from notifications where telegram_id = p_tg and read_at is null),
    'items', coalesce((select jsonb_agg(x) from (
        select r from jsonb_array_elements(my_assignments(p_tg, 40)) r
         where r->>'status' = 'open' and (r->>'submitted_at') is null and (r->>'due_at')::timestamptz > now()
         order by (r->>'due_at')::timestamptz limit 3) y(x)), '[]'::jsonb));
$$;

-- ---------- المهام الدورية (يشغّلها البوت كل دقيقة؛ آمنة للتكرار) ----------
create or replace function run_due_work() returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_closed int; v_rem int := 0; r record; s record; v_a assignments%rowtype; v_new boolean; v_payload jsonb;
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
  return jsonb_build_object('closed', v_closed, 'reminders', v_rem);
end $$;

create or replace function purge_old() returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_files int; v_out int; v_notes int; v_drafts int; v_pend int;
begin
  with d as (delete from files where deleted_at < now() - interval '30 days' returning 1) select count(*) into v_files from d;
  with d as (delete from outbox where status in ('sent','failed','blocked') and created_at < now() - interval '7 days' returning 1) select count(*) into v_out from d;
  with d as (delete from notifications where created_at < now() - interval '90 days' returning 1) select count(*) into v_notes from d;
  with d as (delete from drafts where expires_at < now() - interval '1 day' returning 1) select count(*) into v_drafts from d;
  -- ملفات تسليم لم يُنهَ تسليمها منذ يوم
  with d as (delete from submission_files where finalized_at is null and created_at < now() - interval '1 day' returning 1) select count(*) into v_pend from d;
  return jsonb_build_object('files', v_files, 'outbox', v_out, 'notifications', v_notes, 'drafts', v_drafts, 'pending_files', v_pend);
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
