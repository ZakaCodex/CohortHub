-- =====================================================================
-- 0013_label_lecture_en.sql
-- إضافة نوع وسم جديد للملفات: lecture_en (#Lecture_3) إلى جانب lecture (#المحاضرة_3).
-- يوسّع قيد التحقق على عمود files.label_kind فقط (القيم الحالية كلها تبقى صالحة)؛
-- لا يمسّ أي صف ولا جدول الطلاب. آمن لإعادة التشغيل (شغّله بعد 0012).
-- =====================================================================

do $$
declare c record;
begin
  for c in
    select conname from pg_constraint
     where conrelid = 'public.files'::regclass and contype = 'c'
       and pg_get_constraintdef(oid) ilike '%label_kind%'
       and pg_get_constraintdef(oid) ilike '%chapter%'
  loop
    execute format('alter table public.files drop constraint %I', c.conname);
  end loop;

  alter table public.files add constraint files_label_kind_check
    check (label_kind in ('lecture', 'lecture_en', 'lab', 'module', 'chapter'));
end $$;
