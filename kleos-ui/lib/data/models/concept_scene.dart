import 'dart:math' as math;

import 'package:flutter/foundation.dart';

import 'json.dart';

/// Relative illustration coordinates. These are never engineering dimensions.
@immutable
class ConceptVector {
  const ConceptVector(this.x, this.y, this.z);
  static const zero = ConceptVector(0, 0, 0);
  final double x, y, z;

  factory ConceptVector.fromJson(Object? value,
      {ConceptVector fallback = zero, double limit = 40}) {
    final data = asMap(value);
    double read(String key, double defaultValue) {
      final number = data[key];
      return number is num && number.isFinite
          ? number.toDouble().clamp(-limit, limit)
          : defaultValue;
    }

    return ConceptVector(
        read('x', fallback.x), read('y', fallback.y), read('z', fallback.z));
  }

  ConceptVector operator +(ConceptVector other) =>
      ConceptVector(x + other.x, y + other.y, z + other.z);
  ConceptVector operator -(ConceptVector other) =>
      ConceptVector(x - other.x, y - other.y, z - other.z);
  ConceptVector operator *(double scale) =>
      ConceptVector(x * scale, y * scale, z * scale);
  double dot(ConceptVector other) => x * other.x + y * other.y + z * other.z;
  ConceptVector cross(ConceptVector other) => ConceptVector(
      y * other.z - z * other.y,
      z * other.x - x * other.z,
      x * other.y - y * other.x);
  ConceptVector get normalized {
    final length = math.sqrt(dot(this));
    return length < 1e-10 ? zero : this * (1 / length);
  }

  /// Euler rotations are applied x, then y, then z, in degrees.
  ConceptVector rotated(ConceptVector degrees) {
    final rx = degrees.x * math.pi / 180;
    final ry = degrees.y * math.pi / 180;
    final rz = degrees.z * math.pi / 180;
    final y1 = y * math.cos(rx) - z * math.sin(rx);
    final z1 = y * math.sin(rx) + z * math.cos(rx);
    final x2 = x * math.cos(ry) + z1 * math.sin(ry);
    final z2 = -x * math.sin(ry) + z1 * math.cos(ry);
    return ConceptVector(x2 * math.cos(rz) - y1 * math.sin(rz),
        x2 * math.sin(rz) + y1 * math.cos(rz), z2);
  }
}

@immutable
class ConceptPart {
  const ConceptPart({
    required this.id,
    required this.label,
    required this.role,
    required this.shape,
    required this.size,
    required this.position,
    required this.rotation,
    required this.explode,
    required this.color,
    required this.detail,
    required this.sourceChoices,
    this.geometry,
    this.explicitGeometry = false,
  });
  final JsonMap? geometry;
  final bool explicitGeometry;
  final String id, label, role, shape, color, detail;
  final ConceptVector size, position, rotation, explode;
  final List<String> sourceChoices;

  factory ConceptPart.fromJson(JsonMap data, {bool v2 = false}) {
    if (v2) {
      if (!const {'box', 'rounded_box', 'cylinder', 'sphere', 'feature'}
          .contains(data['shape'])) {
        throw const FormatException('Unsupported concept shape.');
      }
      for (final field in ['position', 'rotation', 'explode']) {
        if (!data.containsKey(field)) continue;
        final vector = asMap(data[field]);
        final limit = field == 'rotation' ? 360 : 40;
        if (!['x', 'y', 'z'].every((key) =>
            vector[key] is num &&
            (vector[key] as num).isFinite &&
            (vector[key] as num).abs() <= limit)) {
          throw const FormatException('Invalid concept part transform.');
        }
      }
      final size = asMap(data['size']);
      if (!['x', 'y', 'z'].every((key) =>
          size[key] is num &&
          (size[key] as num).isFinite &&
          (size[key] as num) > 0 &&
          (size[key] as num) <= 20)) {
        throw const FormatException('Invalid concept part dimensions.');
      }
      if ((data['shape'] == 'feature') != (data['geometry'] is Map)) {
        throw const FormatException(
            'Feature geometry is missing or attached to a primitive.');
      }
    }
    final dimensions = ConceptVector.fromJson(data['size'],
        fallback: const ConceptVector(1, 1, .2), limit: 20);
    return ConceptPart(
      geometry: v2 && data['shape'] == 'feature'
          ? Map.unmodifiable(asMap(data['geometry']))
          : null,
      explicitGeometry: v2,
      id: _text(data['id'], 80),
      label: _text(data['label'], 120),
      role: _text(data['role'], 30),
      shape: (const {'box', 'rounded_box', 'cylinder', 'sphere'}
                  .contains(data['shape']) ||
              (v2 && data['shape'] == 'feature'))
          ? '${data['shape']}'
          : 'rounded_box',
      size: v2
          ? dimensions
          : ConceptVector(
              dimensions.x.abs().clamp(.02, 20),
              dimensions.y.abs().clamp(.02, 20),
              dimensions.z.abs().clamp(.02, 20)),
      position: ConceptVector.fromJson(data['position']),
      rotation: ConceptVector.fromJson(data['rotation'], limit: 360),
      explode: ConceptVector.fromJson(data['explode']),
      color: RegExp(r'^#[a-fA-F0-9]{6}$').hasMatch('${data['color']}')
          ? '${data['color']}'
          : '#8a8c97',
      detail: _text(data['detail'], 400),
      sourceChoices: List.unmodifiable(asList(data['sourceChoices'])
          .take(v2 ? 24 : 12)
          .map((value) => _text(value, 80))),
    );
  }
}

@immutable
class ConceptScene {
  const ConceptScene(
      {this.schemaVersion = 'concept-scene-v1',
      this.validationError,
      required this.title,
      required this.summary,
      required this.deviceKind,
      required this.parts});
  final String schemaVersion;
  final String? validationError;
  final String title, summary, deviceKind;
  final List<ConceptPart> parts;

  factory ConceptScene.fromJson(JsonMap data) {
    final version = data['schemaVersion']?.toString() ?? 'concept-scene-v1';
    final v2 = version == 'concept-scene-v2';
    try {
      if (!const {'concept-scene-v1', 'concept-scene-v2'}.contains(version)) {
        throw const FormatException('Unsupported concept scene version.');
      }
      final records = asMaps(data['parts']);
      if (v2 &&
          (records.isEmpty ||
              records.length > 24 ||
              records.length != asList(data['parts']).length)) {
        throw const FormatException(
            'Invalid concept parts or scene limit exceeded.');
      }
      final seen = <String>{};
      final parts = <ConceptPart>[];
      for (final record in (v2 ? records : records.take(24))) {
        final part = ConceptPart.fromJson(record, v2: v2);
        if (part.id.isEmpty || !seen.add(part.id)) {
          if (v2) {
            throw const FormatException('Concept part IDs must be unique.');
          }
          continue;
        }
        parts.add(part);
      }
      return ConceptScene(
          schemaVersion: version,
          title: _text(data['title'], 160),
          summary: _text(data['summary'], 600),
          deviceKind: _text(data['deviceKind'], 80),
          parts: List.unmodifiable(parts));
    } on FormatException catch (error) {
      return ConceptScene(
          schemaVersion: version,
          title: '',
          summary: '',
          deviceKind: '',
          parts: const [],
          validationError: error.message);
    }
  }
}

String _text(Object? value, int limit) {
  final text = value?.toString() ?? '';
  return text.length > limit ? text.substring(0, limit) : text;
}
