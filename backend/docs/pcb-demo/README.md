# Generated PCB demo: USB-powered LED indicator

`usb-led-top.png` and `usb-led-bottom.png` are the renders the PCB lane published for the accepted circuit contract
(USB-C receptacle, two 5.1k CC resistors, AMS1117-3.3 regulator, two 10 uF capacitors, 120 ohm resistor and a 0603 LED,
40 x 20 mm limit). Nothing was drawn by hand or by a model:

1. `pcb_generate.py` (pcbnew, inside the PCB image) placed KiCad's own footprints (GCT USB4105 receptacle, 0603/0805
   passives, SOT-223) by connectivity, with the connector at the board edge, inside a rounded outline (33.15 x 19.7 mm).
2. Freerouting routed it offline (75 track segments); dangling stubs were pruned.
3. KiCad ERC and DRC ran with 0 findings; Gerbers, drills and these renders were exported (`kicad-cli pcb render`).

Reproduce with the native PCB image: `uv run python <scratch driver>` against `NativeGateway` (build, then finish), or the
opt-in `tests/test_pcb_generate_container.py` for the generator alone. This is a candidate design: the independent
checker reports `UNKNOWN` for boards with real-library pads (roundrect pads, repeated shield pads), and no fabrication or
physical behavior is claimed.
