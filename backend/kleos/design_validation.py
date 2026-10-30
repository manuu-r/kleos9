"""Independent enclosure-form and PCB-layout checks against frozen design intent.

Only authoritative requirements and server-bound engineering interfaces supply
expectations. Candidate file names, previews, and generated reports are not truth.
"""
import hashlib
import itertools
import math
import re

from .contracts import CheckResult

AXES = "xyz"
PROFILE_TOLERANCE_MM = .1
PORT_GUARD_MIN_COVERAGE = .85
SIDE_TARGET_THRESHOLD = .15
SIDE_MINIMUM_POSITION = .025


class DesignUnavailable(ValueError):
    pass


def vector(value, positive=False):
    if not isinstance(value, dict) or set(value) != set(AXES) or any(
            isinstance(number, bool) or not isinstance(number, (int, float)) or abs(number) > 1e6
            or not math.isfinite(number) or positive and number <= 0 for number in value.values()):
        raise ValueError("Approved layout requires finite bounded x/y/z dimensions and positions")
    return value


def _angle_delta(first, second=0):
    return abs((first - second + 180) % 360 - 180)


def _axis_z(rotation):
    return all(abs(rotation[axis] / 180 - round(rotation[axis] / 180)) <= 1e-6 for axis in "xy")


def _aspect(intent, measured):
    source = intent["enclosure"]
    relative = vector(source.get("relativeSize", source.get("size")), True)
    tolerance = intent.get("aspectToleranceFraction", .25)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= .25:
        raise ValueError("Approved enclosure aspect tolerance is invalid")
    expected = {"xy": relative["x"] / relative["y"], "depth": relative["z"] / max(relative["x"], relative["y"])}
    actual = {"xy": measured["x"] / measured["y"], "depth": measured["z"] / max(measured["x"], measured["y"])}
    return expected, actual, all(abs(actual[key] / expected[key] - 1) <= tolerance + 1e-9 for key in expected)


def _preserves_side(target, actual):
    """Keep distinctly left/right/front/back references away from the centre."""
    return abs(target) < SIDE_TARGET_THRESHOLD or target * actual > 0 and abs(actual) >= SIDE_MINIMUM_POSITION


