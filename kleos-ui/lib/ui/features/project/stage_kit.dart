import 'package:flutter/material.dart';

import '../../core/widgets.dart';

/// Building blocks shared by the planning and fulfilment stages. Everything
/// here is presentation only; stages keep their own data and commands.

/// A tappable answer. Multi-select pills show a check when chosen.
class ChoicePill extends StatelessWidget {
  const ChoicePill(
      {super.key,
      required this.label,
      required this.onTap,
      this.selected = false,
      this.multi = false});
  final String label;
  final VoidCallback? onTap;
  final bool selected, multi;
  @override
  Widget build(BuildContext context) => Opacity(
      opacity: onTap == null && !selected ? .5 : 1,
      child: KTap(
          flat: true,
          radius: KRadius.pill,
          onTap: onTap,
          builder: (context, hovered) => AnimatedContainer(
              duration: KMotion.base,
              curve: KMotion.out,
              padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 10),
              decoration: BoxDecoration(
                  color: selected
                      ? KColors.accent.withValues(alpha: .12)
                      : hovered
                          ? KColors.surface3
                          : KColors.surface2.withValues(alpha: .6),
                  borderRadius: BorderRadius.circular(KRadius.pill),
                  border: Border.all(
                      color: selected
                          ? KColors.accent.withValues(alpha: .65)
                          : hovered
                              ? KColors.borderStrong
                              : KColors.border)),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                if (multi) ...[
                  Icon(selected ? Icons.check : Icons.add,
                      size: 14,
                      color: selected ? KColors.accent : KColors.faint),
                  const SizedBox(width: 7),
                ],
                Flexible(
                    child: Text(label,
                        style: TextStyle(
                            fontSize: 13,
                            color: selected ? KColors.text : KColors.muted))),
              ]))));
}

/// Label and value pairs separated by hairlines, in place of a data table.
class KeyValueRows extends StatelessWidget {
  const KeyValueRows({super.key, required this.rows});
  final List<(String, Widget)> rows;
  @override
  Widget build(BuildContext context) => Column(children: [
        for (var i = 0; i < rows.length; i++) ...[
          if (i > 0) const Divider(height: 1, color: KColors.hairline),
          Padding(
              padding: const EdgeInsets.symmetric(vertical: 11),
              child: LayoutBuilder(builder: (context, c) {
                final label = Text(rows[i].$1,
                    style: const TextStyle(
                        fontSize: 12.5, height: 1.5, color: KColors.muted));
                if (c.maxWidth < 300) {
                  return kColumn([label, rows[i].$2], gap: 3);
                }
                return Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(flex: 2, child: label),
                      const SizedBox(width: 16),
                      Expanded(flex: 3, child: rows[i].$2),
                    ]);
              })),
        ]
      ]);
}

/// A panel with a quiet header row; used where cards would become a wall.
class StagePanel extends StatelessWidget {
  const StagePanel(
      {super.key,
      required this.title,
      required this.child,
      this.trailing,
      this.padding = const EdgeInsets.all(20)});
  final String title;
  final Widget child;
  final Widget? trailing;
  final EdgeInsetsGeometry padding;
  @override
  Widget build(BuildContext context) => KPanel(
      padding: EdgeInsets.zero,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
            padding: const EdgeInsets.fromLTRB(20, 14, 16, 12),
            child: Row(children: [
              Expanded(
                  child: Semantics(
                      header: true,
                      child: Text(title,
                          style: const TextStyle(
                              fontSize: 13.5, fontWeight: FontWeight.w600)))),
              if (trailing != null) trailing!,
            ])),
        const Divider(height: 1, color: KColors.hairline),
        Padding(padding: padding, child: child),
      ]));
}

/// One research track or progress figure: a number you can read at a glance.
class MeterTile extends StatelessWidget {
  const MeterTile({super.key, required this.label, required this.value});
  final String label;
  final double value;
  @override
  Widget build(BuildContext context) => KPanel(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
      child: kColumn([
        KMuted(label, size: 12.5),
        TweenAnimationBuilder<double>(
            tween: Tween(end: value),
            duration: MediaQuery.disableAnimationsOf(context)
                ? Duration.zero
                : KMotion.slow,
            curve: KMotion.out,
            builder: (context, v, _) => Text('${v.round()}%',
                style: KType.numeral.copyWith(
                    fontSize: 30,
                    color: value >= 100 ? KColors.pass : KColors.text))),
        KProgress(value),
      ], gap: 10));
}

/// A sequence with one node per step and a line that fills as work advances.
/// Horizontal when there is room, vertical on a phone.
class StepTrack extends StatelessWidget {
  const StepTrack({super.key, required this.steps, required this.reached});
  final List<String> steps;

  /// Index of the latest step that has happened.
  final int reached;

  Widget _node(int i) {
    final done = i < reached || (i == reached && reached == steps.length - 1);
    final current = i == reached && !done;
    return AnimatedContainer(
        duration: KMotion.base,
        width: 22,
        height: 22,
        decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: done
                ? KColors.pass.withValues(alpha: .16)
                : current
                    ? KColors.accent.withValues(alpha: .16)
                    : Colors.transparent,
            border: Border.all(
                color: done
                    ? KColors.pass
                    : current
                        ? KColors.accent
                        : KColors.borderStrong)),
        child: done
            ? const Icon(Icons.check, size: 13, color: KColors.pass)
            : null);
  }

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        if (c.maxWidth < 520) {
          return Column(children: [
            for (var i = 0; i < steps.length; i++)
              IntrinsicHeight(
                  child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                    SizedBox(
                        width: 22,
                        child: Column(children: [
                          _node(i),
                          if (i < steps.length - 1)
                            Expanded(
                                child: Container(
                                    width: 1.5,
                                    color: i < reached
                                        ? KColors.pass.withValues(alpha: .6)
                                        : KColors.border)),
                        ])),
                    const SizedBox(width: 12),
                    Expanded(
                        child: Padding(
                            padding: const EdgeInsets.only(top: 2, bottom: 14),
                            child: Text(steps[i],
                                style: TextStyle(
                                    fontSize: 13,
                                    color: i <= reached
                                        ? KColors.text
                                        : KColors.faint)))),
                  ])),
          ]);
        }
        return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          for (var i = 0; i < steps.length; i++)
            Expanded(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                  Row(children: [
                    _node(i),
                    if (i < steps.length - 1)
                      Expanded(
                          child: AnimatedContainer(
                              duration: KMotion.slow,
                              height: 1.5,
                              margin: const EdgeInsets.symmetric(horizontal: 6),
                              color: i < reached
                                  ? KColors.pass.withValues(alpha: .6)
                                  : KColors.border)),
                  ]),
                  const SizedBox(height: 10),
                  Text(steps[i],
                      style: TextStyle(
                          fontSize: 12.5,
                          color: i <= reached ? KColors.text : KColors.faint)),
                ])),
        ]);
      });
}

/// A letter in a tinted square: bag labels, lane initials and the like.
class LetterTile extends StatelessWidget {
  const LetterTile(this.letter, {super.key});
  final String letter;
  @override
  Widget build(BuildContext context) => Container(
      width: 38,
      height: 38,
      alignment: Alignment.center,
      decoration: BoxDecoration(
          color: KColors.accent.withValues(alpha: .1),
          borderRadius: BorderRadius.circular(KRadius.control),
          border: Border.all(color: KColors.accent.withValues(alpha: .28))),
      child: Text(letter,
          style: KType.mono.copyWith(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: KColors.accent)));
}
