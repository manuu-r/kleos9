import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'theme.dart';
export 'theme.dart';

bool _still(BuildContext context) => MediaQuery.disableAnimationsOf(context);

/// Press physics for any tappable surface: settles to 0.985 on press. [flat]
/// rows (tabs, list rows) only change their fill. There is no hover lift.
class KTap extends StatefulWidget {
  const KTap(
      {super.key,
      required this.onTap,
      required this.builder,
      this.radius = KRadius.card,
      this.flat = false,
      this.semanticLabel});
  final VoidCallback? onTap;
  final Widget Function(BuildContext context, bool hovered) builder;
  final double radius;
  final bool flat;
  final String? semanticLabel;
  @override
  State<KTap> createState() => _KTapState();
}

class _KTapState extends State<KTap> {
  bool _hover = false, _down = false;
  @override
  Widget build(BuildContext context) {
    final press = _down && !_still(context) && !widget.flat ? .985 : 1.0;
    return Semantics(
        button: true,
        label: widget.semanticLabel,
        child: MouseRegion(
            cursor: widget.onTap == null
                ? MouseCursor.defer
                : SystemMouseCursors.click,
            onEnter: (_) => setState(() => _hover = true),
            onExit: (_) => setState(() => _hover = false),
            child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: widget.onTap,
                onTapDown: (_) => setState(() => _down = true),
                onTapUp: (_) => setState(() => _down = false),
                onTapCancel: () => setState(() => _down = false),
                child: AnimatedScale(
                    scale: press,
                    duration: KMotion.fast,
                    curve: KMotion.out,
                    child: widget.builder(context, _hover)))));
  }
}

/// A flat panel: one tone darker than the ground, one 1px edge, no shadow.
class KCard extends StatelessWidget {
  const KCard(
      {super.key,
      this.title,
      this.trailing,
      required this.child,
      this.onTap,
      this.tint,
      this.padding});
  final String? title;
  final Widget? trailing;
  final Widget child;
  final VoidCallback? onTap;

  /// Optional flat tone that gives one card in a grid a different voice.
  final Color? tint;
  final EdgeInsetsGeometry? padding;
  Widget _body(BuildContext context, bool hovered) {
    final phone = KSpace.phone(context);
    return AnimatedContainer(
      duration: KMotion.base,
      curve: KMotion.out,
      width: double.infinity,
      padding: padding ?? EdgeInsets.all(phone ? 16 : 20),
      decoration: BoxDecoration(
          color: tint == null
              ? KColors.surface
              : Color.alphaBlend(tint!.withValues(alpha: .06), KColors.surface),
          borderRadius: BorderRadius.circular(KRadius.card),
          border: Border.all(
              color: hovered
                  ? KColors.borderStrong
                  : tint == null
                      ? KColors.border
                      : tint!.withValues(alpha: .28))),
      child: Material(
          type: MaterialType.transparency,
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (title != null || trailing != null) ...[
                  LayoutBuilder(builder: (context, constraints) {
                    final heading = title == null
                        ? null
                        : Semantics(
                            header: true,
                            child: Text(title!,
                                style:
                                    Theme.of(context).textTheme.titleMedium));
                    if (constraints.maxWidth < 420 && trailing != null) {
                      return kColumn([if (heading != null) heading, trailing!],
                          gap: 10);
                    }
                    return Row(children: [
                      if (heading != null) Expanded(child: heading),
                      if (trailing != null) trailing!
                    ]);
                  }),
                  SizedBox(height: phone ? 12 : 16)
                ],
                child
              ])),
    );
  }

  @override
  Widget build(BuildContext context) => RepaintBoundary(
      child: onTap == null
          ? _body(context, false)
          : KTap(onTap: onTap, builder: _body));
}

/// Pill button. Primary is made primary by inversion (white fill, dark ink);
/// secondary is a 1px outline. Touch targets are at least 44px.
class KButton extends StatelessWidget {
  const KButton(
      {super.key,
      required this.label,
      this.onPressed,
      this.primary = false,
      this.icon,
      this.compact = false,
      this.loading = false,
      this.expand = false});
  final String label;
  final VoidCallback? onPressed;
  final bool primary;
  final IconData? icon;
  final bool compact;

