"""Data model and datastore helpers for Modbus servers."""
from __future__ import annotations

import inspect
import pkgutil
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from importlib import import_module
from typing import Dict, Iterable, Mapping, Sequence, Type, cast


CLASS_ALIASES = {
    "ModbusSlaveContext": ("ModbusDeviceContext",),
}

KNOWN_SUBMODULES: Sequence[str] = (
    "pymodbus.datastore.context",
    "pymodbus.datastore.sequential",
    "pymodbus.datastore.sparse",
    "pymodbus.datastore.simulator",
    "pymodbus.datastore.store",
)


def _resolve_from_module(module: object, name: str) -> Type[object] | None:
    """Return a datastore class from ``module`` if present."""

    for candidate in (name, *CLASS_ALIASES.get(name, ())):
        attr = getattr(module, candidate, None)
        if isinstance(attr, type):
            return attr
    return None


def _load_datastore_class(name: str) -> Type[object]:
    """Return a datastore class regardless of the pymodbus version."""

    base_module = import_module("pymodbus.datastore")
    resolved = _resolve_from_module(base_module, name)
    if resolved is not None:
        return resolved

    for module_name in KNOWN_SUBMODULES:
        try:
            module = import_module(module_name)
        except Exception:  # pragma: no cover - defensive: skip broken modules
            continue
        resolved = _resolve_from_module(module, name)
        if resolved is not None:
            return resolved

    module_path = getattr(base_module, "__path__", None)
    if module_path is not None:
        for _, module_name, _ in pkgutil.walk_packages(
            module_path, base_module.__name__ + "."
        ):
            try:
                module = import_module(module_name)
            except Exception:  # pragma: no cover - defensive: skip broken modules
                continue
            resolved = _resolve_from_module(module, name)
            if resolved is not None:
                return resolved

    raise ImportError(f"Unable to import '{name}' from pymodbus.datastore")


@lru_cache(maxsize=None)
def _datastore_class(name: str) -> Type[object]:
    """Cache loader for pymodbus datastore classes."""

    return _load_datastore_class(name)


ModbusServerContext = cast(Type[object], _datastore_class("ModbusServerContext"))
ModbusSequentialDataBlock = cast(Type[object], _datastore_class("ModbusSequentialDataBlock"))
ModbusSlaveContext = cast(Type[object], _datastore_class("ModbusSlaveContext"))

_SLAVE_SUPPORTS_ZERO_MODE = "zero_mode" in inspect.signature(ModbusSlaveContext.__init__).parameters
_SERVER_CONTEXT_USES_SLAVES = "slaves" in inspect.signature(ModbusServerContext.__init__).parameters

REGISTER_BASES: Dict[str, int] = {
    "holding": 40001,
    "input": 30001,
    "coils": 1,
    "discrete": 10001,
}


def parse_register_value(value: object) -> int:
    """Convert *value* into an integer understood by Modbus registers.

    The configuration files may contain numbers written with the German
    decimal comma (e.g. ``"0,000"``).  Python's :func:`int` does not accept
    this representation, therefore we normalise such inputs before converting
    them.  Values that still contain a fractional component after normalisation
    are rejected to avoid silently truncating data.
    """

    if isinstance(value, bool):  # bool is a subclass of int but be explicit
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"Registerwerte müssen ganze Zahlen sein: {value}")
        return int(value)

    text = str(value).strip()
    if not text:
        raise ValueError("Leerer Registerwert ist ungültig.")

    try:
        return int(text, 0)
    except ValueError:
        normalised = text
        if "," in normalised:
            # Support German decimal comma as well as thousand separators.
            normalised = normalised.replace(".", "").replace(",", ".")
        try:
            decimal_value = Decimal(normalised)
        except InvalidOperation as exc:
            raise ValueError(f"Ungültiger Registerwert: {value}") from exc
        if decimal_value != decimal_value.to_integral_value():
            raise ValueError(f"Registerwerte müssen ganze Zahlen sein: {value}")
        return int(decimal_value)


@dataclass
class RegisterInitialisation:
    """Container describing initial values for a register type."""

    register_type: str
    values: Mapping[str, int | float | str]

    def to_block(self) -> ModbusSequentialDataBlock:
        """Convert stored values into a sequential data block."""
        offsets = [_human_to_offset(self.register_type, address) for address in self.values]
        if not offsets:
            return ModbusSequentialDataBlock(0, [0])
        size = max(offsets) + 1
        data = [0] * size
        for address, value in self.values.items():
            offset = _human_to_offset(self.register_type, address)
            if offset < 0:
                continue
            data[offset] = parse_register_value(value)
        return ModbusSequentialDataBlock(0, data)


def _human_to_offset(register_type: str, address: str | int) -> int:
    """Convert a human readable address into a zero based offset.

    The UI and client helpers accept both the traditional Modbus register
    numbers (e.g. ``40001``) and zero-based offsets (e.g. ``0``).  pymodbus
    always expects offsets, therefore we map any non-negative value below the
    human-readable base directly to its offset representation.
    """

    base = REGISTER_BASES[register_type]
    if isinstance(address, str):
        address_int = int(address, 0)
    else:
        address_int = int(address)

    offset = address_int - base
    if offset >= 0:
        return offset
    if address_int >= 0:
        # ``address`` was already provided as an offset.
        return address_int
    return offset


def build_datastore(initials: Mapping[str, Mapping[str, int]], unit_id: int) -> ModbusServerContext:
    """Create a :class:`ModbusServerContext` populated with initial register values."""
    holding_block = RegisterInitialisation("holding", initials.get("holding", {})).to_block()
    input_block = RegisterInitialisation("input", initials.get("input", {})).to_block()
    coil_block = RegisterInitialisation("coils", initials.get("coils", {})).to_block()
    discrete_block = RegisterInitialisation("discrete", initials.get("discrete", {})).to_block()

    slave_kwargs = dict(
        di=discrete_block,
        co=coil_block,
        hr=holding_block,
        ir=input_block,
    )
    if _SLAVE_SUPPORTS_ZERO_MODE:
        slave_kwargs["zero_mode"] = True
    slave_context = ModbusSlaveContext(**slave_kwargs)
    context_kwargs = dict(single=False)
    if _SERVER_CONTEXT_USES_SLAVES:
        context_kwargs["slaves"] = {unit_id: slave_context}
    else:
        context_kwargs["devices"] = {unit_id: slave_context}
    return ModbusServerContext(**context_kwargs)


def iter_addresses(register_type: str, addresses: Iterable[int]) -> Iterable[int]:
    """Translate human-readable register addresses into offsets for client calls."""
    base = REGISTER_BASES[register_type]
    for address in addresses:
        yield address - base


def human_to_offset(register_type: str, address: int) -> int:
    """Public helper mirroring :func:`_human_to_offset`."""
    return _human_to_offset(register_type, address)


__all__ = [
    "REGISTER_BASES",
    "RegisterInitialisation",
    "build_datastore",
    "human_to_offset",
    "iter_addresses",
    "parse_register_value",
]