def _binding(requirements, interfaces):
    intent = requirements.get("designIntent") if isinstance(requirements, dict) else None
    mechanical = interfaces.get("mechanical") if isinstance(interfaces, dict) else None
    layout = mechanical.get("designIntent") if isinstance(mechanical, dict) else None
    if (not isinstance(intent, dict) or intent.get("schemaVersion") != "approved-design-intent-v1"
            or not isinstance(layout, dict) or layout.get("schemaVersion") != "engineering-design-layout-v1"):
        raise DesignUnavailable("Approve the structured device reference and map it to engineering interfaces before validation")
    if (not re.fullmatch(r"[a-f0-9]{64}", str(intent.get("sceneHash", "")))
            or layout.get("sceneHash") != intent["sceneHash"]
            or layout.get("approvalVersion") != intent.get("approvalVersion")):
        raise ValueError("Engineering layout is not bound to the frozen approved design")
    source, enclosure = intent.get("enclosure"), layout.get("enclosure")
    if not isinstance(source, dict) or not isinstance(enclosure, dict):
        raise ValueError("Approved enclosure shape is unavailable")
    if source.get("axis") != "z" or source.get("profile") not in {"circle", "rectangle", "rounded_rectangle"}:
        raise DesignUnavailable("The approved enclosure needs a qualified Z-axis circle/box/rounded-box profile")
    if source.get("unsupportedReasons") or enclosure.get("unsupportedReasons"):
        raise DesignUnavailable("Approved enclosure contains unsupported rotations or multiple bodies")
    for key in ("profile", "axis", "conceptPartId"):
        if enclosure.get(key) != source.get(key):
            raise ValueError("Engineering enclosure profile changed from the approved device")
    if enclosure.get("centerPolicy") != "enclosure_bbox_center":
        raise DesignUnavailable("Unsupported engineering enclosure coordinate frame")
    outer, inner = vector(mechanical.get("externalMm"), True), vector(mechanical.get("internalMm"), True)
    if enclosure.get("externalMm") != outer or enclosure.get("internalMm") != inner:
        raise ValueError("Engineering envelope dimensions differ from the frozen layout mapping")
    if not _aspect(intent, outer)[2]:
        raise ValueError("Engineering enclosure proportions differ from the approved device")
    if any(inner[axis] >= outer[axis] for axis in AXES):
        raise ValueError("Approved interior must fit inside the exterior")
    if source["profile"] == "circle" and any(abs(size["x"] - size["y"]) > 1e-6 for size in (outer, inner)):
        raise ValueError("A circular approved enclosure cannot become an ellipse or square")
    radius, inner_radius = 0.0, 0.0
    if source["profile"] == "rounded_rectangle":
        ratio = source.get("cornerRadiusRatio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 < ratio < .5:
            raise DesignUnavailable("Rounded enclosure lacks its approved corner-radius mapping")
        radius = ratio * min(outer["x"], outer["y"])
        wall = mechanical.get("minimumWallMm")
        if not isinstance(wall, (int, float)) or isinstance(wall, bool) or not math.isfinite(wall) or wall <= 0:
            raise ValueError("Rounded enclosure needs a finite accepted wall thickness")
        inner_radius = max(0, radius - wall)
        if (enclosure.get("cornerRadiusMm") != radius or enclosure.get("internalCornerRadiusMm") != inner_radius
                or inner_radius * 2 > min(inner["x"], inner["y"])):
            raise ValueError("Rounded engineering radius differs from the approved profile")
    frozen, mapped, components = intent.get("components"), layout.get("components"), mechanical.get("components")
    if not all(isinstance(items, list) and len(items) <= 24 for items in (frozen, mapped, components)):
        raise ValueError("Approved internal component mappings are missing or unbounded")
    if any(not isinstance(item, dict) for item in [*frozen, *mapped, *components]):
        raise ValueError("Approved component mappings must be objects")
    frozen_by_id = {item.get("conceptPartId"): item for item in frozen}
    if len(frozen_by_id) != len(frozen):
        raise ValueError("Frozen design component IDs are ambiguous")
    required = {ident: item for ident, item in frozen_by_id.items() if item.get("requiredMapping") is True}
    actual = {item.get("conceptPartId"): item for item in mapped}
    raw = {item.get("id"): item for item in components}
    if (len(actual) != len(mapped) or not set(required).issubset(actual) or not set(actual).issubset(frozen_by_id)
            or len(raw) != len(components) or len(mapped) != len(components)):
        raise ValueError("Approved internal components are missing or mapped more than once")
    tolerance = intent.get("positionToleranceFraction", .22)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= .25:
        raise ValueError("Approved placement tolerance is invalid")
    centers = {}
    for ident, item in actual.items():
        original = frozen_by_id[ident]
        if not original.get("requiredMapping") and original.get("role") not in {"display", "control", "connector"}:
            raise ValueError("Unsupported concept feature was assigned an internal clearance envelope")
        if any(item.get(key) != original.get(key) for key in ("shape", "role", "rotation", "region", "requiredMapping")):
            raise ValueError("Engineering component identity/layout differs from its frozen reference")
        component = raw.get(item.get("interfaceComponentId"))
        if component is None or item.get("sizeMm") != component.get("sizeMm") or item.get("positionMm") != component.get("positionMm"):
            raise ValueError("Approved internal mapping does not match the accepted engineering envelope")
        size, position, rotation = vector(item.get("sizeMm"), True), vector(item.get("positionMm")), vector(item.get("rotation"))
        if not _axis_z(rotation):
            raise DesignUnavailable("Tilted internal component planes need additional qualified layout checks")
        region = original.get("region")
        target = vector(region.get("center") if isinstance(region, dict) else None)
        center = {axis: position[axis] + size[axis] / 2 - inner[axis] / 2 for axis in AXES}
        if any(abs(center[axis] / outer[axis] - target[axis]) > tolerance
               or not _preserves_side(target[axis], center[axis] / outer[axis]) for axis in AXES):
            raise ValueError(f"Approved internal component {ident} moved outside its accepted region")
        if any(not inside_profile(x, y, inner, source["profile"], inner_radius) or abs(z) > inner["z"] / 2 + 1e-6
               for x, y, z in component_corners(item, center)):
            raise ValueError(f"Approved internal component {ident} does not fit the actual interior profile")
        centers[ident] = center
    order = intent.get("stackingOrder", [])
    if not isinstance(order, list) or set(order) != set(required):
        raise ValueError("Approved component stacking order is incomplete")
    for first, second in zip(order, order[1:]):
        first_z, second_z = required[first]["region"]["center"]["z"], required[second]["region"]["center"]["z"]
        if second_z - first_z > .02 and centers[first]["z"] > centers[second]["z"] + PROFILE_TOLERANCE_MM:
            raise ValueError("Engineering component stacking reversed the approved device layout")
    return {"intent": intent, "layout": layout, "mechanical": mechanical, "enclosure": enclosure,
            "outer": outer, "inner": inner, "profile": source["profile"], "radius": radius,
            "innerRadius": inner_radius, "components": actual, "centers": centers}


def validate_design_binding(requirements, interfaces):
    try:
        data = _binding(requirements, interfaces)
        return CheckResult(name="approved_design_contract", status="PASS", details={
            "required": True, "scopeExcluded": False,
            "sceneHash": data["intent"]["sceneHash"], "approvalVersion": data["intent"]["approvalVersion"],
            "profile": data["profile"], "coverage": "Frozen approved enclosure form and critical internal region/stack mapping",
            "sidePreservation": {"targetThreshold": SIDE_TARGET_THRESHOLD, "minimumActualPosition": SIDE_MINIMUM_POSITION},
            "limitations": ["Surface and external features are references; this is not manufacturing approval"]})
    except DesignUnavailable as exc:
        legacy = not isinstance(requirements, dict) or not requirements.get("designIntent")
        return CheckResult(name="approved_design_contract", status="UNKNOWN", details={
            "required": not legacy, "scopeExcluded": legacy, "reason": str(exc)})
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return CheckResult(name="approved_design_contract", status="FAIL", details={"required": True, "reason": str(exc)[:2000]})


def inside_profile(x, y, size, profile, radius=0, margin=0):
    """Exact convex XY containment with positive inward clearance margin."""
    half_x, half_y = size["x"] / 2 - margin, size["y"] / 2 - margin
    if min(half_x, half_y) < 0:
        return False
    if profile == "circle":
        return x * x + y * y <= min(half_x, half_y) ** 2 + 1e-8
    if profile == "rectangle" or radius <= margin:
        return abs(x) <= half_x + 1e-8 and abs(y) <= half_y + 1e-8
    effective = radius - margin
    dx, dy = max(abs(x) - (half_x - effective), 0), max(abs(y) - (half_y - effective), 0)
    return dx * dx + dy * dy <= effective * effective + 1e-8


def component_corners(item, center):
    angle = math.radians(item.get("rotation", {}).get("z", 0))
    size = item["sizeMm"]
    if item.get("shape") == "cylinder":
        ring = [(math.cos(index * math.tau / 64) * size["x"] / 2,
                 math.sin(index * math.tau / 64) * size["y"] / 2) for index in range(64)]
    elif item.get("shape") == "rounded_box":
        radius = item.get("cornerRadiusRatio", .13) * min(size["x"], size["y"])
        ring = []
        for sx, sy, start in ((1, 1, 0), (-1, 1, 90), (-1, -1, 180), (1, -1, 270)):
            for index in range(33):
                phase = math.radians(start + index * 90 / 32)
                ring.append((sx * (size["x"] / 2 - radius) + math.cos(phase) * radius,
                             sy * (size["y"] / 2 - radius) + math.sin(phase) * radius))
    else:
        ring = [(sx * size["x"] / 2, sy * size["y"] / 2) for sx, sy in itertools.product((-1, 1), repeat=2)]
    for x, y in ring:
        for sz in (-1, 1):
            yield (center["x"] + x * math.cos(angle) - y * math.sin(angle),
                   center["y"] + x * math.sin(angle) + y * math.cos(angle), center["z"] + sz * size["z"] / 2)


def _profile_solid(profile, size, radius=0):
    from build123d import Box, Cylinder

    from .cad_parts import _as_shape
    if profile == "circle":
        return Cylinder(size["x"] / 2, size["z"])
    if profile == "rectangle" or radius <= 0:
        return Box(size["x"], size["y"], size["z"])
    first = Box(size["x"] - 2 * radius, size["y"], size["z"])
    others = [Box(size["x"], size["y"] - 2 * radius, size["z"])]
    for x, y in itertools.product((-1, 1), repeat=2):
        others.append(Cylinder(radius, size["z"]).translate((x * (size["x"] / 2 - radius), y * (size["y"] / 2 - radius), 0)))
    return _as_shape(first.fuse(*others))


def inspect_mechanical_design(path, mechanical, requirements):
    """Executed in trusted isolated CAD inspector, never in the API process."""
    from build123d import Axis, import_step

    from .cad_parts import _volume
    shape = import_step(path)
    bounds = shape.bounding_box()
    size = {axis: float(getattr(bounds.size, axis.upper())) for axis in AXES}
    center = tuple((float(getattr(bounds.min, axis.upper())) + float(getattr(bounds.max, axis.upper()))) / 2 for axis in AXES)
    inner = vector(mechanical["internalMm"], True)
    binding = validate_design_binding(requirements, {"mechanical": mechanical})
    data = _binding(requirements, {"mechanical": mechanical}) if binding.status == "PASS" else None
    profile, radius = (data["profile"], data["innerRadius"]) if data else ("rectangle", 0)
    clearance = _profile_solid(profile, inner, radius).translate(center)
    collisions, component_failures = [], []
    for component in mechanical["components"]:
        dimensions = vector(component["sizeMm"], True)
        position = vector(component["positionMm"])
        local = {axis: position[axis] + dimensions[axis] / 2 - inner[axis] / 2 for axis in AXES}
        mapped = next((item for item in data["components"].values() if item["interfaceComponentId"] == component["id"]), None) if data else None
        rotation = mapped["rotation"]["z"] if mapped else 0
        component_profile = ("circle" if mapped and mapped["shape"] == "cylinder" else
                             "rounded_rectangle" if mapped and mapped["shape"] == "rounded_box" else "rectangle")
        if component_profile == "circle" and abs(dimensions["x"] - dimensions["y"]) > 1e-6:
            raise DesignUnavailable("Elliptical component clearance needs a qualified geometric profile")
        component_radius = mapped.get("cornerRadiusRatio", .13) * min(dimensions["x"], dimensions["y"]) if mapped else 0
        proxy = _profile_solid(component_profile, dimensions, component_radius).rotate(Axis.Z, rotation).translate(
            tuple(center[index] + local[axis] for index, axis in enumerate(AXES)))
        collisions.append({"id": component["id"], "intersectionMm3": _volume(shape & proxy)})
        if mapped and any(not inside_profile(x, y, inner, profile, radius) or abs(z) > inner["z"] / 2 + 1e-6
                          for x, y, z in component_corners(mapped, local)):
            component_failures.append(component["id"])
    valid = shape.is_valid
    measured = {"sizeMm": size, "volumeMm3": float(shape.volume), "solidCount": len(shape.solids()),
        "valid": bool(valid() if callable(valid) else valid), "internalObstructionMm3": _volume(shape & clearance),
        "componentIntersections": collisions, "componentProfileFailures": component_failures,
        "approvedDesign": binding.model_dump(mode="json", by_alias=True)}
    if data:
        outer = data["outer"]
        expanded = {axis: outer[axis] + 2 * PROFILE_TOLERANCE_MM for axis in AXES}
        allowed = _profile_solid(data["profile"], expanded, data["radius"] + PROFILE_TOLERANCE_MM).translate(center)
        outside = _volume(shape - allowed)
        wall = max(.1, min(float(mechanical.get("minimumWallMm", 1)) * .5, 1))
        guard_size = {"x": outer["x"] - .2, "y": outer["y"] - .2, "z": min(inner["z"] * .3, outer["z"] * .2)}
        inset = {"x": guard_size["x"] - 2 * wall, "y": guard_size["y"] - 2 * wall, "z": guard_size["z"]}
        guard = (_profile_solid(data["profile"], guard_size, max(0, data["radius"] - .1))
                 - _profile_solid(data["profile"], inset, max(0, data["radius"] - .1 - wall)))
        guard_volume = _volume(guard)
        if guard_volume <= 0:
            raise ValueError("Approved enclosure profile guard cannot be measured")
        from .cad_parts import _as_shape
        guard = _as_shape(guard)
        coverages = [_volume(shape & guard.translate((center[0], center[1], center[2] + offset * outer["z"]))) / guard_volume
                     for offset in (-.25, 0, .25)]
        coverage = min(coverages)
        tolerance = max(1e-4, float(shape.volume) * 1e-7)
        measured["approvedDesign"].update(status="FAIL" if outside > tolerance or coverage < PORT_GUARD_MIN_COVERAGE
                                          or component_failures or not _aspect(data["intent"], size)[2] else "PASS")
        measured["approvedDesign"]["details"].update({"outsideApprovedProfileMm3": outside,
            "boundaryGuardCoverage": coverage, "minimumBoundaryGuardCoverage": PORT_GUARD_MIN_COVERAGE,
            "boundaryGuardCoverages": coverages, "boundaryGuardHeightOffsets": [-.25, 0, .25],
            "profileToleranceMm": PROFILE_TOLERANCE_MM, "portCutoutScope": "At most 15% missing central perimeter guard",
            "stepSha256": hashlib.sha256(open(path, "rb").read()).hexdigest()})
        expected_aspect, actual_aspect, _ = _aspect(data["intent"], size)
        measured["approvedDesign"]["details"].update(expectedAspect=expected_aspect, actualAspect=actual_aspect)
    return measured


PCB_LAYOUT_SCRIPT = r'''
import hashlib,json,sys
import pcbnew
b=pcbnew.LoadBoard(sys.argv[1]); polys=pcbnew.SHAPE_POLY_SET()
valid=b.GetBoardPolygonOutlines(polys,None,False,False)
outlines=[]
if valid:
 for i in range(polys.OutlineCount()):
  chain=polys.COutline(i)
  if chain.PointCount()>4096: raise ValueError('PCB outline too complex for qualified layout inspection')
  outlines.append([[pcbnew.ToMM(chain.CPoint(j).x),pcbnew.ToMM(chain.CPoint(j).y)] for j in range(chain.PointCount())])
  if len(outlines)>8: raise ValueError('Too many separate PCB outlines')
def rect(box):
 return [[pcbnew.ToMM(x),pcbnew.ToMM(y)] for x,y in ((box.GetX(),box.GetY()),(box.GetRight(),box.GetY()),
                                                  (box.GetRight(),box.GetBottom()),(box.GetX(),box.GetBottom()))]
components=[]
for fp in b.GetFootprints():
 body=[item.GetBoundingBox() for item in fp.GraphicalItems() if item.GetClass()=='PCB_SHAPE'
       and item.GetLayer() in (pcbnew.F_Fab,pcbnew.B_Fab)]
 kind='fabrication_body_and_pads'
 if not body:
  body=[item.GetBoundingBox() for item in fp.GraphicalItems() if item.GetClass()=='PCB_SHAPE'
        and item.GetLayer() in (pcbnew.F_CrtYd,pcbnew.B_CrtYd)]
  kind='conservative_courtyard_and_pads' if body else 'unavailable'
 body += [pad.GetBoundingBox() for pad in fp.Pads()]
 points=[point for box in body for point in rect(box)]
 center=[(min(p[i] for p in points)+max(p[i] for p in points))/2 for i in (0,1)] if points else []
 components.append({'reference':fp.GetReference(),'points':points,'center':center,'extentSource':kind})
tracks=[]; unsupported=[]
for item in b.GetTracks():
 if item.GetClass() not in ('PCB_TRACK','PCB_VIA'):
  unsupported.append(item.GetClass()); continue
 tracks.append({'points':[[pcbnew.ToMM(v.x),pcbnew.ToMM(v.y)] for v in (item.GetStart(),item.GetEnd())],
                'radius':pcbnew.ToMM(item.GetWidth())/2})
 if len(tracks)>20000: raise ValueError('PCB track inspection exceeds bound')
print(json.dumps({'schemaVersion':'pcb-layout-v1','kicadVersion':pcbnew.GetBuildVersion(),'outlines':outlines,
                  'components':components,'tracks':tracks,'unsupported':unsupported,
                  'pcbSha256':hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()},separators=(',',':')))
'''


def validate_pcb_layout(requirements, interfaces, snapshot, pcb_sha256):
    binding = validate_design_binding(requirements, interfaces)
    details = {**binding.details, "coverage": "Actual Edge.Cuts, track capsules and footprint bounds inside the approved enclosure"}
    if binding.status != "PASS":
        return CheckResult(name="approved_pcb_layout", status=binding.status, details=details)
    try:
        data = _binding(requirements, interfaces)
        if (not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != "pcb-layout-v1"
                or not str(snapshot.get("kicadVersion", "")).startswith("9.0.")):
            raise DesignUnavailable("Independent KiCad 9 PCB outline/layout inspection is unavailable")
        if snapshot.get("pcbSha256") != pcb_sha256:
            raise ValueError("PCB layout evidence does not match its candidate hash")
        if snapshot.get("unsupported"):
            raise DesignUnavailable("Curved copper tracks require an additional qualified extent inspector")
        boards = [part for part in data["components"].values() if part.get("role") == "board"]
        if len(boards) != 1:
            raise DesignUnavailable("Map exactly one approved internal PCB for this layout profile")
        board = boards[0]
        outlines = snapshot.get("outlines")
        if not isinstance(outlines, list) or len(outlines) != 1 or not isinstance(outlines[0], list) or len(outlines[0]) < 3:
            raise DesignUnavailable("One actual closed Edge.Cuts board outline is required")
        points = outlines[0]
        if len(points) > 4096 or any(not isinstance(point, list) or len(point) != 2 or any(
                isinstance(number, bool) or not isinstance(number, (int, float)) or abs(number) > 1e6
                or not math.isfinite(number) for number in point) for point in points):
            raise ValueError("PCB outline contains invalid coordinates")
        low = [min(point[i] for point in points) for i in (0, 1)]
        high = [max(point[i] for point in points) for i in (0, 1)]
        measured_size = [high[i] - low[i] for i in (0, 1)]
        if any(abs(measured_size[i] - board["sizeMm"][axis]) > .1 for i, axis in enumerate("xy")):
            raise ValueError("Actual PCB outline dimensions differ from its accepted engineering component")
        origin = [(low[i] + high[i]) / 2 for i in (0, 1)]
        placement = data["centers"][board["conceptPartId"]]
        angle = math.radians(board["rotation"]["z"])
        flip_x = -1 if abs(board["rotation"]["y"] / 180) % 2 > .5 else 1
        flip_y = -1 if abs(board["rotation"]["x"] / 180) % 2 > .5 else 1
        def transform(point):
            # KiCad board coordinates are Y-down; the approved concept is Z-up,
            # X-right/Y-up. Apply accepted board placement only after this flip.
            x, y = (point[0] - origin[0]) * flip_x, -(point[1] - origin[1]) * flip_y
            return (placement["x"] + x * math.cos(angle) - y * math.sin(angle),
                    placement["y"] + x * math.sin(angle) + y * math.cos(angle))
        groups = [("outline", points, 0)]
        components, tracks = snapshot.get("components", []), snapshot.get("tracks", [])
        if not isinstance(components, list) or len(components) > 128 or not isinstance(tracks, list) or len(tracks) > 20000:
            raise ValueError("PCB component/track inspection exceeds its qualified bounds")
        for component in components:
            if not isinstance(component, dict):
                raise ValueError("PCB component measurements must be objects")
            if component.get("extentSource") == "unavailable":
                raise DesignUnavailable("Footprint physical body/courtyard extents are unavailable")
            groups.append((str(component.get("reference", "footprint")), component["points"], 0))
        for track in tracks:
            if not isinstance(track, dict):
                raise ValueError("PCB copper measurements must be objects")
            groups.append(("copper", track["points"], track["radius"]))
        failures = []
        for label, group, margin in groups:
            if not isinstance(group, list) or len(group) > 4096 or not isinstance(margin, (float, int)) or not 0 <= margin <= 100:
                raise ValueError("PCB physical extent data is malformed")
            for point in group:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError("PCB physical extent coordinate is malformed")
                if any(isinstance(number, bool) or not isinstance(number, (float, int)) or abs(number) > 1e6
                       or not math.isfinite(number) for number in point):
                    raise ValueError("PCB physical extents contain invalid coordinates")
                x, y = transform(point)
                if not inside_profile(x, y, data["inner"], data["profile"], data["innerRadius"], margin):
                    failures.append(label)
                    break
        relevant = {part["conceptPartId"]: part for part in data["intent"]["components"]
                    if part.get("role") in {"sensor", "control", "connector"}}
        electronics = interfaces.get("electronics", {})
        circuit_layout = electronics.get("designIntent") if isinstance(electronics, dict) else None
        if relevant:
            if (not isinstance(circuit_layout, dict) or circuit_layout.get("schemaVersion") != "engineering-pcb-layout-v1"
                    or circuit_layout.get("sceneHash") != data["intent"]["sceneHash"]
                    or circuit_layout.get("approvalVersion") != data["intent"]["approvalVersion"]
                    or circuit_layout.get("boardConceptPartId") != board["conceptPartId"]):
                raise DesignUnavailable("Map approved sensor/control/connector identities to actual PCB references")
            refs = circuit_layout.get("componentReferences")
            if not isinstance(refs, list) or len(refs) > 24 or any(not isinstance(ref, dict) for ref in refs):
                raise ValueError("PCB component reference bindings are malformed")
            links = {ref.get("conceptPartId"): ref for ref in refs}
            if len(links) != len(refs):
                raise ValueError("Approved PCB component identity was mapped more than once")
            if set(links) != set(relevant) or circuit_layout.get("unmappedConceptPartIds"):
                raise DesignUnavailable("Approved PCB sensor/control/connector placement mappings are incomplete")
            actual_parts = {part.get("reference"): part for part in components}
            if len(actual_parts) != len(components):
                raise ValueError("Actual PCB footprint references are duplicated")
            references = set()
            for ident, ref in links.items():
                source = relevant[ident]
                if any(ref.get(key) != source.get(key) for key in ("role", "region", "shape", "rotation", "placementClass")):
                    raise ValueError("PCB expected placement differs from its frozen approved component")
                reference = ref.get("reference")
                if not isinstance(reference, str) or reference in references:
                    raise ValueError("PCB footprint identity mapping is ambiguous")
                references.add(reference)
                measured = actual_parts.get(reference)
                if measured is None:
                    failures.append(f"missing approved component {ident}/{reference}")
                    continue
                if not isinstance(measured.get("center"), list) or len(measured["center"]) != 2:
                    raise ValueError("Actual PCB footprint center is unavailable")
                x, y = transform(measured["center"])
                target = vector(source["region"]["center"])
                tolerance = data["intent"].get("positionToleranceFraction", .22)
                if any(abs(actual - target[axis]) > tolerance or not _preserves_side(target[axis], actual)
                       for axis, actual in (("x", x / data["outer"]["x"]), ("y", y / data["outer"]["y"]))):
                    failures.append(f"approved component {ident}/{reference} moved outside its frozen XY region")
        if board["shape"] == "cylinder":
            if abs(board["sizeMm"]["x"] - board["sizeMm"]["y"]) > 1e-6:
                raise DesignUnavailable("An elliptical approved PCB needs a qualified outline checker")
            radius = board["sizeMm"]["x"] / 2
            if any(math.hypot(point[0] - origin[0], point[1] - origin[1]) > radius + .1 for point in points):
                failures.append("approved circular PCB became a noncircular outline")
            area = abs(sum(points[i][0] * points[(i + 1) % len(points)][1]
                           - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))) / 2
            if not .98 <= area / (math.pi * radius * radius) <= 1.02:
                failures.append("approved circular PCB outline coverage is incomplete")
        details.update(pcbSha256=pcb_sha256, actualBoardSizeMm=dict(zip("xy", measured_size)),
                       acceptedPlacementMm=placement, failures=sorted(set(failures)),
                       validatedComponentReferences=len(relevant),
                       coordinateConvention="KiCad Y-down flipped to approved Y-up, then accepted board rotation/placement",
                       limitations=["Filled-zone containment uses the independent all-severity DRC; footprint height, display modules, straps and physical operation are not established"])
        return CheckResult(name="approved_pcb_layout", status="FAIL" if failures else "PASS", details=details)
    except DesignUnavailable as exc:
        return CheckResult(name="approved_pcb_layout", status="UNKNOWN", details={**details, "reason": str(exc)})
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return CheckResult(name="approved_pcb_layout", status="FAIL", details={**details, "reason": str(exc)[:2000]})
