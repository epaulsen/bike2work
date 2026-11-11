"""Registration and status calculations independent of Find My."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .config import TrackerConfig
from .geofence import haversine_distance_meters, location_age_minutes
from .storage import BikeDay, add_if_missing, read_entries

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    observed_at: datetime


class LocationClient(Protocol):
    def get_location(self) -> Location | None:
        """Return latest decrypted location, or None if no report exists."""


@dataclass(frozen=True)
class CheckResult:
    message: str
    registered: bool = False


@dataclass(frozen=True)
class TrackerStatus:
    days: int
    distance_km: float
    reached_days: bool
    reached_distance: bool


def check_and_register(
    config: TrackerConfig,
    client: LocationClient,
    *,
    now: datetime | None = None,
) -> CheckResult:
    """Register one weekday when the latest fresh location is near work."""
    local_now = (now or datetime.now(config.timezone)).astimezone(config.timezone)
    if local_now.weekday() >= 5:
        return CheckResult("Helg – ingen registrering.")

    location = client.get_location()
    if location is None:
        return CheckResult("Find My har ingen posisjon for AirTag-en.")

    try:
        age_minutes = location_age_minutes(location.observed_at, local_now)
    except ValueError as exc:
        return CheckResult(f"Posisjon hoppet over: {exc}.")
    if age_minutes > config.max_location_age_minutes:
        return CheckResult(
            f"Posisjon hoppet over: {age_minutes:.0f} min gammel "
            f"(maks {config.max_location_age_minutes:g} min)."
        )

    try:
        distance = haversine_distance_meters(
            location.latitude,
            location.longitude,
            config.work_latitude,
            config.work_longitude,
        )
    except ValueError as exc:
        return CheckResult(f"Posisjon hoppet over: {exc}.")
    if distance > config.geofence_radius_meters:
        return CheckResult(
            f"Ikke registrert: {distance:.0f} m fra jobb "
            f"(grense {config.geofence_radius_meters:g} m)."
        )

    day = BikeDay(
        date=local_now.date().isoformat(),
        time=local_now.strftime("%H:%M:%S"),
        latitude=f"{location.latitude:.6f}",
        longitude=f"{location.longitude:.6f}",
        distance_m=f"{distance:.1f}",
        location_timestamp=location.observed_at.astimezone(config.timezone).isoformat(
            timespec="seconds"
        ),
    )
    if not add_if_missing(config.data_file, day):
        return CheckResult(f"{day.date} er allerede registrert.")

    logger.info("Registrerte sykkeldag %s, %.1f m fra jobb", day.date, distance)
    return CheckResult(f"Registrerte sykkeldag {day.date}, {distance:.0f} m fra jobb.", True)


def get_status(config: TrackerConfig) -> TrackerStatus:
    entries = read_entries(config.data_file)
    unique_days = {entry.date for entry in entries}
    days = len(unique_days)
    distance_km = days * config.round_trip_distance_km
    return TrackerStatus(
        days=days,
        distance_km=distance_km,
        reached_days=days >= 150,
        reached_distance=distance_km >= 1500,
    )
