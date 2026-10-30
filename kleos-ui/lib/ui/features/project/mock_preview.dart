import 'package:flutter/material.dart';

import '../../../data/models/json.dart';
import '../../core/device_visual.dart';
import 'common.dart';

/// Builds the same preview record the engineering API returns, from the
/// simulated project, so stages 3 and 4 use one studio in both modes.
///
/// Nothing here is a real geometry file: the board is a deterministic layout
/// of the candidate parts and the app screen uses illustrative values, and the
/// panel labels both as approximations.
JsonMap mockPreviewData(JsonMap project) {
  final parts = asMaps(project['candidateParts']);
  final artifacts = asMaps(project['artifacts']);
  final source = asMap(project['generatedSource']);
  String statusOf(String domain) {
    final match = artifacts.where((a) => a['domain'] == domain);
    return match.isEmpty ? 'queued' : '${match.first['status']}';
  }

  Map<String, dynamic> lane(String domain, String sourceKey) {
    final file = artifacts.where((a) => a['domain'] == domain).firstOrNull;
    final code = '${source[sourceKey] ?? ''}';
    return {
      'status': statusOf(domain),
      'source': 'interface',
      if (file != null) 'name': file['name'],
      if (code.isNotEmpty) ...{
        'code': code,
        'codeArtifactId': '${file?['id'] ?? domain}',
        'codeName': '${file?['name'] ?? domain}',
        'codeStatus': statusOf(domain),
      },
    };
  }

  final features =
      asList(asMap(project['answers'])['features']).map((v) => '$v').toSet();
  final tiles = <JsonMap>[
    if (features.isEmpty || features.contains('CO2 level'))
      {'kind': 'metric', 'label': 'CO₂', 'value': '812 ppm'},
    if (features.isEmpty || features.contains('Temperature and humidity'))
      {'kind': 'metric', 'label': 'Climate', 'value': '22.4 °C, 47% RH'},
    if (features.contains('Fine dust (PM2.5)'))
      {'kind': 'metric', 'label': 'PM2.5', 'value': '9 µg/m³'},
    if (features.contains('Alerts on your phone'))
      {'kind': 'toggle', 'label': 'Alerts', 'value': 'Buzzer armed'},
  ];
  final mechanics = lane('Mechanical', 'Mechanical');
  final electronics = {
    ...lane('Electronics', 'Electronics'),
    'summary': 'Candidate parts placed on a two-layer board.',
    'pcb': _board(parts),
  };
  final firmware = lane('Firmware', 'Firmware');
  final app = {
    ...lane('App', 'App'),
    'app': {'title': '${project['name'] ?? 'Your device'}', 'widgets': tiles},
  };
  return {
    'revision': 1,
    'source': 'simulated',
    'mechanics': {
      ...mechanics,
      'summary': 'Exploded view of the printed case, board and battery.',
      'envelope': {'width': 44, 'height': 38, 'depth': 11.5},
    },
    'electronics': electronics,
    if ('${firmware['code'] ?? ''}'.isNotEmpty) 'firmware': firmware,
    'application': app,
    'files': [
      for (final a in artifacts)
        {
          'id': '${a['id']}',
          'name': '${a['name']}',
          'lane': '${a['domain']}',
          'status': '${a['status']}',
        }
    ],
  };
}

/// The simulated assembly, drawn by the same exploded-device painter the home
/// page uses so the CAD view always has something real to show.
Widget mockAssembly(JsonMap project, double height) =>
    DeviceVisual(exploded: true, finish: finishOf(project), guideStep: null);

Map<String, dynamic> _board(List<JsonMap> parts) {
  const slots = <String, (double, double, double, double, String)>{
    'Core module': (22, 18, 18, 12, 'ESP32-S3'),
    'Display': (22, 5, 14, 4, 'DISP'),
    'Battery': (37, 32, 7, 4, 'BAT'),
    'Power': (5, 19, 7, 9, 'USB-C'),
    'CO2 sensor': (10, 8, 7, 7, 'SCD41'),
    'Climate': (36, 9, 5, 5, 'SHT45'),
    'Dust': (12, 32, 10, 5, 'PM'),
    'Alert': (38, 22, 6, 6, 'BZ1'),
  };
  final placed =
      <({String role, double x, double y, double w, double h, String label})>[];
  for (final part in parts) {
    final slot = slots['${part['role']}'];
    if (slot == null) continue;
    placed.add((
      role: '${part['role']}',
      x: slot.$1,
      y: slot.$2,
      w: slot.$3,
      h: slot.$4,
      label: slot.$5
    ));
  }
  final core = placed.where((p) => p.role == 'Core module').firstOrNull;
  return {
    'board': {'width': 46, 'height': 40},
    'components': [
      for (final p in placed)
        {
          'id': p.label,
          'label': p.label,
          'x': p.x,
          'y': p.y,
          'width': p.w,
          'height': p.h
        }
    ],
    'traces': [
      if (core != null)
        for (final p in placed.where((p) => p.role != 'Core module'))
          {
            'layer': p.y > core.y ? 'B.Cu' : 'F.Cu',
            'points': [
              {'x': core.x, 'y': core.y},
              {'x': p.x, 'y': core.y},
              {'x': p.x, 'y': p.y},
            ]
          }
    ],
  };
}