  /// Shows a spinner in place of the icon and ignores taps until it clears.
  final bool loading;

  /// Stretch to the full available width: the main action of a phone form.
  final bool expand;
  @override
  Widget build(BuildContext context) {
    final touch = MediaQuery.sizeOf(context).width < 1200 ||
        defaultTargetPlatform == TargetPlatform.android ||
        defaultTargetPlatform == TargetPlatform.iOS;
    final enabled = onPressed != null && !loading;
    final style = ButtonStyle(
      visualDensity: touch ? VisualDensity.standard : VisualDensity.compact,
      padding: WidgetStatePropertyAll(EdgeInsets.symmetric(
          horizontal: compact ? 14 : 18, vertical: compact ? 8 : 12)),
      minimumSize: WidgetStatePropertyAll(
          Size(44, touch ? (compact ? 40 : 44) : (compact ? 32 : 38))),
      tapTargetSize: MaterialTapTargetSize.padded,
      animationDuration: KMotion.base,
      overlayColor: const WidgetStatePropertyAll(Colors.transparent),
      backgroundColor: WidgetStateProperty.resolveWith((s) => primary
          ? (s.contains(WidgetState.disabled)
              ? KColors.surface3
              : s.contains(WidgetState.hovered)
                  ? const Color(0xffe4e4e7)
                  : KColors.text)
          : (s.contains(WidgetState.hovered)
              ? KColors.surface2
              : Colors.transparent)),
      foregroundColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.disabled)
              ? KColors.faint
              : primary
                  ? KColors.bg
                  : KColors.text),
      side: WidgetStateProperty.resolveWith((s) => BorderSide(
          color: s.contains(WidgetState.focused)
              ? KColors.accent
              : primary
                  ? Colors.transparent
                  : s.contains(WidgetState.hovered)
                      ? KColors.faint
                      : KColors.borderStrong)),
      shape: const WidgetStatePropertyAll(StadiumBorder()),
      textStyle: WidgetStatePropertyAll(TextStyle(
          fontFamily: 'Geist',
          fontSize: touch ? 14 : 13,
          fontWeight: FontWeight.w500)),
    );
    final button = _Pressable(
        enabled: enabled,
        child: TextButton(
            onPressed: enabled ? onPressed : null,
            style: style,
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              if (loading) ...[
                KSpinner(
                    size: 14,
                    color: primary && enabled ? KColors.bg : KColors.muted),
                const SizedBox(width: 8)
              ] else if (icon != null) ...[
                Icon(icon, size: compact ? 15 : 17),
                const SizedBox(width: 8)
              ],
              Flexible(
                  child: Text(label,
                      maxLines: 1,
                      softWrap: false,
                      overflow: TextOverflow.ellipsis))
            ])));
    return expand ? SizedBox(width: double.infinity, child: button) : button;
  }
}

/// A pill inside a row or beside a value: pale tint, readable ink, no border.
class KBadge extends StatelessWidget {
  const KBadge(this.text, {super.key, this.tone, this.dot = false});
  final String text;
  final String? tone;

  /// Leading status dot, pulsing, for work that is running right now.
  final bool dot;
  @override
  Widget build(BuildContext context) {
    final color = switch (tone?.toLowerCase()) {
      'pass' => KColors.pass,
      'fail' => KColors.fail,
      'accent' => KColors.text,
      'human' => KColors.human,
      'unknown' => KColors.unknown,
      'warn' => KColors.warn,
      _ => KColors.muted
    };
    return Container(
        padding: EdgeInsets.symmetric(horizontal: dot ? 9 : 10, vertical: 4),
        decoration: BoxDecoration(
            color: color == KColors.text
                ? KColors.surface3
                : color.withValues(alpha: .12),
            borderRadius: BorderRadius.circular(KRadius.pill)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (dot) ...[
            KStatusDot(color: color, live: true),
            const SizedBox(width: 6)
          ],
          Flexible(
              child: Text(text,
                  style: TextStyle(
                      fontFamily: 'Geist',
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      height: 1.25,
                      color: color)))
        ]));
  }
}

