# Kleos9 theme ("Signal")

The whole look of the app lives in this folder. Nothing outside it picks colours,
radii, type sizes or motion; screens use the tokens and the `K*` components.

| File | What it decides |
|---|---|
| `tokens.dart` | Colour, spacing, type scale, motion. **Edit this to change the look.** |
| `shape.dart` | The corner language: chamfered panels, sharp 3px controls. |
| `smooth_scroll.dart` | `KSmoothController`: eased mouse-wheel scrolling for web and desktop (touch keeps native momentum). |
| `ornaments.dart` | HUD detail: mono readout label (`KLabel`), corner brackets, faint grid. |
| `theme_data.dart` | Flutter's `ThemeData` (fields, dialogs, menus, switches, sliders) built from the tokens. |
| `components.dart` | The widgets: `KCard`, `KPanel`, `KButton`, `KBadge`, `KTabStrip`/`KSegmented`, loaders, notices, the stage, and the shared viewer controls (`KToolButton`, `KToolTile`, `KChip`). |
| `theme.dart` | Barrel. `core/theme.dart` and `core/widgets.dart` re-export it so older imports keep working. |

## Iterate fast

```bash
cd kleos-ui
flutter run -t lib/theme_gallery.dart -d chrome   # no backend needed
```

The gallery shows every token and component on one page (colours with live contrast,
type, shapes, buttons, tabs, fields, panels, loaders, the stage, the CAD viewer). Edit a
file here, hot reload, resize the window for phone, tablet and desktop. `test/theme_gallery_test.dart`
keeps it rendering at 320, 390, 768 and 1280 wide.

## The direction

A flat engineering console, drawn from a HUD-style poster and a pro CAD tablet app.

- **Flat.** No shadows, glows, glass or gradients in chrome. Panels are near-black, outlined by a
  1px line; depth comes from outline and tone, not blur.
- **Sharp.** Controls are 3px rectangles. Panels are never rounded: the top-right and bottom-left
  corners are cut at 45 degrees (`KShape.panel`, size `KRadius.card`). Change that one function to
  change every panel.
- **Two hues, two jobs.** Blue (`action` / `actionInk`) is what you can do and what is selected:
  the primary button, the tab indicator, sliders, switches, focus. Coral (`accent`) is the product
  and live work: running loaders, the part you touch in a 3D view, the brand dot. Status
  (`pass`, `fail`, `warn`, `human`, `unknown`) is always paired with a word.
- **Readout detail, sparingly.** Mono uppercase labels (`KLabel`) for stat labels and meta lines,
  viewfinder brackets and a grid behind products (`KGridBackdrop`). A few per screen at most.
- **Type.** Geist for text, Geist Mono for ids, figures and readouts. Cool-white text.
- **Palette.** Cool, blue-tinted graphite neutrals so the coral product colour is a true complement.
  What the CAD and PCB viewers draw has its own palette, `KViz` (metal and mask tones, copper layers,
  lighting), so the renders can be tuned without touching the interface colours.

## Viewers

Both viewers (`features/project/cad_assembly_preview.dart`, `pcb_preview.dart`) are built to be handled
on a phone and share the tool tiles and chips above.

- **CAD.** One finger orbits (with inertia), two fingers pinch and pan, the wheel zooms, tapping a part
  selects it and the others fall back. A views menu snaps to Front/Back/Left/Right/Top/Bottom/Iso with an
  animated camera. Key and fill light follow the camera, with a specular highlight; a ground grid and soft
  contact shadow anchor the model; an axis gizmo shows orientation. Faces are depth-sorted and drawn in one
  batched `drawVertices` call per frame. Taps are instant: double-tap is detected by hand, since the
  platform recogniser delays every single tap by 300ms.
- **PCB.** Pinch, drag with inertia, wheel and double-tap zoom, drawn in board millimetres with a grid,
  dimensions and a scale bar. Tapping a component (or its chip) selects it, flies the camera to it and
  lights its traces; the layers menu toggles top copper, bottom copper, components and labels.

## Feel

Wheel scrolling eases toward a target and accumulates, trackpads pass through untouched, taps respond on
touch-down with a slight give and a spring back, selection moves with one gliding indicator, and navigation
and primary actions tick on native. Everything stands still under reduced motion.

## Rules that are tested

`test/design_system_test.dart` enforces: every text colour clears 4.5:1 on the surfaces it is used
on, cards are chamfered outlined panels, controls are sharp, tabs share one gliding indicator and
are 44px tall, loaders stand still under reduced motion, progress fills actually paint.

## Changing direction later

To try a different direction, change `tokens.dart` (palette, radii, type), `shape.dart` (corners)
and the few component visuals in `components.dart`; check the gallery; run `flutter test`. The
screens do not need to change. Hard-coded colours and radii in screens are bugs: replace them
with a token.
