import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/widgets.dart';
import 'common.dart';

/// Shared building blocks for the engineering stages (interfaces and
/// generation). Everything here is presentation only; data and commands stay
/// with the stage that uses it.

Color toneColor(String tone) => switch (tone) {
      'pass' => KColors.pass,
      'fail' => KColors.fail,
      'accent' => KColors.accent,
      'unknown' => KColors.unknown,
      _ => KColors.faint,
    };

IconData toneIcon(String tone) => switch (tone) {
      'pass' => Icons.check,
      'fail' => Icons.close,
      'accent' => Icons.sync,
      _ => Icons.remove,
    };

/// Wraps [children] into a responsive grid without intrinsic measurement.
class EngineeringGrid extends StatelessWidget {
  const EngineeringGrid(
      {super.key,
      required this.children,
      this.minWidth = 280,
      this.gap = 14,
      this.maxColumns = 4});
  final List<Widget> children;
  final double minWidth, gap;
  final int maxColumns;
  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final cols = ((c.maxWidth + gap) / (minWidth + gap))
            .floor()
            .clamp(1, math.max(1, math.min(maxColumns, children.length)));
        final width = (c.maxWidth - gap * (cols - 1)) / cols;
        return Wrap(spacing: gap, runSpacing: gap, children: [
          for (var i = 0; i < children.length; i++)
            SizedBox(
                width: width,
                child: KReveal(
                    delay: Duration(milliseconds: 40 * i.clamp(0, 8)),
                    child: children[i]))
        ]);
      });
}

/// Stage title block: large title, one sentence, and the stage's main action.
class StageHeader extends StatelessWidget {
  const StageHeader(
      {super.key,
      required this.title,
      required this.description,
      this.action,
      this.status});
  final String title, description;
  final Widget? action;
  final Widget? status;
  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final copy = pColumn([
          if (status != null) status!,
          Semantics(
              header: true,
              child: Text(title,
                  style: Theme.of(context)
                      .textTheme
                      .headlineLarge
                      ?.copyWith(fontSize: c.maxWidth < 600 ? 22 : 26))),
          ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 560),
              child: KMuted(description, size: 14)),
        ], gap: 10);
        if (action == null) return copy;
        return c.maxWidth > 700
            ? Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
                Expanded(child: copy),
                const SizedBox(width: 24),
                action!
              ])
            : pColumn([copy, action!], gap: 18);
      });
}

/// A band of equal segments, one per item, coloured by state. While work is
/// running a highlight sweeps across so the bar reads as alive.
class SegmentBar extends StatefulWidget {
  const SegmentBar({super.key, required this.tones, this.sweeping = false});
  final List<String> tones;
  final bool sweeping;
  @override
  State<SegmentBar> createState() => _SegmentBarState();
}

