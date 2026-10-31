# Kleos9 theme ("Signal")

The whole look of the app lives in this folder. Nothing outside it picks colours,
radii, type sizes or motion; screens use the tokens and the `K*` components.

| File | What it decides |
|---|---|
| `tokens.dart` | Colour, spacing, type scale, motion. **Edit this to change the look.** |
| `shape.dart` | The corner language: chamfered panels, sharp 3px controls. |
| `ornaments.dart` | HUD detail: mono readout label (`KLabel`), corner brackets, faint grid. |
| `theme_data.dart` | Flutter's `ThemeData` (fields, dialogs, menus, switches, sliders) built from the tokens. |
| `components.dart` | The widgets: `KCard`, `KPanel`, `KButton`, `KBadge`, `KTabStrip`/`KSegmented`, loaders, notices, the stage. |
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
- **Type.** Geist for text, Geist Mono for ids, figures and readouts. Warm off-white text.

## Rules that are tested

`test/design_system_test.dart` enforces: every text colour clears 4.5:1 on the surfaces it is used
on, cards are chamfered outlined panels, controls are sharp, tabs share one gliding indicator and
are 44px tall, loaders stand still under reduced motion, progress fills actually paint.

## Changing direction later

To try a different direction, change `tokens.dart` (palette, radii, type), `shape.dart` (corners)
and the few component visuals in `components.dart`; check the gallery; run `flutter test`. The
screens do not need to change. Hard-coded colours and radii in screens are bugs: replace them
with a token.
