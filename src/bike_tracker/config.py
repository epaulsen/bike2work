"""Configuration loading and validation."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values


class ConfigError(ValueError):
    """Raised when required configuration is invalid or missing."""


@dataclass(frozen=True)
class TrackerConfig:
    airtag_file: Path
    work_latitude: float
    work_longitude: float
    geofence_radius_meters: float
    max_location_age_minutes: float
    round_trip_distance_km: float
    timezone: ZoneInfo
    data_file: Path
    account_file: Path
    anisette_libs_file: Path


def default_config_path() -> Path:
    configured = os.environ.get("BIKE_TRACKER_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "bike-tracker" / "config.env"


def _number(
    name: str,
    values: Mapping[str, str | None],
    *,
    default: str | None = None,
) -> float:
    raw = os.environ.get(name, values.get(name, default))
    if raw is None or not raw.strip():
        raise ConfigError(f"Missing required setting: {name}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{name} must be a finite number")
    return value


def _path(
    name: str,
    values: Mapping[str, str | None],
    *,
    default: str | None = None,
) -> Path:
    raw = os.environ.get(name, values.get(name, default))
    if raw is None or not raw.strip():
        raise ConfigError(f"Missing required setting: {name}")
    return Path(raw.strip()).expanduser()


def load_config(config_path: Path | None = None) -> TrackerConfig:
    """Load settings from an env file and the process environment."""
    path = config_path or default_config_path()
    if not path.is_file():
        raise ConfigError(
            f"Configuration file not found: {path}. "
            "Copy config.example.env there and set the required values."
        )
    values = dotenv_values(path)

    latitude = _number("WORK_LATITUDE", values)
    longitude = _number("WORK_LONGITUDE", values)
    radius = _number("GEOFENCE_RADIUS_METERS", values, default="300")
    max_age = _number("MAX_LOCATION_AGE_MINUTES", values, default="120")
    round_trip = _number("ROUND_TRIP_DISTANCE_KM", values)

    if not -90 <= latitude <= 90:
        raise ConfigError("WORK_LATITUDE must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise ConfigError("WORK_LONGITUDE must be between -180 and 180")
    if radius <= 0:
        raise ConfigError("GEOFENCE_RADIUS_METERS must be greater than zero")
    if max_age <= 0:
        raise ConfigError("MAX_LOCATION_AGE_MINUTES must be greater than zero")
    if round_trip <= 0:
        raise ConfigError("ROUND_TRIP_DISTANCE_KM must be greater than zero")

    timezone_name = os.environ.get("TIMEZONE", values.get("TIMEZONE") or "Europe/Oslo").strip()
    if not timezone_name:
        raise ConfigError("TIMEZONE must be a non-empty IANA time zone")
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"TIMEZONE is not a valid IANA time zone: {timezone_name}") from exc

    config_dir = Path.home() / ".config" / "bike-tracker"
    return TrackerConfig(
        airtag_file=_path("AIRTAG_FILE", values),
        work_latitude=latitude,
        work_longitude=longitude,
        geofence_radius_meters=radius,
        max_location_age_minutes=max_age,
        round_trip_distance_km=round_trip,
        timezone=timezone,
        data_file=_path("DATA_FILE", values, default="~/Documents/sykkeldager.csv"),
        account_file=_path("ACCOUNT_FILE", values, default=str(config_dir / "account.json")),
        anisette_libs_file=_path("ANISETTE_LIBS_FILE", values, default=str(config_dir / "ani_libs.bin")),
    )
