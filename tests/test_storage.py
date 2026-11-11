from pathlib import Path

import pytest

from bike_tracker.storage import BikeDay, StorageError, add_if_missing, read_entries


def entry(day: str) -> BikeDay:
    return BikeDay(day, "08:00:00", "58.000000", "7.000000", "12.3", "2026-10-15T08:00:00+02:00")


def test_add_is_idempotent_and_preserves_existing_days(tmp_path: Path):
    data_file = tmp_path / "days.csv"
    assert add_if_missing(data_file, entry("2026-10-15"))
    assert not add_if_missing(data_file, entry("2026-10-15"))
    assert add_if_missing(data_file, entry("2026-10-16"))
    assert [item.date for item in read_entries(data_file)] == ["2026-10-15", "2026-10-16"]
    assert data_file.stat().st_mode & 0o777 == 0o600


def test_malformed_csv_is_not_overwritten(tmp_path: Path):
    data_file = tmp_path / "days.csv"
    original = "wrong,columns\nkeep,this\n"
    data_file.write_text(original, encoding="utf-8")

    with pytest.raises(StorageError, match="columns"):
        add_if_missing(data_file, entry("2026-10-15"))

    assert data_file.read_text(encoding="utf-8") == original
