-- 0005: موثوقية التسليم والتقارير (آمن لإعادة التشغيل)
-- 1) تسليمات لا تُحجب: عدّاد محاولات + تراجع تصاعدي + حجز (lease) يمنع التكرار ويدعم نسختين من البوت
-- 2) قفل صف التسليم عند إضافة الملفات (منع سباق العدّ والترتيب)
-- 3) إدراج الإشعارات مرة واحدة (dedupe) عبر RPC بدل POST خام
-- 4) كشف الرفع غير المنتهي لإنهائه تلقائيًا/تذكير الطالب

alter table submissions add column if not exists delivery_attempts int not null default 0;
alter table submissions add column if not exists next_delivery_at timestamptz not null default now();
drop index if exists submissions_pending_idx;
create index if not exists submissions_pending_idx on submissions (next_delivery_at, updated_at) where delivery_state = 'pending';

-- تسليم جديد/معدَّل ⇒ يصبح مستحقًا فورًا ويُصفَّر العدّاد
create or replace function trg_submission_due() returns trigger language plpgsql as $$
begin
  if new.delivery_state = 'pending' and (new.revision is distinct from old.revision
       or old.delivery_state is distinct from 'pending') then
    new.next_delivery_at := now();
    new.delivery_attempts := 0;
  end if;
  return new;
end $$;
drop trigger if exists submission_due on submissions;
create trigger submission_due before update on submissions
  for each row execute function trg_submission_due();

-- يحجز المستحق فقط (مع حجز 90 ثانية) بلا انتظار الصفوف المحجوزة عند نسخة أخرى
create or replace function pending_deliveries(p_limit int default 10) returns jsonb
language plpgsql security definer set search_path = public as $$
declare r jsonb;
begin
  with c as (
    select id from submissions
     where delivery_state = 'pending' and updated_at < now() - interval '5 seconds' and next_delivery_at <= now()
     order by next_delivery_at, updated_at
     limit p_limit for update skip locked),
  u as (
    update submissions s set next_delivery_at = now() + interval '90 seconds' from c where s.id = c.id returning s.id)
  select coalesce(jsonb_agg(id), '[]'::jsonb) into r from u;
  return r;
end $$;

-- حجز تسليم بعينه (للإرسال الفوري بعد «إنهاء التسليم») كي لا يلتقطه المجدول أثناء الإرسال
create or replace function claim_delivery(p_sub bigint) returns boolean
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  update submissions set next_delivery_at = now() + interval '90 seconds'
   where id = p_sub and delivery_state = 'pending' and next_delivery_at <= now();
  get diagnostics n = row_count;
  return n > 0;
end $$;

-- فشل التسليم: تراجع تصاعدي 30ث، 60ث، 2د ... حتى 30 دقيقة
create or replace function delivery_failed(p_sub bigint) returns void
language sql security definer set search_path = public as $$
  update submissions
     set delivery_attempts = delivery_attempts + 1,
         next_delivery_at = now() + least(interval '30 minutes', interval '30 seconds' * power(2, least(delivery_attempts, 6)))
   where id = p_sub and delivery_state = 'pending';
$$;

-- إضافة ملف: قفل صف التسليم أولًا فيتسلسل العدّ والترتيب حتى مع ألبوم ملفات متوازٍ
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
  select id into v_sub from submissions where assignment_id = a.id and student_no = s.academic_no for update;
  select count(*) into n from submission_files where submission_id = v_sub and finalized_at is null;
  if n >= 20 then return jsonb_build_object('status', 'too_many', 'pending', n); end if;
  insert into submission_files (submission_id, media_kind, tg_file_id, tg_unique_id, file_name, mime, size, position)
  values (v_sub, p_file->>'media_kind', p_file->>'file_id', p_file->>'unique_id', p_file->>'name', p_file->>'mime', (p_file->>'size')::bigint,
          (select coalesce(max(position), 0) + 1 from submission_files where submission_id = v_sub));
  return jsonb_build_object('status', 'ok', 'pending', n + 1);
end $$;

-- رفع لم يُنهَ منذ p_minutes: first = تسليم أول (يُنهى تلقائيًا)، وإلا تعديل (تذكير فقط كي لا يُستبدل التسليم السابق بنصف ملفات)
create or replace function stale_unfinalized(p_minutes int default 10, p_limit int default 20) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('tg', st.telegram_id, 'assignment', x.assignment_id, 'first', x.first_time)), '[]'::jsonb)
    from (
      select sb.assignment_id, sb.student_no, (sb.submitted_at is null) as first_time
        from submissions sb
        join assignments a on a.id = sb.assignment_id and a.status = 'open' and a.deleted_at is null and now() < a.closes_at
       where exists (select 1 from submission_files f where f.submission_id = sb.id and f.finalized_at is null)
         and (select max(f.created_at) from submission_files f where f.submission_id = sb.id and f.finalized_at is null)
             < now() - make_interval(mins => p_minutes)
         and (select max(f.created_at) from submission_files f where f.submission_id = sb.id and f.finalized_at is null)
             > now() - interval '3 hours'
       limit p_limit) x
    join students st on st.academic_no = x.student_no and st.telegram_id is not null and st.deleted_at is null;
$$;

-- إدراج في الطابور مرة واحدة (يتجاهل المكرَّر بدل أن يرمي 409)
create or replace function enqueue_outbox(p_kind text, p_chat bigint, p_topic int, p_payload jsonb, p_dedupe text) returns boolean
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  insert into outbox (kind, chat_id, topic_id, payload, dedupe_key) values (p_kind, p_chat, p_topic, p_payload, p_dedupe)
  on conflict (dedupe_key) do nothing;
  get diagnostics n = row_count;
  return n > 0;
end $$;

-- ربط/تعديل وجهة (topic) ⇒ المعلّق يُعاد فورًا دون انتظار التراجع التصاعدي
create or replace function trg_destination_linked() returns trigger language plpgsql security definer set search_path = public as $$
begin
  update submissions set next_delivery_at = now() where delivery_state = 'pending';
  return null;
end $$;
drop trigger if exists destination_linked on tg_destinations;
create trigger destination_linked after insert or update on tg_destinations
  for each statement execute function trg_destination_linked();

revoke execute on all functions in schema public from public, anon, authenticated;
grant  execute on all functions in schema public to service_role;