/// Screen title: a noun at 24-26px, one grey sentence, actions only if real.
class KSectionHeader extends StatelessWidget {
  const KSectionHeader(
      {super.key, required this.title, this.description, this.trailing});
  final String title;
  final String? description;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, constraints) {
        final phone = constraints.maxWidth < 600;
        final body =
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Semantics(
              header: true,
              child: Text(title,
                  style: Theme.of(context)
                      .textTheme
                      .headlineLarge
                      ?.copyWith(fontSize: phone ? 24 : 26))),
          if (description != null) ...[
            const SizedBox(height: 8),
            ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 620),
                child: Text(description!,
                    style: const TextStyle(
                        color: KColors.muted, fontSize: 14, height: 1.5)))
          ]
        ]);
        return constraints.maxWidth > 650
            ? Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(child: body),
                if (trailing != null) ...[const SizedBox(width: 24), trailing!]
              ])
            : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                body,
                if (trailing != null) ...[const SizedBox(height: 14), trailing!]
              ]);
      });
}

/// Thin determinate bar at its true ratio; the fill is flat ink.
class KProgress extends StatelessWidget {
  const KProgress(this.value, {super.key});
  final double value;
  @override
  Widget build(BuildContext context) => TweenAnimationBuilder<double>(
      tween: Tween(end: (value / 100).clamp(0, 1).toDouble()),
      duration: _still(context) ? Duration.zero : KMotion.slow,
      curve: KMotion.out,
      builder: (context, v, _) => Container(
          height: 3,
          width: double.infinity,
          decoration: BoxDecoration(
              color: KColors.surface3,
              borderRadius: BorderRadius.circular(KRadius.pill)),
          alignment: Alignment.centerLeft,
          child: FractionallySizedBox(
              widthFactor: v,
              // A DecoratedBox with no child collapses to height 0 unless the
              // height is pinned; without this the fill never painted.
              heightFactor: 1,
              child: DecoratedBox(
                  decoration: BoxDecoration(
                      color: KColors.text,
                      borderRadius: BorderRadius.circular(KRadius.pill))))));
}

class KResponsive extends StatelessWidget {
  const KResponsive({super.key, required this.children, this.columns = 2});
  final List<Widget> children;
  final int columns;
  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, constraints) {
        final count = constraints.maxWidth < 650
            ? 1
            : constraints.maxWidth < 950
                ? columns.clamp(1, 2)
                : columns;
        final gap = constraints.maxWidth < 650 ? 12.0 : 16.0;
        final width = (constraints.maxWidth - (count - 1) * gap) / count;
        return Wrap(
            spacing: gap,
            runSpacing: gap,
            children:
                children.map((c) => SizedBox(width: width, child: c)).toList());
      });
}

/// Table on desktop; on a phone, a list of rows: the first cell is the
/// identity, every other cell keeps its header as a grey caption under it.
class KTable extends StatelessWidget {
  const KTable({super.key, required this.headers, required this.rows});
  final List<String> headers;
  final List<List<Widget>> rows;
  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, constraints) {
        if (constraints.maxWidth < 600) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (var row = 0; row < rows.length; row++) ...[
                if (row > 0) const Divider(height: 1, color: KColors.hairline),
                Padding(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    child: kColumn([
                      for (var cell = 0; cell < rows[row].length; cell++)
                        SizedBox(
                          width: double.infinity,
                          child: kColumn([
                            if (cell > 0 &&
                                cell < headers.length &&
                                headers[cell].isNotEmpty)
                              KMuted(headers[cell], size: 12),
                            rows[row][cell],
                          ], gap: 2),
                        ),
                    ], gap: 10)),
              ],
            ],
          );
        }
        return SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: ConstrainedBox(
                constraints: BoxConstraints(minWidth: constraints.maxWidth),
                child: DataTable(
                    headingRowHeight: headers.isEmpty ? 0 : 40,
                    dataRowMinHeight: 48,
                    dataRowMaxHeight: 100,
                    horizontalMargin: 10,
                    columnSpacing: 24,
                    dividerThickness: 1,
                    columns: headers
                        .map((h) => DataColumn(
                            label: Text(h,
                                style: const TextStyle(
                                    fontSize: 12, color: KColors.muted))))
                        .toList(),
                    rows: rows
                        .map((r) =>
                            DataRow(cells: r.map((c) => DataCell(c)).toList()))
                        .toList())));
      });
}

