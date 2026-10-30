"""Pinned Anatomy guidance and cheap initial concepts shared by agent and UI.

The upstream skill is MIT licensed. It is packaged for reproducibility; models
receive a bounded adaptation, not a fresh copy of the entire kit on every turn.
"""
import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

SKILL_ROOT = Path(__file__).with_name("skills") / "anatomy"


@lru_cache(maxsize=1)
def _load_skill():
    manifest = json.loads((SKILL_ROOT / "UPSTREAM.json").read_text())
    overrides = manifest.get("localOverrides", {})
    for name, expected in manifest["files"].items():
        expected = overrides.get(name, {}).get("sha256", expected)
        if hashlib.sha256((SKILL_ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"The pinned Anatomy skill file changed: {name}")
    guidance = (SKILL_ROOT / "GUIDANCE.md").read_text()
    metadata = {"name": "anatomy", "commit": manifest["commit"], "license": "MIT",
                "source": manifest["skillUrl"],
                "skillSha256": overrides.get("SKILL.md", {}).get("sha256", manifest["files"]["SKILL.md"]),
                "upstreamSkillSha256": manifest["files"]["SKILL.md"],
                "adaptationSha256": hashlib.sha256(guidance.encode()).hexdigest(),
                "execution": "typed_scene_native_renderer"}
    return metadata, guidance


def skill_metadata():
    return deepcopy(_load_skill()[0])


def runtime_guidance():
    metadata, guidance = _load_skill()
    return f"Anatomy skill {metadata['commit']} / {metadata['skillSha256']}.\n{guidance}"


def _v(x=0, y=0, z=0):
    return {"x": float(x), "y": float(y), "z": float(z)}


def _part(ident, label, role, size, position=(0, 0, 0), explode=(0, 0, 0),
          shape="rounded_box", color="#65656f", rotation=(0, 0, 0), detail=""):
    return {"id": ident, "label": label, "role": role, "shape": shape,
            "size": _v(*size), "position": _v(*position), "rotation": _v(*rotation),
            "explode": _v(*explode), "color": color,
            "detail": detail or "Initial visual assumption; refined as you describe the device.",
            "sourceChoices": []}


def seed_concept(brief):
    """Instant, explicitly provisional arrangement before the first model turn.

    Relative coordinates describe appearance, not dimensions or fit. These
    assemblies never enter the artifact/evidence/release validation contracts.
    """
    text = brief.lower()
    title = brief.strip().splitlines()[0][:90] or "Your device concept"
    if any(word in text for word in ("watch", "wrist", "wearable")):
        kind = "wearable"
        parts = [
            _part("case", "Watch case", "shell", (3, 3.3, .8), color="#64646d"),
            _part("battery", "Power pack", "battery", (2.1, 2.3, .22), (0, 0, -.15), (0, 0, .7), color="#41414a"),
            _part("board", "Main board", "board", (2.5, 2.7, .12), (0, 0, .12), (0, 0, 1.4), color="#4e7065"),
            _part("face", "Screen", "display", (2.65, 2.85, .12), (0, 0, .45), (0, 0, 2.2), color="#bfc7cd"),
            _part("strap-front", "Upper strap", "strap", (1.8, 3, .22), (0, 3.15, -.2), (0, 1, 0), color="#34343c"),
            _part("strap-back", "Lower strap", "strap", (1.8, 3.8, .22), (0, -3.55, -.2), (0, -1, 0), color="#34343c"),
            _part("crown", "Side control", "control", (.45, .45, .35), (1.65, .5, 0), (.8, 0, 0), shape="cylinder", rotation=(0, 90, 0)),
        ]
    elif any(word in text for word in ("lamp", "light fixture", "desk light")):
        kind = "lamp"
        parts = [
            _part("base", "Weighted base", "shell", (3.4, 3.4, .4), shape="cylinder", color="#4a4a54"),
            _part("board", "Light controller", "board", (1.8, 1.3, .12), (0, 0, .1), (0, 0, .7), color="#4e7065"),
            _part("stem", "Lamp stem", "other", (.26, .26, 4), (0, 0, 2.1), (0, 0, 1.4), shape="cylinder"),
            _part("shade", "Light head", "cover", (2.9, 2.9, .55), (.7, 0, 4.3), (0, 0, 2.1), shape="cylinder", rotation=(0, 20, 0), color="#9b9ba5"),
            _part("light", "Light surface", "display", (2.5, 2.5, .08), (.7, 0, 4), (0, 0, 1.8), shape="cylinder", color="#f1e8c8"),
            _part("control", "Brightness control", "control", (.5, .5, .15), (1, -.7, .3), (.7, -.6, 0), shape="cylinder"),
        ]
    elif any(word in text for word in ("robot", "rover")):
        kind = "robot"
        parts = [
            _part("chassis", "Chassis", "shell", (3.5, 3.7, .7), color="#62626d"),
            _part("board", "Control board", "board", (2.4, 2.4, .15), (0, 0, .42), (0, 0, 1), color="#4e7065"),
            _part("battery", "Power pack", "battery", (2.4, 1.2, .5), (0, 1, .7), (0, .5, 1.6), color="#41414a"),
            _part("sensor", "Sensing module", "sensor", (1.5, .6, .6), (0, -1.4, .7), (0, -.4, 1.6), color="#a9afb5"),
            *[_part(f"wheel-{i}", "Drive wheel", "other", (1, 1, .4), (x, y, -.4), (0, y * .35, 0), shape="cylinder", rotation=(90, 0, 0), color="#303038")
              for i, (x, y) in enumerate([(-1.1, -2), (1.1, -2), (-1.1, 2), (1.1, 2)])],
        ]
    elif any(word in text for word in ("handheld", "hand held", "remote", "portable")):
        kind = "handheld"
        parts = [
            _part("body", "Handheld body", "shell", (2.4, 1.3, 4), color="#64646e"),
            _part("board", "Main board", "board", (1.9, .12, 3.2), (0, 0, 0), (0, -.7, 0), color="#4e7065"),
            _part("battery", "Power pack", "battery", (1.7, .4, 1.8), (0, .2, -.6), (0, 1, 0), color="#41414a"),
            _part("screen", "Readout", "display", (1.8, .12, 1.4), (0, .7, .7), (0, 1.4, 0), color="#bdc7cd"),
            _part("controls", "Controls", "control", (.9, .16, .5), (0, .75, -.6), (0, 1.4, 0)),
            _part("top", "Sensor opening", "sensor", (.6, .6, .3), (0, 0, 2.1), (0, 0, 1), shape="cylinder"),
        ]
    else:
        kind = "desktop" if any(word in text for word in ("desk", "monitor", "air quality", "air-quality")) else "device"
        parts = [
            _part("base", "Enclosure base", "shell", (4.8, 3.2, .45), (0, 0, -.55), color="#55555f"),
            _part("power", "Power module", "battery", (2.7, 1.4, .4), (0, .4, -.05), (0, .4, .8), color="#41414a", detail="Power arrangement is provisional until USB or battery operation is chosen."),
            _part("board", "Main board", "board", (3.9, 2.4, .14), (0, 0, .25), (0, 0, 1.5), color="#4e7065"),
            _part("sensor", "Sensing module", "sensor", (1.1, 1, .45), (1.15, .55, .55), (0, .2, 1.5), color="#a3aaa8"),
            _part("cover", "Front enclosure", "cover", (4.8, 3.2, 1.2), (0, 0, .25), (0, 0, 2.5), color="#74747e"),
            _part("display", "Readout", "display", (2.5, .12, .95), (0, 1.64, .35), (0, .6, 2.5), color="#c6ced3"),
            _part("port", "Power connection", "connector", (.7, .15, .25), (1.45, 1.67, -.25), (.2, .6, 0), color="#37373f"),
        ]
    return {"schemaVersion": "concept-scene-v1", "title": title,
            "summary": "Starting arrangement from your brief. Shape and parts will evolve with your choices and research.",
            "deviceKind": kind, "units": "relative", "parts": parts}
