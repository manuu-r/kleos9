"""Data-only concept-scene-v2 matching the existing Flutter renderer bounds."""
import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from kleos.contracts.models import WireModel

Coordinate = Annotated[float, Field(ge=-.5, le=.5, allow_inf_nan=False)]
Point2 = tuple[Coordinate, Coordinate]
Point3 = tuple[Coordinate, Coordinate, Coordinate]


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(p, a, b):
    return abs(cross(a, b, p)) < 1e-9 and all(min(a[i], b[i]) - 1e-9 <= p[i] <= max(a[i], b[i]) + 1e-9 for i in range(2))


def intersects(a, b, c, d):
    return ((cross(a, b, c) * cross(a, b, d) < -1e-9 and cross(c, d, a) * cross(c, d, b) < -1e-9)
            or on_segment(c, a, b) or on_segment(d, a, b) or on_segment(a, c, d) or on_segment(b, c, d))


def area(ring):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1], strict=True)) / 2


def ring_valid(ring):
    if abs(area(ring)) < 1e-9:
        raise ValueError('Profile must have area')
    for i, a in enumerate(ring):
        b = ring[(i + 1) % len(ring)]
        if sum((a[k] - b[k]) ** 2 for k in range(2)) < 1e-18:
            raise ValueError('Profile repeats an edge')
        for j in range(i + 1, len(ring)):
            if j == i + 1 or (i == 0 and j == len(ring) - 1):
                continue
            if intersects(a, b, ring[j], ring[(j + 1) % len(ring)]):
                raise ValueError('Profile intersects itself')


def inside(point, ring):
    result = False
    for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
        if on_segment(point, a, b):
            return False
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < (b[0]-a[0]) * (point[1]-a[1]) / (b[1]-a[1]) + a[0]:
            result = not result
    return result


class Extrusion(WireModel):
    kind: Literal['extrusion'] = 'extrusion'
    outer: list[Point2] = Field(min_length=3, max_length=48)
    holes: list[Annotated[list[Point2], Field(min_length=3, max_length=24)]] = Field(default_factory=list, max_length=4)
    floor: float = Field(default=0, ge=0, lt=1, allow_inf_nan=False)

    @model_validator(mode='after')
    def valid(self):
        if len(self.outer) + sum(map(len, self.holes)) > 96 or (self.floor and not self.holes):
            raise ValueError('Extrusion profile or floor is invalid')
        for profile in [self.outer, *self.holes]:
            ring_valid(profile)
        for i, hole in enumerate(self.holes):
            if not all(inside(p, self.outer) for p in hole):
                raise ValueError('Hole lies outside the profile')
            for other in [self.outer, *self.holes[:i]]:
                if any(intersects(a, b, c, d) for a, b in zip(hole, hole[1:]+hole[:1], strict=True)
                       for c, d in zip(other, other[1:]+other[:1], strict=True)):
                    raise ValueError('Profiles touch or intersect')
            if any(inside(hole[0], other) or inside(other[0], hole) for other in self.holes[:i]):
                raise ValueError('Nested holes are unsupported')
        return self


class Revolution(WireModel):
    kind: Literal['revolution'] = 'revolution'
    profile: list[Point2] = Field(min_length=3, max_length=48)

    @model_validator(mode='after')
    def valid(self):
        ring_valid(self.profile)
        if any(p[0] < 0 for p in self.profile):
            raise ValueError('Revolution radius must be nonnegative')
        return self


class Section(WireModel):
    z: Coordinate
    outer: list[Point2] = Field(min_length=3, max_length=32)


