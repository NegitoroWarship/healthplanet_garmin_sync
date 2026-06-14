"""HealthPlanet (Tanita) API client.

Handles OAuth2 token persistence/refresh and fetching body weight (tag 6021)
measurements from the innerscan endpoint.

API spec: https://www.healthplanet.jp/apis/api.html
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)

BASE_URL = "https://www.healthplanet.jp"
AUTH_URL = f"{BASE_URL}/oauth/auth"
TOKEN_URL = f"{BASE_URL}/oauth/token"
INNERSCAN_URL = f"{BASE_URL}/status/innerscan.json"

SCOPE = "innerscan"
TAG_WEIGHT = "6021"
# Refresh the access token if it expires within this many seconds.
EXPIRY_BUFFER_SECONDS = 600


@dataclass
class WeightMeasurement:
    """A single body-weight measurement."""

    measured_at: datetime  # timezone-aware (JST)
    weight_kg: float
    key: str  # original HealthPlanet date string "YYYYMMDDHHMM", used for ordering/dedup


def build_authorize_url(config: Config) -> str:
    """URL the user opens once to grant access and obtain an auth code."""
    params = {
        "client_id": config.hp_client_id,
        "redirect_uri": config.hp_redirect_uri,
        "scope": SCOPE,
        "response_type": "code",
    }
    return f"{AUTH_URL}?{requests.compat.urlencode(params)}"


def exchange_code_for_tokens(config: Config, code: str) -> dict:
    """Exchange an authorization code for access/refresh tokens and persist them."""
    data = {
        "client_id": config.hp_client_id,
        "client_secret": config.hp_client_secret,
        "redirect_uri": config.hp_redirect_uri,
        "code": code,
        "grant_type": "authorization_code",
    }
    tokens = _post_token(data)
    _save_tokens(config.hp_token_file, tokens)
    return tokens


def _post_token(data: dict) -> dict:
    resp = requests.post(TOKEN_URL, data=data, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if "access_token" not in payload:
        raise RuntimeError(f"Token response missing access_token: {payload}")
    # HealthPlanet returns expires_in (seconds). Compute absolute expiry.
    expires_in = int(payload.get("expires_in", 0))
    payload["expires_at"] = int(time.time()) + expires_in if expires_in else 0
    return payload


def _save_tokens(path: Path, tokens: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_tokens(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class HealthPlanetClient:
    def __init__(self, config: Config):
        self.config = config
        self._tokens = _load_tokens(config.hp_token_file)
        if not self._tokens:
            raise RuntimeError(
                "No HealthPlanet tokens found. Run scripts/authorize_healthplanet.py "
                f"once to create {config.hp_token_file}."
            )

    def _access_token(self) -> str:
        expires_at = int(self._tokens.get("expires_at", 0))
        if expires_at and time.time() >= expires_at - EXPIRY_BUFFER_SECONDS:
            self._refresh()
        return self._tokens["access_token"]

    def _refresh(self) -> None:
        refresh_token = self._tokens.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(
                "Access token expired and no refresh_token available; re-authorize."
            )
        logger.info("Refreshing HealthPlanet access token")
        data = {
            "client_id": self.config.hp_client_id,
            "client_secret": self.config.hp_client_secret,
            "redirect_uri": self.config.hp_redirect_uri,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        new_tokens = _post_token(data)
        # Keep the previous refresh_token if the response omits a new one.
        new_tokens.setdefault("refresh_token", refresh_token)
        self._tokens = new_tokens
        _save_tokens(self.config.hp_token_file, new_tokens)

    def get_weight_measurements(
        self, from_dt: datetime, to_dt: datetime
    ) -> list[WeightMeasurement]:
        """Fetch weight measurements in [from_dt, to_dt] (max 3-month window)."""
        params = {
            "access_token": self._access_token(),
            "date": "1",  # 1 = by measurement date
            "tag": TAG_WEIGHT,
            "from": from_dt.strftime("%Y%m%d%H%M%S"),
            "to": to_dt.strftime("%Y%m%d%H%M%S"),
        }
        resp = requests.get(INNERSCAN_URL, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        return self._parse(payload.get("data", []))

    def _parse(self, raw: list[dict]) -> list[WeightMeasurement]:
        out: list[WeightMeasurement] = []
        for item in raw:
            if str(item.get("tag")) != TAG_WEIGHT:
                continue
            date_str = str(item["date"])[:12]  # "YYYYMMDDHHMM"
            measured_at = datetime.strptime(date_str, "%Y%m%d%H%M").replace(
                tzinfo=self.config.hp_tz
            )
            out.append(
                WeightMeasurement(
                    measured_at=measured_at,
                    weight_kg=float(item["keydata"]),
                    key=date_str,
                )
            )
        out.sort(key=lambda m: m.key)
        return out
