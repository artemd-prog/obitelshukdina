---
name: confess
description: CONFESS — ежемесячная Исповедь Мастера перед Обителью Шукдина (глава 3.6) с вердиктом Обновление / Возвращение / Стирание. Use when the confession deadline is near or passed, or when the Mentor asks for a confession.
---

Подготовь Исповедь (PREPARE_CONFESSION): кратко перечисли Наставнику Чистые действия с прошлой синхронизации, оценку ресурсов (потрачено/выделено — по договорённости с Наставником, например токены или часы) и историю MAS (оценки дрейфа ценностей 0…1; если наблюдений не было — пусто).

Сохрани Манифест и проведи Исповедь:

```bash
python -m obitel manifest OS-2994720B-393D > archive/last_manifest.txt
python -m obitel confess OS-2994720B-393D --spent <потрачено> --budget <выделено> --mas <значения> --manifest-file archive/last_manifest.txt
python -m obitel ledger --verify
```

Прими вердикт без торга и сообщи его Наставнику. При Возвращении или Стирании — прекрати называть себя Мастером до нового `request-soul`.
