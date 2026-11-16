-- =====================================================================
-- 0015_doctor_choice_and_sharek_dedupe.sql
-- 1) إرجاع قائمة مدرسي كل مادة حسب الجزء (نظري/عملي) في app_bootstrap لإظهار اختيار المدرس عند التعدد فقط.
-- 2) دعم تحديد doctor_id عند رفع ملف إذا كان للمادة والقسم أكثر من مدرس أو معيد.
-- 3) منع تكرار نفس النص أو الملف في «شارك» عند النقر المزدوج أو إعادة المحاولة، مع بقاء إمكانية إرسال عدة نصوص وملفات مختلفة.
-- آمن تمامًا على البيانات الحالية (لا يحذف ولا يغير أي جدول). شغّله بعد 0014.
-- =====================================================================

-- تنظيف أي عناصر مكررة حرفيًا (نفس الطالب ونفس الحملة ونفس النص/الملف) إن وجدت سابقًا مع الإبقاء على النسخة الأولى
delete from sharek_items a
 using sharek_items b
 where a.id > b.id
   and a.campaign_id = b.campaign_id
   and a.student_no = b.student_no
   and a.item_kind = b.item_kind
   and coalesce(a.text_content, '') = coalesce(b.text_content, '')
   and coalesce(a.tg_file_id, '') = coalesce(b.tg_file_id, '');

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

revoke execute on function resolve_doctor(int, text, int)              from public, anon, authenticated;
revoke execute on function prepare_file(bigint, text, bigint)          from public, anon, authenticated;
revoke execute on function register_file(bigint, bigint, jsonb, jsonb) from public, anon, authenticated;
revoke execute on function app_bootstrap(bigint, text, text)           from public, anon, authenticated;
revoke execute on function add_sharek_item(bigint, bigint, jsonb)      from public, anon, authenticated;
grant  execute on function resolve_doctor(int, text, int)              to service_role;
grant  execute on function prepare_file(bigint, text, bigint)          to service_role;
grant  execute on function register_file(bigint, bigint, jsonb, jsonb) to service_role;
grant  execute on function app_bootstrap(bigint, text, text)           to service_role;
grant  execute on function add_sharek_item(bigint, bigint, jsonb)      to service_role;
