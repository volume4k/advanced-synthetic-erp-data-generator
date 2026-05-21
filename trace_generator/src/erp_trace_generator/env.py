"""Small .env reader for trace-generator runtime settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping


def read_env_values(path: str | Path) -> dict[str, str]:
    path = Path(path).expanduser()
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = _strip_quotes(value.strip())

    return values


def load_env_file(
    path: str | Path,
    *,
    environ: MutableMapping[str, str] | None = None,
    override: bool = False,
) -> dict[str, str]:
    target = environ if environ is not None else os.environ
    values = read_env_values(path)
    for key, value in values.items():
        if override or key not in target:
            target[key] = value
    return values


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
