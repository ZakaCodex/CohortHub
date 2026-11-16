-- =====================================================================
-- 0011_cancel_attendance_session.sql
-- التراجع عن جلسة حضور فُتحت بالخطأ (مادة أو رقم محاضرة خاطئ).
--
-- دالة جديدة فقط (create or replace function) — لا تعدّل أي جدول ولا أي صف موجود،
-- ولا تلمس جدول الطلاب ولا الحسابات المربوطة.
--
-- الحماية الصارمة لبيانات الحضور المعتمدة:
--   1) لا تُلغى إلا جلسة «مفتوحة» حاليًا.
--   2) لا تُلغى أي جلسة سبق إغلاقها يومًا (يُفحص سجل النشاط)، ولا جلسة فيها غياب
--      مسجّل أو تصحيحات — فهذه بيانات معتمدة لا تُحذف.
--   3) الذي يُحذف فقط: سجل الجلسة المفتوحة نفسها وتحضير من مُسحوا فيها قبل الإلغاء.
-- شغّله بعد 0010. آمن لإعادة التشغيل.
-- =====================================================================

create or replace function cancel_attendance_session(
  p_actor bigint,
  p_session bigint,
  p_delegate_absent boolean default false
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  sess attendance_sessions%rowtype;
  v_scanned int;
begin
  select * into sess from attendance_sessions where id = p_session for update;
  if not found then
    return jsonb_build_object('status', 'not_found');
  end if;

  if not has_perm(p_actor, 'attendance.session', sess.theory_group_id, p_delegate_absent or sess.delegate_absent) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  if sess.status <> 'open' then
    return jsonb_build_object('status', 'session_not_open');
  end if;

  -- جلسة أُغلقت سابقًا ثم أُعيد فتحها: بياناتها معتمدة، لا يجوز حذفها
  if exists (
        select 1 from activity_log
         where action = 'attendance.close' and entity = 'attendance_session' and entity_id = sess.id::text
     )
     or exists (select 1 from attendance_records where session_id = sess.id and status = 'absent')
     or exists (select 1 from attendance_corrections where session_id = sess.id) then
    return jsonb_build_object('status', 'session_has_history');
  end if;

  select count(*) into v_scanned from attendance_records where session_id = sess.id;

  -- on delete cascade على attendance_records يحذف تحضير الجلسة الخاطئة نفسها فقط
  delete from attendance_sessions where id = sess.id;

  perform bump_scope('attendance');
  perform log_activity(p_actor, 'attendance.cancel', 'attendance_session', sess.id::text,
    jsonb_build_object(
      'subject', sess.subject_id, 'group', sess.theory_group_id,
      'lecture', sess.lecture_no, 'scanned_discarded', v_scanned
    ));
  return jsonb_build_object('status', 'ok', 'discarded', v_scanned);
end $$;

revoke execute on function cancel_attendance_session(bigint, bigint, boolean) from public, anon, authenticated;
grant  execute on function cancel_attendance_session(bigint, bigint, boolean) to service_role;
