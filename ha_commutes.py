#!/usr/bin/env python3
"""Export weekday cycling commutes from Home Assistant history."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

ACTIVITY_ENTITY = "sensor.erlings_iphone_3_activity"
TRACKER_ENTITY = "device_tracker.erlings_iphone_3"
WORK_ZONE = "Å Energi"
LOCAL_ZONE = ZoneInfo("Europe/Oslo")
FIELDS = (
    "Dato",
    "Avreisetid hjemmefra",
    "Ankomst jobb",
    "Avreise jobb",
    "Ankomst hjem",
)


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    state: str


@dataclass(frozen=True)
class Trip:
    origin: str
    departure: datetime
    arrival: datetime


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_config(path: Path) -> tuple[str, str, str, str, str]:
    values = dotenv_values(path, interpolate=False) if path.is_file() else {}
    settings = {}
    for name, default in (
        ("HA_URL", "http://homeassistant.localdomain:8123"),
        ("HA_TOKEN", ""),
        ("ZONE_WORK_NAME", WORK_ZONE),
        ("ACTIVITY_ENTITY", ACTIVITY_ENTITY),
        ("TRACKER_ENTITY", TRACKER_ENTITY),
    ):
        value = os.environ.get(name, values.get(name, default))
        if value is None or not value.strip():
            raise ValueError(f"Missing required setting: {name}")
        settings[name] = value.strip()
    if settings["ZONE_WORK_NAME"] in {"home", "not_home", "unknown", "unavailable"}:
        raise ValueError("ZONE_WORK_NAME must name a work zone distinct from home.")
    return (
        settings["HA_URL"], settings["HA_TOKEN"], settings["ZONE_WORK_NAME"],
        settings["ACTIVITY_ENTITY"], settings["TRACKER_ENTITY"],
    )


def parse_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("Timestamps must include a time zone.")
    return timestamp.astimezone(timezone.utc)


def history_events(data: object, entity: str, start: datetime, end: datetime) -> list[Event]:
    if not isinstance(data, list) or any(not isinstance(group, list) for group in data):
        raise ValueError(f"Invalid history response for {entity}.")
    events = []
    for group in data:
        for row in group:
            if not isinstance(row, dict) or row.get("entity_id") != entity:
                raise ValueError(f"Unexpected entity in history for {entity}.")
            timestamp = parse_timestamp(row.get("last_updated") or row["last_changed"])
            state = row["state"]
            if not isinstance(state, str):
                raise ValueError(f"Invalid state for {entity}.")
            if timestamp <= end:
                events.append(Event(max(timestamp, start), state))
    events.sort(key=lambda event: event.timestamp)
    if not events:
        raise ValueError(f"No history for {entity}; check the entity ID and recorder configuration.")
    return events


def fetch_history(url: str, token: str, entity: str, start: datetime, end: datetime) -> list[Event]:
    query = urlencode({
        "filter_entity_id": entity,
        "end_time": end.isoformat(),
        "significant_changes_only": "0",
        "no_attributes": "1",
    })
    request = Request(
        f"{url.rstrip('/')}/api/history/period/{start.isoformat()}?{query}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=120) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise ValueError(f"Home Assistant returned HTTP {exc.code} for {entity}.") from exc
    except URLError as exc:
        raise ValueError("Could not connect to Home Assistant.") from exc
    return history_events(data, entity, start, end)


def find_trips(
    zones: list[Event], activities: list[Event], end: datetime,
    work_zone: str = WORK_ZONE,
) -> tuple[list[Trip], datetime | None]:
    cycling = []
    for index, event in enumerate(activities):
        stop = activities[index + 1].timestamp if index + 1 < len(activities) else end
        if event.state == "Cycling" and stop > event.timestamp:
            cycling.append((event.timestamp, stop))

    anchors = {"home", work_zone}
    previous = None
    pending = None
    trips = []
    for event in zones:
        if event.state == previous:
            continue
        if event.state in {"unknown", "unavailable"}:
            pending = None
        else:
            if previous in anchors:
                pending = (previous, event.timestamp)
            if event.state in anchors and pending is not None:
                origin, departure = pending
                if origin != event.state and any(
                    start < event.timestamp and stop > departure for start, stop in cycling
                ):
                    local_departure = departure.astimezone(LOCAL_ZONE)
                    if local_departure.weekday() < 5:
                        trips.append(Trip(origin, local_departure, event.timestamp.astimezone(LOCAL_ZONE)))
                pending = None
        previous = event.state
    return trips, pending[1] if pending is not None else None


def query_start(state: dict, now: datetime) -> datetime:
    floor = now - timedelta(days=30)
    if not state:
        return floor
    last_run = parse_timestamp(state["last_run"])
    if last_run <= floor or last_run > now:
        return floor
    # Replay the previous local day, and any trip still in progress.
    previous_day = last_run.astimezone(LOCAL_ZONE).date() - timedelta(days=1)
    start = datetime.combine(previous_day, time.min, LOCAL_ZONE).astimezone(timezone.utc)
    if state.get("pending_departure"):
        pending = parse_timestamp(state["pending_departure"])
        start = min(start, pending - timedelta(seconds=1))
    return max(start, floor)


def read_csv(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows = {}
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"Unexpected CSV columns in {path}.")
        for row in reader:
            if None in row or any(row.get(field) is None for field in FIELDS):
                raise ValueError(f"Malformed CSV row in {path}.")
            day = date.fromisoformat(row[FIELDS[0]])
            if row[FIELDS[0]] != day.isoformat() or row[FIELDS[0]] in rows:
                raise ValueError(f"Invalid or duplicate CSV date in {path}.")
            for departure, arrival in ((FIELDS[1], FIELDS[2]), (FIELDS[3], FIELDS[4])):
                if bool(row[departure]) != bool(row[arrival]):
                    raise ValueError(f"Incomplete CSV time pair in {path}.")
                for field in (departure, arrival):
                    if row[field]:
                        time.fromisoformat(row[field])
            rows[row[FIELDS[0]]] = row
    return rows


def merge_trips(rows: dict[str, dict[str, str]], trips: list[Trip]) -> None:
    for trip in sorted(trips, key=lambda item: item.departure):
        day = trip.departure.date().isoformat()
        row = rows.setdefault(day, dict.fromkeys(FIELDS, ""))
        row[FIELDS[0]] = day
        departure_field, arrival_field = (
            (FIELDS[1], FIELDS[2]) if trip.origin == "home" else (FIELDS[3], FIELDS[4])
        )
        departure = trip.departure.strftime("%H:%M:%S")
        if not row[departure_field] or departure <= row[departure_field]:
            row[departure_field] = departure
            row[arrival_field] = trip.arrival.strftime("%H:%M:%S")


def atomic_write(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_csv(stream, rows: dict[str, dict[str, str]]) -> None:
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows[day] for day in sorted(rows))


def run(
    csv_path: Path, state_path: Path, url: str, token: str, now: datetime,
    work_zone: str = WORK_ZONE,
    activity_entity: str = ACTIVITY_ENTITY,
    tracker_entity: str = TRACKER_ENTITY,
    yearly: bool = False,
) -> int:
    if csv_path.resolve() == state_path.resolve():
        raise ValueError("CSV and state file must have different paths.")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or (
        parts.username or parts.password or parts.query or parts.fragment
    ):
        raise ValueError("HA_URL must be an HTTP(S) base URL without credentials, query or fragment.")
    if not token:
        raise ValueError("Set HA_TOKEN in the config file or environment before running.")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = csv_path.parent / ".sykkeldager.lock" if yearly else csv_path.with_name(csv_path.name + ".lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        rows = read_csv(csv_path)
        state = {}
        if state_path.exists() and csv_path.exists():
            with state_path.open(encoding="utf-8") as stream:
                state = json.load(stream)
            if not isinstance(state, dict):
                raise ValueError("Invalid state file.")
        start = query_start(state, now)
        zones = fetch_history(url, token, tracker_entity, start, now)
        activities = fetch_history(url, token, activity_entity, start, now)
        trips, pending = find_trips(zones, activities, now, work_zone)
        files = {csv_path: rows}
        for trip in trips:
            path = csv_path.parent / f"{trip.departure.year}-sykkeldager.csv" if yearly else csv_path
            if path not in files:
                if path.resolve() == state_path.resolve():
                    raise ValueError("CSV and state file must have different paths.")
                files[path] = read_csv(path)
            merge_trips(files[path], [trip])
        for path, file_rows in files.items():
            atomic_write(path, lambda stream: write_csv(stream, file_rows))
        # CSV first: a failed checkpoint can safely replay without duplicates.
        checkpoint = {
            "last_run": now.isoformat(),
            "pending_departure": pending.isoformat() if pending is not None else None,
        }
        atomic_write(state_path, lambda stream: json.dump(checkpoint, stream, indent=2))
        return len(rows)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, help="Write all dates to this CSV instead of yearly files.")
    parser.add_argument("--state", type=Path, help="Defaults to ha-commutes.state.json, or the custom CSV path with suffix .state.json.")
    parser.add_argument("--config", type=Path, help="Defaults to .env beside this script; environment overrides file values.")
    args = parser.parse_args()
    try:
        script_dir = Path(__file__).resolve().parent
        now = datetime.now(timezone.utc)
        yearly = args.csv is None
        csv_path = args.csv or script_dir / "data" / f"{now.astimezone(LOCAL_ZONE).year}-sykkeldager.csv"
        state_path = args.state or (script_dir / "ha-commutes.state.json" if yearly else csv_path.with_suffix(".state.json"))
        config_path = args.config.expanduser() if args.config is not None else script_dir / ".env"
        if args.config is not None and not config_path.is_file():
            raise ValueError(f"Configuration file not found: {config_path}")
        url, token, work_zone, activity_entity, tracker_entity = load_config(config_path)
        count = run(
            csv_path, state_path, url, token, now,
            work_zone=work_zone,
            activity_entity=activity_entity,
            tracker_entity=tracker_entity,
            yearly=yearly,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"{count} dates saved to {csv_path.resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
