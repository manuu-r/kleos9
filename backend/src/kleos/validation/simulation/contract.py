"""Accepted simulation bindings (`interfaces.simulation`).

Every binding is explicit. Nothing is inferred from net names, reference prefixes or the
order of loads. The contract names *which* accepted circuit components to simulate and how;
candidate SPICE, includes, model files and control blocks are never accepted.
"""
import math
import re
from typing import Literal

from pydantic import Field, model_validator

from kleos.contracts.models import WireModel

REFERENCE = r'^[A-Z]+[1-9][0-9]{0,5}$'
PIN = r'^[A-Za-z0-9][A-Za-z0-9_.+-]{0,31}$'
LED_VALUE = 'kleos-red-led-v1'
DIODE_VALUE = 'kleos-silicon-diode-v1'


class Terminals(WireModel):
    component_id: str = Field(pattern=REFERENCE)
    positive_pin: str = Field(pattern=PIN)
    negative_pin: str = Field(pattern=PIN)

    @model_validator(mode='after')
    def distinct(self):
        if self.positive_pin == self.negative_pin:
            raise ValueError('Terminals must use two different pins')
        return self


class SimulationLoad(WireModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,31}$')
    min_voltage_v: float = Field(ge=-60, le=60, allow_inf_nan=False)
    max_voltage_v: float = Field(ge=-60, le=60, allow_inf_nan=False)
    current_ma: float = Field(ge=0, le=10000, allow_inf_nan=False)
    probe: Terminals

    @model_validator(mode='after')
    def ordered(self):
        if self.min_voltage_v >= self.max_voltage_v:
            raise ValueError('Load voltage limits must be ordered')
        return self


class SimulatedComponent(WireModel):
    """Two-pin ideal element. For diodes `pins` is [anode, cathode]; otherwise order is irrelevant."""
    component_id: str = Field(pattern=REFERENCE)
    kind: Literal['R', 'C', 'LED', 'DIODE', 'OPEN']
    pins: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode='after')
    def shaped(self):
        if self.kind == 'OPEN':
            if self.pins:
                raise ValueError('An open component simulates no pins')
        elif len(self.pins) != 2 or self.pins[0] == self.pins[1] or any(not re.fullmatch(PIN, p) for p in self.pins):
            raise ValueError('A simulated element needs exactly two distinct pins')
        return self


class SimulationContract(WireModel):
    supply_voltage_v: float = Field(ge=.5, le=60, allow_inf_nan=False)
    max_current_ma: float = Field(gt=0, le=10000, allow_inf_nan=False)
    source: Terminals
    loads: list[SimulationLoad] = Field(min_length=1, max_length=16)
    components: list[SimulatedComponent] = Field(min_length=1, max_length=16)

    @model_validator(mode='after')
    def unique(self):
        if len({load.id for load in self.loads}) != len(self.loads):
            raise ValueError('Load identifiers must be unique')
        if len({item.component_id for item in self.components}) != len(self.components):
            raise ValueError('Each component may be classified once')
        return self


# Case-sensitive on purpose: SPICE reads 'M' as milli, so mega is only ever spelled 'meg'.
_SUFFIX_R = {'': 1.0, 'R': 1.0, 'k': 1e3, 'K': 1e3, 'meg': 1e6}
_SUFFIX_C = {'': 1.0, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3}
_NUMBER = re.compile(r'^([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(meg|[A-Za-z]?)$')


def parse_value(text, kind):
    """Strict decimal/scientific number with one optional suffix; no expressions or unit text."""
    if not isinstance(text, str) or not (match := _NUMBER.match(text.strip())) or text != text.strip():
        raise ValueError('Unsupported component value')
    suffixes, low, high = (_SUFFIX_R, .01, 1e9) if kind == 'R' else (_SUFFIX_C, 1e-12, .1)
    multiplier = suffixes.get(match.group(2))
    if multiplier is None:
        raise ValueError('Unsupported component value suffix')
    value = float(match.group(1)) * multiplier
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Component value is outside the supported range')
    return value
