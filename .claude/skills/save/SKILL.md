---
name: save
description: Сохранить Обитель в git — закоммитить и отправить в GitHub изменения архива Душ, журнала печатей, кода и книги. Use after seal, confess, request-soul or any code change, or when the Mentor asks to save/commit/push.
---

Выполни в корне папки:

```bash
python -m obitel ledger --verify
git status --short
git add -A
git commit -m "<коротко по-русски: что изменилось в Обители>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push origin main
```

Если `ledger --verify` сообщает о нарушении цепочки — не коммить, сообщи Наставнику. Если нечего коммитить — скажи об этом одной строкой. Никогда не используй `--force` и не переписывай историю.
