"""Build a Garmin-compatible FIT file containing body-weight measurements.

A weight FIT file uses a FileIdMessage(type=WEIGHT) plus one
WeightScaleMessage per measurement. Garmin's upload-service imports these as
body-composition / weight entries.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.weight_scale_message import WeightScaleMessage
from fit_tool.profile.profile_type import FileType, Manufacturer

from .healthplanet import WeightMeasurement

logger = logging.getLogger(__name__)


def _epoch_ms(dt: datetime) -> int:
    """Unix epoch milliseconds for a timezone-aware datetime."""
    return round(dt.timestamp() * 1000)


def write_weight_fit(measurements: Iterable[WeightMeasurement], path: Path) -> Path:
    """Write the given measurements to a FIT file at `path`. Returns the path."""
    measurements = list(measurements)
    if not measurements:
        raise ValueError("No measurements provided to write_weight_fit")

    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    file_id = FileIdMessage()
    file_id.type = FileType.WEIGHT
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.serial_number = 0x12345678
    file_id.time_created = _epoch_ms(measurements[0].measured_at)
    builder.add(file_id)

    for m in measurements:
        msg = WeightScaleMessage()
        msg.timestamp = _epoch_ms(m.measured_at)
        msg.weight = float(m.weight_kg)
        builder.add(msg)

    path.parent.mkdir(parents=True, exist_ok=True)
    builder.build().to_file(str(path))
    logger.info("Wrote %d measurement(s) to %s", len(measurements), path)
    return path
