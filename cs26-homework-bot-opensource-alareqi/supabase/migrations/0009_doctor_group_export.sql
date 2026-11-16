-- 0009: تصدير الدكتور لكل مجموعة: صف لكل طالب وعمود لكل واجب (1 سلّم، 0 لم يسلّم).
-- نفس قواعد doctor_dashboard: الدكتور يرى واجباته فقط، والمطوّر يختار الدكتور.
create or replace function doctor_group_export(p_tg bigint, p_group text, p_doctor_id int default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_doc doctors%rowtype;
begin
  perform require_perm(p_tg, 'doctor.assignments');
  if is_developer(p_tg) and p_doctor_id is not null then
    select * into v_doc from doctors where id = p_doctor_id and deleted_at is null;
  else
    select * into v_doc from doctors where telegram_id = p_tg and is_active and deleted_at is null;
    if not found and is_developer(p_tg) then
      select * into v_doc from doctors where is_active and deleted_at is null order by id limit 1;
    end if;
  end if;

  return jsonb_build_object(
    'status', 'ok',
    'group', p_group,
    'doctor', case when v_doc.id is not null then jsonb_build_object('id', v_doc.id, 'name', v_doc.name) end,
    'assignments', coalesce((
      select jsonb_agg(jsonb_build_object('id', a.id, 'number', a.number, 'subject', sj.name_ar, 'part', a.part)
                       order by sj.name_ar, a.number, a.id)
        from assignments a
        join subjects sj on sj.id = a.subject_id
       where a.deleted_at is null
         and coalesce(a.theory_group_id, a.practical_group_id) = p_group
         and ((v_doc.id is not null and a.doctor_id = v_doc.id) or (v_doc.id is null and is_developer(p_tg)))
    ), '[]'::jsonb),
    'students', coalesce((
      select jsonb_agg(jsonb_build_object(
        'academic_no', st.academic_no,
        'full_name', st.full_name,
        'marks', (
          select coalesce(jsonb_agg(
                   case when exists (
                     select 1 from submissions sb
                      where sb.assignment_id = a2.id and sb.student_no = st.academic_no and sb.submitted_at is not null
                   ) then 1 else 0 end
                   order by sj2.name_ar, a2.number, a2.id), '[]'::jsonb)
            from assignments a2
            join subjects sj2 on sj2.id = a2.subject_id
           where a2.deleted_at is null
             and coalesce(a2.theory_group_id, a2.practical_group_id) = p_group
             and ((v_doc.id is not null and a2.doctor_id = v_doc.id) or (v_doc.id is null and is_developer(p_tg)))
        )
      ) order by st.academic_no)
        from students st
       where st.deleted_at is null
         and exists (
           select 1 from assignments a3
            where a3.deleted_at is null
              and coalesce(a3.theory_group_id, a3.practical_group_id) = p_group
              and ((v_doc.id is not null and a3.doctor_id = v_doc.id) or (v_doc.id is null and is_developer(p_tg)))
              and targets_student(a3, st)
         )
    ), '[]'::jsonb)
  );
end $$;

revoke execute on function doctor_group_export(bigint, text, int) from public, anon, authenticated;
grant  execute on function doctor_group_export(bigint, text, int) to service_role;
