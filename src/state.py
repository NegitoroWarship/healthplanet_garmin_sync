"""Sync state persistence.

Tracks the key ("YYYYMMDDHHMM") of the last measurement that was successfully
uploaded to Garmin, so each run only uploads genuinely new measurements.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load_last_key(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("last_measure_key")
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read state file %s: %s", path, exc)
        return None


def save_last_key(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_measure_key": key}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Updated state: last_measure_key=%s", key)
