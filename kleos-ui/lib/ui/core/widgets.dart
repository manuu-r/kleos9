import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'theme.dart';
export 'theme.dart';

class KCard extends StatelessWidget {
  const KCard(
      {super.key,
      this.title,
      this.trailing,
      required this.child,
      this.padding = const EdgeInsets.all(20)});
  final String? title;
  final Widget? trailing;
  final Widget child;
  final EdgeInsetsGeometry padding;
  @override
  Widget build(BuildContext context) => RepaintBoundary(
          child: Container(
        width: double.infinity,
        padding: padding,
        decoration: BoxDecoration(
            color: KColors.surface,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: KColors.border)),
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
                    const SizedBox(height: 16)
                  ],
                  child
                ])),
      ));
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
      backgroundColor: WidgetStateProperty.resolveWith((s) => primary
          ? (s.contains(WidgetState.disabled) ? KColors.border : KColors.text)
          : (s.contains(WidgetState.hovered)
              ? KColors.surface2
              : Colors.transparent)),
      foregroundColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.disabled)
              ? KColors.faint
              : primary
                  ? KColors.bg
                  : KColors.text),
      side: WidgetStatePropertyAll(
          BorderSide(color: primary ? Colors.transparent : KColors.border)),
      shape: WidgetStatePropertyAll(
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(9))),
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
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
        decoration: BoxDecoration(
            color: color.withValues(alpha: .10),
            borderRadius: BorderRadius.circular(6),
            border: Border.all(color: color.withValues(alpha: .14))),
        child: Text(text,
            style: TextStyle(
                fontFamily: 'Geist',
                fontSize: 10,
                fontWeight: FontWeight.w500,
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
            const SizedBox(height: 10),
            Text(description!,
                style: const TextStyle(
                    color: KColors.muted, fontSize: 13, height: 1.6))
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
  Widget build(BuildContext context) => ClipRRect(
      borderRadius: BorderRadius.circular(3),
      child: LinearProgressIndicator(
          value: (value / 100).clamp(0, 1),
          minHeight: 4,
          backgroundColor: KColors.border,
          color: KColors.accent));
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

class KPage extends StatelessWidget {
  const KPage({super.key, required this.children, this.gap = 24});
  final List<Widget> children;
  final double gap;
  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        for (var i = 0; i < children.length; i++) ...[
          if (i > 0) SizedBox(height: gap),
          children[i]
        ]
      ]);
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

class KStats extends StatelessWidget {
  const KStats({super.key, required this.items});
  final List<({String label, String value, String? sub})> items;
  @override
  Widget build(BuildContext context) => KResponsive(
      columns: items.length.clamp(1, 4),
      children: items
          .map((s) => KCard(
                  child: kColumn([
                KMuted(s.label, size: 11),
                Text(s.value,
                    style: const TextStyle(
                        fontSize: 25,
                        fontWeight: FontWeight.w600,
                        letterSpacing: -.8)),
                if (s.sub != null) KMuted(s.sub!, size: 10)
              ], gap: 5)))
          .toList());
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
