"""Garmin Connect uploader.

Uses python-garminconnect (actively maintained, working auth as of 2026) to log
in and upload a weight FIT file to Garmin's upload-service. Session tokens are
cached in a directory so subsequent runs do not need to re-authenticate.
"""
from __future__ import annotations

import logging
from pathlib import Path

from garminconnect import Garmin

from .config import Config

logger = logging.getLogger(__name__)


class GarminUploader:
    def __init__(self, config: Config):
        self.config = config
        self.token_dir = config.garmin_token_dir
        self.token_dir.mkdir(parents=True, exist_ok=True)
        self._client: Garmin | None = None

    def login(self) -> Garmin:
        """Resume a cached session or perform a fresh login (MFA assumed off).

        ``Garmin.login(tokenstore=...)`` handles everything: it loads cached
        tokens from the directory, refreshes them if near expiry, falls back to
        a credential login when no valid cache exists, and persists the tokens
        back to the directory on a fresh login.
        """
        client = Garmin(
            email=self.config.garmin_email,
            password=self.config.garmin_password,
        )
        client.login(tokenstore=str(self.token_dir))
        logger.info("Garmin login OK")
        self._client = client
        return client

    def upload_fit(self, fit_path: Path) -> None:
        """Upload a FIT file. Treats a duplicate (already-known) upload as success."""
        if self._client is None:
            self.login()
        assert self._client is not None
        try:
            result = self._client.upload_activity(str(fit_path))
            logger.info("Garmin upload accepted: %s", _summarize(result))
        except Exception as exc:  # noqa: BLE001 - normalize duplicate handling
            if _is_duplicate(exc):
                logger.info("Garmin reports measurement already present (duplicate); ok")
                return
            raise


def _summarize(result: object) -> str:
    try:
        detail = result["detailedImportResult"]  # type: ignore[index]
        return f"uploadId={detail.get('uploadId')}"
    except Exception:  # noqa: BLE001
        return str(result)[:200]


def _is_duplicate(exc: Exception) -> bool:
    text = str(exc).lower()
    return "409" in text or "conflict" in text or "duplicate" in text
