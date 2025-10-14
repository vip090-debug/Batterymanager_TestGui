"""Configuration utilities for the Modbus test application."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping

DEFAULT_CONFIG: Dict[str, Any] = {
    "battery_server": {"host": "127.0.0.1", "port": 5020, "unit_id": 1},
    "master_server": {"host": "127.0.0.1", "port": 502, "unit_id": 1},
    "initial_registers": {
        "unit_id": 1,
        "holding": {"40001": 1, "40002": 0, "40010": 1234},
        "input": {"30001": 3700, "30002": 251},
        "coils": {"00001": 1, "00002": 0},
        "discrete": {"10001": 1, "10002": 0},
    },
}

CONFIG_PATH = Path(__file__).resolve().parent / "resources" / "config.json"


@dataclass
class ServerConfig:
    """Dataclass describing a single Modbus server configuration."""

    host: str = "127.0.0.1"
    port: int = 5020
    unit_id: int = 1


@dataclass
class AppConfig:
    """Top-level configuration model."""

    battery_server: ServerConfig = field(default_factory=ServerConfig)
    master_server: ServerConfig = field(default_factory=lambda: ServerConfig(port=502))
    initial_registers: Dict[str, Dict[str, int]] = field(
        default_factory=lambda: DEFAULT_CONFIG["initial_registers"].copy()
    )


class ConfigManager:
    """Utility class for loading and persisting the application configuration."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or CONFIG_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._config: Dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def ensure_exists(self) -> None:
        """Create the configuration file with defaults if it does not yet exist."""
        if not self._path.exists():
            self._path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")

    def load(self) -> Dict[str, Any]:
        """Load configuration from disk, creating defaults if required."""
        self.ensure_exists()
        with self._path.open("r", encoding="utf-8") as handle:
            self._config = json.load(handle)
        return self._config

    def save(self, config: Mapping[str, Any]) -> None:
        """Persist the provided configuration mapping to disk."""
        data: MutableMapping[str, Any] = dict(config)
        with self._path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        self._config = dict(data)

    def get_config(self) -> Dict[str, Any]:
        """Return the in-memory configuration, loading it if necessary."""
        if self._config is None:
            return self.load()
        return self._config


def get_server_config(config: Mapping[str, Any], key: str) -> ServerConfig:
    """Extract a :class:`ServerConfig` for the given key from a config mapping."""

    entry = config.get(key, {})
    return ServerConfig(
        host=entry.get("host", "127.0.0.1"),
        port=int(entry.get("port", 5020)),
        unit_id=int(entry.get("unit_id", 1)),
    )


__all__ = [
    "AppConfig",
    "ConfigManager",
    "DEFAULT_CONFIG",
    "CONFIG_PATH",
    "ServerConfig",
    "get_server_config",
]
