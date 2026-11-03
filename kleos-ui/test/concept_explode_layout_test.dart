import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/concept_scene.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/concept_geometry.dart';

JsonMap part(String id,
        {double z = 0, JsonMap? explode, bool feature = false}) =>
    {
      'id': id,
      'shape': feature ? 'feature' : 'box',
      'role': 'other',
      'size': {'x': 2, 'y': 1, 'z': feature ? .08 : .5},
      'position': {'x': 0, 'y': 0, 'z': z},
      'rotation': {'x': feature ? 55 : 0, 'y': feature ? 30 : 0, 'z': 0},
      'explode': explode ?? {'x': 0, 'y': 0, 'z': 0},
      if (feature)
        'geometry': {
          'kind': 'extrusion',
          'outer': [
            [-.5, -.5],
            [.5, -.5],
            [.5, .5],
            [-.5, .5]
          ],
          'holes': [
            [
              [-.15, -.15],
              [.15, -.15],
              [.15, .15],
              [-.15, .15]
            ]
          ]
        }
    };
ConceptScene scene(List<JsonMap> parts) => ConceptScene.fromJson(
    {'schemaVersion': 'concept-scene-v2', 'parts': parts});
List<double> boundsZ(ConceptGeometry g, ConceptPart part,
    {bool exploded = true}) {
  final offset = exploded ? g.effectiveExplode[part.id]! : ConceptVector.zero;
  final vertices = g.meshes[part.id]!
      .expand((f) => f.points)
      .map((p) => (p.rotated(part.rotation) + part.position + offset).z);
  return [vertices.reduce(math.min), vertices.reduce(math.max)];
}

List<List<double>> signature(ConceptDrawing drawing) => [
      for (final face in drawing.faces)
        for (final p in face.points) [p.dx, p.dy]
    ];
void main() {
  test(
      'ten identical offsets produce separated actual envelopes without changing source',
      () {
    final raw = [for (var i = 0; i < 10; i++) part('part$i', feature: i == 3)];
    final before = jsonEncode(raw);
    final g = ConceptGeometry(scene(raw));
    expect(g.validationError, isNull);
    final assembled = g.project(const Size(1100, 650), exploded: 0),
        exploded = g.project(const Size(1100, 650), exploded: 1);
    expect(signature(exploded), isNot(signature(assembled)));
    expect(jsonEncode(raw), before);
    final screenBounds = <String, Rect>{};
    for (final face in exploded.faces) {
      screenBounds.update(
          face.partId, (r) => r.expandToInclude(face.path.getBounds()),
          ifAbsent: () => face.path.getBounds());
    }
    final boxes = screenBounds.values.toList();
    for (var i = 0; i < boxes.length; i++) {
      for (var j = i + 1; j < boxes.length; j++) {
        expect(boxes[i].overlaps(boxes[j]), isFalse);
      }
    }
    final packed = boxes.reduce((a, b) => a.expandToInclude(b));
    expect(packed.width / packed.height, inInclusiveRange(.8, 2.7));
    expect(boxes.map((b) => b.width).reduce(math.max), greaterThan(180));
    expect(exploded.faces.map((f) => f.partId).toSet(), hasLength(10));
  });
  test('rotated thin hole mesh and assembled projection remain unchanged', () {
    final parts = [part('hole', feature: true), part('plate')];
    final g = ConceptGeometry(scene(parts));
    final reference = ConceptGeometry(scene(parts));
    // Reference the former all-zero layout without invoking automatic fit.
    reference.effectiveExplode = Map.unmodifiable(
        {for (final p in reference.scene.parts) p.id: ConceptVector.zero});
    expect(signature(g.project(const Size(600, 400), exploded: 0)),
        signature(reference.project(const Size(600, 400), exploded: 0)));
    final holeMesh = g.meshes['hole']!;
    expect(holeMesh.length, reference.meshes['hole']!.length);
    final top =
        holeMesh.where((f) => f.points.every((p) => (p.z - .04).abs() < 1e-9));
    expect(
        top.any((f) => (Path()
              ..addPolygon([for (final p in f.points) Offset(p.x, p.y)], true))
            .contains(Offset.zero)),
        isFalse);
    expect(
        boundsZ(g, g.scene.parts.first, exploded: false)[1], greaterThan(.04));
  });
  test(
      'explicit differing offsets stay exact and layout is stable by ID under reorder',
      () {
    final explicit = scene([
      part('a', explode: {'x': 1, 'y': 2, 'z': 3}),
      part('b', explode: {'x': -2, 'y': 0, 'z': 4})
    ]);
    final g = ConceptGeometry(explicit);
    for (final p in explicit.parts) {
      expect(identical(g.effectiveExplode[p.id], p.explode), isTrue);
    }
    final parts = [part('b'), part('a'), part('c', z: .1)];
    final first = ConceptGeometry(scene(parts)),
        second = ConceptGeometry(scene(parts.reversed.toList()));
    for (final p in first.scene.parts) {
      expect([
        first.effectiveExplode[p.id]!.x,
        first.effectiveExplode[p.id]!.y,
        first.effectiveExplode[p.id]!.z
      ], [
        second.effectiveExplode[p.id]!.x,
        second.effectiveExplode[p.id]!.y,
        second.effectiveExplode[p.id]!.z
      ]);
    }
  });
  test(
      'single part does not acquire invented pieces or offsets; identical nonzero offsets separate',
      () {
    final one = ConceptGeometry(scene([part('only')]));
    expect(one.effectiveExplode['only'], same(one.scene.parts.single.explode));
    expect(signature(one.project(const Size(400, 400), exploded: 0)),
        signature(one.project(const Size(400, 400), exploded: 1)));
    final group = ConceptGeometry(scene([
      part('a', explode: {'x': 1, 'y': 1, 'z': 1}),
      part('b', explode: {'x': 1, 'y': 1, 'z': 1})
    ]));
    final delta = group.effectiveExplode['a']! - group.effectiveExplode['b']!;
    expect(delta.dot(delta), greaterThan(0));
    expect(group.meshes.keys, hasLength(2));
  });
}