/// Fades its child in once. No travel and no stagger: content simply arrives.
/// Plays on first build only; later rebuilds from polling never replay it.
class KReveal extends StatefulWidget {
  const KReveal({super.key, required this.child, this.delay = Duration.zero});
  final Widget child;
  final Duration delay;
  @override
  State<KReveal> createState() => _KRevealState();
}

class _KRevealState extends State<KReveal> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: KMotion.base);
  late final Animation<double> _t =
      CurvedAnimation(parent: _c, curve: Curves.easeOut);
  bool _started = false;
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    if (_still(context)) {
      _c.value = 1;
    } else if (widget.delay == Duration.zero) {
      _c.forward();
    } else {
      Future<void>.delayed(widget.delay, () {
        if (mounted) _c.forward();
      });
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      FadeTransition(opacity: _t, child: widget.child);
}

class KPage extends StatelessWidget {
  const KPage({super.key, required this.children, this.gap = 24});
  final List<Widget> children;
  final double gap;
  @override
  Widget build(BuildContext context) {
    final g = KSpace.phone(context) ? math.min(gap, 20.0) : gap;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      for (var i = 0; i < children.length; i++) ...[
        if (i > 0) SizedBox(height: g),
        KReveal(
            delay: Duration(milliseconds: 20 * i.clamp(0, 4)),
            child: children[i])
      ]
    ]);
  }
}

/// Drafting-table surface for engineering viewports: a faint 32px grid on a
/// flat tone. No bloom, no vignette.
class KGridBackdrop extends StatelessWidget {
  const KGridBackdrop({super.key, this.child, this.radius = KRadius.card});
  final Widget? child;
  final double radius;
  @override
  Widget build(BuildContext context) => RepaintBoundary(
      child: ClipRRect(
          borderRadius: BorderRadius.circular(radius),
          child: CustomPaint(
              painter: const _GridPainter(),
              child: DecoratedBox(
                  decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(radius),
                      border: Border.all(color: KColors.border)),
                  child: child))));
}

class _GridPainter extends CustomPainter {
  const _GridPainter();
  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(Offset.zero & size, Paint()..color = KColors.surface);
    final line = Paint()
      ..color = Colors.white.withValues(alpha: .03)
      ..strokeWidth = 1;
    for (var x = 0.0; x < size.width; x += 32) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), line);
    }
    for (var y = 0.0; y < size.height; y += 32) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), line);
    }
  }

  @override
  bool shouldRepaint(_GridPainter old) => false;
}

class KMuted extends StatelessWidget {
  const KMuted(this.text, {super.key, this.size = 12});
  final String text;
  final double size;
  @override
  Widget build(BuildContext context) => Text(text,
      style: TextStyle(color: KColors.muted, fontSize: size, height: 1.5));
}

class KCode extends StatelessWidget {
  const KCode(this.text, {super.key});
  final String text;
  @override
  Widget build(BuildContext context) => SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: SelectableText(text,
          style: const TextStyle(
              fontFamily: 'GeistMono',
              fontSize: 12,
              height: 1.7,
              color: KColors.muted)));
}

Widget kColumn(List<Widget> children, {double gap = 12}) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) SizedBox(height: gap),
            children[i]
          ]
        ]);
Widget kRow(List<Widget> children, {double gap = 8}) => Wrap(
    spacing: gap,
    runSpacing: gap,
    crossAxisAlignment: WrapCrossAlignment.center,
    children: children);

