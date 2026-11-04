import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/json.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import 'common.dart';

typedef CircuitSimulationRequest = ({String projectId, int revision});

final circuitSimulationProvider = FutureProvider.autoDispose
    .family<JsonMap, CircuitSimulationRequest>((ref, request) {
  final repository = ref.watch(productionRepositoryProvider);
  return repository.api.circuitSimulation(request.projectId,
      organizationId: repository.organizationId);
}, retry: (_, __) => null);

class CircuitSimulationPanel extends ConsumerWidget {
  const CircuitSimulationPanel(
      {super.key, required this.projectId, required this.revision});
  final String projectId;
  final int revision;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final request = (projectId: projectId, revision: revision);
    final provider = circuitSimulationProvider(request);
    final state = ref.watch(provider);
    return KCard(
        child: state.when(
            loading: () =>
                const Text('Loading independent circuit simulation…'),
            error: (_, __) => pColumn([
                  pText(
                      'Circuit simulation unavailable. Your files are still available.'),
                  KButton(
                      label: 'Retry simulation read',
                      onPressed: () => ref.invalidate(provider)),
                ]),
            data: (data) => data['status'] == 'available' &&
                    number(data['revision']) >= revision
                ? CircuitWaveformViewer(
                    key: ValueKey(data['artifactSha256']),
                    result: asMap(data['result']))
                : pColumn([
                    pText('Power-on · simulated, not measured', strong: true),
                    pText(
                        data['reason'] ??
                            'Waiting for current independent evidence.',
                        muted: true),
                    pText(
                        'Independent validation needs accepted circuit parts and explicit supply/load terminals.',
                        small: true,
                        muted: true),
                  ])));
  }
}

class WaveSignal {
  const WaveSignal(
      this.name, this.unit, this.values, this.low, this.high, this.limits);
  final String name, unit;
  final List<double> values, low, high;
  final Map<String, double> limits;
}

class CircuitWaveData {
  const CircuitWaveData(this.times, this.signals);
  final List<double> times;
  final List<WaveSignal> signals;

  factory CircuitWaveData.parse(JsonMap result) {
    if (result['schemaVersion'] != 'circuit-waveform-v1' ||
        result['evidenceKind'] != 'digital' ||
        result['physicalAcceptance'] != false) {
      throw const FormatException('Unsupported circuit evidence');
    }
    List<double> numbers(Object? raw) {
      if (raw is! List ||
          raw.length > 501 ||
          raw.any((v) => v is! num || !v.isFinite || v.abs() > 1e12)) {
        throw const FormatException('Invalid waveform samples');
      }
      return List<double>.unmodifiable(raw.map((v) => (v as num).toDouble()));
    }

    final wave = asMap(result['waveform']);
    final times = numbers(wave['timeSeconds']);
    if (times.length == 1 ||
        times.any((t) => t < 0 || t > 0.100001) ||
        List.generate(math.max(0, times.length - 1), (i) => i)
            .any((i) => times[i + 1] <= times[i])) {
      throw const FormatException('Invalid time axis');
    }
    final rawSignals = wave['signals'];
    if (rawSignals is! List || rawSignals.length > 64) {
      throw const FormatException('Too many signals');
    }
    final signals = <WaveSignal>[];
    for (final raw in rawSignals) {
      final signal = asMap(raw);
      final values = numbers(signal['values']);
      final low = numbers(signal['low']);
      final high = numbers(signal['high']);
      if (times.isEmpty ||
          values.length != times.length ||
          low.length != times.length ||
          high.length != times.length ||
          !['V', 'mA'].contains(signal['unit']) ||
          signal['name'] is! String ||
          '${signal['name']}'.length > 300) {
        throw const FormatException('Invalid signal dimensions');
      }
      for (var i = 0; i < times.length; i++) {
        if (low[i] > values[i] || values[i] > high[i]) {
          throw const FormatException('Invalid tolerance band');
        }
      }
      final limits = <String, double>{};
      for (final item in asMap(signal['limits']).entries) {
        if (!['min', 'max'].contains(item.key) ||
            item.value is! num ||
            !(item.value as num).isFinite ||
            (item.value as num).abs() > 1e12) {
          throw const FormatException('Invalid waveform limits');
        }
        limits[item.key] = (item.value as num).toDouble();
      }
      signals.add(WaveSignal(
          '${signal['name']}', '${signal['unit']}', values, low, high, limits));
    }
    if (times.isNotEmpty && signals.isEmpty) {
      throw const FormatException('Missing waveform signals');
    }
    return CircuitWaveData(times, signals);
  }
}