class _SegmentBarState extends State<SegmentBar>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 1600));
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _sync();
  }

  @override
  void didUpdateWidget(SegmentBar old) {
    super.didUpdateWidget(old);
    _sync();
  }

  void _sync() {
    final run = widget.sweeping && !MediaQuery.disableAnimationsOf(context);
    if (run && !_c.isAnimating) {
      _c.repeat();
    } else if (!run && _c.isAnimating) {
      _c.stop();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => RepaintBoundary(
      child: SizedBox(
          height: 8,
          width: double.infinity,
          child: CustomPaint(
              painter: _SegmentPainter(widget.tones, _c, widget.sweeping))));
}

class _SegmentPainter extends CustomPainter {
  _SegmentPainter(this.tones, this.anim, this.sweeping) : super(repaint: anim);
  final List<String> tones;
  final Animation<double> anim;
  final bool sweeping;
  @override
  void paint(Canvas canvas, Size size) {
    if (tones.isEmpty) return;
    const gap = 4.0;
    final w = (size.width - gap * (tones.length - 1)) / tones.length;
    for (var i = 0; i < tones.length; i++) {
      final x = i * (w + gap);
      final rect =
          RRect.fromLTRBR(x, 0, x + w, size.height, const Radius.circular(3));
      var color = tones[i] == '' || tones[i] == 'unknown'
          ? KColors.surface3
          : toneColor(tones[i]);
      if (tones[i] == 'accent' && sweeping) {
        final phase = (anim.value * (tones.length + 2)) - 1;
        final d = (i - phase).abs().clamp(0, 2);
        color = Color.lerp(KColors.accent, Colors.white, (1 - d / 2) * .55)!;
      }
      canvas.drawRRect(rect, Paint()..color = color);
    }
  }

  @override
  bool shouldRepaint(_SegmentPainter old) =>
      old.tones != tones || old.sweeping != sweeping;
}

/// Circular progress that eases between values, with a centre label.
class ProgressRing extends StatelessWidget {
  const ProgressRing(
      {super.key,
      required this.value,
      this.size = 48,
      this.running = false,
      this.child});
  final double value;
  final double size;
  final bool running;
  final Widget? child;
  @override
  Widget build(BuildContext context) {
    final done = value >= 100;
    return TweenAnimationBuilder<double>(
        tween: Tween(end: (value / 100).clamp(0, 1).toDouble()),
        duration: MediaQuery.disableAnimationsOf(context)
            ? Duration.zero
            : KMotion.slow,
        curve: KMotion.out,
        builder: (context, v, _) => SizedBox(
            width: size,
            height: size,
            child: CustomPaint(
                painter: _RingPainter(
                    v, done ? KColors.pass : KColors.accent, running && !done),
                child: Center(child: child))));
  }
}

class _RingPainter extends CustomPainter {
  _RingPainter(this.v, this.color, this.glow);
  final double v;
  final Color color;
  final bool glow;
  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 3.0;
    final rect = Rect.fromLTWH(
        stroke / 2, stroke / 2, size.width - stroke, size.height - stroke);
    canvas.drawArc(
        rect,
        0,
        math.pi * 2,
        false,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = stroke
          ..color = KColors.surface3);
    if (v <= 0) return;
    final arc = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.round
      ..color = color;
    if (glow) {
      canvas.drawArc(
          rect,
          -math.pi / 2,
          math.pi * 2 * v,
          false,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = stroke + 4
            ..strokeCap = StrokeCap.round
            ..color = color.withValues(alpha: .18)
            ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 5));
    }
    canvas.drawArc(rect, -math.pi / 2, math.pi * 2 * v, false, arc);
  }

  @override
  bool shouldRepaint(_RingPainter old) =>
      old.v != v || old.color != color || old.glow != glow;
}

/// One interface contract: name, the agreed value, and its solver state.
class ContractTile extends StatelessWidget {
  const ContractTile(
      {super.key,
      required this.name,
      required this.value,
      required this.state,
      this.owner,
      this.footer});
  final String name;
  final String value;
  final String state;
  final String? owner;
  final Widget? footer;
  @override
  Widget build(BuildContext context) {
    final tone = evidenceTone(state);
    final color = toneColor(tone);
    return Container(
        padding: const EdgeInsets.fromLTRB(16, 14, 14, 14),
        decoration: BoxDecoration(
            color: KColors.surface,
            borderRadius: BorderRadius.circular(KRadius.card - 4),
            border: Border.all(color: KColors.border)),
        child: pColumn([
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
                child: Text(name,
                    style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w500,
                        height: 1.4))),
            const SizedBox(width: 10),
            Tooltip(
                message: state,
                child: Container(
                    width: 22,
                    height: 22,
                    decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: color.withValues(alpha: .14)),
                    child: Icon(toneIcon(tone), size: 13, color: color))),
          ]),
          Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                  color: KColors.bg,
                  borderRadius: BorderRadius.circular(KRadius.control - 2),
                  border: Border.all(color: KColors.border)),
              child: Text(value,
                  style: const TextStyle(
                      fontFamily: 'GeistMono',
                      fontSize: 12,
                      height: 1.5,
                      color: KColors.text))),
          if (owner != null) KBadge(owner!),
          if (footer != null) footer!,
        ], gap: 10));
  }
}

