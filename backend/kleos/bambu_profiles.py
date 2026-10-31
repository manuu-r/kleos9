"""Compile an operator-selected preset bundle. JSON only; never invokes a slicer."""

import argparse
import json
from pathlib import Path

from .bambu_contracts import STUDIO_VERSION, BambuProfile
from .bambu_slicing import load_profile
from .storage import digest


def resolve_preset(root, filename):
    paths = list(root.rglob("*.json"))
    if len(paths) > 10000:
        raise ValueError("Preset tree exceeds its bound")
    lookup = {}
    for path in paths:
        lookup.setdefault(path.stem, []).append(path)
    reads = 0

    def read(name, seen):
        nonlocal reads
        reads += 1
        if reads > 128 or name in seen or len(seen) >= 16 or len(lookup.get(name, [])) != 1:
            raise ValueError("Preset inheritance is cyclic, ambiguous or missing")
        path = lookup[name][0]
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 2_000_000:
            raise ValueError("Unsafe preset file")
        config = json.loads(path.read_text())
        if not isinstance(config, dict):
            raise ValueError("Preset must be an object")
        parent = config.pop("inherits", "")
        if not isinstance(parent, str):
            raise ValueError("Only single-parent preset inheritance is supported")
        includes = config.pop("include", [])
        if isinstance(includes, str):
            includes = [includes]
        if not isinstance(includes, list) or len(includes) > 32 or any(not isinstance(n, str) for n in includes):
            raise ValueError("Invalid preset template references")
        chain = [*seen, name]
        merged = read(parent, chain) if parent else {}
        # Vendor order: parent, referenced JSON templates, then the preset's own fields.
        for template in includes:
            merged.update(read(template, chain))
        return {**merged, **config}
    return read(filename.removesuffix(".json"), [])


def build_bundle(root, machine, process, filament, destination, image):
    configs = {"machine.json": resolve_preset(root, machine), "process.json": resolve_preset(root, process),
               "filament.json": resolve_preset(root, filament)}
    from .bambu_contracts import model_catalog
    model = next((m for m in model_catalog()["models"] if m["name"] == configs["machine.json"].get("printer_model")), None)
    if model is None:
        raise ValueError("Preset model is not in the pinned Bambu catalog")
    nozzle = [float(v) for v in configs["machine.json"]["nozzle_diameter"]]
    material = configs["filament.json"]["filament_type"]
    if not nozzle or len(set(nozzle)) != 1 or not isinstance(material, list) or len(material) != 1:
        raise ValueError("Select one nozzle size and one material type")
    area = [list(map(float, point.split("x"))) for point in configs["machine.json"]["printable_area"]]
    dimensions = [max(p[a] for p in area) - min(p[a] for p in area) for a in (0, 1)] + [float(configs["machine.json"]["printable_height"])]
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, config in configs.items():
        (destination / name).write_text(json.dumps(config, sort_keys=True, allow_nan=False))
    manifest = {"studioVersion": STUDIO_VERSION, "printerModelId": model["id"], "nozzleMm": nozzle[0],
                "material": material[0], "buildVolumeMm": dimensions,
                "files": {name: digest((destination / name).read_bytes()) for name in configs}}
    raw = json.dumps(manifest, sort_keys=True, allow_nan=False).encode()
    (destination / "manifest.json").write_bytes(raw)
    checksum = digest(raw)
    load_profile(destination, checksum)
    return BambuProfile(printerModelId=model["id"], nozzleMm=nozzle[0], material=material[0],
                        bundleSha256=checksum, image=image, qualified=False)


def main():
    parser = argparse.ArgumentParser(description="Resolve a pinned Bambu preset bundle for operator qualification")
    parser.add_argument("--preset-root", type=Path, required=True)
    for name in ("machine", "process", "filament", "image"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = build_bundle(args.preset_root, args.machine, args.process, args.filament, args.output, args.image)
    print(profile.model_dump_json(by_alias=True, indent=2))


if __name__ == "__main__":
    main()
