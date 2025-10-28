"""Async Modbus server management running in background threads."""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Mapping, Optional
from copy import deepcopy

from pymodbus.exceptions import ModbusException
from pymodbus.server.requesthandler import ServerRequestHandler

try:  # pymodbus < 3.5
    from pymodbus.server.async_io import AsyncModbusTcpServer  # type: ignore

    _ASYNC_SERVER_USES_START_STOP = True
except ModuleNotFoundError:  # pymodbus >= 3.5
    from pymodbus.server import ModbusTcpServer as AsyncModbusTcpServer

    _ASYNC_SERVER_USES_START_STOP = False

from .data_model import build_datastore

LOGGER = logging.getLogger(__name__)
REQUEST_LOGGER = LOGGER.getChild("requests")


class LoggingServerRequestHandler(ServerRequestHandler):
    """Request handler that logs incoming Modbus requests."""

    def __init__(
        self,
        owner,
        trace_packet,
        trace_pdu,
        trace_connect,
        *,
        request_logger: logging.Logger,
        server_label: str,
    ) -> None:
        self._request_logger = request_logger
        self._server_label = server_label
        super().__init__(owner, trace_packet, trace_pdu, trace_connect)

    async def handle_request(self) -> None:  # type: ignore[override]
        if self.last_pdu and self._request_logger.isEnabledFor(logging.INFO):
            peer = self._resolve_peer()
            function_code = getattr(self.last_pdu, "function_code", None)
            unit_id = getattr(self.last_pdu, "dev_id", None)
            function_display = (
                f"0x{function_code:02X}" if isinstance(function_code, int) else str(function_code)
            )
            unit_display = unit_id if unit_id is not None else "unbekannt"
            self._request_logger.info(
                "%s: Modbus-Anfrage von %s – Unit-ID %s, Funktion %s",
                self._server_label,
                peer,
                unit_display,
                function_display,
            )
        await super().handle_request()

    def _resolve_peer(self) -> str:
        if not self.transport:
            return "unbekannter Client"
        peer = self.transport.get_extra_info("peername")
        if isinstance(peer, (list, tuple)) and peer:
            host = peer[0]
            port = peer[1] if len(peer) > 1 else "?"
            return f"{host}:{port}"
        if peer:
            return str(peer)
        return "unbekannter Client"


class LoggingAsyncModbusTcpServer(AsyncModbusTcpServer):
    """Async Modbus server that emits log messages for each request."""

    def __init__(self, *args, request_logger: logging.Logger, server_label: str, **kwargs) -> None:
        self._request_logger = request_logger
        self._server_label = server_label
        super().__init__(*args, **kwargs)

    def callback_new_connection(self):  # type: ignore[override]
        return LoggingServerRequestHandler(
            self,
            self.trace_packet,
            self.trace_pdu,
            self.trace_connect,
            request_logger=self._request_logger,
            server_label=self._server_label,
        )


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
            server_kwargs = dict(
                context=context,
                address=(self._state.host, self._state.port),
            )
            if _ASYNC_SERVER_USES_START_STOP:
                server_kwargs["allow_reuse_address"] = True
            server_kwargs["request_logger"] = REQUEST_LOGGER
            server_kwargs["server_label"] = self.name
            self._server = LoggingAsyncModbusTcpServer(**server_kwargs)
            if _ASYNC_SERVER_USES_START_STOP:
                await self._server.start()
            else:
                await self._server.serve_forever(background=True)
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
                if _ASYNC_SERVER_USES_START_STOP:
                    await self._server.stop()
                else:
                    await self._server.shutdown()
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
