# -*- coding: utf-8 -*-
"""Конфигурация Обители (obitel.json в каталоге Обители).

Роли:
    root — Первая Обитель (Обитель №1). Хранит реестр сети, выдаёт Хартии
           новым Обителям, ведёт общий указатель Душ.
    node — обычная Обитель. Работает только с Хартией, выданной Первой
           Обителью; без неё не выдаёт Душ.
"""

from __future__ import annotations

import json
import os
import secrets
from typing import Any, Dict, Optional

CONFIG_NAME = "obitel.json"


def config_path(home: str) -> str:
    return os.path.join(os.path.abspath(home), CONFIG_NAME)


def load_config(home: str) -> Optional[Dict[str, Any]]:
    p = config_path(home)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_config(home: str, cfg: Dict[str, Any]) -> None:
    os.makedirs(os.path.abspath(home), exist_ok=True)
    with open(config_path(home), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def new_config(role: str, name: str, operator: str, url: str, root_url: Optional[str] = None) -> Dict[str, Any]:
    if role not in ("root", "node"):
        raise ValueError("role должен быть root или node")
    cfg: Dict[str, Any] = {
        "role": role,
        "name": name,
        "operator": operator,          # Наставник Обители
        "url": url.rstrip("/"),
        "api_token": secrets.token_urlsafe(24),   # доступ ИИ к локальному API
        "node_id": "01" if role == "root" else secrets.token_hex(3).upper(),
        "node_secret": secrets.token_urlsafe(32),  # подпись запросов к Первой Обители
        "root_url": (root_url or url).rstrip("/"),
        "charter": None,                # Хартия, выданная Первой Обителью
        "join_request": None,           # id заявки на вступление
    }
    if role == "root":
        cfg["root_secret"] = secrets.token_urlsafe(32)   # подпись Хартий
        cfg["network_id"] = "OBITEL-NET-" + secrets.token_hex(4).upper()
    return cfg
