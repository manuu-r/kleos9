import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'theme.dart';
export 'theme.dart';

/// Hover and press physics for any tappable surface. Lifts 2px on hover and
/// settles to 0.99 scale on press; [flat] rows only change their fill.
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
    final still = MediaQuery.disableAnimationsOf(context);
    final lift = _hover && !_down && !still && !widget.flat ? -2.0 : 0.0;
    final press = _down && !still && !widget.flat ? .99 : 1.0;
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
                child: AnimatedContainer(
                    duration: KMotion.base,
                    curve: KMotion.out,
                    transform: Matrix4.translationValues(0, lift, 0)
                      ..scaleByDouble(press, press, 1, 1),
                    transformAlignment: Alignment.center,
                    child: widget.builder(context, _hover)))));
  }
}

class KCard extends StatelessWidget {
  const KCard(
      {super.key,
      this.title,
      this.trailing,
      required this.child,
      this.onTap,
      this.tint,
      this.padding = const EdgeInsets.all(22)});
  final String? title;
  final Widget? trailing;
  final Widget child;
  final VoidCallback? onTap;

  /// Optional wash that gives one card in a grid a different voice.
  final Color? tint;
  final EdgeInsetsGeometry padding;
  Widget _body(BuildContext context, bool hovered) => AnimatedContainer(
        duration: KMotion.base,
        curve: KMotion.out,
        width: double.infinity,
        padding: padding,
        foregroundDecoration: BoxDecoration(
            borderRadius: BorderRadius.circular(KRadius.card),
            // A 1px light edge along the top: the surface reads as lit from
            // above, which is what separates a panel from a flat rectangle.
            gradient: const LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [KColors.highlight, Color(0x00ffffff)],
                stops: [0, .012])),
        decoration: BoxDecoration(
            gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Color.alphaBlend(
                      (tint ?? Colors.white)
                          .withValues(alpha: tint == null ? .028 : .08),
                      KColors.surface),
                  KColors.surface
                ]),
            borderRadius: BorderRadius.circular(KRadius.card),
            border: Border.all(
                color: hovered
                    ? KColors.borderStrong
                    : tint == null
                        ? KColors.border
                        : tint!.withValues(alpha: .26)),
            boxShadow: hovered ? KShadow.lift : const []),
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
                      if (constraints.maxWidth < 480 && trailing != null) {
                        return kColumn(
                            [if (heading != null) heading, trailing!],
                            gap: 12);
                      }
                      return Row(children: [
                        if (heading != null) Expanded(child: heading),
                        if (trailing != null) trailing!
                      ]);
                    }),
                    const SizedBox(height: 18)
                  ],
                  child
                ])),
      );
  @override
  Widget build(BuildContext context) => RepaintBoundary(
      child: onTap == null
          ? _body(context, false)
          : KTap(onTap: onTap, builder: _body));
}

class KButton extends StatelessWidget {
  const KButton(
      {super.key,
      required this.label,
      this.onPressed,
      this.primary = false,
      this.icon,
      this.compact = false});
  final String label;
  final VoidCallback? onPressed;
  final bool primary;
  final IconData? icon;
  final bool compact;
  @override
  Widget build(BuildContext context) {
    final touch = MediaQuery.sizeOf(context).width < 1200 ||
        defaultTargetPlatform == TargetPlatform.android ||
        defaultTargetPlatform == TargetPlatform.iOS;
    final style = ButtonStyle(
      visualDensity: touch ? VisualDensity.standard : VisualDensity.compact,
      padding: WidgetStatePropertyAll(EdgeInsets.symmetric(
          horizontal: compact ? 11 : 15, vertical: compact ? 11 : 15)),
      minimumSize:
          WidgetStatePropertyAll(Size(48, touch ? 48 : (compact ? 32 : 38))),
      animationDuration: KMotion.base,
      overlayColor: const WidgetStatePropertyAll(Colors.transparent),
      backgroundColor: WidgetStateProperty.resolveWith((s) => primary
          ? (s.contains(WidgetState.disabled)
              ? KColors.border
              : s.contains(WidgetState.hovered)
                  ? KColors.accent
                  : KColors.text)
          : (s.contains(WidgetState.hovered)
              ? KColors.surface3
              : KColors.surface2.withValues(alpha: .55))),
      foregroundColor: WidgetStateProperty.resolveWith((s) =>
          s.contains(WidgetState.disabled)
              ? KColors.faint
              : primary
                  ? (s.contains(WidgetState.hovered)
                      ? KColors.onAccent
                      : KColors.bg)
                  : KColors.text),
      side: WidgetStateProperty.resolveWith((s) => BorderSide(
          color: s.contains(WidgetState.focused)
              ? KColors.accent
              : primary
                  ? Colors.transparent
                  : s.contains(WidgetState.hovered)
                      ? KColors.borderStrong
                      : KColors.border)),
      shape: WidgetStatePropertyAll(RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(KRadius.control))),
      textStyle: WidgetStatePropertyAll(TextStyle(
          fontFamily: 'Geist',
          fontSize: touch ? 13 : (compact ? 11 : 12),
          fontWeight: FontWeight.w500)),
    );
    return _Pressable(
        enabled: onPressed != null,
        child: TextButton(
            onPressed: onPressed,
            style: style,
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              if (icon != null) ...[
                Icon(icon, size: compact ? 13 : 15),
                const SizedBox(width: 7)
              ],
              Flexible(child: Text(label))
            ])));
  }
}

