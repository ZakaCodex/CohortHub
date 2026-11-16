-- 0008: حد المرشّحين في بحث الملفات الذكي قابل للضبط (كان ثابتًا 60 فتُهمَل الملفات الأقدم عند البحث بكلمات).
-- نحذف التوقيع القديم أولًا كي لا يصير عندنا overload غامض بين 6 و7 معاملات.
drop function if exists search_files_smart(bigint, int, text, text, text, smallint[]);

create or replace function search_files_smart(
  p_tg bigint,
  p_subject int default null,
  p_part text default null,
  p_type text default null,
  p_exam_kind text default null,
  p_numbers smallint[] default null,
  p_limit int default 60
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
       order by f.created_at desc, f.id desc
       limit greatest(1, least(coalesce(p_limit, 60), 500))
    ) r
  ), '[]'::jsonb);
end $$;

revoke execute on function search_files_smart(bigint, int, text, text, text, smallint[], int) from public, anon, authenticated;
grant  execute on function search_files_smart(bigint, int, text, text, text, smallint[], int) to service_role;
