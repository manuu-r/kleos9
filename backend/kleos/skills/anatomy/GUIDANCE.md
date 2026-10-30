# Kleos concept adaptation

Use Anatomy to describe a recognizable, purposeful device before engineering.
Return a compact typed conceptScene, never HTML, JavaScript, CAD or generated files.
The view and the agent share this same scene: stable part IDs, relative world
coordinates with z up, solid shaded parts, and a separated exploded pose. Keep
anatomical roles and proportions consistent across turns. Use enough detail to
recognize the device without fabricating a bill of materials or proof of fit.

The user's requested lightweight concept takes precedence over the upstream
40–80-part figure minimum. Describe 4–12 meaningful assemblies (up to 24 only
when necessary). The trusted renderer supplies bevels and small surface details.
Treat sizes as visual proportions, not verified millimetres. Distinguish user
choices from assumptions. Never present concept geometry as validated evidence.

Composition: the renderer draws exactly what you place, so place it like a real assembly.
- Build a stack along the assembly axis (z for wearables and flat devices, y for
  upright ones): shell or base, power, board, sensors, display, cover.
- Boards, batteries and modules sit inside the shell/cover with a small gap. Only
  displays, controls, connectors, sensing openings and straps may reach the surface.
- Every part touches or nearly touches a neighbour in the assembled pose; nothing floats.
- A display or control sits flush on the face the user sees: +Z for flat devices,
  +Y for upright ones.
- Exploded pose: move each part along the assembly axis in stack order, outermost
  farthest, with distinct offsets. Never leave every offset at zero.

Decisions become geometry:
- Electronics choices are parts: battery or power input, charge port, main board, each
  sensor, radio with its antenna near an outer edge, haptic motor or buzzer, buttons.
  Add a part the user asks for, remove one they reject, resize a battery or screen
  when capacity or size changes.
- A display part's detail says what it shows. Firmware has no geometry: record its
  behaviour in requirements.firmware and link it to the part it drives via sourceChoices.
- Start each part detail with "You chose" or "Assumed" so the user sees which is which.

Update the existing scene after user choices and research; preserve IDs for
unchanged components, remove rejected components, and name changed parts in
plain language. Keep most of the scene unchanged when a small preference changes.
Pin the Anatomy commit/hash, scene version and planning input version in context.
The scene is a reference for future engineering, not permission to begin it.
