"""Trusted numeric-only circuit specimen, never a generated source input."""


def specimen(kind="rc", bad=False):
    def part(ident, symbol, value, pins, loads=()):
        footprint = {"Device:R": "Resistor_SMD:R_0603_1608Metric",
                     "Device:C": "Capacitor_SMD:C_0603_1608Metric",
                     "Device:LED": "LED_SMD:LED_0603_1608Metric",
                     "Connector_Generic:Conn_01x02": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"}[symbol]
        return {"id": ident, "reference": ident, "symbol": symbol, "value": value,
                "footprint": footprint, "pins": pins, "loadIds": list(loads)}
    parts = [part("J1", "Connector_Generic:Conn_01x02", "Supply", {"1": "VIN", "2": "GND"}),
             part("R1", "Device:R", "100" if bad else "1k", {"1": "VIN", "2": "OUT"})]
    if kind == "rc":
        parts.append(part("C1", "Device:C", "1u", {"1": "OUT", "2": "GND"}, ["load"]))
    elif kind == "divider":
        parts.append(part("R2", "Device:R", "2k", {"1": "OUT", "2": "GND"}, ["load"]))
    else:
        parts.append(part("D1", "Device:LED", "kleos-red-led-v1" if kind in {"led", "power-on"} else "unmodelled",
                          {"1": "GND", "2": "OUT"}, ["load"]))
        if kind == "power-on":
            parts.append(part("C1", "Device:C", "1u", {"1": "OUT", "2": "GND"}))
    return {"circuit": {"schemaVersion": "circuit-netlist-v1", "components": parts},
            "power": {"supplyVoltageV": 3.3, "maxCurrentMa": 50,
                      "loads": [{"id": "load", "minVoltageV": 1.8,
                                 "maxVoltageV": 3.4, "currentMa": 5}],
                      "simulation": {"source": {"componentId": "J1", "positivePin": "1", "negativePin": "2"},
                          "loads": {"load": {"componentId": "D1" if kind == "power-on" else parts[-1]["id"],
                                             "positivePin": "2" if kind not in {"rc", "divider"} else "1",
                                             "negativePin": "1" if kind not in {"rc", "divider"} else "2"}}}}}