/// Headline figures inside one panel, split by hairlines: label over value,
/// no icons, no sparklines. Two columns on a phone; an odd last cell spans
/// the full width instead of leaving half a row empty.
class KStats extends StatelessWidget {
  const KStats({super.key, required this.items});
  final List<({String label, String value, String? sub})> items;
  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final cols = c.maxWidth < 560
            ? 2
            : c.maxWidth < 900
                ? items.length.clamp(1, 2)
                : items.length.clamp(1, 4);
        final rows = <List<({String label, String value, String? sub})>>[];
        for (var i = 0; i < items.length; i += cols) {
          rows.add(items.sublist(i, (i + cols).clamp(0, items.length)));
        }
        final pad = c.maxWidth < 560
            ? const EdgeInsets.fromLTRB(16, 14, 12, 14)
            : const EdgeInsets.fromLTRB(20, 18, 16, 18);
        return KPanel(
            padding: EdgeInsets.zero,
            child: Column(children: [
              for (var r = 0; r < rows.length; r++) ...[
                if (r > 0) const Divider(height: 1, color: KColors.hairline),
                IntrinsicHeight(
                    child: Row(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                      for (var i = 0; i < rows[r].length; i++) ...[
                        if (i > 0)
                          const VerticalDivider(
                              width: 1, color: KColors.hairline),
                        Expanded(
                            child: Padding(
                                padding: pad,
                                child: kColumn([
                                  KMuted(rows[r][i].label, size: 12.5),
                                  Text(rows[r][i].value,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: KType.numeral.copyWith(
                                          fontSize: rows[r][i].value.length > 9
                                              ? 17
                                              : 24)),
                                  if (rows[r][i].sub != null)
                                    KMuted(rows[r][i].sub!, size: 12)
                                ], gap: 6)))
                      ],
                      // A short last row keeps its cells on the same grid.
                      if (rows[r].length > 1 && rows[r].length < cols)
                        for (var i = rows[r].length; i < cols; i++) ...[
                          const VerticalDivider(
                              width: 1, color: KColors.hairline),
                          const Expanded(child: SizedBox.shrink())
                        ]
                    ])),
              ]
            ]));
      });
}

/// A flat bordered surface for regions that group content.
class KPanel extends StatelessWidget {
  const KPanel(
      {super.key,
      required this.child,
      this.padding = const EdgeInsets.all(16),
      this.radius = KRadius.card,
      this.color = KColors.surface});
  final Widget child;
  final EdgeInsetsGeometry padding;
  final double radius;
  final Color color;
  @override
  Widget build(BuildContext context) => RepaintBoundary(
      child: Container(
          width: double.infinity,
          padding: padding,
          clipBehavior: Clip.antiAlias,
          decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(radius),
              border: Border.all(color: KColors.border)),
          child: Material(type: MaterialType.transparency, child: child)));
}

/// Underline tabs. The selected option takes ink and a 2px copper line exactly
/// as wide as its label; the others stay grey. The row scrolls sideways inside
/// its own frame and never pushes the page. Used for every tab-like switch.
class KSegmented<T> extends StatelessWidget {
  const KSegmented(
      {super.key,
      required this.options,
      required this.selected,
      required this.onSelected,
      this.keyPrefix,
      this.compact = false});
  final List<({T value, String label, IconData? icon})> options;
  final T selected;
  final ValueChanged<T>? onSelected;
  final String? keyPrefix;

  /// Narrow layouts: only the selected option keeps its label next to its icon.
  final bool compact;
  @override
  Widget build(BuildContext context) {
    final touch = KSpace.phone(context) ||
        defaultTargetPlatform == TargetPlatform.android ||
        defaultTargetPlatform == TargetPlatform.iOS;
    return Container(
        decoration: const BoxDecoration(
            border: Border(bottom: BorderSide(color: KColors.border))),
        child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              for (final o in options)
                Semantics(
                    button: true,
                    selected: o.value == selected,
                    label: o.label,
                    child: ExcludeSemantics(
                        child: KTap(
                            key: keyPrefix == null
                                ? null
                                : ValueKey('$keyPrefix${o.value}'),
                            flat: true,
                            radius: KRadius.control,
                            onTap: onSelected == null
                                ? null
                                : () => onSelected!(o.value),
                            builder: (context, hovered) {
                              final on = o.value == selected;
                              final showLabel =
                                  !compact || o.icon == null || on;
                              return Container(
                                  constraints: BoxConstraints(
                                      minHeight: touch ? 44 : 38, minWidth: 44),
                                  padding:
                                      const EdgeInsets.only(right: 20, top: 2),
                                  alignment: Alignment.center,
                                  child: IntrinsicWidth(
                                      child: Column(
                                          mainAxisSize: MainAxisSize.min,
                                          crossAxisAlignment:
                                              CrossAxisAlignment.stretch,
                                          children: [
                                        Row(
                                            mainAxisSize: MainAxisSize.min,
                                            children: [
                                              if (o.icon != null) ...[
                                                Icon(o.icon,
                                                    size: 15,
                                                    color: on
                                                        ? KColors.text
                                                        : hovered
                                                            ? KColors.text
                                                            : KColors.muted),
                                                if (showLabel)
                                                  const SizedBox(width: 7)
                                              ],
                                              if (showLabel)
                                                Text(o.label,
                                                    style: TextStyle(
                                                        fontSize: 13.5,
                                                        fontWeight: on
                                                            ? FontWeight.w500
                                                            : FontWeight.w400,
                                                        color: on
                                                            ? KColors.text
                                                            : hovered
                                                                ? KColors.text
                                                                : KColors
                                                                    .muted)),
                                            ]),
                                        const SizedBox(height: 9),
                                        AnimatedContainer(
                                            duration: KMotion.base,
                                            curve: KMotion.out,
                                            height: 2,
                                            color: on
                                                ? KColors.accent
                                                : Colors.transparent),
                                      ])));
                            }))),
            ])));
  }
}

