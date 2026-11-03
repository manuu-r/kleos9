import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:ui';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/concept_scene.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/concept_geometry.dart';

JsonMap fixture() =>
    asMap(jsonDecode(File('../backend/tests/fixtures/concept-geometry-v2.json')
        .readAsStringSync()));
ConceptScene scene(JsonMap feature,
        {ConceptVector size = const ConceptVector(1, 1, 1),
        String role = 'shell'}) =>
    ConceptScene.fromJson({
      'schemaVersion': 'concept-scene-v2',
      'parts': [
        {
          'id': 'target',
          'shape': 'feature',
          'role': role,
          'geometry': feature,
          'size': {'x': size.x, 'y': size.y, 'z': size.z},
          'color': '#88AACC'
        }
      ]
    });
final outer = <List<double>>[
  [-.5, -.5],
  [.5, -.5],
  [.5, .5],
  [-.5, .5]
];
final hole = <List<double>>[
  [-.2, -.2],
  [.2, -.2],
  [.2, .2],
  [-.2, .2]
];
List<ConceptFace> mesh(JsonMap feature) {
  final geometry = ConceptGeometry(scene(feature));
  expect(geometry.validationError, isNull);
  return geometry.meshes['target']!;
}

String vertex(ConceptVector p) => [p.x, p.y, p.z]
    .map((v) => v.abs() < 1e-8 ? '0.00000000' : v.toStringAsFixed(8))
    .join(',');
void manifold(List<ConceptFace> faces) {
  final edges = <String, int>{};
  final directions = <String, int>{};
  for (final f in faces) {
    for (var i = 0; i < f.points.length; i++) {
      final a = vertex(f.points[i]),
          b = vertex(f.points[(i + 1) % f.points.length]);
      expect(a, isNot(b));
      final key = a.compareTo(b) < 0 ? '$a/$b' : '$b/$a';
      edges.update(key, (v) => v + 1, ifAbsent: () => 1);
      directions.update(key, (v) => v + (a.compareTo(b) < 0 ? 1 : -1),
          ifAbsent: () => a.compareTo(b) < 0 ? 1 : -1);
    }
  }
  expect(edges.values.every((count) => count == 2), isTrue,
      reason: 'Every geometric edge has exactly two faces');
  expect(directions.values.every((count) => count == 0), isTrue,
      reason: 'Adjacent winding opposes');
}

double volume(List<ConceptFace> faces) => faces.fold(
    0.0, (sum, f) => sum + f.points[0].dot(f.points[1].cross(f.points[2])) / 6);
bool coversXY(ConceptFace f, double x, double y) {
  final p = Path()
    ..addPolygon([for (final v in f.points) Offset(v.x, v.y)], true);
  return p.contains(Offset(x, y));
}

