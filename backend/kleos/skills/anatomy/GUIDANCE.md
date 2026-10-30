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

Update the existing scene after user choices and research; preserve IDs for
unchanged components, remove rejected components, and name changed parts in
plain language. Keep most of the scene unchanged when a small preference changes.
Pin the Anatomy commit/hash, scene version and planning input version in context.
The scene is a reference for future engineering, not permission to begin it.