class KBadge extends StatelessWidget {
  const KBadge(this.text, {super.key, this.tone});
  final String text;
  final String? tone;
  @override
  Widget build(BuildContext context) {
    final color = switch (tone?.toLowerCase()) {
      'pass' => KColors.pass,
      'fail' => KColors.fail,
      'accent' => KColors.accent,
      'human' => KColors.human,
      'unknown' => KColors.unknown,
      _ => KColors.muted
    };
    return Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3.5),
        decoration: BoxDecoration(
            color: color.withValues(alpha: .09),
            borderRadius: BorderRadius.circular(KRadius.pill),
            border: Border.all(color: color.withValues(alpha: .2))),
        child: Text(text,
            style: TextStyle(
                fontFamily: 'Geist',
                fontSize: 10.5,
                fontWeight: FontWeight.w500,
                letterSpacing: .1,
                height: 1.25,
                color: color)));
  }
}

class KSectionHeader extends StatelessWidget {
  const KSectionHeader(
      {super.key, required this.title, this.description, this.trailing});
  final String title;
  final String? description;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, constraints) {
        final body =
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Semantics(
              header: true,
              child: Text(title,
                  style: Theme.of(context).textTheme.headlineLarge)),
          if (description != null) ...[
            const SizedBox(height: 12),
            ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 620),
                child: Text(description!,
                    style: const TextStyle(
                        color: KColors.muted, fontSize: 14, height: 1.6)))
          ]
        ]);
        return constraints.maxWidth > 650
            ? Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(child: body),
                if (trailing != null) ...[const SizedBox(width: 24), trailing!]
              ])
            : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                body,
                if (trailing != null) ...[const SizedBox(height: 16), trailing!]
              ]);
      });
}

class KProgress extends StatelessWidget {
  const KProgress(this.value, {super.key});
  final double value;
  @override
  Widget build(BuildContext context) => TweenAnimationBuilder<double>(
      tween: Tween(end: (value / 100).clamp(0, 1).toDouble()),
      duration: MediaQuery.disableAnimationsOf(context)
          ? Duration.zero
          : KMotion.slow,
      curve: KMotion.out,
      builder: (context, v, _) => Container(
          height: 4,
          width: double.infinity,
          decoration: BoxDecoration(
              color: KColors.surface3,
              borderRadius: BorderRadius.circular(KRadius.pill)),
          alignment: Alignment.centerLeft,
          child: FractionallySizedBox(
              widthFactor: v,
              child: DecoratedBox(
                  decoration: BoxDecoration(
                      gradient: const LinearGradient(
                          colors: [Color(0xffc9622f), KColors.accent]),
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
        final width = (constraints.maxWidth - (count - 1) * 16) / count;
        return Wrap(
            spacing: 16,
            runSpacing: 16,
            children:
                children.map((c) => SizedBox(width: width, child: c)).toList());
      });
}

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
                if (row > 0) const Divider(height: 32),
                kColumn([
                  for (var cell = 0; cell < rows[row].length; cell++)
                    SizedBox(
                      width: double.infinity,
                      child: kColumn([
                        if (cell < headers.length && headers[cell].isNotEmpty)
                          KMuted(headers[cell], size: 11),
                        rows[row][cell],
                      ], gap: 4),
                    ),
                ], gap: 14),
              ],
            ],
          );
        }
        return SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: ConstrainedBox(
                constraints: BoxConstraints(minWidth: constraints.maxWidth),
                child: DataTable(
                    headingRowHeight: headers.isEmpty ? 0 : 36,
                    dataRowMinHeight: 44,
                    dataRowMaxHeight: 100,
                    horizontalMargin: 10,
                    columnSpacing: 22,
                    dividerThickness: .5,
                    columns: headers
                        .map((h) => DataColumn(
                            label: Text(h,
                                style: const TextStyle(
                                    fontSize: 11, color: KColors.faint))))
                        .toList(),
                    rows: rows
                        .map((r) =>
                            DataRow(cells: r.map((c) => DataCell(c)).toList()))
                        .toList())));
      });
}

