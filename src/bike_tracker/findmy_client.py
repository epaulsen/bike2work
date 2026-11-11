"""FindMy.py adapter; isolates unofficial Apple Find My integration."""

from __future__ import annotations

import getpass
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import TrackerConfig
from .registration import Location

if TYPE_CHECKING:
    from findmy import AppleAccount, LoginState

logger = logging.getLogger(__name__)


class FindMyClientError(RuntimeError):
    """Raised when FindMy.py cannot authenticate or fetch an AirTag location."""


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


class FindMyClient:
    """Load saved FindMy.py account/device state and fetch an AirTag report."""

    def __init__(self, config: TrackerConfig) -> None:
        self._config = config

    def get_location(self) -> Location | None:
        try:
            from aiohttp import ClientError
            from findmy import (
                AppleAccount,
                FindMyAccessory,
                InvalidCredentialsError,
                InvalidStateError,
                LocalAnisetteProvider,
                LoginState,
                UnauthorizedError,
                UnhandledProtocolError,
            )
            from findmy.errors import EmptyResponseError
        except ImportError as exc:
            raise FindMyClientError(
                "FindMy.py is unavailable. Install the project with `pip install -e .`."
            ) from exc

        device_path = self._config.airtag_file
        if not device_path.is_file():
            raise FindMyClientError(
                f"AirTag key file not found: {device_path}. "
                "See README for FindMy.py device-key setup."
            )
        try:
            device = FindMyAccessory.from_json(device_path)
        except (OSError, ValueError, KeyError) as exc:
            raise FindMyClientError(f"Could not load AirTag key file {device_path}: {exc}") from exc

        self._config.account_file.parent.mkdir(parents=True, exist_ok=True)
        self._config.anisette_libs_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            account = AppleAccount.from_json(
                str(self._config.account_file),
                anisette_libs_path=str(self._config.anisette_libs_file),
            )
        except FileNotFoundError:
            account = AppleAccount(
                LocalAnisetteProvider(libs_path=str(self._config.anisette_libs_file))
            )
            try:
                self._login(account, LoginState)
            except (
                InvalidCredentialsError,
                InvalidStateError,
                UnauthorizedError,
                ClientError,
                OSError,
                TimeoutError,
            ) as exc:
                raise FindMyClientError(f"Apple-innlogging mislyktes: {exc}") from exc
        except (OSError, ValueError) as exc:
            raise FindMyClientError(
                f"Could not restore Apple session {self._config.account_file}: {exc}. "
                "Remove that file only if you want to authenticate again."
            ) from exc

        try:
            report = account.fetch_location(device)
            if report is None:
                return None
            return Location(
                latitude=report.latitude,
                longitude=report.longitude,
                observed_at=report.timestamp,
            )
        except (
            EmptyResponseError,
            InvalidCredentialsError,
            InvalidStateError,
            UnauthorizedError,
            UnhandledProtocolError,
            OSError,
            TimeoutError,
            ClientError,
        ) as exc:
            raise FindMyClientError(f"Find My location request failed: {exc}") from exc
        finally:
            try:
                _write_private_json(self._config.account_file, account.to_json())
                _write_private_json(device_path, device.to_json())
            finally:
                account.close()

    @staticmethod
    def _login(account: AppleAccount, login_state: type[LoginState]) -> None:
        if not sys.stdin.isatty():
            raise FindMyClientError(
                "Førstegangsinnlogging krever en interaktiv terminal. "
                "Kjør `bike-tracker check` manuelt først."
            )
        try:
            email = input("Apple Account e-postadresse: ")
            password = getpass.getpass("Apple Account-passord: ")
        except EOFError as exc:
            raise FindMyClientError("Apple-innlogging ble avbrutt.") from exc
        state = account.login(email, password)
        if state == login_state.REQUIRE_2FA:
            methods = account.get_2fa_methods()
            if not methods:
                raise FindMyClientError("Apple krevde 2FA, men tilbød ingen metoder.")
            for index, method in enumerate(methods):
                phone = getattr(method, "phone_number", None)
                label = f"SMS ({phone})" if phone else "Godkjenn på betrodd enhet"
                print(f"{index}: {label}")
            try:
                selected = int(input("Velg 2FA-metode: "))
                method = methods[selected]
            except (ValueError, IndexError, EOFError) as exc:
                raise FindMyClientError("Ugyldig valg av 2FA-metode.") from exc
            method.request()
            try:
                code = getpass.getpass("Apple 2FA-kode: ")
            except EOFError as exc:
                raise FindMyClientError("Apple-innlogging ble avbrutt.") from exc
            method.submit(code)
        elif state != login_state.LOGGED_IN:
            raise FindMyClientError(f"Apple-innlogging mislyktes (tilstand: {state}).")

        logger.info("Apple Account-innlogging fullført; sesjon lagres lokalt.")
