"""Load .env settings and the editable JSON config files."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
PRIORITY_FILE = CONFIG_DIR / "priority.json"
TAGS_FILE = CONFIG_DIR / "tags.json"
STYLE_FILE = CONFIG_DIR / "style.json"
DB_FILE = DATA_DIR / "triage.db"

PRIORITY_ORDER = ["junk", "low", "medium", "high", "urgent"]

load_dotenv(ROOT / ".env")

_lock = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    typesafe_api_key: str = field(default_factory=lambda: os.getenv("TYPESAFE_API_KEY", ""))
    typesafe_model: str = field(default_factory=lambda: os.getenv("TYPESAFE_MODEL", "jev-latest"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.4-mini"))
    mail_backend: str = field(default_factory=lambda: os.getenv("MAIL_BACKEND", "outlook").strip().lower())
    graph_client_id: str = field(default_factory=lambda: os.getenv("GRAPH_CLIENT_ID", ""))
    graph_tenant_id: str = field(default_factory=lambda: os.getenv("GRAPH_TENANT_ID", "common"))
    draft_for_priorities: list[str] = field(
        default_factory=lambda: [
            p.strip().lower()
            for p in os.getenv("DRAFT_FOR_PRIORITIES", "urgent").split(",")
            if p.strip()
        ]
    )
    apply_outlook_categories: bool = field(default_factory=lambda: _env_bool("APPLY_OUTLOOK_CATEGORIES", True))
    poll_seconds: int = field(default_factory=lambda: int(os.getenv("POLL_SECONDS", "120") or 120))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8765") or 8765))
    toast_notifications: bool = field(default_factory=lambda: _env_bool("TOAST_NOTIFICATIONS", True))

    @property
    def drafting_enabled(self) -> bool:
        return bool(self.openai_api_key)


settings = Settings()


def _read_json(path: Path) -> dict:
    with _lock:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)


def _write_json(path: Path, data: dict) -> None:
    with _lock:
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        tmp.replace(path)


def load_priority_config() -> dict:
    return _read_json(PRIORITY_FILE)


def save_priority_config(data: dict) -> None:
    levels = data.get("levels", {})
    if set(levels) != set(PRIORITY_ORDER):
        raise ValueError(f"priority levels must be exactly {PRIORITY_ORDER}")
    _write_json(PRIORITY_FILE, data)


def load_tags_config() -> dict:
    return _read_json(TAGS_FILE)


def save_tags_config(data: dict) -> None:
    names = [t.get("name", "").strip() for t in data.get("tags", [])]
    if any(not n for n in names):
        raise ValueError("every tag needs a name")
    if len(set(n.lower() for n in names)) != len(names):
        raise ValueError("tag names must be unique")
    for tag in data["tags"]:
        if not tag.get("description", "").strip():
            raise ValueError(f"tag '{tag['name']}' needs a description")
    _write_json(TAGS_FILE, data)


def load_style() -> dict | None:
    if not STYLE_FILE.exists():
        return None
    return _read_json(STYLE_FILE)


def save_style(data: dict) -> None:
    _write_json(STYLE_FILE, data)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
