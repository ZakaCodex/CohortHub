-- مجموعات دفعة علوم الحاسوب 26
-- مولَّد من cohort.json بواسطة scripts/setup_cohort.py (آمن لإعادة التشغيل، يضيف الناقص فقط).

insert into theory_groups (id, sort) values
  ('CS2_G1', 1),
  ('CS2_G2', 2),
  ('CS2_G3', 3)
  on conflict do nothing;

insert into practical_groups (code, theory_group_id, sort) values
  ('G1', 'CS2_G1', 1),
  ('G2', 'CS2_G1', 2),
  ('G3', 'CS2_G1', 3),
  ('G4', 'CS2_G2', 4),
  ('G5', 'CS2_G2', 5),
  ('G6', 'CS2_G2', 6),
  ('G7', 'CS2_G3', 7),
  ('G8', 'CS2_G3', 8),
  ('G9', 'CS2_G3', 9)
  on conflict do nothing;