class CircuitWaveformViewer extends StatefulWidget {
  const CircuitWaveformViewer({super.key, required this.result});
  final JsonMap result;

  @override
  State<CircuitWaveformViewer> createState() => _CircuitWaveformViewerState();
}

class _CircuitWaveformViewerState extends State<CircuitWaveformViewer>
    with SingleTickerProviderStateMixin {
  late final AnimationController replay =
      AnimationController(vsync: this, duration: const Duration(seconds: 6))
        ..addListener(() {
          if (mounted) setState(() {});
        });
  int selected = 0;
  bool startupZoom = true;
  JsonMap? parsedResult;
  CircuitWaveData? parsedData;

  @override
  void didUpdateWidget(covariant CircuitWaveformViewer oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.result != widget.result) {
      replay.stop();
      replay.value = 0;
      selected = 0;
      startupZoom = true;
    }
  }

  @override
  void dispose() {
    replay.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!identical(parsedResult, widget.result)) {
      parsedResult = widget.result;
      try {
        parsedData = CircuitWaveData.parse(widget.result);
      } on FormatException {
        parsedData = null;
      }
    }
    final data = parsedData;
    if (data == null) {
      return pText('Circuit evidence is malformed; waveform unavailable.');
    }
    final reduced = MediaQuery.disableAnimationsOf(context);
    if (reduced && replay.isAnimating) replay.stop();
    final checks = asMaps(widget.result['checks']);
    final signal = data.signals.isEmpty ? null : data.signals[selected];
    final startupEnd = data.times.lastIndexWhere((t) => t <= 0.005001);
    final canZoom = startupEnd >= 2 && startupEnd < data.times.length - 1;
    final end = canZoom && startupZoom ? startupEnd : data.times.length - 1;
    final visibleTimes =
        data.times.take(math.max(0, end + 1)).toList(growable: false);
    final index = data.times.isEmpty ? 0 : (replay.value * end).round();
    return pColumn([
      Wrap(
          spacing: 8,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            pText('Power-on', strong: true),
            const KBadge('Simulated · not measured', tone: 'accent'),
            const KBadge('Advisory', tone: 'unknown'),
          ]),
      pText(
          'Watch the accepted circuit power up. Replay changes the view, never the evidence.',
          small: true,
          muted: true),
      if (signal == null)
        pText(
            'UNKNOWN · No supported waveform. ${checks.isEmpty ? '' : asMap(checks.first['details'])['reason'] ?? ''}')
      else ...[
        DropdownButtonFormField<int>(
            key: const ValueKey('waveform-net'),
            initialValue: selected,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Net or current'),
            items: [
              for (var i = 0; i < data.signals.length; i++)
                DropdownMenuItem(
                    value: i,
                    child: Text(data.signals[i].name,
                        maxLines: 1, overflow: TextOverflow.ellipsis))
            ],
            onChanged: (value) => setState(() => selected = value ?? 0)),
        Semantics(
            label:
                'Interactive waveform. Drag to scrub time. Use the time slider for keyboard access.',
            child: LayoutBuilder(
                builder: (context, box) => GestureDetector(
                    key: const ValueKey('waveform-plot'),
                    onTapDown: (event) => scrub((event.localPosition.dx - 50) /
                        math.max(1, box.maxWidth - 62)),
                    onHorizontalDragUpdate: (event) => scrub(
                        (event.localPosition.dx - 50) /
                            math.max(1, box.maxWidth - 62)),
                    child: SizedBox(
                        height: box.maxWidth < 500 ? 210 : 270,
                        width: double.infinity,
                        child: CustomPaint(
                            painter:
                                _WavePainter(visibleTimes, signal, index)))))),
        Wrap(spacing: 16, runSpacing: 8, children: [
          pText('${(data.times[index] * 1000).toStringAsFixed(2)} ms',
              mono: true),
          pText('${signal.values[index].toStringAsFixed(3)} ${signal.unit}',
              mono: true),
          for (final limit in signal.limits.entries)
            pText(
                '${limit.key} ${limit.value.toStringAsFixed(3)} ${signal.unit}',
                small: true,
                color: KColors.warn),
        ]),
        pText(
            'Line: nominal · band: R ±5%, C ±20% endpoint corners and raw peaks',
            small: true,
            muted: true),
        Slider(
            key: const ValueKey('waveform-time'),
            label: '${(data.times[index] * 1000).toStringAsFixed(2)} ms',
            value: replay.value,
            onChanged: scrub,
            semanticFormatterCallback: (value) =>
                '${(value * visibleTimes.last * 1000).toStringAsFixed(1)} milliseconds'),
        Wrap(spacing: 10, runSpacing: 8, children: [
          KButton(
              label: replay.isAnimating ? 'Pause' : 'Replay power-on',
              icon: replay.isAnimating ? Icons.pause : Icons.play_arrow,
              onPressed: reduced
                  ? null
                  : () => setState(() {
                        if (replay.isAnimating) {
                          replay.stop();
                        } else {
                          replay.forward(
                              from: replay.value >= 1 ? 0 : replay.value);
                        }
                      })),
          KButton(
              label: 'Reset time',
              icon: Icons.replay,
              onPressed: () => scrub(0)),
          if (canZoom)
            KButton(
                label:
                    startupZoom ? 'Show full 100 ms' : 'Zoom to startup 5 ms',
                icon: Icons.zoom_in,
                onPressed: () => setState(() {
                      startupZoom = !startupZoom;
                      scrub(0);
                    })),
        ]),
        if (reduced)
          pText('Reduced motion: scrub manually to inspect the power-on.',
              small: true),
        for (final device
            in asMaps(asMap(widget.result['metadata'])['devices']))
          if (device['safeCurrentMa'] != null) _ledReadout(device, data, index),
      ],
      for (final check in checks.take(8))
        pColumn([
          Wrap(
              spacing: 8,
              runSpacing: 6,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                evidence(check['status']),
                pText(_checkTitle('${check['name']}'), strong: true),
              ]),
          if (asMap(check['details'])['reason'] != null)
            pText(asMap(check['details'])['reason'], small: true, muted: true),
          for (final measurement
              in asMaps(asMap(check['details'])['measurements'])
                  .where((m) => asList(m['corner']).every((v) => v == 0))
                  .take(16))
            pText(_measurement(measurement), small: true, mono: true),
          if (asMap(check['details'])['peakCurrentMa'] is num)
            pText(
                'Peak ${asMap(check['details'])['peakCurrentMa']} mA / limit ${asMap(check['details'])['limitMa']} mA',
                small: true),
        ], gap: 4),
      pText(
          'Check statuses apply to the complete run and every corner. Readouts above are nominal.',
          small: true,
          muted: true),
      pDisclosure(
          title: 'Simulation limits',
          child: pColumn([
            for (final limit
                in asList(asMap(widget.result['metadata'])['limitations'])
                    .take(10))
              pText(limit, small: true, muted: true),
            pText(
                'Not physical acceptance. Independent PCB correspondence and release checks remain separate.',
                small: true),
          ])),
    ], gap: 12);
  }

  void scrub(double value) {
    replay.stop();
    replay.value = value.clamp(0, 1);
  }

  Widget _ledReadout(JsonMap device, CircuitWaveData data, int index) {
    final matches = data.signals
        .where((signal) => signal.name == '${device['componentId']} current');
    if (matches.isEmpty) return const SizedBox.shrink();
    final current = matches.first.values[index];
    final on = current >= 0.1;
    return Wrap(
        spacing: 10,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Icon(on ? Icons.lightbulb : Icons.lightbulb_outline,
              color: on ? KColors.accent : KColors.muted),
          pText(
              '${device['componentId']} · LED ${on ? 'on' : 'off'} · ${current.toStringAsFixed(3)} mA'),
        ]);
  }
}