/// Fades and lifts its child in once, after [delay]. Plays on first build
/// only; later rebuilds from polling never replay it.
class KReveal extends StatefulWidget {
  const KReveal({super.key, required this.child, this.delay = Duration.zero});
  final Widget child;
  final Duration delay;
  @override
  State<KReveal> createState() => _KRevealState();
}

class _KRevealState extends State<KReveal> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: KMotion.slow);
  late final Animation<double> _t =
      CurvedAnimation(parent: _c, curve: KMotion.out);
  bool _started = false;
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    if (MediaQuery.disableAnimationsOf(context)) {
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
  Widget build(BuildContext context) => FadeTransition(
      opacity: _t,
      child: AnimatedBuilder(
          animation: _t,
          child: widget.child,
          builder: (context, child) => Transform.translate(
              offset: Offset(0, (1 - _t.value) * 14), child: child)));
}

class KPage extends StatelessWidget {
  const KPage({super.key, required this.children, this.gap = 24});
  final List<Widget> children;
  final double gap;
  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        for (var i = 0; i < children.length; i++) ...[
          if (i > 0) SizedBox(height: gap),
          KReveal(
              delay: Duration(milliseconds: 60 * i.clamp(0, 5)),
              child: children[i])
        ]
      ]);
}

/// Fine engineering grid with a soft accent bloom, painted once.
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
    final rect = Offset.zero & size;
    canvas.drawRect(rect, Paint()..color = KColors.surface);
    final line = Paint()
      ..color = Colors.white.withValues(alpha: .035)
      ..strokeWidth = 1;
    for (var x = 0.0; x < size.width; x += 32) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), line);
    }
    for (var y = 0.0; y < size.height; y += 32) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), line);
    }
    canvas.drawRect(
        rect,
        Paint()
          ..shader = RadialGradient(
              center: const Alignment(.1, .15),
              radius: .75,
              colors: [
                KColors.accent.withValues(alpha: .16),
                Colors.transparent
              ]).createShader(rect));
    canvas.drawRect(
        rect,
        Paint()
          ..shader = RadialGradient(
              radius: 1.0,
              colors: [Colors.transparent, KColors.bg.withValues(alpha: .55)],
              stops: const [.6, 1]).createShader(rect));
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
      style: TextStyle(color: KColors.muted, fontSize: size, height: 1.6));
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
              fontSize: 11,
              height: 1.8,
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

/// A row of headline numbers inside one panel, separated by hairlines.
/// Metrics do not each get their own box: the numbers carry the hierarchy.
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
        return KPanel(
            padding: EdgeInsets.zero,
            child: Column(children: [
              for (var r = 0; r < rows.length; r++) ...[
                if (r > 0) const Divider(height: 1, color: KColors.hairline),
                IntrinsicHeight(
                    child: Row(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                      for (var i = 0; i < cols; i++) ...[
                        if (i > 0)
                          const VerticalDivider(
                              width: 1, color: KColors.hairline),
                        Expanded(
                            child: i < rows[r].length
                                ? Padding(
                                    padding: const EdgeInsets.fromLTRB(
                                        22, 20, 18, 20),
                                    child: kColumn([
                                      KMuted(rows[r][i].label, size: 12),
                                      Text(rows[r][i].value,
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          style: KType.numeral.copyWith(
                                              fontSize:
                                                  rows[r][i].value.length > 9
                                                      ? 18
                                                      : 28)),
                                      if (rows[r][i].sub != null)
                                        KMuted(rows[r][i].sub!, size: 11)
                                    ], gap: 8))
                                : const SizedBox.shrink())
                      ]
                    ])),
              ]
            ]));
      });
}

