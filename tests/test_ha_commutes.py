import csv
import json
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

import ha_commutes as commutes


def timestamp(value):
    return commutes.parse_timestamp(value)


def event(value, state):
    return commutes.Event(timestamp(value), state)


def day_events(day="2026-11-03"):
    zones = [
        event(f"{day}T05:00:00Z", "home"),
        event(f"{day}T06:10:00Z", "not_home"),
        event(f"{day}T06:15:00Z", "not_home"),
        event(f"{day}T06:37:00Z", commutes.WORK_ZONE),
        event(f"{day}T14:29:00Z", "not_home"),
        event(f"{day}T14:41:00Z", "Fagerholt"),
        event(f"{day}T14:42:00Z", "not_home"),
        event(f"{day}T14:53:00Z", "home"),
    ]
    activities = [
        event(f"{day}T05:00:00Z", "Stationary"),
        event(f"{day}T06:11:00Z", "Cycling"),
        event(f"{day}T06:36:00Z", "Unknown"),
        event(f"{day}T14:30:00Z", "Cycling"),
        event(f"{day}T14:54:00Z", "Walking"),
    ]
    return zones, activities


def test_round_trip_uses_zone_times_and_local_timezone():
    zones, activities = day_events()
    trips, pending = commutes.find_trips(zones, activities, timestamp("2026-11-03T18:00:00Z"))
    rows = {}
    commutes.merge_trips(rows, trips)
    assert pending is None
    assert rows["2026-11-03"] == dict(zip(
        commutes.FIELDS, ["2026-11-03", "07:10:00", "07:37:00", "15:29:00", "15:53:00"],
    ))


def test_one_way_trips_on_separate_days_with_unrestricted_hours():
    zones = [
        event("2026-11-03T09:00:00Z", "home"),
        event("2026-11-03T10:00:00Z", "not_home"),
        event("2026-11-03T10:30:00Z", commutes.WORK_ZONE),
        event("2026-11-05T19:00:00Z", "not_home"),
        event("2026-11-05T19:30:00Z", "home"),
    ]
    activities = [
        event("2026-11-03T09:00:00Z", "Walking"),
        event("2026-11-03T10:05:00Z", "Cycling"),
        event("2026-11-03T10:29:00Z", "Walking"),
        event("2026-11-05T19:05:00Z", "Cycling"),
        event("2026-11-05T19:29:00Z", "Stationary"),
    ]
    trips, _ = commutes.find_trips(zones, activities, timestamp("2026-11-05T21:00:00Z"))
    rows = {}
    commutes.merge_trips(rows, trips)
    assert list(rows["2026-11-03"].values()) == ["2026-11-03", "11:00:00", "11:30:00", "", ""]
    assert list(rows["2026-11-05"].values()) == ["2026-11-05", "", "", "20:00:00", "20:30:00"]


@pytest.mark.parametrize("day", ["2026-11-07", "2026-11-08"])
def test_weekend_trips_are_excluded(day):
    zones, activities = day_events(day)
    assert commutes.find_trips(zones, activities, timestamp(f"{day}T18:00:00Z"))[0] == []


def test_cycling_elsewhere_or_after_arrival_does_not_count():
    zones = [
        event("2026-11-03T06:00:00Z", "home"),
        event("2026-11-03T06:10:00Z", "not_home"),
        event("2026-11-03T06:30:00Z", "home"),
        event("2026-11-03T07:00:00Z", "not_home"),
        event("2026-11-03T07:30:00Z", commutes.WORK_ZONE),
    ]
    activities = [
        event("2026-11-03T06:00:00Z", "Cycling"),
        event("2026-11-03T06:30:00Z", "Automotive"),
        event("2026-11-03T07:30:00Z", "Cycling"),
    ]
    assert commutes.find_trips(zones, activities, timestamp("2026-11-03T08:00:00Z"))[0] == []


def test_zone_observation_gap_does_not_invent_a_trip():
    zones, activities = day_events()
    zones.insert(3, event("2026-11-03T06:20:00Z", "unavailable"))
    trips, _ = commutes.find_trips(zones, activities, timestamp("2026-11-03T18:00:00Z"))
    assert [trip.origin for trip in trips] == [commutes.WORK_ZONE]


