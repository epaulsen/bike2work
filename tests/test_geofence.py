from datetime import datetime, timedelta, timezone

import pytest

from bike_tracker.geofence import haversine_distance_meters, location_age_minutes


def test_same_point_is_zero_distance():
    assert haversine_distance_meters(58.0, 7.0, 58.0, 7.0) == 0


def test_haversine_distance_is_approximately_correct():
    distance = haversine_distance_meters(0, 0, 0, 1)
    assert distance == pytest.approx(111_195, abs=2)


def test_haversine_rejects_invalid_coordinates():
    with pytest.raises(ValueError, match="Latitude"):
        haversine_distance_meters(91, 0, 0, 0)


def test_location_age_checks_timezone_and_future_values():
    now = datetime.now(timezone.utc)
    assert location_age_minutes(now - timedelta(minutes=30), now) == pytest.approx(30)
    with pytest.raises(ValueError, match="future"):
        location_age_minutes(now + timedelta(minutes=1), now)
    with pytest.raises(ValueError, match="time zone"):
        location_age_minutes(datetime(2026, 1, 1), now)