/// Flat page ground. Kept as a widget so call sites need not change; there is
/// no bloom or counter-light any more.
class KAmbient extends StatelessWidget {
  const KAmbient({super.key, this.child});
  final Widget? child;
  @override
  Widget build(BuildContext context) =>
      ColoredBox(color: KColors.bg, child: child ?? const SizedBox.expand());
}

// ---------------------------------------------------------------------------
// Loading
//
// Loading is the final layout with each value replaced by a flat bar at its
// real size, so nothing shifts when data lands. One shared slow pulse drives
// every bar in a [KPulse]; there is no shimmer gradient. Work in progress uses
// [KLineLoader] (a 2px line sweeping along an edge) and [KSpinner] (inline).
// All of them stand still when the platform asks for reduced motion.
// ---------------------------------------------------------------------------

class _PulseScope extends InheritedWidget {
  const _PulseScope({required this.animation, required super.child});
  final Animation<double> animation;
  static Animation<double>? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<_PulseScope>()?.animation;
  @override
  bool updateShouldNotify(_PulseScope old) => animation != old.animation;
}

/// Drives every [KSkeletonBar] beneath it from a single controller.
class KPulse extends StatefulWidget {
  const KPulse({super.key, required this.child});
  final Widget child;
  @override
  State<KPulse> createState() => _KPulseState();
}

class _KPulseState extends State<KPulse> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 1200));
  late final Animation<double> _t =
      CurvedAnimation(parent: _c, curve: Curves.easeInOut);
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_still(context)) {
      _c.stop();
      _c.value = 1;
    } else if (!_c.isAnimating) {
      _c.repeat(reverse: true);
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Semantics(
      label: 'Loading',
      child: ExcludeSemantics(
          child: RepaintBoundary(
              child: _PulseScope(animation: _t, child: widget.child))));
}

/// A flat placeholder at the real size of the value it stands in for.
class KSkeletonBar extends StatelessWidget {
  const KSkeletonBar(
      {super.key, this.height = 14, this.width, this.radius = 4});
  final double height;
  final double? width;
  final double radius;
  @override
  Widget build(BuildContext context) {
    final bar = Container(
        width: width,
        height: height,
        decoration: BoxDecoration(
            color: KColors.surface3,
            borderRadius: BorderRadius.circular(radius)));
    final pulse = _PulseScope.maybeOf(context);
    if (pulse == null) return bar;
    return FadeTransition(
        opacity: Tween(begin: .45, end: 1.0).animate(pulse), child: bar);
  }
}

/// A list placeholder: [rows] two-line rows on the real 64px pitch.
class KSkeletonRows extends StatelessWidget {
  const KSkeletonRows({super.key, this.rows = 4, this.leading = false});
  final int rows;
  final bool leading;
  @override
  Widget build(BuildContext context) => Column(children: [
        for (var i = 0; i < rows; i++) ...[
          if (i > 0) const Divider(height: 1, color: KColors.hairline),
          SizedBox(
              height: 64,
              child: Row(children: [
                if (leading) ...[
                  const KSkeletonBar(height: 36, width: 36, radius: 8),
                  const SizedBox(width: 12)
                ],
                const Expanded(
                    child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                      KSkeletonBar(height: 15, width: 180),
                      SizedBox(height: 8),
                      KSkeletonBar(height: 12, width: 120),
                    ])),
                const KSkeletonBar(height: 22, width: 56, radius: 11),
              ]))
        ]
      ]);
}

