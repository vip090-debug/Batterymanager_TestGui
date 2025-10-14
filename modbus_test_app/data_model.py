"""Data model and datastore helpers for Modbus servers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping

from pymodbus.datastore import (
    ModbusServerContext,
    ModbusSequentialDataBlock,
    ModbusSlaveContext,
)

REGISTER_BASES: Dict[str, int] = {
    "holding": 40001,
    "input": 30001,
    "coils": 1,
    "discrete": 10001,
}


@dataclass
class RegisterInitialisation:
    """Container describing initial values for a register type."""

    register_type: str
    values: Mapping[str, int]

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
            data[offset] = int(value)
        return ModbusSequentialDataBlock(0, data)


def _human_to_offset(register_type: str, address: str | int) -> int:
    """Convert a human readable address into a zero based offset."""
    base = REGISTER_BASES[register_type]
    if isinstance(address, str):
        address_int = int(address)
    else:
        address_int = address
    return address_int - base


def build_datastore(initials: Mapping[str, Mapping[str, int]], unit_id: int) -> ModbusServerContext:
    """Create a :class:`ModbusServerContext` populated with initial register values."""
    holding_block = RegisterInitialisation("holding", initials.get("holding", {})).to_block()
    input_block = RegisterInitialisation("input", initials.get("input", {})).to_block()
    coil_block = RegisterInitialisation("coils", initials.get("coils", {})).to_block()
    discrete_block = RegisterInitialisation("discrete", initials.get("discrete", {})).to_block()

    slave_context = ModbusSlaveContext(
        di=discrete_block,
        co=coil_block,
        hr=holding_block,
        ir=input_block,
        zero_mode=True,
    )
    return ModbusServerContext(slaves={unit_id: slave_context}, single=False)


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
]
