"""Load .env settings and the editable JSON config files."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .prompts import DEFAULT_BANNED_PHRASES, DEFAULT_REVISION_PROMPT, DEFAULT_SYSTEM_PROMPT, PROMPT_PLACEHOLDERS

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
ENV_FILE = ROOT / ".env"
PRIORITY_FILE = CONFIG_DIR / "priority.json"
TAGS_FILE = CONFIG_DIR / "tags.json"
SHARED_TAGS_CACHE = CONFIG_DIR / "shared_tags.cache.json"
STYLE_FILE = CONFIG_DIR / "style.json"
DRAFTING_FILE = CONFIG_DIR / "drafting.json"
DB_FILE = DATA_DIR / "triage.db"

PRIORITY_ORDER = ["junk", "low", "medium", "high", "urgent"]

# Variables that were already in the process environment win over .env, both at start-up and on reload.
_PROCESS_ENV_KEYS = set(os.environ)
load_dotenv(ENV_FILE)

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
            for p in os.getenv("DRAFT_FOR_PRIORITIES", "urgent,high").split(",")
            if p.strip()
        ]
    )
    apply_outlook_categories: bool = field(default_factory=lambda: _env_bool("APPLY_OUTLOOK_CATEGORIES", True))
    poll_seconds: int = field(default_factory=lambda: int(os.getenv("POLL_SECONDS", "120") or 120))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8765") or 8765))
    toast_notifications: bool = field(default_factory=lambda: _env_bool("TOAST_NOTIFICATIONS", True))
    # stage 2
    shared_tags_source: str = field(default_factory=lambda: os.getenv("SHARED_TAGS_SOURCE", "").strip())
    shared_tags_refresh_hours: float = field(default_factory=lambda: float(os.getenv("SHARED_TAGS_REFRESH_HOURS", "6") or 6))
    slack_webhook_url: str = field(default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL", "").strip())
    digest_time: str = field(default_factory=lambda: os.getenv("DIGEST_TIME", "08:00").strip())
    digest_weekdays_only: bool = field(default_factory=lambda: _env_bool("DIGEST_WEEKDAYS_ONLY", True))
    digest_lookback_hours: float = field(default_factory=lambda: float(os.getenv("DIGEST_LOOKBACK_HOURS", "24") or 24))
    digest_on: bool = field(default_factory=lambda: _env_bool("DIGEST_ENABLED", True))

    @property
    def digest_enabled(self) -> bool:
        return bool(self.digest_on and self.digest_time and self.slack_webhook_url)

    def reload(self) -> None:
        """Re-read .env (after the dashboard saved it) and refresh every field in place."""
        for key, value in dotenv_values(ENV_FILE).items():
            if key not in _PROCESS_ENV_KEYS:
                os.environ[key] = value or ""
        self.__init__()

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


def load_local_tags_config() -> dict:
    return _read_json(TAGS_FILE)


def load_shared_tags_cache() -> dict | None:
    if not SHARED_TAGS_CACHE.exists():
        return None
    try:
        return _read_json(SHARED_TAGS_CACHE)
    except Exception:
        return None


def save_shared_tags_cache(data: dict) -> None:
    _write_json(SHARED_TAGS_CACHE, data)


def load_tags_config() -> dict:
    """Local tags merged with the cached shared set. Shared tags win on a name clash.

    Each tag carries `shared: bool` so the UI can render them read-only."""
    local = load_local_tags_config()
    shared = load_shared_tags_cache() or {}
    shared_tags = [dict(t, shared=True) for t in shared.get("tags", [])]
    shared_names = {t["name"].strip().lower() for t in shared_tags}
    local_tags = [dict(t, shared=False) for t in local.get("tags", []) if t.get("name", "").strip().lower() not in shared_names]
    return {
        "default_threshold": local.get("default_threshold", shared.get("default_threshold", 0.6)),
        "tags": shared_tags + local_tags,
        "shared_source": settings.shared_tags_source,
        "shared_synced_at": shared.get("synced_at"),
        "shared_error": shared.get("error"),
    }


def _validate_tags(tags: list[dict]) -> None:
    names = [t.get("name", "").strip() for t in tags]
    if any(not n for n in names):
        raise ValueError("every tag needs a name")
    if len(set(n.lower() for n in names)) != len(names):
        raise ValueError("tag names must be unique")
    for tag in tags:
        if not tag.get("description", "").strip():
            raise ValueError(f"tag '{tag['name']}' needs a description")


def save_tags_config(data: dict) -> None:
    """Persist only the local tags; shared ones come from the shared source."""
    local_tags = [
        {k: v for k, v in t.items() if k != "shared"}
        for t in data.get("tags", [])
        if not t.get("shared")
    ]
    _validate_tags(local_tags)
    shared_names = {t["name"].strip().lower() for t in (load_shared_tags_cache() or {}).get("tags", [])}
    clash = [t["name"] for t in local_tags if t["name"].strip().lower() in shared_names]
    if clash:
        raise ValueError(f"these names belong to shared tags: {', '.join(clash)}")
    current = load_local_tags_config()
    current["tags"] = local_tags
    if "default_threshold" in data:
        current["default_threshold"] = data["default_threshold"]
    _write_json(TAGS_FILE, current)


def load_style() -> dict | None:
    if not STYLE_FILE.exists():
        return None
    return _read_json(STYLE_FILE)


def save_style(data: dict) -> None:
    _write_json(STYLE_FILE, data)


# ---- .env editing (Settings page) ----------------------------------------------

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _quote_env(value: str) -> str:
    value = str(value)
    if value == "" or not re.search(r"""[\s#"'\\]""", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_env(changes: dict[str, str], path: Path | None = None) -> None:
    """Set keys in .env, keeping comments, order and unrelated lines. Missing keys are appended."""
    path = path or ENV_FILE
    raw = path.read_bytes() if path.exists() else b""
    newline = "\r\n" if b"\r\n" in raw else "\n"  # keep whatever line endings the file already uses
    lines = raw.decode("utf-8-sig").splitlines()
    remaining = dict(changes)
    out: list[str] = []
    for line in lines:
        m = _ENV_LINE.match(line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append(f"{key}={_quote_env(remaining.pop(key))}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        if not any(l.strip() == "# ---- Set from the dashboard ----" for l in out):
            out.append("# ---- Set from the dashboard ----")
        for key, value in remaining.items():
            out.append(f"{key}={_quote_env(value)}")
    with _lock:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes((newline.join(out) + newline).encode("utf-8"))
        tmp.replace(path)


# Every setting the dashboard can edit: env key -> (Settings attribute, type, group).
# Types: str, secret, bool, int, float, list (comma separated), choice:<a|b>.
SETTINGS_FIELDS: dict[str, dict] = {
    "MAIL_BACKEND": {"attr": "mail_backend", "type": "choice:outlook|graph", "group": "account"},
    "GRAPH_CLIENT_ID": {"attr": "graph_client_id", "type": "str", "group": "account"},
    "GRAPH_TENANT_ID": {"attr": "graph_tenant_id", "type": "str", "group": "account"},
    "TYPESAFE_API_KEY": {"attr": "typesafe_api_key", "type": "secret", "group": "connections"},
    "TYPESAFE_MODEL": {"attr": "typesafe_model", "type": "str", "group": "connections"},
    "OPENAI_API_KEY": {"attr": "openai_api_key", "type": "secret", "group": "connections"},
    "OPENAI_MODEL": {"attr": "openai_model", "type": "str", "group": "drafting"},
    "DRAFT_FOR_PRIORITIES": {"attr": "draft_for_priorities", "type": "list", "group": "drafting"},
    "APPLY_OUTLOOK_CATEGORIES": {"attr": "apply_outlook_categories", "type": "bool", "group": "triage"},
    "POLL_SECONDS": {"attr": "poll_seconds", "type": "int", "group": "triage", "min": 30, "max": 3600},
    "TOAST_NOTIFICATIONS": {"attr": "toast_notifications", "type": "bool", "group": "triage"},
    "SHARED_TAGS_SOURCE": {"attr": "shared_tags_source", "type": "str", "group": "tags"},
    "SHARED_TAGS_REFRESH_HOURS": {"attr": "shared_tags_refresh_hours", "type": "float", "group": "tags", "min": 0.1, "max": 168},
    "SLACK_WEBHOOK_URL": {"attr": "slack_webhook_url", "type": "secret", "group": "digest"},
    "DIGEST_ENABLED": {"attr": "digest_on", "type": "bool", "group": "digest"},
    "DIGEST_TIME": {"attr": "digest_time", "type": "str", "group": "digest"},
    "DIGEST_WEEKDAYS_ONLY": {"attr": "digest_weekdays_only", "type": "bool", "group": "digest"},
    "DIGEST_LOOKBACK_HOURS": {"attr": "digest_lookback_hours", "type": "float", "group": "digest", "min": 1, "max": 168},
}
SECRET_MASK = "••••••••"


def _mask(value: str) -> dict:
    return {"set": bool(value), "hint": value[-4:] if len(value) >= 12 else ""}


def settings_view() -> dict:
    """Current settings for the dashboard. Secrets are never returned, only whether they are set."""
    out: dict = {}
    for key, spec in SETTINGS_FIELDS.items():
        value = getattr(settings, spec["attr"])
        if spec["type"] == "secret":
            out[key] = _mask(value)
        elif spec["type"] == "list":
            out[key] = ",".join(value)
        else:
            out[key] = value
    return out


def _coerce(key: str, spec: dict, value) -> str:
    """Validate one incoming value and return the string to write to .env."""
    t = spec["type"]
    if t == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in {"1", "true", "yes", "on"}
        return "true" if bool(value) else "false"
    if t in ("int", "float"):
        try:
            num = int(value) if t == "int" else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if "min" in spec and num < spec["min"]:
            raise ValueError(f"{key} must be at least {spec['min']}")
        if "max" in spec and num > spec["max"]:
            raise ValueError(f"{key} must be at most {spec['max']}")
        return str(num)
    if t == "list":
        items = value if isinstance(value, list) else str(value).split(",")
        items = [str(i).strip().lower() for i in items if str(i).strip()]
        if key == "DRAFT_FOR_PRIORITIES" and any(i not in PRIORITY_ORDER for i in items):
            raise ValueError(f"{key} may only contain {', '.join(PRIORITY_ORDER)}")
        return ",".join(items)
    if t.startswith("choice:"):
        options = t.split(":", 1)[1].split("|")
        value = str(value).strip().lower()
        if value not in options:
            raise ValueError(f"{key} must be one of {', '.join(options)}")
        return value
    value = str(value if value is not None else "").strip()
    if key == "DIGEST_TIME":
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", value):
            raise ValueError("DIGEST_TIME must look like 08:00")
        hh, mm = value.split(":")
        value = f"{int(hh):02d}:{mm}"
    if key == "SLACK_WEBHOOK_URL" and value and not value.startswith("https://hooks.slack.com/"):
        raise ValueError("That does not look like a Slack incoming webhook URL (it starts with https://hooks.slack.com/)")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{key} cannot contain line breaks")
    return value


def apply_settings(changes: dict) -> list[str]:
    """Validate, write to .env and reload. Returns the keys that changed.

    Secrets: the mask placeholder means "leave as is"; an empty string clears the key."""
    to_write: dict[str, str] = {}
    for key, raw in changes.items():
        spec = SETTINGS_FIELDS.get(key)
        if spec is None:
            raise ValueError(f"unknown setting {key}")
        if spec["type"] == "secret" and (raw is None or raw == SECRET_MASK):
            continue
        to_write[key] = _coerce(key, spec, raw)
    if not to_write:
        return []
    update_env(to_write)
    settings.reload()
    return list(to_write)


# ---- drafting prompt --------------------------------------------------------

def load_drafting() -> dict:
    data = {}
    if DRAFTING_FILE.exists():
        try:
            data = _read_json(DRAFTING_FILE)
        except Exception:
            data = {}
    out = {
        "system_prompt": data.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
        "revision_prompt": data.get("revision_prompt") or DEFAULT_REVISION_PROMPT,
        "banned_phrases": data.get("banned_phrases") if isinstance(data.get("banned_phrases"), list) else list(DEFAULT_BANNED_PHRASES),
    }
    out["is_default"] = (
        out["system_prompt"] == DEFAULT_SYSTEM_PROMPT
        and out["revision_prompt"] == DEFAULT_REVISION_PROMPT
        and out["banned_phrases"] == list(DEFAULT_BANNED_PHRASES)
    )
    out["placeholders"] = PROMPT_PLACEHOLDERS
    return out


def validate_prompt(template: str, placeholders: dict) -> None:
    """Make sure a template only uses known placeholders and has balanced braces."""
    try:
        template.format_map({k: "x" for k in placeholders})
    except KeyError as err:
        raise ValueError(f"Unknown placeholder {{{err.args[0]}}}. Available: " + ", ".join("{" + k + "}" for k in placeholders))
    except (ValueError, IndexError) as err:
        raise ValueError(f"Unbalanced braces in the prompt: {err}. Write literal braces as {{{{ and }}}}.")


def save_drafting(data: dict) -> None:
    system_prompt = str(data.get("system_prompt", "")).strip()
    revision_prompt = str(data.get("revision_prompt", "")).strip()
    phrases = data.get("banned_phrases", [])
    if not system_prompt:
        raise ValueError("The drafting prompt cannot be empty")
    if "{style_brief}" not in system_prompt and "{samples}" not in system_prompt:
        raise ValueError("The prompt should include {style_brief} or {samples}, otherwise drafts will not sound like you")
    validate_prompt(system_prompt, PROMPT_PLACEHOLDERS)
    if not revision_prompt:
        revision_prompt = DEFAULT_REVISION_PROMPT
    validate_prompt(revision_prompt, {"problems": ""})
    if not isinstance(phrases, list):
        raise ValueError("banned_phrases must be a list")
    cleaned = []
    for p in phrases:
        p = str(p).strip().lower()
        if p and p not in cleaned:
            cleaned.append(p)
    _write_json(DRAFTING_FILE, {"system_prompt": system_prompt, "revision_prompt": revision_prompt, "banned_phrases": cleaned})


def reset_drafting() -> None:
    if DRAFTING_FILE.exists():
        DRAFTING_FILE.unlink()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
