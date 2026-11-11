"""Distance and location freshness checks."""

from __future__ import annotations

import math
from datetime import datetime

EARTH_RADIUS_METERS = 6_371_000


def haversine_distance_meters(
    latitude_1: float,
    longitude_1: float,
    latitude_2: float,
    longitude_2: float,
) -> float:
    """Return great-circle distance between two WGS84 coordinates."""
    if not -90 <= latitude_1 <= 90 or not -90 <= latitude_2 <= 90:
        raise ValueError("Latitude must be between -90 and 90")
    if not -180 <= longitude_1 <= 180 or not -180 <= longitude_2 <= 180:
        raise ValueError("Longitude must be between -180 and 180")

    lat_1, lat_2 = math.radians(latitude_1), math.radians(latitude_2)
    delta_lat = lat_2 - lat_1
    delta_lon = math.radians(longitude_2 - longitude_1)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_1) * math.cos(lat_2) * math.sin(delta_lon / 2) ** 2
    )
    return EARTH_RADIUS_METERS * 2 * math.asin(math.sqrt(min(1.0, haversine)))


def location_age_minutes(observed_at: datetime, now: datetime) -> float:
    """Return a location report's age, rejecting naive or future timestamps."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Location timestamp must include a time zone")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Current time must include a time zone")
    age = (now - observed_at).total_seconds() / 60
    if age < 0:
        raise ValueError("Location timestamp is in the future")
    return age
