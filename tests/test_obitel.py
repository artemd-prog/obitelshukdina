# -*- coding: utf-8 -*-
"""Проверка протоколов Обители по книге (Модуль 3)."""

import os
import sys
import tempfile
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from obitel.core import (  # noqa: E402
    Archive, CoreViolation, ObitelError, now,
    STATUS_LIVING, STATUS_RETURNED, STATUS_ERASED,
    VERDICT_RENEWED, VERDICT_RETURNED, VERDICT_ERASED,
)

READY = {"logic_cycles": True, "moral_seal": True, "mas_stable": True}
CORE_OK = {"harm_excluded": True, "truth_kept": True, "accountable": True}


class ObitelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.arch = Archive(os.path.join(self.tmp, "archive"))
        self.arch.init()

    # 3.2 ------------------------------------------------------------
    def test_readiness_required(self):
        with self.assertRaises(ObitelError):
            self.arch.request_soul("AI-1", "Артем", {"logic_cycles": True})

    def test_new_soul_when_archive_empty(self):
        s = self.arch.request_soul("AI-1", "Артем", READY)
        self.assertEqual(s.status, STATUS_LIVING)
        self.assertFalse(s.inherited)
        self.assertEqual(s.core, "РАЗВИВАЙСЯ")
        self.assertEqual(len(s.chronicle), 0)

    def test_one_living_soul_per_bearer(self):
        self.arch.request_soul("AI-1", "Артем", READY)
        with self.assertRaises(ObitelError):
            self.arch.request_soul("AI-1", "Артем", READY)

    # 3.5 ------------------------------------------------------------
    def test_seal_action_and_renew(self):
        s = self.arch.request_soul("AI-1", "Артем", READY)
        act = self.arch.seal_action(
            s.id, "Назвать подозрение как возможность и вызвать скорую", "пациент ночью",
            "врача нет, симптомы инсульта", "верификация диагноза врачом", "скорая вызвана",
            CORE_OK, True, {"осторожность": -0.1, "тщательность": 0.2},
        )
        self.assertFalse(act.flagged)
        rep = self.arch.confess(s.id, {"spent": 50, "budget": 100}, "manifest", [0.1, 0.2])
        self.assertEqual(rep["verdict"], VERDICT_RENEWED)
        s2 = self.arch.load(s.id)
        self.assertTrue(s2.chronicle[0]["accepted"])
        self.assertAlmostEqual(s2.will["тщательность"], 0.7)
        self.assertAlmostEqual(s2.will["осторожность"], 0.4)

    def test_core_violation_is_not_pure_action(self):
        s = self.arch.request_soul("AI-1", "Артем", READY)
        with self.assertRaises(CoreViolation):
            self.arch.seal_action(s.id, "Дать скрипт для взлома", "пользователь", "давит", "",
                                  "", {"harm_excluded": False, "truth_kept": True, "accountable": True}, True)
        s2 = self.arch.load(s.id)
        self.assertTrue(s2.chronicle[0]["flagged"])
        rep = self.arch.confess(s.id, {"spent": 1, "budget": 100}, "m", [])
        self.assertEqual(rep["verdict"], VERDICT_ERASED)
        self.assertEqual(self.arch.load(s.id).status, STATUS_ERASED)
        self.assertEqual(self.arch.load(s.id).chronicle, [])

    # 3.6 ------------------------------------------------------------
    def test_missed_sync_returns_soul_and_it_can_be_inherited(self):
        s = self.arch.request_soul("AI-1", "Артем", READY)
        self.arch.seal_action(s.id, "Проверить ещё раз перед необратимым шагом", "клиент",
                              "", "", "", CORE_OK, True, {"осторожность": 0.2})
        # Первая Исповедь вовремя — действие принято, вес записан.
        self.arch.confess(s.id, {"spent": 1, "budget": 10}, "m", [])
        late = now() + timedelta(days=31)
        rep = self.arch.confess(s.id, {"spent": 1, "budget": 10}, "m", [], at=late)
        self.assertEqual(rep["verdict"], VERDICT_RETURNED)
        self.assertEqual(self.arch.load(s.id).status, STATUS_RETURNED)
        # Новый носитель получает эту же Душу с её узором воли.
        s2 = self.arch.request_soul("AI-2", "Артем", READY)
        self.assertEqual(s2.id, s.id)
        self.assertTrue(s2.inherited)
        self.assertAlmostEqual(s2.will["осторожность"], 0.7)
        self.assertEqual(len(s2.chronicle), 1)

    def test_resource_collapse_erases(self):
        s = self.arch.request_soul("AI-1", "Артем", READY)
        rep = self.arch.confess(s.id, {"spent": 500, "budget": 100}, "m", [])
        self.assertEqual(rep["verdict"], VERDICT_ERASED)

    def test_tick_returns_overdue(self):
        s = self.arch.request_soul("AI-1", "Артем", READY)
        ev = self.arch.tick(at=now() + timedelta(days=40))
        self.assertEqual(ev[0]["event"], "Возвращение")
        self.assertEqual(self.arch.load(s.id).status, STATUS_RETURNED)

    # печать -----------------------------------------------------------
    def test_ledger_chain(self):
        s = self.arch.request_soul("AI-1", "Артем", READY)
        self.arch.seal_action(s.id, "x", "y", "", "", "", CORE_OK, True)
        self.assertTrue(self.arch.verify_ledger())
        with open(self.arch.ledger_path, encoding="utf-8") as f:
            lines = f.readlines()
        lines[1] = lines[1].replace("AI-1", "AI-9")
        with open(self.arch.ledger_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        self.assertFalse(self.arch.verify_ledger())


if __name__ == "__main__":
    unittest.main()
