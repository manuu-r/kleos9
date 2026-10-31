import 'package:flutter/material.dart';
import 'tokens.dart';

/// HUD detail, used sparingly: a mono readout label, corner brackets and a
/// faint engineering grid. All of it is decoration; none of it carries meaning
/// that is not also in the text.

/// A small uppercase mono readout label ("CHECKS", "REV 3"). A few per screen.
class KLabel extends StatelessWidget {
  const KLabel(this.text, {super.key, this.color, this.maxLines = 1});
  final String text;
  final Color? color;
  final int maxLines;
  @override
  Widget build(BuildContext context) => Text(text.toUpperCase(),
      maxLines: maxLines,
      overflow: TextOverflow.ellipsis,
      style: color == null ? KType.hud : KType.hud.copyWith(color: color));
}

/// Four L-shaped corner brackets around [child], like a viewfinder. They sit
/// inside the child's bounds and never take touches.
class KBrackets extends StatelessWidget {
  const KBrackets(
      {super.key,
      required this.child,
      this.color = KColors.borderStrong,
      this.length = 12,
      this.inset = 10});
  final Widget child;
  final Color color;
  final double length, inset;
  @override
  Widget build(BuildContext context) =>
      Stack(fit: StackFit.passthrough, children: [
        child,
        Positioned.fill(
            child: IgnorePointer(
                child: CustomPaint(
                    painter: _BracketPainter(color, length, inset)))),
      ]);
}

class _BracketPainter extends CustomPainter {
  const _BracketPainter(this.color, this.length, this.inset);
  final Color color;
  final double length, inset;
  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()
      ..color = color
      ..strokeWidth = 1
      ..style = PaintingStyle.stroke;
    final l = inset, t = inset, r = size.width - inset, b = size.height - inset;
    final path = Path()
      ..moveTo(l, t + length)
      ..lineTo(l, t)
      ..lineTo(l + length, t)
      ..moveTo(r - length, t)
      ..lineTo(r, t)
      ..lineTo(r, t + length)
      ..moveTo(r, b - length)
      ..lineTo(r, b)
      ..lineTo(r - length, b)
      ..moveTo(l + length, b)
      ..lineTo(l, b)
      ..lineTo(l, b - length);
    canvas.drawPath(path, p);
  }

  @override
  bool shouldRepaint(_BracketPainter old) =>
      old.color != color || old.length != length || old.inset != inset;
}

/// A faint square grid, painted once. Used behind products and viewports.
class KGridPaint extends CustomPainter {
  const KGridPaint({this.spacing = 24, this.alpha = .045});
  final double spacing, alpha;
  @override
  void paint(Canvas canvas, Size size) {
    final line = Paint()
      ..color = Colors.white.withValues(alpha: alpha)
      ..strokeWidth = 1;
    for (var x = 0.0; x <= size.width; x += spacing) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), line);
    }
    for (var y = 0.0; y <= size.height; y += spacing) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), line);
    }
  }

  @override
  bool shouldRepaint(KGridPaint old) =>
      old.spacing != spacing || old.alpha != alpha;
}
