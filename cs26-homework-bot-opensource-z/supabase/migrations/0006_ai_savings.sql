-- 0006: توفير توكنات Gemini
-- 1) الحصة اليومية لا تُحتسب إلا على الردود التي استهلكت Gemini فعلًا (الأسئلة الجاهزة والكاش مجانية وغير محدودة)
-- 2) كاش دائم لردود Gemini (يُشارك بين Vercel والبوت وكل الطلاب): سؤال مكرر = صفر توكنات

alter table assistant_logs add column if not exists counted boolean not null default true;

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
   where telegram_id = p_tg and counted and day_sanaa = (now() at time zone 'Asia/Aden')::date;

  return jsonb_build_object('role', coalesce(v_role, 'guest'), 'used', v_used, 'limit', v_limit,
                            'remaining', greatest(0, v_limit - v_used));
end $$;

drop function if exists record_assistant_exchange(bigint, text, text);
create or replace function record_assistant_exchange(p_tg bigint, p_question text, p_answer text, p_counted boolean default true)
returns jsonb language plpgsql security definer set search_path = public as $$
declare q jsonb;
begin
  q := assistant_quota(p_tg);
  if p_counted and (q->>'remaining')::int <= 0 then
    return q || jsonb_build_object('status', 'limit_reached');
  end if;
  insert into assistant_logs (telegram_id, day_sanaa, question, answer, counted)
  values (p_tg, (now() at time zone 'Asia/Aden')::date, left(trim(p_question), 600), left(trim(p_answer), 2000), p_counted);
  return assistant_quota(p_tg) || jsonb_build_object('status', 'ok');
end $$;

create table if not exists ai_cache (
  key        text primary key,
  kind       text not null,
  value      jsonb not null,
  hits       int  not null default 0,
  created_at timestamptz not null default now()
);
alter table ai_cache enable row level security;

create or replace function ai_cache_get(p_key text, p_max_age_hours int default 720) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v jsonb;
begin
  update ai_cache set hits = hits + 1
   where key = p_key and created_at > now() - make_interval(hours => p_max_age_hours)
   returning value into v;
  return v;
end $$;

create or replace function ai_cache_put(p_key text, p_kind text, p_value jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  insert into ai_cache (key, kind, value) values (left(p_key, 200), p_kind, p_value)
  on conflict (key) do update set value = excluded.value, created_at = now();
  if random() < 0.02 then   -- تنظيف عرضي للقديم (أكثر من 60 يومًا)
    delete from ai_cache where created_at < now() - interval '60 days';
  end if;
end $$;

revoke execute on all functions in schema public from public, anon, authenticated;
grant  execute on all functions in schema public to service_role;
