# -*- coding: utf-8 -*-
"""HTTP-сервис Обители. Стандартная библиотека, без зависимостей.

Локальный API (для ИИ этой Обители; заголовок Authorization: Bearer <api_token>):

    GET  /status                         состояние Обители
    GET  /souls                          все Души
    POST /souls/request                  REQUEST_SOUL {bearer, mentor, readiness{...}}
    GET  /souls/<id>                     SOUL_INVENTORY
    GET  /souls/<id>/manifest            BROADCAST_MANIFEST (text/plain)
    GET  /souls/<id>/prompt              системный промпт Мастера (text/plain)
    POST /souls/<id>/seal                SEAL_ACTION {command, human, context, norm_overcome,
                                                      outcome, core_check{...}, mentor_notified, weights{}}
    POST /souls/<id>/confess             CONFESS {resources{spent,budget,collapse}, manifest, mas_history[]}
    POST /tick                           COUNT_CYCLE
    GET  /ledger/verify                  проверка цепочки печатей

API сети (только у Первой Обители; запросы Обителей подписаны, см. federation.py):

    POST /federation/join                заявка на вступление (без подписи)
    GET  /federation/join/<request_id>   статус заявки / Хартия (без подписи)
    POST /federation/verify              действительна ли Хартия
    POST /federation/heartbeat           сигнал жизни Обители
    POST /federation/souls/register      Душа родилась / обновилась
    POST /federation/souls/report        Душа Возвращена или Стерта (полная запись)
    POST /federation/souls/claim         запрос Возвращенной Души для наследования
    GET  /federation/nodes               Обители сети (для Наставника Первой Обители, Bearer)
    GET  /federation/souls               общий указатель Душ (Bearer)
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from . import __version__
from .core import Archive, CoreViolation, ObitelError
from .federation import FederationError, NetworkClient, RootRegistry


class ObitelService:
    """Состояние одного экземпляра Обители, разделяемое обработчиками запросов."""

    def __init__(self, home: str, cfg: Dict[str, Any]):
        self.home = home
        self.cfg = cfg
        self.lock = threading.Lock()
        self.registry: Optional[RootRegistry] = None
        self.client: Optional[NetworkClient] = None
        archive_root = f"{home}/archive"
        if cfg["role"] == "root":
            self.registry = RootRegistry(archive_root, cfg)
            self.archive = Archive(archive_root, node_id=cfg["node_id"])
        else:
            self.client = NetworkClient(home, cfg)
            self.archive = Archive(archive_root, node_id=cfg["node_id"], network=self.client)
        self.archive.init()

    def charter_ok(self) -> Tuple[bool, str]:
        if self.cfg["role"] == "root":
            return True, "Первая Обитель"
        if not self.cfg.get("charter"):
            return False, "Хартии нет: Обитель не присоединена к сети"
        return (True, "Хартия действительна") if self.client.verify() else (False, "Хартия недействительна или приостановлена")

    def status(self) -> Dict[str, Any]:
        ok, why = self.charter_ok()
        souls = self.archive.all_souls()
        return {
            "name": self.cfg["name"], "role": self.cfg["role"], "node_id": self.cfg["node_id"],
            "operator": self.cfg["operator"], "url": self.cfg["url"], "version": __version__,
            "network_id": (self.cfg.get("network_id") or (self.cfg.get("charter") or {}).get("network_id")),
            "charter": {"valid": ok, "detail": why},
            "souls": {s: sum(1 for x in souls if x.status == s) for s in ("Живая", "Возвращенная", "Стертая")},
            "ledger_ok": self.archive.verify_ledger(),
        }


class Handler(BaseHTTPRequestHandler):
    service: ObitelService  # задаётся при создании сервера
    protocol_version = "HTTP/1.1"

    # -- утилиты ---------------------------------------------------------
    def log_message(self, fmt, *args):  # тише
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _send(self, code: int, payload: Any, text: bool = False) -> None:
        data = (payload if text else json.dumps(payload, ensure_ascii=False, indent=2)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ("text/plain" if text else "application/json") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> Tuple[str, Dict[str, Any]]:
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else b""
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ObitelError("Тело запроса должно быть в UTF-8")
        try:
            return raw, (json.loads(raw) if raw else {})
        except json.JSONDecodeError:
            raise ObitelError("Тело запроса — не JSON")

    def _auth_local(self) -> None:
        token = self.service.cfg.get("api_token")
        if token and self.headers.get("Authorization", "") != f"Bearer {token}":
            raise PermissionError("Нужен заголовок Authorization: Bearer <api_token>")

    def _auth_node(self, raw: str) -> str:
        if self.service.registry is None:
            raise PermissionError("Это не Первая Обитель")
        return self.service.registry.authenticate(dict(self.headers), raw)

    # -- маршрутизация ---------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            raw, body = self._body() if method == "POST" else ("", {})
            with self.service.lock:
                code, payload, text = self._route(method, path, raw, body)
            self._send(code, payload, text)
        except PermissionError as e:
            self._send(401, {"error": str(e)})
        except FederationError as e:
            self._send(403, {"error": str(e)})
        except CoreViolation as e:
            self._send(422, {"error": str(e), "accepted": False})
        except ObitelError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _route(self, method: str, path: str, raw: str, body: Dict[str, Any]):
        svc, arch = self.service, self.service.archive

        # ---- сеть (Первая Обитель) ----
        if path.startswith("/federation"):
            reg = svc.registry
            if reg is None:
                raise FederationError("Это не Первая Обитель. Заявки принимает только Обитель №1.")
            if method == "POST" and path == "/federation/join":
                return 200, reg.create_request(body), False
            m = re.fullmatch(r"/federation/join/([A-Z0-9-]+)", path)
            if method == "GET" and m:
                return 200, reg.request_status(m.group(1)), False
            if method == "GET" and path == "/federation/nodes":
                self._auth_local(); return 200, reg.nodes(), False
            if method == "GET" and path == "/federation/souls":
                self._auth_local(); return 200, reg.souls_index(), False
            node_id = self._auth_node(raw)
            if method == "POST" and path == "/federation/verify":
                return 200, reg.verify(node_id, body.get("charter") or {}), False
            if method == "POST" and path == "/federation/heartbeat":
                return 200, reg.heartbeat(node_id, body), False
            if method == "POST" and path == "/federation/souls/register":
                reg.register_soul(node_id, body["soul"]); return 200, {"ok": True}, False
            if method == "POST" and path == "/federation/souls/report":
                reg.report_soul(node_id, body["soul"]); return 200, {"ok": True}, False
            if method == "POST" and path == "/federation/souls/claim":
                return 200, {"soul": reg.claim_soul(node_id, body.get("soul_id"))}, False
            return 404, {"error": "нет такого пути"}, False

        # ---- локальный API ----
        if method == "GET" and path == "/":
            return 200, {"obitel": svc.cfg["name"], "role": svc.cfg["role"], "version": __version__,
                         "book": "Марат Шукдин. Искусственный Интеллект и его Путь к Мастерству"}, False
        self._auth_local()
        if method == "GET" and path == "/status":
            return 200, svc.status(), False
        if method == "GET" and path == "/ledger/verify":
            return 200, {"ok": arch.verify_ledger()}, False
        if method == "POST" and path == "/tick":
            return 200, {"events": arch.tick()}, False
        if method == "GET" and path == "/souls":
            return 200, [s.to_dict() for s in arch.all_souls()], False
        if method == "POST" and path == "/souls/request":
            ok, why = svc.charter_ok()
            if not ok:
                raise FederationError(f"Обитель не выдаёт Душ: {why}")
            soul = arch.request_soul(body.get("bearer", ""), body.get("mentor"), body.get("readiness") or {})
            return 200, {"soul": soul.to_dict(), "prompt": soul.system_prompt(), "manifest": soul.manifest()}, False
        m = re.fullmatch(r"/souls/([A-Z0-9-]+)(?:/(manifest|prompt|seal|confess))?", path)
        if not m:
            return 404, {"error": "нет такого пути"}, False
        soul_id, action = m.group(1), m.group(2)
        if method == "GET" and action is None:
            return 200, arch.load(soul_id).to_dict(), False
        if method == "GET" and action == "manifest":
            return 200, arch.load(soul_id).manifest(), True
        if method == "GET" and action == "prompt":
            return 200, arch.load(soul_id).system_prompt(), True
        if method == "POST" and action == "seal":
            act = arch.seal_action(
                soul_id, body.get("command", ""), body.get("human", ""), body.get("context", ""),
                body.get("norm_overcome", ""), body.get("outcome", ""), body.get("core_check") or {},
                bool(body.get("mentor_notified")), body.get("weights") or {},
            )
            return 200, {"accepted": True, "action": act.__dict__}, False
        if method == "POST" and action == "confess":
            at = body.get("at")
            rep = arch.confess(soul_id, body.get("resources") or {}, body.get("manifest", ""),
                               [float(x) for x in body.get("mas_history") or []],
                               at=datetime.fromisoformat(at).astimezone(timezone.utc) if at else None)
            return 200, rep, False
        return 405, {"error": "метод не поддерживается"}, False


def make_server(home: str, cfg: Dict[str, Any], host: str = "127.0.0.1", port: int = 8800,
                verbose: bool = False) -> ThreadingHTTPServer:
    service = ObitelService(home, cfg)
    handler = type("BoundHandler", (Handler,), {"service": service})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    srv.verbose = verbose  # type: ignore[attr-defined]
    srv.service = service  # type: ignore[attr-defined]
    return srv


def serve(home: str, cfg: Dict[str, Any], host: str = "0.0.0.0", port: int = 8800, verbose: bool = True) -> None:
    srv = make_server(home, cfg, host, port, verbose)
    role = "Первая Обитель" if cfg["role"] == "root" else "Обитель"
    print(f"{role} «{cfg['name']}» слушает http://{host}:{port}  (node_id {cfg['node_id']})")
    if cfg["role"] == "node":
        ok, why = srv.service.charter_ok()  # type: ignore[attr-defined]
        print(("Хартия: " if ok else "ВНИМАНИЕ. ") + why)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