/// Section label used between groups of tiles.
class GroupLabel extends StatelessWidget {
  const GroupLabel(this.title, {super.key, this.count, this.trailing});
  final String title;
  final int? count;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) => Row(children: [
        Flexible(
            child: Text(title, style: Theme.of(context).textTheme.titleMedium)),
        if (count != null) ...[
          const SizedBox(width: 8),
          Text('$count',
              style: const TextStyle(
                  fontFamily: 'GeistMono', fontSize: 12, color: KColors.faint))
        ],
        if (trailing != null)
          Expanded(
              child: Align(alignment: Alignment.centerRight, child: trailing!))
        else
          const Spacer(),
      ]);
}

/// Checklist of criteria that must hold, rendered as quiet rows in two
/// columns when there is room.
class CriteriaList extends StatelessWidget {
  const CriteriaList({super.key, required this.items, this.satisfied = false});
  final List<String> items;
  final bool satisfied;

  Widget _row(String item) =>
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        AnimatedContainer(
            duration: KMotion.base,
            curve: KMotion.out,
            width: 18,
            height: 18,
            margin: const EdgeInsets.only(top: 1),
            decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: satisfied
                    ? KColors.pass.withValues(alpha: .14)
                    : Colors.transparent,
                border: Border.all(
                    color: satisfied ? KColors.pass : KColors.borderStrong)),
            child: satisfied
                ? const Icon(Icons.check, size: 11, color: KColors.pass)
                : null),
        const SizedBox(width: 12),
        Expanded(
            child: Text(item,
                style: const TextStyle(
                    fontSize: 13, height: 1.5, color: KColors.muted)))
      ]);

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        if (c.maxWidth < 720 || items.length < 4) {
          return pColumn([for (final item in items) _row(item)], gap: 12);
        }
        final half = (items.length / 2).ceil();
        return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
              child: pColumn([for (final item in items.take(half)) _row(item)],
                  gap: 12)),
          const SizedBox(width: 32),
          Expanded(
              child: pColumn([for (final item in items.skip(half)) _row(item)],
                  gap: 12)),
        ]);
      });
}

/// Scrolling monospaced log with line numbers; sticks to the newest line.
class TerminalPanel extends StatefulWidget {
  const TerminalPanel(
      {super.key,
      required this.lines,
      this.live = false,
      this.maxHeight = 280});
  final List<String> lines;
  final bool live;
  final double maxHeight;
  @override
  State<TerminalPanel> createState() => _TerminalPanelState();
}

class _TerminalPanelState extends State<TerminalPanel> {
  final _scroll = ScrollController();
  @override
  void didUpdateWidget(TerminalPanel old) {
    super.didUpdateWidget(old);
    if (old.lines.length != widget.lines.length) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) {
          _scroll.animateTo(_scroll.position.maxScrollExtent,
              duration: KMotion.base, curve: KMotion.out);
        }
      });
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Color _color(String line) {
    final l = line.toLowerCase();
    if (l.contains('fail') || l.contains('error')) return KColors.fail;
    if (l.contains('pass') || l.contains(' ok') || l.contains('done')) {
      return KColors.pass;
    }
    return KColors.muted;
  }

  @override
  Widget build(BuildContext context) => Container(
      width: double.infinity,
      constraints: BoxConstraints(maxHeight: widget.maxHeight),
      decoration: BoxDecoration(
          color: KColors.bg,
          borderRadius: BorderRadius.circular(KRadius.card - 4),
          border: Border.all(color: KColors.border)),
      child: ListView.builder(
          controller: _scroll,
          shrinkWrap: true,
          padding: const EdgeInsets.all(16),
          itemCount: widget.lines.length,
          itemBuilder: (context, i) => Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child:
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                SizedBox(
                    width: 34,
                    child: Text((i + 1).toString().padLeft(3, '0'),
                        style: const TextStyle(
                            fontFamily: 'GeistMono',
                            fontSize: 12,
                            height: 1.6,
                            color: KColors.faint))),
                Expanded(
                    child: Text(widget.lines[i],
                        style: TextStyle(
                            fontFamily: 'GeistMono',
                            fontSize: 12,
                            height: 1.6,
                            color: _color(widget.lines[i]))))
              ]))));
}