String _checkTitle(String name) => switch (name) {
      'circuit_dc_rails' => 'DC rail voltage limits',
      'circuit_startup' => 'Rise and settling',
      'circuit_led_current' => 'LED current limit',
      'circuit_total_current' => 'Supply current budget',
      _ => name,
    };

String _measurement(JsonMap m) {
  String fixed(Object? value) =>
      value is num ? value.toStringAsFixed(3) : 'unknown';
  if (m['voltageV'] is num) {
    return '${m['loadId']}: ${fixed(m['voltageV'])} V / ${fixed(m['minVoltageV'])}–${fixed(m['maxVoltageV'])} V';
  }
  if (m['peakCurrentMa'] is num) {
    return '${m['componentId']}: ${fixed(m['peakCurrentMa'])} mA / limit ${fixed(m['limitMa'])} mA';
  }
  return '${m['loadId']}: 90% at ${m['rise90Seconds'] == null ? 'unknown' : '${fixed((m['rise90Seconds'] as num) * 1000)} ms'} · ${m['settled'] == true ? 'settled' : 'unresolved'}';
}

class _WavePainter extends CustomPainter {
  const _WavePainter(this.times, this.signal, this.index);
  final List<double> times;
  final WaveSignal signal;
  final int index;

  @override
  void paint(Canvas canvas, Size size) {
    final rect =
        Rect.fromLTWH(50, 14, math.max(1, size.width - 62), size.height - 44);
    final extent = [
      0.0,
      ...signal.low.take(times.length),
      ...signal.high.take(times.length),
      ...signal.limits.values
    ];
    var low = extent.reduce(math.min), high = extent.reduce(math.max);
    final padding = math.max(0.1, (high - low) * 0.12);
    low -= padding;
    high += padding;
    double y(double value) =>
        rect.bottom - (value - low) / (high - low) * rect.height;
    double x(int i) =>
        rect.left +
        (times[i] - times.first) / (times.last - times.first) * rect.width;
    final grid = Paint()
      ..color = KColors.borderStrong
      ..strokeWidth = 0.5;
    void label(String text, Offset point) {
      final painter = TextPainter(
          text: TextSpan(
              text: text,
              style: const TextStyle(
                  color: KColors.muted,
                  fontSize: 11.5,
                  fontFamily: 'GeistMono')),
          textDirection: TextDirection.ltr)
        ..layout();
      painter.paint(canvas, point);
    }

    for (var i = 0; i <= 4; i++) {
      final dy = rect.top + i / 4 * rect.height;
      canvas.drawLine(Offset(rect.left, dy), Offset(rect.right, dy), grid);
      label(
          (high - (high - low) * i / 4).toStringAsFixed(1), Offset(0, dy - 6));
    }
    label(signal.unit, const Offset(0, 0));
    label('0 ms', Offset(rect.left, rect.bottom + 10));
    label('${(times.last * 1000).toStringAsFixed(0)} ms',
        Offset(rect.right - 45, rect.bottom + 10));
    canvas.save();
    canvas.clipRect(rect);
    for (final limit in signal.limits.values) {
      for (double dx = rect.left; dx < rect.right; dx += 10) {
        canvas.drawLine(
            Offset(dx, y(limit)),
            Offset(math.min(dx + 5, rect.right), y(limit)),
            Paint()
              ..color = KColors.warn
              ..strokeWidth = 1);
      }
    }
    final band = Path()..moveTo(x(0), y(signal.low[0]));
    for (var i = 1; i <= index; i++) {
      band.lineTo(x(i), y(signal.low[i]));
    }
    for (var i = index; i >= 0; i--) {
      band.lineTo(x(i), y(signal.high[i]));
    }
    band.close();
    canvas.drawPath(
        band, Paint()..color = KColors.actionInk.withValues(alpha: 0.18));
    final line = Path()..moveTo(x(0), y(signal.values[0]));
    for (var i = 1; i <= index; i++) {
      line.lineTo(x(i), y(signal.values[i]));
    }
    canvas.drawPath(
        line,
        Paint()
          ..color = KColors.actionInk
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2);
    canvas.drawLine(Offset(x(index), rect.top), Offset(x(index), rect.bottom),
        Paint()..color = KColors.accent);
    canvas.drawCircle(Offset(x(index), y(signal.values[index])), 4,
        Paint()..color = KColors.accent);
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant _WavePainter old) =>
      old.index != index || old.signal != signal || old.times != times;
}