def test_query_window_with_overlap_pending_and_retention_limit():
    now = timestamp("2026-11-08T19:00:00Z")
    assert commutes.query_start({}, now) == now - timedelta(days=30)
    assert commutes.query_start({"last_run": "2026-10-01T00:00:00Z"}, now) == now - timedelta(days=30)
    assert commutes.query_start({"last_run": "2026-11-09T00:00:00Z"}, now) == now - timedelta(days=30)
    state = {"last_run": "2026-11-06T12:00:00Z"}
    assert commutes.query_start(state, now) == timestamp("2026-11-04T23:00:00Z")
    state["pending_departure"] = "2026-11-03T06:10:00Z"
    assert commutes.query_start(state, now) == timestamp("2026-11-03T06:09:59Z")
    state["pending_departure"] = "2026-09-01T06:10:00Z"
    assert commutes.query_start(state, now) == now - timedelta(days=30)


def test_history_initial_state_and_attribute_updates():
    start = timestamp("2026-11-03T06:00:00Z")
    rows = [[
        {"entity_id": commutes.TRACKER_ENTITY, "state": "home", "last_changed": "2026-11-01T01:00:00Z"},
        {
            "entity_id": commutes.TRACKER_ENTITY, "state": "not_home",
            "last_changed": "2026-11-03T06:10:00Z", "last_updated": "2026-11-03T06:15:00Z",
        },
    ]]
    events = commutes.history_events(rows, commutes.TRACKER_ENTITY, start, start + timedelta(hours=1))
    assert events[0].timestamp == start
    assert events[1].timestamp == timestamp("2026-11-03T06:15:00Z")
    with pytest.raises(ValueError, match="No history"):
        commutes.history_events([], commutes.TRACKER_ENTITY, start, start)


def test_repeated_runs_complete_pending_trip_and_preserve_csv(tmp_path, monkeypatch):
    csv_path = tmp_path / "commutes.csv"
    state_path = tmp_path / "state.json"
    zones, activities = day_events()
    calls = []

    def fetch(url, token, entity, start, end):
        calls.append(start)
        source = zones if entity == commutes.TRACKER_ENTITY else activities
        return [item for item in source if item.timestamp <= end]

    monkeypatch.setattr(commutes, "fetch_history", fetch)
    midday = timestamp("2026-11-03T14:40:00Z")
    commutes.run(csv_path, state_path, "http://example.invalid", "dummy", midday)
    assert commutes.read_csv(csv_path)["2026-11-03"][commutes.FIELDS[3]] == ""
    assert json.loads(state_path.read_text())["pending_departure"] == "2026-11-03T14:29:00+00:00"
    evening = timestamp("2026-11-03T18:00:00Z")
    commutes.run(csv_path, state_path, "http://example.invalid", "dummy", evening)
    original = csv_path.read_bytes()
    commutes.run(csv_path, state_path, "http://example.invalid", "dummy", evening)
    assert csv_path.read_bytes() == original
    with csv_path.open() as stream:
        assert len(list(csv.DictReader(stream))) == 1
    assert commutes.read_csv(csv_path)["2026-11-03"][commutes.FIELDS[4]] == "15:53:00"
    assert calls[0] == midday - timedelta(days=30)
    assert calls[2] < midday
    assert csv_path.stat().st_mode & 0o777 == 0o600
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_yearly_files_replay_across_new_year_without_losing_existing_rows(tmp_path, monkeypatch):
    previous_path = tmp_path / "2025-sykkeldager.csv"
    current_path = tmp_path / "2026-sykkeldager.csv"
    state_path = tmp_path / "state.json"
    zones_2025, activities_2025 = day_events("2025-12-31")
    zones_2026, activities_2026 = day_events("2026-01-01")
    zones, activities = zones_2025 + zones_2026, activities_2025 + activities_2026

    def fetch(url, token, entity, start, end):
        source = zones if entity == commutes.TRACKER_ENTITY else activities
        return [item for item in source if item.timestamp <= end]

    monkeypatch.setattr(commutes, "fetch_history", fetch)
    commutes.run(previous_path, state_path, "http://example.invalid", "dummy",
                 timestamp("2025-12-31T12:00:00Z"), yearly=True)
    assert commutes.read_csv(previous_path)["2025-12-31"][commutes.FIELDS[3]] == ""

    now = timestamp("2026-01-01T18:00:00Z")
    commutes.run(current_path, state_path, "http://example.invalid", "dummy", now, yearly=True)
    assert commutes.read_csv(previous_path)["2025-12-31"][commutes.FIELDS[4]] == "15:53:00"
    assert list(commutes.read_csv(current_path)) == ["2026-01-01"]
    original = previous_path.read_bytes(), current_path.read_bytes()
    commutes.run(current_path, state_path, "http://example.invalid", "dummy", now, yearly=True)
    assert (previous_path.read_bytes(), current_path.read_bytes()) == original


