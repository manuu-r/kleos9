# Frozen frontend test data only

`bambu-models.json` is retained at this path because the existing Flutter tests
read it directly. This directory contains no Python backend implementation,
import shim or runtime entry point. The replacement code is in `../src/kleos`.

The fixture does not qualify current printer dispatch or slicer installations.
