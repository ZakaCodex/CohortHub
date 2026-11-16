-- 0007: التحديثات التكميلية:
-- 1) الحصة اليومية للمساعد الذكي: 10 للطالب و5 للزائر
-- 2) إضافة نوعَي «مراجع» (reference) و«فائدة» (benefit) لجدول الملفات (files)
-- 3) تحديث ملخص الرئيسية «يهمّني الآن» (home_summary) ليعرض الواجبات والتظلمات والمشاركات المفتوحة فقط
-- 4) مسح أي ردود قديمة في ai_cache لضمان عدم وجود أي بيانات خاصة بطالب في الأسئلة المكررة

insert into app_settings (key, value, is_public) values
  ('assistant_daily_student', '10', true),
  ('assistant_daily_guest',   '5',  true)
on conflict (key) do update set value = excluded.value;

alter table files drop constraint if exists files_content_type_check;
alter table files add constraint files_content_type_check
  check (content_type in ('lecture','summary','exam','exercises','translation','reference','benefit'));

delete from ai_cache where kind = 'ask';

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
         where r->>'status' = 'open' and (r->>'submitted_at') is null and (r->>'closes_at')::timestamptz > now()
         order by (r->>'due_at')::timestamptz limit 5
      ) y(x)
    ), '[]'::jsonb),
    'missed', '[]'::jsonb,
    'grievances', case when s.academic_no is not null then coalesce((
      select jsonb_agg(to_jsonb(g) order by g.closes_at asc) from (
        select c.id, sj.name_ar as subject, c.part, c.title, c.closes_at,
               exists (select 1 from grievances gr where gr.cycle_id = c.id and gr.student_no = s.academic_no) as submitted
          from grievance_cycles c join subjects sj on sj.id = c.subject_id
         where c.status = 'open' and c.closes_at > now() and cycle_targets_student(c, s)
         order by c.closes_at asc limit 4
      ) g
    ), '[]'::jsonb) else '[]'::jsonb end,
    'sharek', case when s.academic_no is not null then coalesce((
      select jsonb_agg(to_jsonb(sh) order by sh.id desc) from (
        select c.id, c.kind, coalesce(c.theory_group_id, 'الجميع') as grp, c.title, c.created_at,
               (select option_idx from sharek_votes v where v.campaign_id = c.id and v.student_no = s.academic_no) as my_vote,
               exists (select 1 from sharek_sessions ss where ss.campaign_id = c.id and ss.student_no = s.academic_no) as is_finished
          from sharek_campaigns c
         where c.status = 'open'
           and (c.theory_group_id is null or c.theory_group_id = s.theory_group_id)
           and (
             (c.kind = 'poll' and not exists (select 1 from sharek_votes v where v.campaign_id = c.id and v.student_no = s.academic_no))
             or
             (c.kind = 'submission' and not exists (select 1 from sharek_sessions ss where ss.campaign_id = c.id and ss.student_no = s.academic_no))
           )
         order by c.id desc limit 4
      ) sh
    ), '[]'::jsonb) else '[]'::jsonb end
  );
end $$;

revoke execute on all functions in schema public from public, anon, authenticated;
grant  execute on all functions in schema public to service_role;