def test_yearly_run_does_not_overwrite_files_if_previous_year_is_malformed(tmp_path, monkeypatch):
    previous_path = tmp_path / "2025-sykkeldager.csv"
    current_path = tmp_path / "2026-sykkeldager.csv"
    previous_path.write_text("invalid\n")
    zones, activities = day_events("2025-12-31")
    monkeypatch.setattr(
        commutes, "fetch_history",
        lambda url, token, entity, start, end: zones if entity == commutes.TRACKER_ENTITY else activities,
    )
    with pytest.raises(ValueError, match="columns"):
        commutes.run(current_path, tmp_path / "state.json", "http://example.invalid", "dummy",
                     timestamp("2026-01-01T18:00:00Z"), yearly=True)
    assert previous_path.read_text() == "invalid\n"
    assert not current_path.exists()


def test_trip_arriving_after_new_year_uses_local_departure_year(tmp_path, monkeypatch):
    zones = [
        event("2025-12-31T22:00:00Z", "home"),
        event("2025-12-31T22:50:00Z", "not_home"),
        event("2025-12-31T23:10:00Z", commutes.WORK_ZONE),
    ]
    activities = [
        event("2025-12-31T22:00:00Z", "Stationary"),
        event("2025-12-31T22:51:00Z", "Cycling"),
        event("2025-12-31T23:09:00Z", "Walking"),
    ]
    monkeypatch.setattr(
        commutes, "fetch_history",
        lambda url, token, entity, start, end: zones if entity == commutes.TRACKER_ENTITY else activities,
    )
    current_path = tmp_path / "2026-sykkeldager.csv"
    commutes.run(current_path, tmp_path / "state.json", "http://example.invalid", "dummy",
                 timestamp("2025-12-31T23:30:00Z"), yearly=True)
    previous_rows = commutes.read_csv(tmp_path / "2025-sykkeldager.csv")
    assert previous_rows["2025-12-31"][commutes.FIELDS[1]] == "23:50:00"
    assert previous_rows["2025-12-31"][commutes.FIELDS[2]] == "00:10:00"
    assert commutes.read_csv(current_path) == {}


def test_failed_request_keeps_checkpoint_and_csv(tmp_path, monkeypatch):
    csv_path = tmp_path / "commutes.csv"
    state_path = tmp_path / "state.json"
    commutes.atomic_write(csv_path, lambda stream: commutes.write_csv(stream, {}))
    state_path.write_text('{"last_run":"2026-11-03T12:00:00Z"}')
    original_csv, original_state = csv_path.read_bytes(), state_path.read_bytes()

    def fail(*args):
        raise ValueError("No history")

    monkeypatch.setattr(commutes, "fetch_history", fail)
    with pytest.raises(ValueError, match="No history"):
        commutes.run(csv_path, state_path, "http://example.invalid", "dummy", timestamp("2026-11-04T12:00:00Z"))
    assert csv_path.read_bytes() == original_csv
    assert state_path.read_bytes() == original_state


def test_malformed_csv_is_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "commutes.csv"
    path.write_text("wrong,columns\nkeep,this\n")
    with pytest.raises(ValueError, match="columns"):
        commutes.run(path, tmp_path / "state.json", "http://example.invalid", "dummy", timestamp("2026-11-04T12:00:00Z"))
    assert path.read_text() == "wrong,columns\nkeep,this\n"


def test_first_trip_per_direction_wins():
    zones, activities = day_events()
    trips, _ = commutes.find_trips(zones, activities, timestamp("2026-11-03T18:00:00Z"))
    later = commutes.Trip("home", timestamp("2026-11-03T12:00:00Z").astimezone(commutes.LOCAL_ZONE),
                          timestamp("2026-11-03T12:30:00Z").astimezone(commutes.LOCAL_ZONE))
    rows = {}
    commutes.merge_trips(rows, trips + [later])
    assert rows["2026-11-03"][commutes.FIELDS[1]] == "07:10:00"


def test_pending_trip_replays_origin_even_after_several_days(tmp_path, monkeypatch):
    csv_path = tmp_path / "commutes.csv"
    state_path = tmp_path / "state.json"
    zones = [
        event("2026-11-03T05:00:00Z", "home"),
        event("2026-11-03T06:10:00Z", "not_home"),
    ]
    activities = [
        event("2026-11-03T05:00:00Z", "Stationary"),
        event("2026-11-03T06:11:00Z", "Cycling"),
        event("2026-11-03T06:36:00Z", "Unknown"),
    ]
    starts = []

    def fetch(url, token, entity, start, end):
        starts.append(start)
        source = zones if entity == commutes.TRACKER_ENTITY else activities
        before = [item for item in source if item.timestamp < start]
        initial = [commutes.Event(start, before[-1].state)] if before else []
        return initial + [item for item in source if start <= item.timestamp <= end]

    monkeypatch.setattr(commutes, "fetch_history", fetch)
    for now in ["2026-11-03T06:20:00Z", "2026-11-05T06:00:00Z"]:
        commutes.run(csv_path, state_path, "http://example.invalid", "dummy", timestamp(now))
    zones.append(event("2026-11-03T06:37:00Z", commutes.WORK_ZONE))
    commutes.run(csv_path, state_path, "http://example.invalid", "dummy", timestamp("2026-11-06T06:00:00Z"))
    assert starts[4] < timestamp("2026-11-03T06:10:00Z")
    assert commutes.read_csv(csv_path)["2026-11-03"][commutes.FIELDS[2]] == "07:37:00"
    assert json.loads(state_path.read_text())["pending_departure"] is None


