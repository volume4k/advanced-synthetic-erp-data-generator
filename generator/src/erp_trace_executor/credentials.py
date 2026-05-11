"""Credential loading for trace initialization."""

from __future__ import annotations

from pathlib import Path

from erp_trace_executor.errors import TraceExecutorError


class CredentialLookupError(TraceExecutorError):
    """Raised when a trace user has no available password."""


class EnvCredentialStore:
    """Password lookup keyed by SAP username."""

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        self._credentials = credentials or {}

    def password_for_username(self, username: str) -> str:
        try:
            return self._credentials[username]
        except KeyError as exc:
            raise CredentialLookupError(f"No password found for username '{username}'") from exc


def load_env_credentials(path: str | Path) -> EnvCredentialStore:
    values = _read_env_values(Path(path))
    credentials: dict[str, str] = {}

    for key, username in values.items():
        if not key.endswith("_UN"):
            continue
        password_key = f"{key[:-3]}_PW"
        password = values.get(password_key)
        if password is not None:
            credentials[username] = password

    return EnvCredentialStore(credentials)


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = _strip_quotes(value.strip())

    return values


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