/// A flat bordered surface with no gradient: for regions that group content
/// without competing with the cards inside them.
class KPanel extends StatelessWidget {
  const KPanel(
      {super.key,
      required this.child,
      this.padding = const EdgeInsets.all(20),
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

/// Segmented control: one rounded track with a raised selected segment. Used
/// for every tab-like switch so they all behave and look the same.
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

  /// Narrow layouts: only the selected segment keeps its label.
  final bool compact;
  @override
  Widget build(BuildContext context) => SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Container(
          padding: const EdgeInsets.all(3),
          decoration: BoxDecoration(
              color: KColors.bg.withValues(alpha: .6),
              borderRadius: BorderRadius.circular(KRadius.control + 3),
              border: Border.all(color: KColors.border)),
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
                          builder: (context, hovered) => AnimatedContainer(
                              duration: KMotion.base,
                              curve: KMotion.out,
                              padding: EdgeInsets.symmetric(
                                  horizontal: compact &&
                                          o.icon != null &&
                                          o.value != selected
                                      ? 10
                                      : 13,
                                  vertical: 8),
                              decoration: BoxDecoration(
                                  color: o.value == selected
                                      ? KColors.surface3
                                      : hovered
                                          ? KColors.surface2
                                          : Colors.transparent,
                                  borderRadius:
                                      BorderRadius.circular(KRadius.control),
                                  border: Border.all(
                                      color: o.value == selected
                                          ? KColors.borderStrong
                                          : Colors.transparent)),
                              child: Row(children: [
                                if (o.icon != null) ...[
                                  Icon(o.icon,
                                      size: 14,
                                      color: o.value == selected
                                          ? KColors.accent
                                          : KColors.faint),
                                  if (!compact || o.value == selected)
                                    const SizedBox(width: 7)
                                ],
                                if (!compact ||
                                    o.icon == null ||
                                    o.value == selected)
                                  Text(o.label,
                                      style: TextStyle(
                                          fontSize: 12,
                                          fontWeight: o.value == selected
                                              ? FontWeight.w500
                                              : FontWeight.w400,
                                          color: o.value == selected
                                              ? KColors.text
                                              : KColors.muted)),
                              ]))))),
          ])));
}

/// Quiet ambient light behind the app: a faint accent bloom near the top and
/// a cool counter-light low on the opposite side. Painted once, never animated.
class KAmbient extends StatelessWidget {
  const KAmbient({super.key, this.child});
  final Widget? child;
  @override
  Widget build(BuildContext context) => RepaintBoundary(
      child: CustomPaint(
          painter: const _AmbientPainter(),
          child: child ?? const SizedBox.expand()));
}

class _AmbientPainter extends CustomPainter {
  const _AmbientPainter();
  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    canvas.drawRect(rect, Paint()..color = KColors.bg);
    canvas.drawRect(
        rect,
        Paint()
          ..shader = RadialGradient(
              center: const Alignment(.55, -1.15),
              radius: .9,
              colors: [
                KColors.accent.withValues(alpha: .085),
                Colors.transparent
              ]).createShader(rect));
    canvas.drawRect(
        rect,
        Paint()
          ..shader = RadialGradient(
              center: const Alignment(-1.1, .9),
              radius: .8,
              colors: [
                const Color(0xff6f86ad).withValues(alpha: .05),
                Colors.transparent
              ]).createShader(rect));
  }

  @override
  bool shouldRepaint(_AmbientPainter old) => false;
}

/// Layout-shaped placeholder shown while the first snapshot loads. One shared
/// pulse drives every block, so it costs a single opacity layer.
class KPageSkeleton extends StatefulWidget {
  const KPageSkeleton({super.key});
  @override
  State<KPageSkeleton> createState() => _KPageSkeletonState();
}

class _KPageSkeletonState extends State<KPageSkeleton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 1100));
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (MediaQuery.disableAnimationsOf(context)) {
      _pulse.stop();
    } else if (!_pulse.isAnimating) {
      _pulse.repeat(reverse: true);
    }
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  Widget _block(double h, {double? w, double r = 8}) => Container(
      width: w,
      height: h,
      decoration: BoxDecoration(
          color: KColors.surface2, borderRadius: BorderRadius.circular(r)));
  @override
  Widget build(BuildContext context) => Semantics(
      label: 'Loading',
      child: ExcludeSemantics(
          child: RepaintBoundary(
              child: FadeTransition(
                  opacity: Tween(begin: .45, end: 1.0).animate(
                      CurvedAnimation(parent: _pulse, curve: Curves.easeInOut)),
                  child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 1136),
                          child: kColumn([
                            _block(40, w: 360),
                            _block(14, w: 520),
                            const SizedBox(height: 16),
                            Row(children: [
                              Expanded(child: _block(150, r: 14)),
                              const SizedBox(width: 16),
                              Expanded(child: _block(150, r: 14)),
                              const SizedBox(width: 16),
                              Expanded(child: _block(150, r: 14)),
                            ]),
                          ], gap: 12)))))));
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
          scale: _down && !MediaQuery.disableAnimationsOf(context) ? .98 : 1,
          duration: KMotion.fast,
          curve: KMotion.out,
          child: widget.child));
}