def test_fetch_history_requests_all_changes_without_coordinates(monkeypatch):
    import io

    start = timestamp("2026-11-03T05:00:00Z")
    end = timestamp("2026-11-03T18:00:00Z")

    class Opener:
        def open(self, request, timeout):
            query = parse_qs(urlsplit(request.full_url).query)
            assert query == {
                "filter_entity_id": [commutes.TRACKER_ENTITY],
                "end_time": [end.isoformat()],
                "significant_changes_only": ["0"],
                "no_attributes": ["1"],
            }
            assert request.get_header("Authorization") == "Bearer dummy"
            assert timeout == 120
            return io.BytesIO(json.dumps([[
                {"entity_id": commutes.TRACKER_ENTITY, "state": "home", "last_changed": start.isoformat()},
            ]]).encode())

    monkeypatch.setattr(commutes, "build_opener", lambda *args: Opener())
    assert commutes.fetch_history("http://example.invalid", "dummy", commutes.TRACKER_ENTITY, start, end) == [
        commutes.Event(start, "home"),
    ]

    def unauthorized(*args):
        raise HTTPError("http://example.invalid", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(commutes, "build_opener", unauthorized)
    with pytest.raises(ValueError, match="HTTP 401"):
        commutes.fetch_history("http://example.invalid", "dummy", commutes.TRACKER_ENTITY, start, end)


def test_config_file_supports_quoted_zone_and_literal_token(tmp_path, monkeypatch):
    for name in ("HA_URL", "HA_TOKEN", "ZONE_WORK_NAME", "ACTIVITY_ENTITY", "TRACKER_ENTITY"):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / ".env"
    config.write_text(
        'HA_URL=http://example.invalid\nHA_TOKEN=dummy-${HA_URL}\nZONE_WORK_NAME="Å Energi"\n',
        encoding="utf-8",
    )
    assert commutes.load_config(config) == (
        "http://example.invalid", "dummy-${HA_URL}", "Å Energi",
        commutes.ACTIVITY_ENTITY, commutes.TRACKER_ENTITY,
    )
    monkeypatch.setenv("ZONE_WORK_NAME", "Other office")
    assert commutes.load_config(config)[2] == "Other office"
    monkeypatch.setenv("HA_TOKEN", "")
    with pytest.raises(ValueError, match="Missing required setting: HA_TOKEN"):
        commutes.load_config(config)


def test_main_reads_config_beside_script_not_working_directory(tmp_path, monkeypatch):
    for name in ("HA_URL", "HA_TOKEN", "ZONE_WORK_NAME", "ACTIVITY_ENTITY", "TRACKER_ENTITY"):
        monkeypatch.delenv(name, raising=False)
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    (script_dir / ".env").write_text(
        'HA_URL=http://example.invalid\nHA_TOKEN=dummy\nZONE_WORK_NAME="Other office"\n',
    )
    monkeypatch.setattr(commutes, "__file__", str(script_dir / "ha_commutes.py"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commutes.sys, "argv", ["ha_commutes.py"])
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return 0

    monkeypatch.setattr(commutes, "run", run)
    assert commutes.main() == 0
    assert calls[0][0][2:4] == ("http://example.invalid", "dummy")
    year = calls[0][0][4].astimezone(commutes.LOCAL_ZONE).year
    assert calls[0][0][0] == script_dir / "data" / f"{year}-sykkeldager.csv"
    assert calls[0][0][1] == script_dir / "ha-commutes.state.json"
    assert calls[0][1] == {
        "work_zone": "Other office",
        "activity_entity": commutes.ACTIVITY_ENTITY,
        "tracker_entity": commutes.TRACKER_ENTITY,
        "yearly": True,
    }

    custom = tmp_path / "custom.env"
    custom.write_text(
        'HA_URL=http://custom.invalid\nHA_TOKEN=dummy\nZONE_WORK_NAME="Custom office"\n'
        'ACTIVITY_ENTITY=sensor.other_activity\nTRACKER_ENTITY=device_tracker.other_phone\n',
    )
    monkeypatch.setattr(commutes.sys, "argv", ["ha_commutes.py", "--config", str(custom)])
    assert commutes.main() == 0
    assert calls[1][0][2:4] == ("http://custom.invalid", "dummy")
    assert calls[1][1] == {
        "work_zone": "Custom office",
        "activity_entity": "sensor.other_activity",
        "tracker_entity": "device_tracker.other_phone",
        "yearly": True,
    }
    custom_csv = tmp_path / "custom.csv"
    monkeypatch.setattr(commutes.sys, "argv", ["ha_commutes.py", "--csv", str(custom_csv)])
    assert commutes.main() == 0
    assert calls[2][0][:2] == (custom_csv, custom_csv.with_suffix(".state.json"))
    assert calls[2][1]["yearly"] is False
    monkeypatch.setattr(commutes.sys, "argv", ["ha_commutes.py", "--config", str(tmp_path / "missing.env")])
    assert commutes.main() == 1
    assert len(calls) == 3


def test_main_uses_local_year_for_default_path(tmp_path, monkeypatch):
    class FixedDatetime(commutes.datetime):
        @classmethod
        def now(cls, tz=None):
            return timestamp("2025-12-31T23:30:00Z")

    calls = []
    monkeypatch.setattr(commutes, "datetime", FixedDatetime)
    monkeypatch.setattr(commutes, "__file__", str(tmp_path / "ha_commutes.py"))
    monkeypatch.setattr(commutes.sys, "argv", ["ha_commutes.py"])
    monkeypatch.setattr(commutes, "load_config", lambda path: ("http://example.invalid", "dummy", "work", "a", "t"))
    monkeypatch.setattr(commutes, "run", lambda *args, **kwargs: calls.append(args) or 0)
    assert commutes.main() == 0
    assert calls[0][0] == tmp_path / "data" / "2026-sykkeldager.csv"


def test_custom_work_zone_recognizes_both_directions():
    zones, activities = day_events()
    zones = [
        commutes.Event(item.timestamp, "Other office" if item.state == commutes.WORK_ZONE else item.state)
        for item in zones
    ]
    end = timestamp("2026-11-03T18:00:00Z")
    assert commutes.find_trips(zones, activities, end)[0] == []
    trips, _ = commutes.find_trips(zones, activities, end, work_zone="Other office")
    assert [trip.origin for trip in trips] == ["home", "Other office"]


@pytest.mark.parametrize("name", ["ACTIVITY_ENTITY", "TRACKER_ENTITY"])
def test_entity_config_environment_override_and_empty_value(tmp_path, monkeypatch, name):
    for setting in ("HA_URL", "HA_TOKEN", "ZONE_WORK_NAME", "ACTIVITY_ENTITY", "TRACKER_ENTITY"):
        monkeypatch.delenv(setting, raising=False)
    config = tmp_path / ".env"
    config.write_text(
        'HA_TOKEN=dummy\nACTIVITY_ENTITY=sensor.file_activity\n'
        'TRACKER_ENTITY=device_tracker.file_phone\n',
    )
    index = 3 if name == "ACTIVITY_ENTITY" else 4
    assert commutes.load_config(config)[index] == (
        "sensor.file_activity" if name == "ACTIVITY_ENTITY" else "device_tracker.file_phone"
    )
    monkeypatch.setenv(name, "sensor.environment_activity" if index == 3 else "device_tracker.environment_phone")
    assert "environment" in commutes.load_config(config)[index]
    monkeypatch.setenv(name, "")
    with pytest.raises(ValueError, match=f"Missing required setting: {name}"):
        commutes.load_config(config)


def test_run_fetches_configured_entities(tmp_path, monkeypatch):
    zones, activities = day_events()
    entities = []

    def fetch(url, token, entity, start, end):
        entities.append(entity)
        return zones if entity == "device_tracker.other_phone" else activities

    monkeypatch.setattr(commutes, "fetch_history", fetch)
    csv_path = tmp_path / "commutes.csv"
    commutes.run(
        csv_path, tmp_path / "state.json", "http://example.invalid", "dummy",
        timestamp("2026-11-03T18:00:00Z"),
        activity_entity="sensor.other_activity", tracker_entity="device_tracker.other_phone",
    )
    assert entities == ["device_tracker.other_phone", "sensor.other_activity"]
    assert commutes.read_csv(csv_path)["2026-11-03"][commutes.FIELDS[2]] == "07:37:00"
