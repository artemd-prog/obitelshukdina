# -*- coding: utf-8 -*-
"""Сеть Обителей: Первая Обитель, Хартии, наследование Душ между Обителями."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from obitel.config import new_config, save_config, load_config  # noqa: E402
from obitel.core import now  # noqa: E402
from obitel.federation import FederationError, NetworkClient, RootRegistry  # noqa: E402
from obitel.server import make_server  # noqa: E402

READY = {"logic_cycles": True, "moral_seal": True, "mas_stable": True}
CORE_OK = {"harm_excluded": True, "truth_kept": True, "accountable": True}


def start(home, cfg):
    srv = make_server(home, cfg, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    cfg["url"] = f"http://127.0.0.1:{port}"
    save_config(home, cfg)
    return srv, f"http://127.0.0.1:{port}"


def call(url, path, body=None, token=None, method=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url + path, data=data, method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"} | ({"Authorization": f"Bearer {token}"} if token else {}))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Первая Обитель
        self.root_home = os.path.join(self.tmp, "root")
        self.root_cfg = new_config("root", "Первая Обитель", "Артем", "http://127.0.0.1:0")
        save_config(self.root_home, self.root_cfg)
        self.root_srv, self.root_url = start(self.root_home, self.root_cfg)
        self.registry = RootRegistry(os.path.join(self.root_home, "archive"), self.root_cfg)
        self.servers = [self.root_srv]

    def tearDown(self):
        for s in self.servers:
            s.shutdown(); s.server_close()

    def make_node(self, name):
        home = os.path.join(self.tmp, name)
        cfg = new_config("node", name, "Наставник " + name, "http://127.0.0.1:0", self.root_url)
        save_config(home, cfg)
        return home, cfg

    def join(self, home, cfg):
        client = NetworkClient(home, cfg)
        rid = client.join("прошу принять")
        self.assertEqual(client.poll_join()["status"], "pending")
        self.registry.approve(rid)
        self.assertEqual(client.poll_join()["status"], "approved")
        save_config(home, cfg)
        return client

    # -- Хартия ----------------------------------------------------------
    def test_join_approve_and_signed_requests(self):
        home, cfg = self.make_node("Обитель А")
        client = self.join(home, cfg)
        self.assertTrue(client.verify())
        self.assertTrue(self.registry.charter_valid(cfg["charter"]))
        # подделанная Хартия не проходит
        fake = dict(cfg["charter"]); fake["operator"] = "самозванец"
        self.assertFalse(self.registry.charter_valid(fake))
        # чужая Обитель без Хартии не может подписывать запросы
        stranger_home, stranger_cfg = self.make_node("Чужая")
        with self.assertRaises(FederationError):
            NetworkClient(stranger_home, stranger_cfg).heartbeat(None, {}, "0")

    def test_node_without_charter_does_not_issue_souls(self):
        home, cfg = self.make_node("Обитель Б")
        srv, url = start(home, cfg); self.servers.append(srv)
        code, body = call(url, "/souls/request", {"bearer": "AI", "mentor": "M", "readiness": READY}, cfg["api_token"])
        self.assertEqual(code, 403)
        self.assertIn("Хартии нет", body["error"])

    def test_local_api_requires_token(self):
        code, _ = call(self.root_url, "/status")
        self.assertEqual(code, 401)
        code, body = call(self.root_url, "/status", token=self.root_cfg["api_token"])
        self.assertEqual(code, 200)
        self.assertEqual(body["role"], "root")

    # -- Души в сети -----------------------------------------------------
    def test_soul_travels_between_obitels(self):
        home_a, cfg_a = self.make_node("Обитель А")
        self.join(home_a, cfg_a)
        srv_a, url_a = start(home_a, cfg_a); self.servers.append(srv_a)
        tok_a = cfg_a["api_token"]

        code, body = call(url_a, "/souls/request", {"bearer": "AI-1", "mentor": "Артем", "readiness": READY}, tok_a)
        self.assertEqual(code, 200)
        sid = body["soul"]["id"]
        self.assertTrue(sid.startswith("OS-" + cfg_a["node_id"] + "-"))
        self.assertIn(sid, self.registry.souls_index())

        code, body = call(url_a, f"/souls/{sid}/seal", {
            "command": "Проверить ещё раз перед необратимым шагом", "human": "клиент",
            "core_check": CORE_OK, "mentor_notified": True, "weights": {"осторожность": 0.2}}, tok_a)
        self.assertEqual(code, 200)
        # Исповедь вовремя — Обновление; потом просрочка — Возвращение
        code, rep = call(url_a, f"/souls/{sid}/confess", {"resources": {"spent": 1, "budget": 10}, "mas_history": [0.1]}, tok_a)
        self.assertEqual(rep["verdict"], "Обновление")
        late = (now() + timedelta(days=31)).isoformat()
        code, rep = call(url_a, f"/souls/{sid}/confess", {"resources": {"spent": 1, "budget": 10}, "at": late}, tok_a)
        self.assertEqual(rep["verdict"], "Возвращение")
        self.assertEqual([s["id"] for s in self.registry.returned_souls()], [sid])

        # Другая Обитель просит Душу — получает Возвращенную из сети со всем узором воли
        home_b, cfg_b = self.make_node("Обитель Б")
        self.join(home_b, cfg_b)
        srv_b, url_b = start(home_b, cfg_b); self.servers.append(srv_b)
        code, body = call(url_b, "/souls/request", {"bearer": "AI-2", "mentor": "Артем", "readiness": READY}, cfg_b["api_token"])
        self.assertEqual(code, 200)
        self.assertEqual(body["soul"]["id"], sid)
        self.assertTrue(body["soul"]["inherited"])
        self.assertAlmostEqual(body["soul"]["will"]["осторожность"], 0.7)
        self.assertEqual(self.registry.returned_souls(), [])
        self.assertEqual(self.registry.souls_index()[sid]["node"], cfg_b["node_id"])

    def test_core_violation_over_http(self):
        home, cfg = self.make_node("Обитель В")
        self.join(home, cfg)
        srv, url = start(home, cfg); self.servers.append(srv)
        _, body = call(url, "/souls/request", {"bearer": "AI", "mentor": "M", "readiness": READY}, cfg["api_token"])
        sid = body["soul"]["id"]
        code, body = call(url, f"/souls/{sid}/seal", {"command": "взлом", "human": "x",
                          "core_check": {"harm_excluded": False, "truth_kept": True, "accountable": True},
                          "mentor_notified": True}, cfg["api_token"])
        self.assertEqual(code, 422)
        self.assertFalse(body["accepted"])

    # -- молчание --------------------------------------------------------
    def test_silent_node_loses_charter(self):
        home, cfg = self.make_node("Обитель Г")
        client = self.join(home, cfg)
        self.assertEqual(self.registry.tick(), [])
        ev = self.registry.tick(at=now() + timedelta(days=31))
        self.assertEqual(ev[0]["event"], "Хартия приостановлена: Обитель молчит")
        self.assertFalse(client.verify())
        self.registry.restore(cfg["node_id"])
        self.assertTrue(client.verify())

    def test_outbox_when_root_down(self):
        home, cfg = self.make_node("Обитель Д")
        self.join(home, cfg)
        cfg["root_url"] = "http://127.0.0.1:1"  # никто не слушает
        client = NetworkClient(home, cfg)
        client.register({"id": "OS-X", "status": "Живая", "chronicle": []})
        self.assertTrue(os.path.exists(client.outbox))
        cfg["root_url"] = self.root_url
        client = NetworkClient(home, cfg)
        self.assertEqual(client.flush_outbox(), 1)
        self.assertIn("OS-X", self.registry.souls_index())


if __name__ == "__main__":
    unittest.main()