/// Read-only source with a line-number gutter and a copy action.
class CodePanel extends StatefulWidget {
  const CodePanel({super.key, required this.code, this.maxHeight = 380});
  final String code;
  final double maxHeight;
  @override
  State<CodePanel> createState() => _CodePanelState();
}

class _CodePanelState extends State<CodePanel> {
  bool _copied = false;
  static const _style = TextStyle(
      fontFamily: 'GeistMono', fontSize: 12, height: 1.65, color: KColors.text);
  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: widget.code));
    if (!mounted) return;
    setState(() => _copied = true);
    await Future<void>.delayed(const Duration(seconds: 2));
    if (mounted) setState(() => _copied = false);
  }

  @override
  Widget build(BuildContext context) {
    final lines = '\n'.allMatches(widget.code).length + 1;
    return Container(
        width: double.infinity,
        decoration: BoxDecoration(
            color: KColors.bg,
            borderRadius: BorderRadius.circular(KRadius.card - 4),
            border: Border.all(color: KColors.border)),
        child: Stack(children: [
          ConstrainedBox(
              constraints: BoxConstraints(maxHeight: widget.maxHeight),
              child: SingleChildScrollView(
                  child: SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      padding: const EdgeInsets.all(16),
                      child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                                [for (var i = 1; i <= lines; i++) '$i']
                                    .join('\n'),
                                textAlign: TextAlign.right,
                                style: _style.copyWith(color: KColors.faint)),
                            const SizedBox(width: 18),
                            SelectableText(widget.code, style: _style),
                            const SizedBox(width: 48),
                          ])))),
          Positioned(
              top: 6,
              right: 6,
              child: IconButton(
                  tooltip: _copied ? 'Copied' : 'Copy source',
                  visualDensity: VisualDensity.compact,
                  onPressed: _copy,
                  icon: Icon(_copied ? Icons.check : Icons.copy_outlined,
                      size: 15, color: _copied ? KColors.pass : KColors.faint)))
        ]));
  }
}

/// Pill tabs; selected state is a raised dark fill with an accent underline.
class PillTabs extends StatelessWidget {
  const PillTabs(
      {super.key,
      required this.labels,
      required this.selected,
      required this.onSelected});
  final List<String> labels;
  final String? selected;
  final ValueChanged<String> onSelected;
  @override
  Widget build(BuildContext context) =>
      Wrap(spacing: 6, runSpacing: 6, children: [
        for (final label in labels)
          Semantics(
              button: true,
              selected: label == selected,
              label: label,
              child: ExcludeSemantics(
                  child: KTap(
                      flat: true,
                      radius: KRadius.pill,
                      onTap: () => onSelected(label),
                      builder: (context, hovered) => AnimatedContainer(
                          duration: KMotion.base,
                          curve: KMotion.out,
                          padding: const EdgeInsets.symmetric(
                              horizontal: 14, vertical: 8),
                          decoration: BoxDecoration(
                              color: label == selected
                                  ? KColors.surface3
                                  : hovered
                                      ? KColors.surface2
                                      : Colors.transparent,
                              borderRadius: BorderRadius.circular(KRadius.pill),
                              border: Border.all(
                                  color: label == selected
                                      ? KColors.accent.withValues(alpha: .6)
                                      : KColors.border)),
                          child: Text(label,
                              style: TextStyle(
                                  fontSize: 12,
                                  fontWeight: label == selected
                                      ? FontWeight.w500
                                      : FontWeight.w400,
                                  color: label == selected
                                      ? KColors.text
                                      : KColors.muted))))))
      ]);
}

