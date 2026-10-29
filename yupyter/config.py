"""User configuration (~/.config/yupyter/config.toml).

Stub: only ``aliases`` for now. The kernel-pool work expands this
(kernelspec-derived defaults, per-notebook overrides).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:  # py3.11+
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

DEFAULT_ALIASES: dict[str, str] = {
    "py": "python3",
    "claude": "agent:claude",
}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "yupyter" / "config.toml"


def load_config() -> dict[str, Any]:
    p = config_path()
    data: dict[str, Any] = {}
    if p.exists():
        try:
            data = tomllib.loads(p.read_text())
        except Exception:
            data = {}
    aliases = dict(DEFAULT_ALIASES)
    aliases.update(data.get("aliases", {}))
    data["aliases"] = aliases
    return data


def aliases() -> dict[str, str]:
    return load_config()["aliases"]


def resolve_alias(name: str, overrides: dict[str, str] | None = None) -> str:
    """Map a short name (``py``) to an executor name (``python3``); identity if unknown."""
    table = aliases()
    if overrides:
        table = {**table, **overrides}
    return table.get(name, name)