class Loft(WireModel):
    kind: Literal['loft'] = 'loft'
    sections: list[Section] = Field(min_length=2, max_length=8)

    @model_validator(mode='after')
    def valid(self):
        for index, section in enumerate(self.sections):
            ring_valid(section.outer)
            if not index:
                continue
            previous = self.sections[index-1]
            if section.z <= previous.z or len(section.outer) != len(previous.outer) or area(section.outer)*area(previous.outer) <= 0:
                raise ValueError('Loft sections need increasing heights and matching winding/vertices')
            for i in range(len(section.outer)):
                j = (i+1) % len(section.outer)
                if sum((previous.outer[j][k]-previous.outer[i][k]) * (section.outer[j][k]-section.outer[i][k]) for k in range(2)) <= 0:
                    raise ValueError('Loft correspondence twists too far')
            for fraction in (.125, .25, .375, .5, .625, .75, .875):
                ring_valid([tuple((1-fraction)*a[k] + fraction*b[k] for k in range(2))
                            for a, b in zip(previous.outer, section.outer, strict=True)])
        return self


def sub(a, b):
    return tuple(x-y for x, y in zip(a, b, strict=True))


def dot(a, b):
    return sum(x*y for x, y in zip(a, b, strict=True))


def segment_distance(a, b, c, d):
    u, v, w = sub(b, a), sub(d, c), sub(a, c)
    aa, bb, cc, dd, ee = dot(u, u), dot(u, v), dot(v, v), dot(u, w), dot(v, w)
    denominator = aa*cc-bb*bb
    s = max(0, min(1, (bb*ee-cc*dd)/denominator)) if denominator > 1e-12 else 0
    t = (bb*s+ee)/cc
    if t < 0:
        t, s = 0, max(0, min(1, -dd/aa))
    elif t > 1:
        t, s = 1, max(0, min(1, (bb-dd)/aa))
    return math.sqrt(sum((w[k]+s*u[k]-t*v[k])**2 for k in range(3)))


class Sweep(WireModel):
    kind: Literal['sweep'] = 'sweep'
    path: list[Point3] = Field(min_length=2, max_length=32)
    radius: float = Field(gt=0, le=.2, allow_inf_nan=False)
    inner_radius: float = Field(default=0, ge=0, le=.2, allow_inf_nan=False)

    @model_validator(mode='after')
    def valid(self):
        if self.inner_radius >= self.radius or any(abs(value)+self.radius > .5+1e-9 for p in self.path for value in p):
            raise ValueError('Sweep radius exceeds its bounds')
        segments = list(zip(self.path, self.path[1:]))
        for i, (a, b) in enumerate(segments):
            vector = sub(b, a)
            if dot(vector, vector) < 1e-9:
                raise ValueError('Sweep repeats a point')
            if i:
                previous = sub(segments[i-1][1], segments[i-1][0])
                if dot(vector, previous) / math.sqrt(dot(vector, vector)*dot(previous, previous)) < -.999:
                    raise ValueError('Sweep reverses direction')
            if any(segment_distance(a, b, c, d) <= 2*self.radius+1e-9 for c, d in segments[:max(0, i-1)]):
                raise ValueError('Sweep segments collide')
        return self


Geometry = Annotated[Extrusion | Revolution | Loft | Sweep, Field(discriminator='kind')]


class Vector(WireModel):
    x: float = Field(default=0, ge=-40, le=40, allow_inf_nan=False)
    y: float = Field(default=0, ge=-40, le=40, allow_inf_nan=False)
    z: float = Field(default=0, ge=-40, le=40, allow_inf_nan=False)


class Size(WireModel):
    x: float = Field(gt=0, le=20, allow_inf_nan=False)
    y: float = Field(gt=0, le=20, allow_inf_nan=False)
    z: float = Field(gt=0, le=20, allow_inf_nan=False)


class Rotation(WireModel):
    x: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)
    y: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)
    z: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)