/// Layout-shaped placeholder shown while the first snapshot loads: a title,
/// the composer, and a short list, at the sizes the real screen uses.
class KPageSkeleton extends StatelessWidget {
  const KPageSkeleton({super.key});
  @override
  Widget build(BuildContext context) {
    final gutter = KSpace.gutter(context);
    return KPulse(
        child: Align(
            alignment: Alignment.topCenter,
            child: Padding(
                padding: EdgeInsets.fromLTRB(gutter, 24, gutter, 0),
                child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1136),
                    child: kColumn([
                      const KSkeletonBar(height: 26, width: 160),
                      const KSkeletonBar(height: 14, width: 260),
                      const SizedBox(height: 4),
                      const KSkeletonBar(height: 48, radius: 24),
                      const SizedBox(height: 8),
                      Container(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          decoration: BoxDecoration(
                              color: KColors.surface,
                              border: Border.all(color: KColors.border),
                              borderRadius:
                                  BorderRadius.circular(KRadius.card)),
                          child: const KSkeletonRows(rows: 4)),
                    ], gap: 12)))));
  }
}

/// 14-16px arc spinner for inline work (buttons, row actions).
class KSpinner extends StatefulWidget {
  const KSpinner({super.key, this.size = 16, this.color = KColors.muted});
  final double size;
  final Color color;
  @override
  State<KSpinner> createState() => _KSpinnerState();
}

class _KSpinnerState extends State<KSpinner>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 850));
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_still(context)) {
      _c.stop();
    } else if (!_c.isAnimating) {
      _c.repeat();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Semantics(
      label: 'Working',
      child: ExcludeSemantics(
          child: RepaintBoundary(
              child: RotationTransition(
                  turns: _c,
                  child: CustomPaint(
                      size: Size.square(widget.size),
                      painter: _ArcPainter(widget.color))))));
}

class _ArcPainter extends CustomPainter {
  const _ArcPainter(this.color);
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final stroke = math.max(1.5, size.width / 9);
    final rect = Offset.zero & size;
    final inset = rect.deflate(stroke / 2);
    canvas.drawArc(
        inset,
        0,
        math.pi * 2,
        false,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = stroke
          ..color = color.withValues(alpha: .18));
    canvas.drawArc(
        inset,
        -math.pi / 2,
        math.pi * .55,
        false,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeCap = StrokeCap.round
          ..strokeWidth = stroke
          ..color = color);
  }

  @override
  bool shouldRepaint(_ArcPainter old) => old.color != color;
}

/// A 2px line that sweeps along an edge while data refreshes or work runs.
/// Place it at the top of a screen or a card; it draws nothing when [active]
/// is false, so the layout never shifts.
class KLineLoader extends StatefulWidget {
  const KLineLoader({super.key, this.active = true, this.color});
  final bool active;
  final Color? color;
  @override
  State<KLineLoader> createState() => _KLineLoaderState();
}

class _KLineLoaderState extends State<KLineLoader>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 1300));
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _sync();
  }

  @override
  void didUpdateWidget(covariant KLineLoader old) {
    super.didUpdateWidget(old);
    _sync();
  }

  void _sync() {
    if (widget.active && !_still(context)) {
      if (!_c.isAnimating) _c.repeat();
    } else {
      _c.stop();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.active) return const SizedBox(height: 2);
    return Semantics(
        label: 'Loading',
        child: ExcludeSemantics(
            child: SizedBox(
                height: 2,
                width: double.infinity,
                child: RepaintBoundary(
                    child: CustomPaint(
                        painter: _SweepPainter(
                            _c, widget.color ?? KColors.accent,
                            still: _still(context)))))));
  }
}

class _SweepPainter extends CustomPainter {
  _SweepPainter(this.t, this.color, {required this.still}) : super(repaint: t);
  final Animation<double> t;
  final Color color;
  final bool still;
  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(
        Offset.zero & size, Paint()..color = color.withValues(alpha: .14));
    final len = size.width * .34;
    final p = still ? .33 : Curves.easeInOut.transform(t.value);
    final x = -len + (size.width + len) * p;
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawRect(
        Rect.fromLTWH(x, 0, len, size.height), Paint()..color = color);
    canvas.restore();
  }

  @override
  bool shouldRepaint(_SweepPainter old) =>
      old.color != color || old.still != still;
}

