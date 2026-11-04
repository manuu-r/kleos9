import 'dart:convert';
import 'package:flutter/material.dart';
import '../../core/widgets.dart';

class ProductionPage extends StatefulWidget {
  const ProductionPage({super.key, required this.children});
  final List<Widget> children;
  @override
  State<ProductionPage> createState() => _PageState();
}

class _PageState extends State<ProductionPage> {
  final _scroll = KSmoothController();
  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  List<Widget> get children => widget.children;
  @override
  Widget build(BuildContext context) => ListView(
        controller: _scroll,
        key: const ValueKey('production-page-scroll'),
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        padding:
            EdgeInsets.all(MediaQuery.sizeOf(context).width < 600 ? 16 : 32),
        children: [
          for (final child in children)
            Padding(padding: const EdgeInsets.only(bottom: 24), child: child)
        ],
      );
}

class ProductionNotice extends StatelessWidget {
  const ProductionNotice(this.message, {super.key, this.error = false});
  final String message;
  final bool error;
  @override
  Widget build(BuildContext context) => KNotice(message, error: error);
}

/// Placeholder rows at the real row size while a block loads.
class ProductionLoading extends StatelessWidget {
  const ProductionLoading({super.key});
  @override
  Widget build(BuildContext context) => const KPulse(
      child: Padding(
          padding: EdgeInsets.symmetric(vertical: 8),
          child: KSkeletonRows(rows: 3)));
}

class ProductionRetry extends StatelessWidget {
  const ProductionRetry(this.message, {super.key, required this.retry});
  final String message;
  final VoidCallback retry;
  @override
  Widget build(BuildContext context) => KErrorState(message, retry: retry);
}

class ProductionMetric extends StatelessWidget {
  const ProductionMetric(this.label, this.value, {super.key});
  final String label;
  final String value;
  @override
  Widget build(BuildContext context) => kColumn([
        KMuted(label),
        Text(value, style: KType.numeral.copyWith(fontSize: 20)),
      ], gap: 6);
}

class ProductionJsonDetails extends StatelessWidget {
  const ProductionJsonDetails(this.title, this.value, {super.key});
  final String title;
  final Object? value;
  @override
  Widget build(BuildContext context) => ExpansionTile(
        tilePadding: EdgeInsets.zero,
        title: Text(title, style: const TextStyle(fontSize: 13)),
        children: [
          Align(
              alignment: Alignment.centerLeft,
              child: SelectionArea(
                  child: Text(
                      value is String
                          ? value as String
                          : const JsonEncoder.withIndent('  ').convert(value),
                      style: const TextStyle(
                          fontFamily: 'GeistMono', fontSize: 12, height: 1.5))))
        ],
      );
}

String productionMoney(Object? value) =>
    '\$${((value as num?) ?? 0).toStringAsFixed(2)}';
