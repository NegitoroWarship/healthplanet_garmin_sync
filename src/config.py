"""Configuration loaded from environment variables.

Values come from the process environment (k8s Secret/env). For local runs a
`.env` file in the project root is loaded automatically if present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Optional


def _load_dotenv() -> None:
    """Minimal .env loader (no external dependency). Existing env wins."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


@dataclass(frozen=True)
class Config:
    hp_client_id: str
    hp_client_secret: str
    hp_redirect_uri: str
    garmin_email: str
    garmin_password: str
    data_dir: Path
    lookback_days: int
    hp_tz: timezone
    sync_since_key: Optional[str]  # normalized "YYYYMMDDHHMM" floor, or None

    @property
    def hp_token_file(self) -> Path:
        return self.data_dir / "healthplanet_tokens.json"

    @property
    def garmin_token_dir(self) -> Path:
        return self.data_dir / "garmin_tokens"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"


def _normalize_since(raw: Optional[str]) -> Optional[str]:
    """Normalize SYNC_SINCE to a 12-char 'YYYYMMDDHHMM' key, or None.

    Accepts forms like '20260614', '2026-06-14', '202606141200'. Pads missing
    time fields with zeros (so a date means 00:00 of that day).
    """
    if not raw:
        return None
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) < 8:
        raise RuntimeError(f"SYNC_SINCE must contain at least YYYYMMDD: {raw!r}")
    return (digits + "000000")[:12]


def load_config() -> Config:
    _load_dotenv()
    data_dir = Path(os.environ.get("DATA_DIR", "./data")).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    tz_hours = int(os.environ.get("HEALTHPLANET_TZ_OFFSET_HOURS", "9"))
    return Config(
        hp_client_id=_require("HEALTHPLANET_CLIENT_ID"),
        hp_client_secret=_require("HEALTHPLANET_CLIENT_SECRET"),
        hp_redirect_uri=os.environ.get(
            "HEALTHPLANET_REDIRECT_URI", "https://www.healthplanet.jp/success.html"
        ),
        garmin_email=_require("GARMIN_EMAIL"),
        garmin_password=_require("GARMIN_PASSWORD"),
        data_dir=data_dir,
        lookback_days=int(os.environ.get("LOOKBACK_DAYS", "90")),
        hp_tz=timezone(timedelta(hours=tz_hours)),
        sync_since_key=_normalize_since(os.environ.get("SYNC_SINCE")),
    )
