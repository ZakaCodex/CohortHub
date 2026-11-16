-- 0010: نسخة احتياطية أسبوعية يرسلها البوت للمطوّر + دوال تصدير/استرجاع جداول القاعدة.
-- جميع الدوال للـ service_role فقط (البوت وسكربت الاسترجاع).

create table if not exists backup_runs (
  week_key    text primary key,                       -- مثل 2026-W44: نسخة واحدة لكل أسبوع حتى لو أُعيد تشغيل البوت
  status      text not null default 'running' check (status in ('running', 'ok', 'failed')),
  bytes       bigint,
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);
alter table backup_runs enable row level security;

-- الجداول المشمولة بالنسخة (بترتيب يحترم المفاتيح الأجنبية)؛ يُستثنى outbox وai_cache لأنهما يُعاد بناؤهما.
create or replace function backup_tables() returns text[] language sql immutable as $$
  select array[
    'app_settings','data_versions','theory_groups','practical_groups','subjects','doctors','teaching',
    'tg_destinations','students','users','role_grants','role_permissions','activity_log','link_attempts',
    'user_settings','notifications','drafts','files','assignments','assignment_files','submissions',
    'submission_files','submission_deliveries','reminders_sent','attendance_sessions','scanner_devices',
    'attendance_records','attendance_corrections','barcode_logs','grievance_cycles','grievances',
    'assistant_logs','support_tickets','report_templates','daily_reports','weekly_reports',
    'report_reminders_sent','sharek_campaigns','sharek_votes','sharek_items','sharek_sessions',
    'mcq_exams','mcq_questions','mcq_attempts'
  ];
$$;

-- حجز نسخة هذا الأسبوع: true إن كان لنا حق التنفيذ (جديد، أو فاشل سابقًا، أو عالق أكثر من ساعتين)
create or replace function backup_claim(p_key text) returns boolean
language plpgsql security definer set search_path = public as $$
declare v_n int;
begin
  insert into backup_runs (week_key) values (p_key)
  on conflict (week_key) do update
     set status = 'running', created_at = now(), finished_at = null
   where backup_runs.status = 'failed'
      or (backup_runs.status = 'running' and backup_runs.created_at < now() - interval '2 hours');
  get diagnostics v_n = row_count;
  return v_n > 0;
end $$;

create or replace function backup_finish(p_key text, p_status text, p_bytes bigint default null) returns void
language sql security definer set search_path = public as $$
  update backup_runs
     set status = case when p_status = 'ok' then 'ok' else 'failed' end,
         bytes = p_bytes, finished_at = now()
   where week_key = p_key;
$$;

-- تصدير جدول كاملًا كمصفوفة JSON
create or replace function backup_export_table(p_table text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  if not (p_table = any (backup_tables())) then
    raise exception 'table_not_allowed: %', p_table;
  end if;
  execute format('select coalesce(jsonb_agg(to_jsonb(t)), ''[]''::jsonb) from public.%I t', p_table) into v;
  return v;
end $$;

-- استرجاع صفوف جدول (على قاعدة جديدة/فارغة): لا يستبدل الموجود (on conflict do nothing)،
-- ويحفظ قيم id الأصلية ثم يضبط العدّادات كي لا تتصادم الإدخالات القادمة.
create or replace function backup_restore_table(p_table text, p_rows jsonb) returns int
language plpgsql security definer set search_path = public as $$
declare v_n int; v_col text;
begin
  if not (p_table = any (backup_tables())) then
    raise exception 'table_not_allowed: %', p_table;
  end if;
  execute format(
    'with ins as (insert into public.%1$I overriding system value
                  select * from jsonb_populate_recordset(null::public.%1$I, $1)
                  on conflict do nothing returning 1)
     select count(*) from ins', p_table) into v_n using p_rows;
  for v_col in
    select a.attname from pg_attribute a
     where a.attrelid = ('public.' || quote_ident(p_table))::regclass and a.attidentity <> '' and not a.attisdropped
  loop
    execute format('select setval(pg_get_serial_sequence(%L, %L), greatest(coalesce((select max(%I) from public.%I), 1), 1))',
                   'public.' || quote_ident(p_table), v_col, v_col, p_table);
  end loop;
  return v_n;
end $$;

revoke execute on function backup_tables()                          from public, anon, authenticated;
revoke execute on function backup_claim(text)                       from public, anon, authenticated;
revoke execute on function backup_finish(text, text, bigint)        from public, anon, authenticated;
revoke execute on function backup_export_table(text)                from public, anon, authenticated;
revoke execute on function backup_restore_table(text, jsonb)        from public, anon, authenticated;
grant  execute on function backup_tables()                          to service_role;
grant  execute on function backup_claim(text)                       to service_role;
grant  execute on function backup_finish(text, text, bigint)        to service_role;
grant  execute on function backup_export_table(text)                to service_role;
grant  execute on function backup_restore_table(text, jsonb)        to service_role;
