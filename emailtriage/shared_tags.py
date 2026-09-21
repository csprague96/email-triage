"""Pull a shared tag set from a URL or a file path so a team triages the same way.

SHARED_TAGS_SOURCE can be:
  - an https URL, e.g. the raw tags.json in a GitHub repo
  - a local or UNC path, e.g. \\\\fileserver\\team\\email-triage-tags.json or a synced SharePoint folder

The file has the same shape as config/tags.json. It is cached in config/shared_tags.cache.json
and re-fetched every SHARED_TAGS_REFRESH_HOURS (default 6) by the watcher, or on demand.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .config import load_shared_tags_cache, save_shared_tags_cache, settings


def _fetch(source: str) -> dict:
    if source.lower().startswith(("http://", "https://")):
        resp = httpx.get(source, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    else:
        data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    tags = data.get("tags")
    if not isinstance(tags, list):
        raise ValueError("shared tags file has no 'tags' list")
    cleaned = []
    for t in tags:
        name = str(t.get("name", "")).strip()
        desc = str(t.get("description", "")).strip()
        if not name or not desc:
            continue
        entry = {"name": name, "description": desc, "color": t.get("color", "#64748b")}
        if isinstance(t.get("threshold"), (int, float)):
            entry["threshold"] = float(t["threshold"])
        cleaned.append(entry)
    return {"tags": cleaned, "default_threshold": data.get("default_threshold")}


def sync(force: bool = True) -> dict:
    """Refresh the cache. Returns a summary dict; never raises (errors are recorded in the cache)."""
    source = settings.shared_tags_source
    if not source:
        return {"enabled": False}
    cache = load_shared_tags_cache() or {}
    if not force and cache.get("synced_at"):
        try:
            last = datetime.fromisoformat(cache["synced_at"])
            if datetime.now(timezone.utc) - last < timedelta(hours=settings.shared_tags_refresh_hours):
                return {"enabled": True, "skipped": True, "tags": len(cache.get("tags", []))}
        except Exception:
            pass
    now = datetime.now(timezone.utc).isoformat()
    try:
        data = _fetch(source)
        payload = {"source": source, "synced_at": now, "tags": data["tags"], "error": None}
        if data.get("default_threshold") is not None:
            payload["default_threshold"] = data["default_threshold"]
        save_shared_tags_cache(payload)
        return {"enabled": True, "tags": len(data["tags"]), "synced_at": now}
    except Exception as err:
        # keep the previous tags but record the failure so the dashboard can show it
        cache.update({"source": source, "error": f"{type(err).__name__}: {err}", "error_at": now})
        cache.setdefault("tags", [])
        save_shared_tags_cache(cache)
        return {"enabled": True, "error": cache["error"], "tags": len(cache["tags"])}