void main() {
  test(
      'shared v2 fixture compiles deterministically with outward manifold feature surfaces',
      () {
    final parsed = ConceptScene.fromJson(fixture());
    expect(parsed.validationError, isNull);
    final a = ConceptGeometry(parsed), b = ConceptGeometry(parsed);
    expect(a.validationError, isNull);
    expect(a.meshes.keys, containsAll(['mount', 'pocket']));
    for (final part in parsed.parts.where((p) => p.geometry != null)) {
      final faces = a.meshes[part.id]!;
      expect(faces.length, lessThanOrEqualTo(4096));
      manifold(faces);
      expect(volume(faces), greaterThan(0), reason: part.id);
      expect(
          faces.map((f) => f.points.map(vertex).join('|')).toList(),
          b.meshes[part.id]!
              .map((f) => f.points.map(vertex).join('|'))
              .toList());
      expect(faces.every((f) => f.partId == part.id), isTrue);
    }
  });
  test('extrusion really removes hole caps and retains inward cavity walls',
      () {
    final faces = mesh({
      'kind': 'extrusion',
      'outer': outer,
      'holes': [hole]
    });
    manifold(faces);
    final top =
        faces.where((f) => f.points.every((p) => (p.z - .5).abs() < 1e-8));
    final area = top.fold(
        0.0,
        (sum, f) =>
            sum +
            (f.points[1] - f.points[0]).cross(f.points[2] - f.points[0]).z / 2);
    expect(area, closeTo(.84, 1e-8));
    expect(top.any((f) => coversXY(f, 0, 0)), isFalse);
    expect(
        faces
            .where((f) => f.points.every((p) => (p.x - .2).abs() < 1e-8))
            .every((f) => f.normal.x < 0),
        isTrue);
    expect(volume(faces), closeTo(.84, 1e-8));
  });
  test(
      'blind pocket has a real floor at requested depth, full bottom and no top fill',
      () {
    final faces = mesh({
      'kind': 'extrusion',
      'outer': outer,
      'holes': [hole],
      'floor': .2
    });
    manifold(faces);
    final hits = faces
        .where((f) => coversXY(f, .01, .013) && f.normal.z.abs() > .99)
        .map((f) => f.points.first.z)
        .toSet();
    expect(hits, containsAll([-.5, -.3]));
    expect(hits, isNot(contains(.5)));
    expect(volume(faces), closeTo(1 - .16 * .8, 1e-8));
  });
  test('revolution has true bore and variable radial profile', () {
    final faces = mesh({
      'kind': 'revolution',
      'profile': [
        [.15, -.5],
        [.4, -.5],
        [.3, .5],
        [.15, .5]
      ]
    });
    manifold(faces);
    expect(
        faces.any((f) => f.normal.z.abs() > .99 && coversXY(f, 0, 0)), isFalse);
    final lower = faces
        .expand((f) => f.points)
        .where((p) => p.z == -.5)
        .map((p) => math.sqrt(p.x * p.x + p.y * p.y))
        .reduce(math.max);
    final upper = faces
        .expand((f) => f.points)
        .where((p) => p.z == .5)
        .map((p) => math.sqrt(p.x * p.x + p.y * p.y))
        .reduce(math.max);
    expect(lower, closeTo(.4, 1e-8));
    expect(upper, closeTo(.3, 1e-8));
    expect(volume(faces), greaterThan(0));
  });
  test('loft taper and hollow bent sweep preserve topology', () {
    final loft = mesh({
      'kind': 'loft',
      'sections': [
        {'z': -.5, 'outer': outer},
        {'z': .5, 'outer': hole}
      ]
    });
    manifold(loft);
    expect(
        loft
            .expand((f) => f.points)
            .where((p) => p.z == .5)
            .map((p) => p.x.abs())
            .reduce(math.max),
        .2);
    final tube = mesh({
      'kind': 'sweep',
      'path': [
        [-.3, 0, 0],
        [0, 0, 0],
        [.2, .2, 0]
      ],
      'radius': .08,
      'innerRadius': .04
    });
    manifold(tube);
    expect(volume(tube), greaterThan(0));
  });
  test(
      'invalid topology and oversized definitions fail closed without box fallback',
      () {
    for (final bad in [
      {
        'kind': 'extrusion',
        'outer': [
          [-.5, -.5],
          [.5, .5],
          [-.5, .5],
          [.5, -.5]
        ]
      },
      {
        'kind': 'extrusion',
        'outer': hole,
        'holes': [outer]
      },
      {'kind': 'unknown'},
      {
        'kind': 'sweep',
        'path': [
          [0, 0, 0],
          [0, 0, 0]
        ],
        'radius': .1
      },
      {
        'kind': 'extrusion',
        'outer': List.generate(
            49,
            (i) => [
                  .4 * math.cos(i * 2 * math.pi / 49),
                  .4 * math.sin(i * 2 * math.pi / 49)
                ])
      },
    ]) {
      final geometry = ConceptGeometry(scene(bad));
      expect(geometry.validationError, isNotNull);
      expect(geometry.meshes, isEmpty);
      expect(geometry.project(const Size(300, 300)).faces, isEmpty);
    }
  });
  test('whole-scene budget rejects instead of truncating a valid part', () {
    final part = {
      'id': 'a',
      'shape': 'feature',
      'size': {'x': 1, 'y': 1, 'z': 1},
      'geometry': {
        'kind': 'revolution',
        'profile': [
          [.1, -.5],
          [.4, -.5],
          [.4, -.25],
          [.3, 0],
          [.4, .25],
          [.4, .5],
          [.1, .5],
          [.1, .25],
          [.12, 0],
          [.1, -.25]
        ]
      }
    };
    final data = {
      'schemaVersion': 'concept-scene-v2',
      'parts': [
        for (var i = 0; i < 24; i++) {...part, 'id': 'part$i'}
      ]
    };
    final geometry = ConceptGeometry(ConceptScene.fromJson(data));
    expect(geometry.validationError, contains('budget'));
    expect(geometry.meshes, isEmpty);
  });
  test(
      'v2 primitives bypass invented shell/board details while v1 stays unchanged',
      () {
    final part = {
      'id': 'board',
      'shape': 'box',
      'role': 'board',
      'size': {'x': 2, 'y': 2, 'z': .2}
    };
    final v2 = ConceptGeometry(ConceptScene.fromJson({
      'schemaVersion': 'concept-scene-v2',
      'parts': [part]
    }));
    final v1 = ConceptGeometry(ConceptScene.fromJson({
      'schemaVersion': 'concept-scene-v1',
      'parts': [part]
    }));
    expect(v2.meshes['board'], hasLength(6));
    expect(v1.meshes['board']!.length, greaterThan(6));
  });
  test('four holes triangulate without filled caps or cracked surfaces', () {
    final holes = <List<List<double>>>[];
    for (final x in [-.25, .25]) {
      for (final y in [-.25, .25]) {
        holes.add([
          [x - .08, y - .08],
          [x + .08, y - .08],
          [x + .08, y + .08],
          [x - .08, y + .08]
        ]);
      }
    }
    final faces = mesh({
      'kind': 'extrusion',
      'outer': outer.reversed.toList(),
      'holes': holes
    });
    manifold(faces);
    final top = faces.where((f) => f.points.every((p) => p.z == .5));
    for (final x in [-.25, .25]) {
      for (final y in [-.25, .25]) {
        expect(top.any((f) => coversXY(f, x, y)), isFalse);
      }
    }
    expect(volume(faces), closeTo(1 - 4 * .16 * .16, 1e-8));
  });
  test('projected through-opening is not selectable as the parent cap', () {
    final geometry = ConceptGeometry(scene({
      'kind': 'extrusion',
      'outer': outer,
      'holes': [hole]
    }, size: const ConceptVector(1, 1, .1)));
    final drawing = geometry.project(const Size(400, 400),
        elevation: 78, azimuth: 0, exploded: 0);
    expect(drawing.partAt(const Offset(200, 196)), isNull);
    expect(drawing.faces.every((face) => face.partId == 'target'), isTrue);
    expect(
        drawing.faces.any(
            (face) => drawing.partAt(face.path.getBounds().center) == 'target'),
        isTrue);
  });
  test('loft twists, colliding sweeps and unsupported v2 shapes fail visibly',
      () {
    final twisted = ConceptGeometry(scene({
      'kind': 'loft',
      'sections': [
        {'z': -.5, 'outer': outer},
        {
          'z': .5,
          'outer': [...outer.skip(2), ...outer.take(2)]
        }
      ]
    }));
    expect(twisted.validationError, isNotNull);
    final crossed = ConceptGeometry(scene({
      'kind': 'sweep',
      'path': [
        [-.3, -.3, 0],
        [.3, .3, 0],
        [-.3, .3, 0],
        [.3, -.3, 0]
      ],
      'radius': .04
    }));
    expect(crossed.validationError, isNotNull);
    final invalid = ConceptScene.fromJson({
      'schemaVersion': 'concept-scene-v2',
      'parts': [
        {
          'id': 'bad',
          'shape': 'torus',
          'size': {'x': 1, 'y': 1, 'z': 1}
        }
      ]
    });
    expect(invalid.validationError, isNotNull);
    expect(invalid.parts, isEmpty);
  });
  test(
      'solid sweep and axis-touching revolution remain closed with outward winding',
      () {
    manifold(mesh({
      'kind': 'sweep',
      'path': [
        [0, 0, -.3],
        [0, 0, .3]
      ],
      'radius': .1
    }));
    final lathe = mesh({
      'kind': 'revolution',
      'profile': [
        [0, -.5],
        [.4, -.5],
        [.4, .5],
        [0, .5]
      ]
    });
    manifold(lathe);
    expect(volume(lathe), greaterThan(0));
  });
  test('normalized feature coordinates scale exactly once even for thin parts',
      () {
    final geometry = ConceptGeometry(scene(
        {'kind': 'extrusion', 'outer': outer},
        size: const ConceptVector(2, 3, .001)));
    expect(geometry.validationError, isNull);
    final vertices = geometry.meshes['target']!.expand((f) => f.points);
    expect(vertices.map((p) => p.x.abs()).reduce(math.max), 1);
    expect(vertices.map((p) => p.y.abs()).reduce(math.max), 1.5);
    expect(vertices.map((p) => p.z.abs()).reduce(math.max), .0005);
  });
}
