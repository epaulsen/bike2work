"""Locked, atomic CSV storage for recorded bike days."""

from __future__ import annotations

import csv
import fcntl
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

CSV_FIELDS = (
    "date",
    "time",
    "latitude",
    "longitude",
    "distance_m",
    "location_timestamp",
)


class StorageError(ValueError):
    """Raised when the registration CSV is malformed or cannot be stored."""


@dataclass(frozen=True)
class BikeDay:
    date: str
    time: str
    latitude: str
    longitude: str
    distance_m: str
    location_timestamp: str

    def as_dict(self) -> dict[str, str]:
        return {
            "date": self.date,
            "time": self.time,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "distance_m": self.distance_m,
            "location_timestamp": self.location_timestamp,
        }


@contextmanager
def _locked(path: Path, *, exclusive: bool) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_unlocked(path: Path) -> list[BikeDay]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                raise StorageError(f"Unexpected CSV columns in {path}")
            entries = []
            for row in reader:
                if None in row or any(row.get(field) is None for field in CSV_FIELDS):
                    raise StorageError(f"Malformed CSV row in {path}")
                try:
                    date.fromisoformat(row["date"])
                except ValueError as exc:
                    raise StorageError(f"Invalid date in {path}: {row['date']}") from exc
                entries.append(BikeDay(**{field: row[field] for field in CSV_FIELDS}))
            return entries
    except UnicodeDecodeError as exc:
        raise StorageError(f"CSV is not valid UTF-8: {path}") from exc
    except csv.Error as exc:
        raise StorageError(f"Malformed CSV in {path}: {exc}") from exc


def _write_unlocked(path: Path, entries: list[BikeDay]) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(entry.as_dict() for entry in sorted(entries, key=lambda item: item.date))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def read_entries(path: Path) -> list[BikeDay]:
    """Read all stored days under a shared lock."""
    with _locked(path, exclusive=False):
        return _read_unlocked(path)


def add_if_missing(path: Path, entry: BikeDay) -> bool:
    """Atomically add a day unless its local date is already present."""
    with _locked(path, exclusive=True):
        entries = _read_unlocked(path)
        if any(existing.date == entry.date for existing in entries):
            return False
        entries.append(entry)
        _write_unlocked(path, entries)
        return True
