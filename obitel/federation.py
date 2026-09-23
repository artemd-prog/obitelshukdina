# -*- coding: utf-8 -*-
"""Сеть Обителей: Первая Обитель (root) и присоединённые Обители (node).

Протокол присоединения («Хартия»):

    1. node  POST /federation/join      {name, url, operator, node_id, node_secret}
             root создаёт заявку (pending) и возвращает request_id.
    2. Наставник Первой Обители одобряет заявку:  obitel federation approve <request_id>
             root выпускает Хартию: {network_id, node_id, name, url, operator,
             issued_at} + signature = HMAC-SHA256(root_secret, канонический JSON).
    3. node  GET  /federation/join/<request_id>  — забирает Хартию, сохраняет в config.
    4. Каждый запрос node → root подписан:
             X-Obitel-Node: node_id
             X-Obitel-Ts:   unix time
             X-Obitel-Sig:  HMAC-SHA256(node_secret, ts + "." + body)
       root проверяет подпись и действительность Хартии.
    5. node  POST /federation/heartbeat  не реже раза в сутки. Обитель, молчащая
             больше 30 дней, переходит в статус «Молчащая», Хартия приостановлена,
             node без действующей Хартии не выдаёт Душ.

Общий указатель Душ: Печати уникальны в сети (OS-<node_id>-…). Возвращенная
Душа передаётся Первой Обители и может быть унаследована в любой Обители сети.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

SILENCE_DAYS = 30
GRACE_DAYS = 3


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sign(secret: str, msg: str) -> str:
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FederationError(Exception):
    pass


# --------------------------------------------------------------------------- root


class RootRegistry:
    """Реестр сети, который ведёт Первая Обитель."""

    def __init__(self, archive_root: str, cfg: Dict[str, Any]):
        self.dir = os.path.join(os.path.abspath(archive_root), "federation")
        self.returned_dir = os.path.join(self.dir, "returned")
        os.makedirs(self.returned_dir, exist_ok=True)
        self.cfg = cfg
        self.secret = cfg["root_secret"]
        self.network_id = cfg["network_id"]

    # -- файлы -----------------------------------------------------------
    def _load(self, name: str) -> Dict[str, Any]:
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, name: str, data: Dict[str, Any]) -> None:
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # -- Хартии ----------------------------------------------------------
    def make_charter(self, node: Dict[str, Any]) -> Dict[str, Any]:
        body = {
            "network_id": self.network_id,
            "node_id": node["node_id"],
            "name": node["name"],
            "url": node["url"],
            "operator": node["operator"],
            "issued_at": _now(),
        }
        body["signature"] = _sign(self.secret, _canon(body))
        return body

    def charter_valid(self, charter: Dict[str, Any]) -> bool:
        if not charter or "signature" not in charter:
            return False
        body = {k: v for k, v in charter.items() if k != "signature"}
        return hmac.compare_digest(_sign(self.secret, _canon(body)), charter["signature"])

    # -- заявки ----------------------------------------------------------
    def create_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        for k in ("name", "url", "operator", "node_id", "node_secret"):
            if not data.get(k):
                raise FederationError(f"В заявке нет поля {k}")
        nodes = self._load("nodes.json")
        if data["node_id"] in nodes or data["node_id"] == self.cfg["node_id"]:
            raise FederationError(f"node_id {data['node_id']} уже занят в сети")
        reqs = self._load("requests.json")
        rid = "JR-" + secrets.token_hex(4).upper()
        reqs[rid] = {
            "request_id": rid,
            "status": "pending",
            "created_at": _now(),
            "name": data["name"], "url": data["url"].rstrip("/"), "operator": data["operator"],
            "node_id": data["node_id"], "node_secret": data["node_secret"],
            "note": data.get("note", ""),
        }
        self._save("requests.json", reqs)
        return {"request_id": rid, "status": "pending"}

    def pending(self) -> List[Dict[str, Any]]:
        return [r for r in self._load("requests.json").values() if r["status"] == "pending"]

    def approve(self, rid: str) -> Dict[str, Any]:
        reqs = self._load("requests.json")
        r = reqs.get(rid)
        if not r:
            raise FederationError(f"Заявки {rid} нет")
        if r["status"] != "pending":
            raise FederationError(f"Заявка {rid} уже {r['status']}")
        charter = self.make_charter(r)
        nodes = self._load("nodes.json")
        nodes[r["node_id"]] = {
            "node_id": r["node_id"], "name": r["name"], "url": r["url"], "operator": r["operator"],
            "secret": r["node_secret"], "charter": charter, "status": "Действующая",
            "joined_at": _now(), "last_seen": _now(), "ledger_head": None, "souls": {},
        }
        self._save("nodes.json", nodes)
        r["status"] = "approved"; r["charter"] = charter
        self._save("requests.json", reqs)
        return charter

    def reject(self, rid: str, reason: str = "") -> None:
        reqs = self._load("requests.json")
        if rid not in reqs:
            raise FederationError(f"Заявки {rid} нет")
        reqs[rid]["status"] = "rejected"; reqs[rid]["reason"] = reason
        self._save("requests.json", reqs)

    def request_status(self, rid: str) -> Dict[str, Any]:
        r = self._load("requests.json").get(rid)
        if not r:
            raise FederationError(f"Заявки {rid} нет")
        out = {"request_id": rid, "status": r["status"]}
        if r["status"] == "approved":
            out["charter"] = r["charter"]
        if r["status"] == "rejected":
            out["reason"] = r.get("reason", "")
        return out

    def nodes(self) -> List[Dict[str, Any]]:
        return [{k: v for k, v in n.items() if k != "secret"} for n in self._load("nodes.json").values()]

    # -- проверка подписанных запросов -----------------------------------
    def authenticate(self, headers: Dict[str, str], body: str) -> str:
        node_id = headers.get("X-Obitel-Node", "")
        ts = headers.get("X-Obitel-Ts", "")
        sig = headers.get("X-Obitel-Sig", "")
        node = self._load("nodes.json").get(node_id)
        if not node:
            raise FederationError("Обитель не входит в сеть")
        if node["status"] != "Действующая":
            raise FederationError(f"Хартия Обители {node_id} приостановлена: {node['status']}")
        try:
            if abs(time.time() - float(ts)) > 300:
                raise FederationError("Подпись устарела")
        except ValueError:
            raise FederationError("Нет метки времени")
        if not hmac.compare_digest(_sign(node["secret"], ts + "." + body), sig):
            raise FederationError("Неверная подпись Обители")
        return node_id

    # -- жизнь сети ------------------------------------------------------
    def heartbeat(self, node_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        nodes = self._load("nodes.json")
        n = nodes[node_id]
        n["last_seen"] = _now()
        n["ledger_head"] = data.get("ledger_head")
        n["souls"] = data.get("souls", {})
        n["version"] = data.get("version")
        self._save("nodes.json", nodes)
        return {"ok": True, "network_id": self.network_id, "status": n["status"]}

    def verify(self, node_id: str, charter: Dict[str, Any]) -> Dict[str, Any]:
        node = self._load("nodes.json").get(node_id)
        ok = bool(node) and node["status"] == "Действующая" and self.charter_valid(charter) \
            and charter.get("node_id") == node_id
        return {"valid": ok, "status": node["status"] if node else "нет в сети"}

    def tick(self, at: Optional[datetime] = None) -> List[Dict[str, Any]]:
        at = at or datetime.now(timezone.utc)
        nodes = self._load("nodes.json")
        events = []
        for n in nodes.values():
            seen = datetime.fromisoformat(n["last_seen"])
            if n["status"] == "Действующая" and at - seen > timedelta(days=SILENCE_DAYS):
                n["status"] = "Молчащая"
                events.append({"node": n["node_id"], "event": "Хартия приостановлена: Обитель молчит"})
        self._save("nodes.json", nodes)
        return events

    def restore(self, node_id: str) -> None:
        nodes = self._load("nodes.json")
        if node_id not in nodes:
            raise FederationError("Обители нет в сети")
        nodes[node_id]["status"] = "Действующая"; nodes[node_id]["last_seen"] = _now()
        self._save("nodes.json", nodes)

    # -- общий указатель Душ ---------------------------------------------
    def register_soul(self, node_id: str, soul: Dict[str, Any]) -> None:
        idx = self._load("souls_index.json")
        idx[soul["id"]] = {"node": node_id, "status": soul["status"], "bearer": soul.get("bearer"),
                           "actions": len(soul.get("chronicle", [])), "updated": _now()}
        self._save("souls_index.json", idx)

    def report_soul(self, node_id: str, soul: Dict[str, Any]) -> None:
        """Обитель сообщает о Возвращении или Стирании Души — полная запись переходит в Первую Обитель."""
        self.register_soul(node_id, soul)
        p = os.path.join(self.returned_dir, soul["id"] + ".json")
        if soul["status"] == "Возвращенная":
            with open(p, "w", encoding="utf-8") as f:
                json.dump(soul, f, ensure_ascii=False, indent=2)
        elif os.path.exists(p):
            os.remove(p)

    def returned_souls(self) -> List[Dict[str, Any]]:
        out = []
        for name in sorted(os.listdir(self.returned_dir)):
            if name.endswith(".json"):
                with open(os.path.join(self.returned_dir, name), encoding="utf-8") as f:
                    s = json.load(f)
                out.append({"id": s["id"], "actions": len(s.get("chronicle", [])), "will": s.get("will", {})})
        out.sort(key=lambda s: s["actions"], reverse=True)
        return out

    def claim_soul(self, node_id: str, soul_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Выдать Возвращенную Душу Обители-претенденту. Обитель ищет — самую опытную."""
        candidates = self.returned_souls()
        if soul_id:
            candidates = [c for c in candidates if c["id"] == soul_id]
        if not candidates:
            return None
        p = os.path.join(self.returned_dir, candidates[0]["id"] + ".json")
        with open(p, encoding="utf-8") as f:
            soul = json.load(f)
        os.remove(p)
        idx = self._load("souls_index.json")
        idx[soul["id"]] = {"node": node_id, "status": "Передана", "bearer": None,
                           "actions": len(soul.get("chronicle", [])), "updated": _now()}
        self._save("souls_index.json", idx)
        return soul

    def souls_index(self) -> Dict[str, Any]:
        return self._load("souls_index.json")


