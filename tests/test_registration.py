from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bike_tracker.config import TrackerConfig
from bike_tracker.registration import Location, check_and_register, get_status


def config(tmp_path):
    return TrackerConfig(
        airtag_file=tmp_path / "bike.json",
        work_latitude=58.0,
        work_longitude=7.0,
        geofence_radius_meters=300,
        max_location_age_minutes=120,
        round_trip_distance_km=20,
        timezone=ZoneInfo("Europe/Oslo"),
        data_file=tmp_path / "days.csv",
        account_file=tmp_path / "account.json",
        anisette_libs_file=tmp_path / "ani.bin",
    )


class FakeClient:
    def __init__(self, location):
        self.location = location
        self.calls = 0

    def get_location(self):
        self.calls += 1
        return self.location


def test_registers_once_within_fresh_geofence(tmp_path):
    settings = config(tmp_path)
    now = datetime(2026, 10, 15, 8, 0, tzinfo=settings.timezone)
    client = FakeClient(Location(58.0, 7.0, now - timedelta(minutes=5)))

    first = check_and_register(settings, client, now=now)
    second = check_and_register(settings, client, now=now)

    assert first.registered
    assert not second.registered
    assert "allerede registrert" in second.message


def test_ignores_weekends_and_stale_locations(tmp_path):
    settings = config(tmp_path)
    saturday = datetime(2026, 10, 17, 8, 0, tzinfo=settings.timezone)
    weekend_client = FakeClient(Location(58.0, 7.0, saturday))
    assert not check_and_register(settings, weekend_client, now=saturday).registered
    assert weekend_client.calls == 0

    monday = datetime(2026, 10, 19, 8, 0, tzinfo=settings.timezone)
    stale_client = FakeClient(Location(58.0, 7.0, monday - timedelta(hours=3)))
    result = check_and_register(settings, stale_client, now=monday)
    assert "gammel" in result.message
    assert not result.registered


def test_ignores_locations_outside_geofence_and_calculates_status(tmp_path):
    settings = config(tmp_path)
    now = datetime(2026, 10, 15, 8, 0, tzinfo=settings.timezone)
    far_away = FakeClient(Location(58.01, 7.0, now))
    assert not check_and_register(settings, far_away, now=now).registered

    nearby = FakeClient(Location(58.0, 7.0, now))
    assert check_and_register(settings, nearby, now=now).registered
    status = get_status(settings)
    assert status.days == 1
    assert status.distance_km == 20


def test_status_counts_unique_days(tmp_path):
    settings = config(tmp_path)
    result = check_and_register(
        settings,
        FakeClient(Location(58.0, 7.0, datetime(2026, 10, 15, 8, tzinfo=settings.timezone))),
        now=datetime(2026, 10, 15, 8, tzinfo=settings.timezone),
    )
    assert result.registered
    assert get_status(replace(settings, round_trip_distance_km=1500)).reached_distance
