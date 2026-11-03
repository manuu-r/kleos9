import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:file_selector/file_selector.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/production_repository.dart';

class ReferenceImageDraft {
  ReferenceImageDraft(
      {required this.name, required this.mediaType, required this.bytes});
  final String name, mediaType;
  final Uint8List bytes;
  final Map<String, String> _uploaded = {};
  String get hash => sha256.convert(bytes).toString();

  Future<String> upload(
      ProductionRepository repository, String projectId) async {
    final scope =
        '${repository.api.baseUrl}|${repository.organizationId}|$projectId';
    if (_uploaded.containsKey(scope)) return _uploaded[scope]!;
    final result = await repository.uploadReferenceImage(projectId,
        name: name, mediaType: mediaType, contentBase64: base64Encode(bytes));
    return _uploaded[scope] = result['id'] as String;
  }
}

typedef ReferenceImagePicker = Future<List<ReferenceImageDraft>> Function();
final referenceImagePickerProvider =
    Provider<ReferenceImagePicker>((ref) => pickReferenceImages);

Future<List<ReferenceImageDraft>> pickReferenceImages() async {
  final files = await openFiles(acceptedTypeGroups: const [
    XTypeGroup(label: 'Sketches and photos', extensions: [
      'png',
      'jpg',
      'jpeg',
      'webp'
    ], mimeTypes: [
      'image/png',
      'image/jpeg',
      'image/webp'
    ], uniformTypeIdentifiers: [
      'public.png',
      'public.jpeg',
      'org.webmproject.webp'
    ])
  ]);
  return readReferenceImages(files);
}

/// Shared validation for picker and dropped files before attachment/upload.
Future<List<ReferenceImageDraft>> readReferenceImages(List<XFile> files) async {
  if (files.length > 4) {
    throw const FormatException('Choose up to four images.');
  }
  final images = <ReferenceImageDraft>[];
  for (final file in files) {
    if (await file.length() > 6 * 1024 * 1024) {
      throw const FormatException('Each image must be 6 MB or smaller.');
    }
    final extension = file.name.split('.').last.toLowerCase();
    final mediaType = switch (extension) {
      'png' => 'image/png',
      'jpg' || 'jpeg' => 'image/jpeg',
      'webp' => 'image/webp',
      _ => throw const FormatException('Choose a PNG, JPEG or WebP image.'),
    };
    final bytes = await file.readAsBytes();
    if (bytes.length > 6 * 1024 * 1024) {
      throw const FormatException('Each image must be 6 MB or smaller.');
    }
    images.add(ReferenceImageDraft(
        name: file.name, mediaType: mediaType, bytes: bytes));
  }
  return images;
}