/// A titled panel for stage sections. Lighter than KCard: no gradient, so
/// stacked sections do not turn into a wall of identical boxes.
class StageSection extends StatelessWidget {
  const StageSection(
      {super.key, required this.title, this.trailing, required this.child});
  final String title;
  final Widget? trailing;
  final Widget child;
  @override
  Widget build(BuildContext context) => pColumn([
        GroupLabel(title, trailing: trailing),
        child,
      ], gap: 16);
}

/// The shared spec at the top of a fan of lanes: one node, one bracket, one
/// tick per lane. Hidden when the lanes wrap onto several rows.
class SpecFan extends StatelessWidget {
  const SpecFan(
      {super.key,
      required this.label,
      required this.count,
      this.minWidth = 250});
  final String label;
  final int count;
  final double minWidth;
  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        const gap = 14.0;
        final cols =
            ((c.maxWidth + gap) / (minWidth + gap)).floor().clamp(1, 4);
        final node = Center(
            child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                decoration: BoxDecoration(
                    color: KColors.surface2,
                    borderRadius: BorderRadius.circular(KRadius.pill),
                    border: Border.all(color: KColors.borderStrong)),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.hub_outlined,
                      size: 14, color: KColors.muted),
                  const SizedBox(width: 8),
                  Text(label,
                      style: const TextStyle(
                          fontSize: 12, fontWeight: FontWeight.w500)),
                ])));
        if (cols < count || count < 2) return node;
        return Column(children: [
          node,
          SizedBox(
              height: 28,
              width: double.infinity,
              child: CustomPaint(painter: _FanPainter(count, gap))),
        ]);
      });
}

class _FanPainter extends CustomPainter {
  const _FanPainter(this.count, this.gap);
  final int count;
  final double gap;
  @override
  void paint(Canvas canvas, Size size) {
    final w = (size.width - gap * (count - 1)) / count;
    final xs = [for (var i = 0; i < count; i++) i * (w + gap) + w / 2];
    final paint = Paint()
      ..color = KColors.borderStrong
      ..strokeWidth = 1
      ..style = PaintingStyle.stroke;
    final mid = size.width / 2;
    final path = Path()
      ..moveTo(mid, 0)
      ..lineTo(mid, 12);
    path.moveTo(xs.first, 12);
    path.lineTo(xs.last, 12);
    for (final x in xs) {
      path.moveTo(x, 12);
      path.lineTo(x, size.height);
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(_FanPainter old) => old.count != count;
}

/// The solver's headline: how many shared contracts hold, large, beside the
/// segment bar that shows each one's state.
class SolverBand extends StatelessWidget {
  const SolverBand(
      {super.key,
      required this.passed,
      required this.total,
      required this.tones,
      this.running = false,
      this.label = 'contracts satisfied'});
  final int passed, total;
  final List<String> tones;
  final bool running;
  final String label;
  @override
  Widget build(BuildContext context) => KPanel(
      padding: const EdgeInsets.fromLTRB(24, 22, 24, 22),
      child: LayoutBuilder(builder: (context, c) {
        final readout = Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            mainAxisSize:
                c.maxWidth >= 640 ? MainAxisSize.min : MainAxisSize.max,
            children: [
              TweenAnimationBuilder<double>(
                  tween: Tween(end: passed.toDouble()),
                  duration: MediaQuery.disableAnimationsOf(context)
                      ? Duration.zero
                      : KMotion.slow,
                  curve: KMotion.out,
                  builder: (context, v, _) => Text('${v.round()}',
                      style: KType.numeral.copyWith(
                          fontSize: 40, letterSpacing: -1.6, height: 1))),
              Padding(
                  padding: const EdgeInsets.only(bottom: 5, left: 8),
                  child: Text('/ $total',
                      style: KType.mono
                          .copyWith(fontSize: 20, color: KColors.faint))),
              const SizedBox(width: 18),
              Flexible(
                  child: Padding(
                      padding: const EdgeInsets.only(bottom: 7),
                      child: KMuted(label, size: 13))),
            ]);
        final bar = SegmentBar(tones: tones, sweeping: running);
        return c.maxWidth >= 640
            ? Row(children: [
                readout,
                const SizedBox(width: 40),
                Expanded(child: bar)
              ])
            : kColumn([readout, bar], gap: 20);
      }));
}

typedef ContractItem = ({
  String name,
  String value,
  String state,
  String? group,
  Widget? footer
});

/// Contracts as a quiet list: state glyph, name, agreed value. Sits beside the
/// viewport so each contract can be read against the geometry it constrains.
class ContractExplorer extends StatelessWidget {
  const ContractExplorer(
      {super.key,
      required this.title,
      required this.items,
      required this.bounded,
      this.trailing,
      this.emptyText =
          'Run the solver to draft the interface spec. Every contract appears here with its owner and result.'});
  final String title;
  final List<ContractItem> items;
  final bool bounded;
  final Widget? trailing;
  final String emptyText;

