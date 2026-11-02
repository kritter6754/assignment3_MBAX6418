"""Playlists ("folders" in the library): named, ordered lists of track ids.

Stored as one JSON file in DATA_DIR (git-ignored), next to the generated songs, so they last
as long as the songs they point to.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

MAX_NAME = 40
MAX_PLAYLISTS = 50


class PlaylistStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "playlists.json"
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        if self.path.is_file():
            try:
                self._items = {p["id"]: p for p in json.loads(self.path.read_text())}
            except (ValueError, KeyError, TypeError):
                self._items = {}  # a corrupt file should not stop the app

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(self._items.values()), indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def _clean_name(self, name: str, exclude: str | None = None) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise ValueError("Give the playlist a name.")
        if len(name) > MAX_NAME:
            raise ValueError(f"Playlist names can be at most {MAX_NAME} characters.")
        if any(p["name"].lower() == name.lower() and p["id"] != exclude for p in self._items.values()):
            raise ValueError(f"A playlist called “{name}” already exists.")
        return name

    def list(self) -> list[dict]:
        with self._lock:
            return sorted((dict(p) for p in self._items.values()), key=lambda p: p["created_at"])

    def get(self, pid: str) -> dict:
        with self._lock:
            if pid not in self._items:
                raise KeyError(pid)
            return dict(self._items[pid])

    def create(self, name: str, track_ids: list[str] | None = None) -> dict:
        with self._lock:
            if len(self._items) >= MAX_PLAYLISTS:
                raise ValueError(f"You can have at most {MAX_PLAYLISTS} playlists.")
            p = {"id": secrets.token_hex(5), "name": self._clean_name(name),
                 "track_ids": list(dict.fromkeys(track_ids or [])), "created_at": time.time()}
            self._items[p["id"]] = p
            self._save()
            return dict(p)

    def update(self, pid: str, name: str | None = None, track_ids: list[str] | None = None) -> dict:
        with self._lock:
            if pid not in self._items:
                raise KeyError(pid)
            p = self._items[pid]
            if name is not None:
                p["name"] = self._clean_name(name, exclude=pid)
            if track_ids is not None:
                p["track_ids"] = list(dict.fromkeys(track_ids))
            self._save()
            return dict(p)

    def add_track(self, pid: str, track_id: str) -> dict:
        with self._lock:
            if pid not in self._items:
                raise KeyError(pid)
            if track_id not in self._items[pid]["track_ids"]:
                self._items[pid]["track_ids"].append(track_id)
                self._save()
            return dict(self._items[pid])

    def remove_track(self, pid: str, track_id: str) -> dict:
        with self._lock:
            if pid not in self._items:
                raise KeyError(pid)
            ids = self._items[pid]["track_ids"]
            if track_id in ids:
                ids.remove(track_id)
                self._save()
            return dict(self._items[pid])

    def delete(self, pid: str) -> None:
        with self._lock:
            if self._items.pop(pid, None) is not None:
                self._save()
