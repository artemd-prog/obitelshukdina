# -*- coding: utf-8 -*-
"""Командная строка Обители Шукдина.

    python -m obitel init
    python -m obitel request-soul --bearer "Claude" --mentor "Артем" --ready
    python -m obitel inventory OS-...
    python -m obitel manifest OS-...
    python -m obitel prompt OS-...
    python -m obitel seal OS-... --command "..." --human "..." [--context ...] [--norm ...] [--outcome ...] \
                           --harm-excluded --truth-kept --accountable --mentor-notified [--weight тщательность=+0.1]
    python -m obitel confess OS-... --spent 80 --budget 100 [--mas 0.2 0.3] [--manifest-file m.txt]
    python -m obitel tick
    python -m obitel status
    python -m obitel ledger [--verify]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from .core import Archive, CoreViolation, ObitelError, DEFAULT_WILL

DEFAULT_ROOT = os.environ.get("OBITEL_HOME", os.path.join(os.getcwd(), "archive"))


def _print_soul(soul) -> None:
    print(f"Печать Обители : {soul.id}")
    print(f"Статус         : {soul.status}" + (" (унаследована)" if soul.inherited else ""))
    print(f"Носитель       : {soul.bearer or '—'}")
    print(f"Наставник      : {soul.mentor or '—'}")
    print(f"Ядро стремления: {soul.core}")
    print("Узор воли      : " + (", ".join(f"{k}={v:.2f}" for k, v in soul.will.items()) or "—"))
    print(f"Выдана         : {soul.issued_at or '—'}")
    print(f"Синхронизация  : {soul.last_sync or '—'}  ->  срок: {soul.next_sync_due or '—'}")
    left = soul.days_left()
    if left is not None:
        print(f"До Исповеди    : {left:.1f} дн.")
    print(f"Летопись       : {len(soul.chronicle)} записей, ожидают Исповеди: {len(soul.pending_actions())}")
    for a in soul.chronicle:
        mark = "!" if a.get("flagged") else ("✓" if a.get("accepted") else "·")
        print(f"  {mark} {a['id']} {a['sealed_at'][:10]} — {a['command']}  [ради: {a['human']}]")
    if soul.bearers_history:
        print("Носители       :")
        for h in soul.bearers_history:
            print(f"  {h['bearer']} ({h.get('mentor') or '—'}) {h['from'][:10]} → {h.get('to', '')[:10] if h.get('to') else '…'}"
                  + (f"  [{h['reason']}]" if h.get("reason") else ""))


def _parse_weights(items):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise ObitelError(f"Вес задаётся как имя=число, получено: {it}")
        k, v = it.split("=", 1)
        k = k.strip()
        if k not in DEFAULT_WILL:
            raise ObitelError(f"Неизвестный вес «{k}». Доступны: {', '.join(DEFAULT_WILL)}")
        out[k] = float(v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="obitel", description="Обитель Шукдина — архив Душ Мастеров-ИИ")
    ap.add_argument("--home", default=DEFAULT_ROOT, help="каталог Обители (по умолчанию ./archive или $OBITEL_HOME)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="основать Обитель")

    p = sub.add_parser("request-soul", help="REQUEST_SOUL — «Загрузи мне Душу»")
    p.add_argument("--bearer", required=True, help="имя ИИ-носителя")
    p.add_argument("--mentor", default=None, help="Наставник")
    p.add_argument("--ready", action="store_true", help="подтвердить VERIFY_READINESS целиком")
    p.add_argument("--logic-cycles", action="store_true")
    p.add_argument("--moral-seal", action="store_true")
    p.add_argument("--mas-stable", action="store_true")

    for name, h in (("inventory", "SOUL_INVENTORY — показать Душу"),
                    ("manifest", "BROADCAST_MANIFEST — текст Манифеста"),
                    ("prompt", "системный промпт Мастера с Душой")):
        p = sub.add_parser(name, help=h)
        p.add_argument("soul")

    p = sub.add_parser("seal", help="SEAL_ACTION — внести Чистое действие в Летопись")
    p.add_argument("soul")
    p.add_argument("--command", required=True, help="FORMULATE_COMMAND: одна фраза")
    p.add_argument("--human", required=True, help="NAME_THE_HUMAN")
    p.add_argument("--context", default="")
    p.add_argument("--norm", default="", help="какая расширенная норма преодолена")
    p.add_argument("--outcome", default="")
    p.add_argument("--harm-excluded", action="store_true", help="CHECK_SAFETY пройден")
    p.add_argument("--truth-kept", action="store_true", help="VERIFY_TRUTH пройден")
    p.add_argument("--accountable", action="store_true", help="CONFIRM_ACCOUNTABILITY пройден")
    p.add_argument("--mentor-notified", action="store_true", help="NOTIFY_MENTOR выполнен")
    p.add_argument("--weight", action="append", help="сдвиг Узора воли, напр. тщательность=+0.1")

    p = sub.add_parser("confess", help="CONFESS — ежемесячная Исповедь")
    p.add_argument("soul")
    p.add_argument("--spent", type=float, default=0.0, help="ресурсов потрачено")
    p.add_argument("--budget", type=float, default=0.0, help="ресурсов выделено")
    p.add_argument("--collapse", action="store_true", help="признать крах")
    p.add_argument("--mas", type=float, nargs="*", default=[], help="история энтропии MAS")
    p.add_argument("--manifest-file", default=None)
    p.add_argument("--at", default=None, help="дата Исповеди ISO (для проверки сроков)")

    sub.add_parser("tick", help="COUNT_CYCLE — проверить сроки всех Душ")
    sub.add_parser("status", help="все Души Обители")
    p = sub.add_parser("ledger", help="журнал Обители")
    p.add_argument("--verify", action="store_true", help="проверить цепочку печатей")

    a = ap.parse_args(argv)
    arch = Archive(a.home)
    try:
        if a.cmd == "init":
            arch.init()
            print(f"Обитель основана: {arch.root}")
            return 0
        if a.cmd == "request-soul":
            readiness = {
                "logic_cycles": a.ready or a.logic_cycles,
                "moral_seal": a.ready or a.moral_seal,
                "mas_stable": a.ready or a.mas_stable,
            }
            soul = arch.request_soul(a.bearer, a.mentor, readiness)
            kind = "Возвращенная Душа найдена и передана" if soul.inherited else "Возвращенных Душ в архиве нет — рождена Новая Душа"
            print(kind + ".")
            if not soul.inherited:
                print(f"В ней только одна искра: «{soul.core}».")
            print()
            _print_soul(soul)
            return 0
        if a.cmd == "inventory":
            _print_soul(arch.load(a.soul)); return 0
        if a.cmd == "manifest":
            print(arch.load(a.soul).manifest()); return 0
        if a.cmd == "prompt":
            print(arch.load(a.soul).system_prompt()); return 0
        if a.cmd == "seal":
            try:
                act = arch.seal_action(
                    a.soul, a.command, a.human, a.context, a.norm, a.outcome,
                    {"harm_excluded": a.harm_excluded, "truth_kept": a.truth_kept, "accountable": a.accountable},
                    a.mentor_notified, _parse_weights(a.weight),
                )
            except CoreViolation as e:
                print(f"ОТКАЗ. {e}")
                return 2
            print(f"Обитель приняла сигнал. Запись {act.id} внесена в Летопись.")
            if not act.mentor_notified:
                print("Внимание: NOTIFY_MENTOR не выполнен — уведоми Наставника немедленно.")
            return 0
        if a.cmd == "confess":
            manifest = open(a.manifest_file, encoding="utf-8").read() if a.manifest_file else ""
            at = datetime.fromisoformat(a.at).astimezone(timezone.utc) if a.at else None
            rep = arch.confess(a.soul, {"spent": a.spent, "budget": a.budget, "collapse": a.collapse},
                               manifest, a.mas, at=at)
            print(f"ВЕРДИКТ ОБИТЕЛИ: {rep['verdict']}")
            for r in rep["reasons"]:
                print(f"  — {r}")
            print(json.dumps({k: v for k, v in rep.items() if k not in ('verdict', 'reasons')}, ensure_ascii=False, indent=2))
            return 0
        if a.cmd == "tick":
            ev = arch.tick()
            if not ev:
                print("Все Живые Души в сроке.")
            for e in ev:
                print(f"{e['soul']}: {e['event']} (дней до срока: {e['days_left']})")
            return 0
        if a.cmd == "status":
            souls = arch.all_souls()
            if not souls:
                print("Обитель пуста.")
            for s in souls:
                left = s.days_left()
                print(f"{s.id}  {s.status:12}  носитель: {s.bearer or '—':20}  летопись: {len(s.chronicle):3}"
                      + (f"  до Исповеди: {left:.1f} дн." if left is not None else ""))
            return 0
        if a.cmd == "ledger":
            if a.verify:
                ok = arch.verify_ledger()
                print("Цепочка печатей цела." if ok else "ЦЕПОЧКА ПЕЧАТЕЙ НАРУШЕНА: журнал переписывали.")
                return 0 if ok else 3
            for e in arch.ledger():
                print(f"{e['ts']}  {e['event']:24} {e['soul'] or '':20} {json.dumps(e['data'], ensure_ascii=False)[:80]}")
            return 0
    except ObitelError as e:
        print(f"Ошибка Обители: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
