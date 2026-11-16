-- =====================================================================
-- 0014_assignment_allow_text.sql
-- خيار «السماح بإرسال نصوص» في الواجب: يقبل البوت نصًّا أو روابط بجانب الملفات.
--
-- آمن على بياناتك الحالية:
--   * عمود جديد واحد في assignments قيمته الافتراضية false → كل الواجبات الحالية (المفتوحة والمغلقة)
--     تبقى كما هي: لا تقبل إلا الملفات.
--   * لا تعديل على submissions أو submission_files أو students ولا على أي صف موجود.
--   * الدوال الثلاث أدناه نسخ مطابقة للقديمة مع إضافة سطور allow_text فقط.
-- شغّله بعد 0013. آمن لإعادة التشغيل.
-- =====================================================================

alter table assignments add column if not exists allow_text boolean not null default false;

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

  insert into assignments (subject_id, part, theory_group_id, practical_group_id, number, description, due_at, closes_at, doctor_id, created_by, allow_text)
  values ((pl->>'subject_id')::smallint, v_part,
          case when v_part = 'theory' then v_group end, case when v_part = 'practical' then v_group end,
          v_n, nullif(pl->>'description', ''), v_due, v_due + grace_interval(), v_doc, p_actor,
          coalesce(pl->>'allow_text', 'false') in ('true', 't', '1'))
  returning id into v_id;

  insert into assignment_files (assignment_id, media_kind, tg_file_id, file_name, position)
  select v_id, a->>'media_kind', a->>'file_id', a->>'name', ord::int
    from jsonb_array_elements(d.attachments) with ordinality t(a, ord);
  update drafts set state = 'done' where id = d.id;

  v_payload := jsonb_build_object('assignment_id', v_id, 'number', v_n, 'subject', v_subject, 'part', v_part, 'group', v_group,
                                  'description', nullif(pl->>'description', ''), 'due_at', v_due,
                                  'has_attachment', jsonb_array_length(d.attachments) > 0,
                                  'allow_text', coalesce(pl->>'allow_text', 'false') in ('true', 't', '1'));
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
    'due_at', a.due_at, 'closes_at', a.closes_at, 'status_db', a.status, 'allow_text', a.allow_text,
    'attachments', coalesce((select jsonb_agg(jsonb_build_object('id', af.id, 'name', af.file_name) order by af.position)
                               from assignment_files af where af.assignment_id = a.id), '[]'::jsonb),
    'submission', (select jsonb_build_object('submitted_at', sb.submitted_at, 'is_late', sb.is_late, 'received_at', sb.received_at, 'revision', sb.revision)
                     from submissions sb where sb.assignment_id = a.id and sb.student_no = s.academic_no and sb.submitted_at is not null));
end $$;

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
  -- عنصر نصي/رابط: يُقبل فقط إن فعّل المشرف «السماح بإرسال نصوص» عند إنشاء الواجب (النص محفوظ في tg_file_id)
  if p_file->>'media_kind' = 'text' then
    if not a.allow_text then return jsonb_build_object('status', 'text_not_allowed'); end if;
    if char_length(coalesce(p_file->>'file_id', '')) not between 1 and 3000 then return jsonb_build_object('status', 'bad_text'); end if;
  end if;

  insert into submissions (assignment_id, student_no) values (a.id, s.academic_no) on conflict (assignment_id, student_no) do nothing;
  select id into v_sub from submissions where assignment_id = a.id and student_no = s.academic_no for update;
  select count(*) into n from submission_files where submission_id = v_sub and finalized_at is null;
  if n >= 20 then return jsonb_build_object('status', 'too_many', 'pending', n); end if;
  insert into submission_files (submission_id, media_kind, tg_file_id, tg_unique_id, file_name, mime, size, position)
  values (v_sub, p_file->>'media_kind', p_file->>'file_id', p_file->>'unique_id', p_file->>'name', p_file->>'mime', (p_file->>'size')::bigint,
          (select coalesce(max(position), 0) + 1 from submission_files where submission_id = v_sub));
  return jsonb_build_object('status', 'ok', 'pending', n + 1);
end $$;

revoke execute on function publish_assignment(bigint, bigint)          from public, anon, authenticated;
revoke execute on function assignment_detail(bigint, bigint)           from public, anon, authenticated;
revoke execute on function add_submission_file(bigint, bigint, jsonb)  from public, anon, authenticated;
grant  execute on function publish_assignment(bigint, bigint)          to service_role;
grant  execute on function assignment_detail(bigint, bigint)           to service_role;
grant  execute on function add_submission_file(bigint, bigint, jsonb)  to service_role;