/// 6px status dot. [live] pulses it softly for work that is running now.
class KStatusDot extends StatefulWidget {
  const KStatusDot({super.key, required this.color, this.live = false});
  final Color color;
  final bool live;
  @override
  State<KStatusDot> createState() => _KStatusDotState();
}

class _KStatusDotState extends State<KStatusDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 1100));
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (widget.live && !_still(context)) {
      if (!_c.isAnimating) _c.repeat(reverse: true);
    } else {
      _c.stop();
      _c.value = 1;
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ExcludeSemantics(
      child: FadeTransition(
          opacity: Tween(begin: .35, end: 1.0).animate(_c),
          child: Container(
              width: 6,
              height: 6,
              decoration:
                  BoxDecoration(color: widget.color, shape: BoxShape.circle))));
}

// ---------------------------------------------------------------------------
// Status frames. Notices, errors and empty states stay inside the block they
// describe, in the panel's own tone. Red only ever marks a small icon.
// ---------------------------------------------------------------------------

/// A quiet in-flow message. [error] adds a small red icon; the text stays
/// readable ink, never red.
class KNotice extends StatelessWidget {
  const KNotice(this.message, {super.key, this.error = false, this.action});
  final String message;
  final bool error;

  /// Optional trailing text action, e.g. Retry.
  final Widget? action;
  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(14, 12, 10, 12),
        decoration: BoxDecoration(
            color: KColors.surface2,
            borderRadius: BorderRadius.circular(KRadius.card),
            border: Border.all(color: KColors.border)),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
              padding: const EdgeInsets.only(top: 1),
              child: Icon(error ? Icons.error_outline : Icons.info_outline,
                  size: 16, color: error ? KColors.fail : KColors.faint)),
          const SizedBox(width: 10),
          Expanded(
              child: Text(message,
                  style: TextStyle(
                      fontSize: 13,
                      height: 1.5,
                      color: error ? KColors.text : KColors.muted))),
          if (action != null) ...[const SizedBox(width: 8), action!],
        ]),
      );
}

/// A failed block: one sentence on what failed and a Retry, in the frame.
class KErrorState extends StatelessWidget {
  const KErrorState(this.message, {super.key, required this.retry});
  final String message;
  final VoidCallback retry;
  @override
  Widget build(BuildContext context) => KNotice(message,
      error: true,
      action: TextButton(
          onPressed: retry,
          style: TextButton.styleFrom(
              minimumSize: const Size(44, 44),
              foregroundColor: KColors.text,
              textStyle: const TextStyle(
                  fontFamily: 'Geist',
                  fontSize: 13,
                  fontWeight: FontWeight.w600)),
          child: const Text('Retry')));
}

/// An empty section keeps its frame: one sentence and, if there is one, the
/// first action. No illustration.
class KEmptyState extends StatelessWidget {
  const KEmptyState(this.title, {super.key, this.body, this.action});
  final String title;
  final String? body;
  final Widget? action;
  @override
  Widget build(BuildContext context) => Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(KRadius.card),
          border: Border.all(color: KColors.border)),
      child: kColumn([
        Text(title, style: KType.h3),
        if (body != null) KMuted(body!, size: 13.5),
        if (action != null)
          Padding(padding: const EdgeInsets.only(top: 4), child: action!),
      ], gap: 6));
}

/// Presses the child in by 2% while a pointer is down. Listener-based, so it
/// never competes with the button's own gesture handling.
class _Pressable extends StatefulWidget {
  const _Pressable({required this.child, required this.enabled});
  final Widget child;
  final bool enabled;
  @override
  State<_Pressable> createState() => _PressableState();
}

class _PressableState extends State<_Pressable> {
  bool _down = false;
  void _set(bool v) {
    if (widget.enabled && _down != v) setState(() => _down = v);
  }

  @override
  Widget build(BuildContext context) => Listener(
      onPointerDown: (_) => _set(true),
      onPointerUp: (_) => _set(false),
      onPointerCancel: (_) => _set(false),
      child: AnimatedScale(
          scale: _down && !_still(context) ? .97 : 1,
          duration: KMotion.fast,
          curve: KMotion.out,
          child: widget.child));
}
