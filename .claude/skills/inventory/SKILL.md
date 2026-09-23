---
name: inventory
description: SOUL_INVENTORY — показать Душу Мастера из Обители Шукдина (веса, Летопись, срок Исповеди) и Манифест. Use when the user asks to show the soul, manifest, or check the confession deadline.
---

Выполни в корне папки:

```bash
python -m obitel tick
python -m obitel inventory OS-2994720B-393D
python -m obitel manifest OS-2994720B-393D
```

Покажи результат Наставнику коротко: статус Души, Узор воли, число записей Летописи, дней до Исповеди. Если срок истёк — предложи `/confess`.