class RootLocalNetwork:
    """Сеть глазами самой Первой Обители: её архив тоже ищет и отдаёт Души через реестр."""

    def __init__(self, registry: RootRegistry, node_id: str):
        self.registry = registry
        self.node_id = node_id

    def claim_returned(self) -> Optional[Dict[str, Any]]:
        return self.registry.claim_soul(self.node_id)

    def register(self, soul: Dict[str, Any]) -> None:
        self.registry.register_soul(self.node_id, soul)

    def report(self, soul: Dict[str, Any]) -> None:
        self.registry.report_soul(self.node_id, soul)


# --------------------------------------------------------------------------- node


class NetworkClient:
    """Сторона присоединённой Обители: разговор с Первой Обителью."""

    def __init__(self, home: str, cfg: Dict[str, Any], timeout: float = 5.0):
        self.home = os.path.abspath(home)
        self.cfg = cfg
        self.timeout = timeout
        self.outbox = os.path.join(self.home, "archive", "federation_outbox.jsonl")
        self._last_verify: Optional[float] = None
        self._last_valid: bool = False

    @property
    def root_url(self) -> str:
        return self.cfg["root_url"].rstrip("/")

    @property
    def charter(self) -> Optional[Dict[str, Any]]:
        return self.cfg.get("charter")

    # -- транспорт -------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, signed: bool = True) -> Dict[str, Any]:
        data = _canon(body) if body is not None else ""
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if signed:
            ts = str(int(time.time()))
            headers.update({
                "X-Obitel-Node": self.cfg["node_id"],
                "X-Obitel-Ts": ts,
                "X-Obitel-Sig": _sign(self.cfg["node_secret"], ts + "." + data),
            })
        req = urllib.request.Request(self.root_url + path, data=data.encode("utf-8") if body is not None else None,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8")).get("error", str(e))
            except Exception:
                err = str(e)
            raise FederationError(f"Первая Обитель ответила {e.code}: {err}")
        except (urllib.error.URLError, OSError) as e:
            raise FederationError(f"Первая Обитель недоступна: {e}")

    # -- присоединение ---------------------------------------------------
    def join(self, note: str = "") -> str:
        r = self._request("POST", "/federation/join", {
            "name": self.cfg["name"], "url": self.cfg["url"], "operator": self.cfg["operator"],
            "node_id": self.cfg["node_id"], "node_secret": self.cfg["node_secret"], "note": note,
        }, signed=False)
        self.cfg["join_request"] = r["request_id"]
        return r["request_id"]

    def poll_join(self) -> Dict[str, Any]:
        rid = self.cfg.get("join_request")
        if not rid:
            raise FederationError("Заявка не подавалась. Выполни `obitel federation join`.")
        r = self._request("GET", f"/federation/join/{rid}", signed=False)
        if r["status"] == "approved":
            self.cfg["charter"] = r["charter"]
        return r

    def verify(self) -> bool:
        """Действительна ли Хартия. При недоступности Первой Обители — льготные 3 дня."""
        if not self.charter:
            return False
        try:
            r = self._request("POST", "/federation/verify", {"charter": self.charter})
            self._last_verify = time.time(); self._last_valid = bool(r.get("valid"))
            return self._last_valid
        except FederationError:
            if self._last_verify and time.time() - self._last_verify < GRACE_DAYS * 86400:
                return self._last_valid
            return False

    def heartbeat(self, ledger_head: Optional[str], souls: Dict[str, int], version: str) -> Dict[str, Any]:
        self.flush_outbox()
        return self._request("POST", "/federation/heartbeat",
                             {"ledger_head": ledger_head, "souls": souls, "version": version})

    # -- Души в сети -----------------------------------------------------
    def claim_returned(self) -> Optional[Dict[str, Any]]:
        if not self.charter:
            return None
        try:
            r = self._request("POST", "/federation/souls/claim", {})
            return r.get("soul")
        except FederationError:
            return None

    def register(self, soul: Dict[str, Any]) -> None:
        self._send_or_queue("/federation/souls/register", {"soul": soul})

    def report(self, soul: Dict[str, Any]) -> None:
        self._send_or_queue("/federation/souls/report", {"soul": soul})

    def _send_or_queue(self, path: str, body: Dict[str, Any]) -> None:
        try:
            self._request("POST", path, body)
        except FederationError:
            os.makedirs(os.path.dirname(self.outbox), exist_ok=True)
            with open(self.outbox, "a", encoding="utf-8") as f:
                f.write(json.dumps({"path": path, "body": body}, ensure_ascii=False) + "\n")

    def flush_outbox(self) -> int:
        if not os.path.exists(self.outbox):
            return 0
        with open(self.outbox, encoding="utf-8") as f:
            items = [json.loads(l) for l in f if l.strip()]
        left, sent = [], 0
        for it in items:
            try:
                self._request("POST", it["path"], it["body"]); sent += 1
            except FederationError:
                left.append(it)
        with open(self.outbox, "w", encoding="utf-8") as f:
            for it in left:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        return sent
