"""Local, atomically-persisted resume state for the upload tool.

Issue #15 §6a (Resumability): a tool crash / close must let the operator
continue - "14 Karten waren in Upload, fortsetzen?". This module owns the
on-disk record keyed by the marker's ``card_uuid`` (the stable local key,
independent of the server-side id).

Writes are atomic (temp file + ``os.replace``) so a crash mid-write never
leaves a half JSON that would confuse the next start - the same INV-4
discipline the pipeline uses for its status files.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Dict, Optional

SCHEMA_VERSION = 1


class StateStore:
    """A tiny JSON document mapping ``card_uuid`` -> card record dict.

    Thread-safe: the GUI uploads several cards in parallel (1-24 reader
    slots), so every accessor takes a re-entrant lock around the in-memory
    dict and the atomic file write.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cards: Dict[str, dict] = {}
        self._lock = threading.RLock()
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if not self.path.is_file():
            self._cards = {}
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            # A corrupt state file must not brick the tool; start clean.
            self._cards = {}
            return
        cards = raw.get("cards") if isinstance(raw, dict) else None
        self._cards = cards if isinstance(cards, dict) else {}

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        doc = {"version": SCHEMA_VERSION, "cards": self._cards}
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    # -- accessors ---------------------------------------------------------

    def get(self, card_uuid: str) -> Optional[dict]:
        with self._lock:
            rec = self._cards.get(card_uuid)
            return copy.deepcopy(rec) if rec is not None else None

    def all(self) -> Dict[str, dict]:
        with self._lock:
            return copy.deepcopy(self._cards)

    def put(self, card_uuid: str, record: dict) -> None:
        with self._lock:
            self._cards[card_uuid] = copy.deepcopy(record)
            self._flush()

    def remove(self, card_uuid: str) -> None:
        with self._lock:
            if card_uuid in self._cards:
                del self._cards[card_uuid]
                self._flush()

    def clear(self) -> None:
        with self._lock:
            self._cards = {}
            self._flush()
