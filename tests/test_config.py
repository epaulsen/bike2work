from pathlib import Path

import pytest

from bike_tracker.config import ConfigError, load_config


def test_loads_and_expands_paths_from_env_file(tmp_path: Path):
    config_file = tmp_path / "config.env"
    config_file.write_text(
        "\n".join(
            [
                "AIRTAG_FILE=~/bike.json",
                "WORK_LATITUDE=58.1",
                "WORK_LONGITUDE=7.2",
                "ROUND_TRIP_DISTANCE_KM=18",
                "DATA_FILE=~/days.csv",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_config(config_file)

    assert settings.airtag_file == Path.home() / "bike.json"
    assert settings.data_file == Path.home() / "days.csv"
    assert settings.geofence_radius_meters == 300
    assert settings.max_location_age_minutes == 120
    assert settings.timezone.key == "Europe/Oslo"


def test_rejects_invalid_work_coordinates(tmp_path: Path):
    config_file = tmp_path / "config.env"
    config_file.write_text(
        "\n".join(
            [
                "AIRTAG_FILE=bike.json",
                "WORK_LATITUDE=91",
                "WORK_LONGITUDE=7",
                "ROUND_TRIP_DISTANCE_KM=18",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="WORK_LATITUDE"):
        load_config(config_file)


def test_missing_config_file_is_an_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="Configuration file not found"):
        load_config(tmp_path / "missing.env")