  Widget _row(ContractItem item) {
    final tone = evidenceTone(item.state);
    final color = toneColor(tone);
    return Padding(
        padding: const EdgeInsets.fromLTRB(8, 7, 8, 7),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Tooltip(
              message: item.state,
              child: Container(
                  width: 20,
                  height: 20,
                  margin: const EdgeInsets.only(top: 1),
                  decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: color.withValues(alpha: .14)),
                  child: Icon(toneIcon(tone), size: 12, color: color))),
          const SizedBox(width: 11),
          Expanded(
              child: pColumn([
            Text(item.name,
                style: const TextStyle(
                    fontSize: 12.5, fontWeight: FontWeight.w500, height: 1.35)),
            Text(item.value,
                maxLines: 4,
                overflow: TextOverflow.ellipsis,
                style: KType.monoSmall.copyWith(fontSize: 12, height: 1.55)),
            if (item.footer != null) item.footer!,
          ], gap: 3)),
        ]));
  }

  @override
  Widget build(BuildContext context) {
    final groups = <String, List<ContractItem>>{};
    for (final item in items) {
      groups.putIfAbsent(item.group ?? '', () => []).add(item);
    }
    final rows = <Widget>[
      for (final entry in groups.entries) ...[
        if (entry.key.isNotEmpty)
          Padding(
              padding: const EdgeInsets.fromLTRB(8, 10, 8, 4),
              child: Row(children: [
                Expanded(child: Text(entry.key, style: KType.label)),
                Text('${entry.value.length}', style: KType.monoSmall),
              ])),
        for (final item in entry.value) _row(item),
      ]
    ];
    final header = Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
        child: Row(children: [
          Flexible(
              child: Text(title,
                  style: const TextStyle(
                      fontSize: 13, fontWeight: FontWeight.w600))),
          const SizedBox(width: 8),
          Text('${items.length}', style: KType.monoSmall),
          if (trailing != null) ...[const Spacer(), trailing!],
        ]));
    final body = items.isEmpty
        ? Padding(
            padding: const EdgeInsets.all(18),
            child: KMuted(emptyText, size: 12.5))
        : Padding(
            padding: const EdgeInsets.fromLTRB(8, 4, 8, 12),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start, children: rows));
    return KPanel(
        padding: EdgeInsets.zero,
        child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: bounded ? MainAxisSize.max : MainAxisSize.min,
            children: [
              header,
              const Divider(height: 1, color: KColors.hairline),
              if (bounded)
                Expanded(child: SingleChildScrollView(child: body))
              else
                body,
            ]));
  }
}
