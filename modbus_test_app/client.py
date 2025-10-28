"""Client helper functions performing Modbus read/write operations."""
from __future__ import annotations

from inspect import signature
from typing import List, Sequence

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException

from .data_model import human_to_offset

READABLE_TYPES = {"holding", "input", "coils", "discrete"}
WRITABLE_TYPES = {"holding", "coils"}


class ClientError(RuntimeError):
    """Base class for client interaction errors."""


def _connect_client(host: str, port: int) -> ModbusTcpClient:
    client = ModbusTcpClient(host=host, port=port)
    if not client.connect():
        client.close()
        raise ClientError(f"Unable to connect to Modbus server at {host}:{port}")
    return client


def _call_with_unit_support(fn, *args, unit_id: int, **kwargs):
    """Invoke *fn* using the parameter supported by the pymodbus version."""

    params = signature(fn).parameters
    if "slave" in params:
        return fn(*args, slave=unit_id, **kwargs)
    if "unit" in params:
        return fn(*args, unit=unit_id, **kwargs)
    return fn(*args, **kwargs)


def read_registers(
    *,
    host: str,
    port: int,
    unit_id: int,
    register_type: str,
    address: int,
    count: int,
) -> List[int | bool]:
    """Read one or more registers from the server."""
    register_type = register_type.lower()
    if register_type not in READABLE_TYPES:
        raise ValueError(f"Unsupported register type: {register_type}")
    if count < 1:
        raise ValueError("Count must be at least 1")
    offset = human_to_offset(register_type, address)
    if offset < 0:
        raise ValueError("Address must be a non-negative integer")

    client = _connect_client(host, port)
    try:
        if register_type == "holding":
            response = _call_with_unit_support(
                client.read_holding_registers, offset, count=count, unit_id=unit_id
            )
        elif register_type == "input":
            response = _call_with_unit_support(
                client.read_input_registers, offset, count=count, unit_id=unit_id
            )
        elif register_type == "coils":
            response = _call_with_unit_support(
                client.read_coils, offset, count=count, unit_id=unit_id
            )
        else:
            response = _call_with_unit_support(
                client.read_discrete_inputs, offset, count=count, unit_id=unit_id
            )

        if response.isError():
            raise ClientError(str(response))

        if register_type in {"holding", "input"}:
            return list(response.registers)
        return list(response.bits)[:count]
    except (ConnectionException, ModbusException) as exc:
        raise ClientError(str(exc)) from exc
    finally:
        client.close()


def write_register(
    *,
    host: str,
    port: int,
    unit_id: int,
    register_type: str,
    address: int,
    value: int | Sequence[int] | bool | Sequence[bool],
) -> None:
    """Write registers/coils on the server."""
    register_type = register_type.lower()
    if register_type not in WRITABLE_TYPES:
        raise ValueError(f"Register type '{register_type}' does not support write operations")
    offset = human_to_offset(register_type, address)
    if offset < 0:
        raise ValueError("Address must be a non-negative integer")

    client = _connect_client(host, port)
    try:
        if register_type == "holding":
            if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
                values = [int(v) for v in value]
                if len(values) == 1:
                    response = _call_with_unit_support(
                        client.write_register, offset, values[0], unit_id=unit_id
                    )
                else:
                    response = _call_with_unit_support(
                        client.write_registers, offset, values, unit_id=unit_id
                    )
            else:
                response = _call_with_unit_support(
                    client.write_register, offset, int(value), unit_id=unit_id
                )
        else:  # coils
            if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
                bools = [bool(v) for v in value]
                if len(bools) == 1:
                    response = _call_with_unit_support(
                        client.write_coil, offset, bools[0], unit_id=unit_id
                    )
                else:
                    response = _call_with_unit_support(
                        client.write_coils, offset, bools, unit_id=unit_id
                    )
            else:
                response = _call_with_unit_support(
                    client.write_coil, offset, bool(value), unit_id=unit_id
                )

        if response.isError():
            raise ClientError(str(response))
    except (ConnectionException, ModbusException) as exc:
        raise ClientError(str(exc)) from exc
    finally:
        client.close()


__all__ = ["ClientError", "read_registers", "write_register"]