class ConceptPart(WireModel):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,79}$')
    label: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=30)
    shape: Literal['box', 'rounded_box', 'cylinder', 'sphere', 'feature']
    size: Size
    position: Vector = Field(default_factory=Vector)
    rotation: Rotation = Field(default_factory=Rotation)
    explode: Vector = Field(default_factory=Vector)
    color: str = Field(pattern=r'^#[a-fA-F0-9]{6}$')
    detail: str = Field(default='', max_length=400)
    source_choices: list[str] = Field(default_factory=list, max_length=24)
    circuit_role: Literal['none', 'peripheral', 'board'] = 'none'
    geometry: Geometry | None = None

    @model_validator(mode='after')
    def valid(self):
        if (self.shape == 'feature') != (self.geometry is not None):
            raise ValueError('Only feature parts require explicit geometry')
        return self

    def triangles(self):
        shape = self.geometry
        if isinstance(shape, Revolution):
            return len(shape.profile)*64
        if isinstance(shape, Loft):
            return len(shape.sections[0].outer)*len(shape.sections)*2
        if isinstance(shape, Sweep):
            return len(shape.path)*64+64
        if isinstance(shape, Extrusion):
            return (len(shape.outer)+sum(map(len, shape.holes)))*6+32
        return {'box': 12, 'rounded_box': 76, 'cylinder': 92, 'sphere': 256}[self.shape]


class ConceptScene(WireModel):
    schema_version: Literal['concept-scene-v2'] = 'concept-scene-v2'
    units: Literal['relative'] = 'relative'
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(max_length=600)
    device_kind: str = Field(min_length=1, max_length=80)
    parts: list[ConceptPart] = Field(min_length=1, max_length=24)

    @model_validator(mode='after')
    def valid(self):
        if len({part.id for part in self.parts}) != len(self.parts):
            raise ValueError('Part IDs must be unique')
        if any(part.triangles() > 4096 for part in self.parts) or sum(part.triangles() for part in self.parts) > 12000:
            raise ValueError('Concept exceeds the viewer triangle budget')
        return self


class DesignConstraint(WireModel):
    id: str = Field(min_length=1, max_length=80)
    part_ids: list[str] = Field(default_factory=list, max_length=24)
    kind: Literal['dimension', 'clearance', 'mating', 'keepout', 'load', 'process', 'material', 'other']
    description: str = Field(min_length=1, max_length=1000)
    value: float | None = Field(default=None, allow_inf_nan=False, ge=-1000000, le=1000000)
    unit: Literal['mm', 'deg', 'N', 'g', 'V', 'A', 'C', 'none'] = 'none'
    certainty: Literal['known', 'assumed', 'unknown']
    critical: bool = False
    source_ids: list[str] = Field(default_factory=list, max_length=12)


class Choice(WireModel):
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)


class Question(WireModel):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,79}$')
    prompt: str = Field(min_length=1, max_length=1000)
    type: Literal['text', 'number', 'single_select', 'multi_select'] = 'text'
    required: bool = True
    options: list[Choice] = Field(default_factory=list, max_length=12)
    allow_custom: bool = False
    help_text: str = Field(default='', max_length=500)
    unit: str = Field(default='', max_length=20)
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode='after')
    def valid(self):
        if self.type in {'single_select', 'multi_select'} and len(self.options) < 2:
            raise ValueError('Selection questions need at least two options')
        if len({option.value for option in self.options}) != len(self.options):
            raise ValueError('Question options must be unique')
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError('Question range is invalid')
        return self


class CaptureResult(WireModel):
    message: str = Field(min_length=1, max_length=4000)
    scene: ConceptScene | None = None
    requirements: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=50)
    constraints: list[DesignConstraint] = Field(default_factory=list, max_length=100)
    questions: list[Question] = Field(default_factory=list, max_length=12)
    limitations: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=20)
    ready_to_review: bool = False

    @model_validator(mode='after')
    def valid(self):
        if self.scene is None and not self.questions:
            raise ValueError('Capture needs a concrete candidate scene or clarification questions')
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError('Question IDs must be unique')
        if len({constraint.id for constraint in self.constraints}) != len(self.constraints):
            raise ValueError('Constraint IDs must be unique')
        return self
