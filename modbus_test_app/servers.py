"""Async Modbus server management running in background threads."""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Mapping, Optional
from copy import deepcopy

from pymodbus.exceptions import ModbusException
from pymodbus.server.async_io import AsyncModbusTcpServer

from .data_model import build_datastore

LOGGER = logging.getLogger(__name__)


@dataclass
class ServerState:
    """Representation of the runtime state for a Modbus server."""

    host: str
    port: int
    unit_id: int
    initials: Mapping[str, Mapping[str, int]]


class BaseServer:
    """Wraps an :class:`AsyncModbusTcpServer` inside a dedicated thread."""

    def __init__(self, name: str, state: ServerState) -> None:
        self.name = name
        self._state = state
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[AsyncModbusTcpServer] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._startup_event = threading.Event()
        self._running = threading.Event()
        self._startup_exception: Optional[Exception] = None

    @property
    def host(self) -> str:
        return self._state.host

    @property
    def port(self) -> int:
        return self._state.port

    @property
    def unit_id(self) -> int:
        return self._state.unit_id

    def configure(self, *, host: str, port: int, unit_id: int, initials: Mapping[str, Mapping[str, int]]) -> None:
        """Update server configuration used on the next start."""
        self._state = ServerState(host=host, port=port, unit_id=unit_id, initials=initials)

    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self.is_running():
            LOGGER.info("%s already running", self.name)
            return
        self._startup_event.clear()
        self._startup_exception = None
        self._thread = threading.Thread(target=self._run_thread, name=f"{self.name}-Server", daemon=True)
        self._thread.start()
        self._startup_event.wait()
        if self._startup_exception:
            raise self._startup_exception

    def stop(self) -> None:
        if not self.is_running():
            return
        if self._loop and self._shutdown_event:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)
        if self._thread:
            self._thread.join(timeout=5)
        self._running.clear()

    def _run_thread(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._shutdown_event = asyncio.Event()
            self._loop.run_until_complete(self._start_async())
        finally:
            if self._loop:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                self._loop.close()

    async def _start_async(self) -> None:
        try:
            context = build_datastore(self._state.initials, self._state.unit_id)
            self._server = AsyncModbusTcpServer(
                context=context,
                address=(self._state.host, self._state.port),
                allow_reuse_address=True,
            )
            await self._server.start()
            LOGGER.info("%s started on %s:%s", self.name, self._state.host, self._state.port)
            self._running.set()
            self._startup_event.set()
            if not self._shutdown_event:
                return
            await self._shutdown_event.wait()
        except PermissionError as exc:
            message = (
                f"{self.name} failed to start on port {self._state.port}. "
                "Permission denied. Please choose a port above 1024."
            )
            LOGGER.error(message)
            self._startup_exception = PermissionError(message)
            self._startup_event.set()
        except OSError as exc:
            LOGGER.exception("%s failed to start due to OS error", self.name)
            self._startup_exception = exc
            self._startup_event.set()
        except ModbusException as exc:
            LOGGER.exception("%s failed to start", self.name)
            self._startup_exception = exc
            self._startup_event.set()
        except Exception as exc:  # pragma: no cover - safety net
            LOGGER.exception("Unexpected error starting %s", self.name)
            self._startup_exception = exc
            self._startup_event.set()
        finally:
            if self._server:
                await self._server.stop()
            self._running.clear()
            if self._shutdown_event:
                self._shutdown_event.clear()
            self._server = None


class BatteryServer(BaseServer):
    """Modbus server representing the battery device."""

    def __init__(self, state: ServerState) -> None:
        super().__init__("Battery Server", state)


class MasterServer(BaseServer):
    """Modbus server representing the master controller."""

    def __init__(self, state: ServerState) -> None:
        super().__init__("Master Server", state)


class ServerManager:
    """Manage both Modbus servers and allow reconfiguration at runtime."""

    def __init__(self, battery_state: ServerState, master_state: ServerState) -> None:
        self.battery_server = BatteryServer(battery_state)
        self.master_server = MasterServer(master_state)

    def apply_configuration(self, config: Mapping[str, Mapping[str, int] | int | str]) -> None:
        initials = config["initial_registers"]
        battery_cfg = config["battery_server"]
        master_cfg = config["master_server"]
        self.battery_server.configure(
            host=battery_cfg["host"],
            port=int(battery_cfg["port"]),
            unit_id=int(battery_cfg["unit_id"]),
            initials=deepcopy(initials),
        )
        self.master_server.configure(
            host=master_cfg["host"],
            port=int(master_cfg["port"]),
            unit_id=int(master_cfg["unit_id"]),
            initials=deepcopy(initials),
        )

    def stop_all(self) -> None:
        self.battery_server.stop()
        self.master_server.stop()


__all__ = [
    "ServerManager",
    "BatteryServer",
    "MasterServer",
    "ServerState",
    "BaseServer",
]
