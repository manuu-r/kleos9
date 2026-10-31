// A preview page for the whole theme. Run it on its own:
//
//   flutter run -t lib/theme_gallery.dart -d chrome
//
// then edit anything in lib/ui/theme/ and hot-reload. It needs no backend.
// Resize the window (or use a device) to check phone, tablet and desktop.
import 'package:flutter/material.dart';

import 'ui/features/project/cad_assembly_preview.dart';
import 'ui/theme/components.dart';

void main() => runApp(const ThemeGalleryApp());

class ThemeGalleryApp extends StatelessWidget {
  const ThemeGalleryApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
      title: 'Kleos9 theme gallery',
      debugShowCheckedModeBanner: false,
      theme: kTheme(),
      scrollBehavior: const KScrollBehavior(),
      home: const ThemeGallery());
}

double _contrast(Color a, Color b) {
  final hi = a.computeLuminance() > b.computeLuminance() ? a : b;
  final lo = identical(hi, a) ? b : a;
  return (hi.computeLuminance() + .05) / (lo.computeLuminance() + .05);
}

List<List<List<double>>> _box(double w, double d, double h) {
  final v = [
    [0.0, 0.0, 0.0],
    [w, 0.0, 0.0],
    [w, d, 0.0],
    [0.0, d, 0.0],
    [0.0, 0.0, h],
    [w, 0.0, h],
    [w, d, h],
    [0.0, d, h],
  ];
  const f = [
    [0, 1, 2],
    [0, 2, 3],
    [4, 6, 5],
    [4, 7, 6],
    [0, 4, 5],
    [0, 5, 1],
    [1, 5, 6],
    [1, 6, 2],
    [2, 6, 7],
    [2, 7, 3],
    [3, 7, 4],
    [3, 4, 0],
  ];
  return [
    for (final t in f) [for (final i in t) v[i]]
  ];
}

Map<String, dynamic> _part(String id, String label, double w, double d,
        double h, double z, String material) =>
    {
      'id': id,
      'label': label,
      'quantity': 1,
      'material': material,
      'mesh': {'triangles': _box(w, d, h)},
      'transform': {
        'translation': {'x': 0, 'y': 0, 'z': z}
      },
    };

class ThemeGallery extends StatefulWidget {
  const ThemeGallery({super.key});
  @override
  State<ThemeGallery> createState() => _ThemeGalleryState();
}

class _ThemeGalleryState extends State<ThemeGallery> {
  String tab = 'overview';
  double slider = .4;
  bool toggle = true;
  bool loading = false;

  Widget _section(String title, List<Widget> children) => Padding(
      padding: const EdgeInsets.only(bottom: 32),
      child: kColumn([
        KLabel(title),
        ...children,
      ], gap: 14));

  Widget _swatch(String name, Color color, {Color? on}) {
    final ink = on ?? KColors.text;
    return SizedBox(
        width: 148,
        child: Container(
            padding: const EdgeInsets.all(12),
            decoration: ShapeDecoration(
                color: color,
                shape: KShape.control(
                    side: const BorderSide(color: KColors.borderStrong))),
            child: kColumn([
              Text(name,
                  style: TextStyle(
                      color: ink, fontSize: 13, fontWeight: FontWeight.w600)),
              Text(
                  '#${color.toARGB32().toRadixString(16).substring(2).toUpperCase()}',
                  style: KType.monoSmall.copyWith(color: ink)),
              Text(
                  '${_contrast(KColors.text, color).toStringAsFixed(1)}:1 text',
                  style: KType.monoSmall.copyWith(color: ink, fontSize: 11)),
            ], gap: 2)));
  }

  @override
  Widget build(BuildContext context) {
    final gutter = KSpace.gutter(context);
    final assembly = {
      'schemaVersion': 'cad-assembly-v1',
      'separation': 'named_parts',
      'parts': [
        _part('base', 'Base shell', 60, 40, 8, 0, 'PETG'),
        _part('board', 'Main board', 52, 34, 2, 10, 'FR4'),
        _part('lid', 'Top lid', 60, 40, 6, 14, 'PETG'),
      ]
    };
    return Scaffold(
        body: SafeArea(
            child: SingleChildScrollView(
                padding: EdgeInsets.fromLTRB(gutter, 24, gutter, 48),
                child: Center(
                    child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 960),
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const KLabel('Kleos9 · Theme gallery · Signal'),
                              const SizedBox(height: 8),
                              Text('Everything in one page', style: KType.h1),
                              const SizedBox(height: 6),
                              const KMuted(
                                  'Edit lib/ui/theme/*.dart and hot reload. Tokens: tokens.dart. Corners: shape.dart.',
                                  size: 14),
                              const SizedBox(height: 32),
                              _section('Colour', [
                                Wrap(spacing: 8, runSpacing: 8, children: [
                                  _swatch('bg', KColors.bg),
                                  _swatch('surface', KColors.surface),
                                  _swatch('surface2', KColors.surface2),
                                  _swatch('surface3', KColors.surface3),
                                  _swatch('action', KColors.action,
                                      on: KColors.onAction),
                                  _swatch('accent', KColors.accent,
                                      on: KColors.onAccent),
                                ]),
                                Wrap(spacing: 8, runSpacing: 8, children: [
                                  for (final (n, _) in [
                                    ('pass', KColors.pass),
                                    ('fail', KColors.fail),
                                    ('warn', KColors.warn),
                                    ('human', KColors.human),
                                    ('unknown', KColors.unknown),
                                    ('actionInk', KColors.actionInk),
                                  ])
                                    KBadge(n,
                                        tone: n == 'actionInk' ? null : n),
                                ]),
                              ]),
                              _section('Type', [
                                Text('Display 32', style: KType.display),
                                Text('Heading 28', style: KType.h1),
                                Text('Section 20', style: KType.h2),
                                Text('Title 16', style: KType.h3),
                                Text(
                                    'Body 15. Agents design the enclosure, board and firmware.',
                                    style: KType.body),
                                Text('Small 13.5 secondary text',
                                    style: KType.small),
                                const KLabel('Readout label'),
                                Text('0x00FF · 12.50 mm · 10 / 13',
                                    style: KType.mono),
                              ]),
                              _section('Shape', [
                                Wrap(spacing: 12, runSpacing: 12, children: [
                                  Container(
                                      width: 160,
                                      height: 90,
                                      alignment: Alignment.center,
                                      decoration:
                                          KShape.surface(KColors.surface),
                                      child: const Text('Panel · cut corners')),
                                  Container(
                                      width: 160,
                                      height: 90,
                                      alignment: Alignment.center,
                                      decoration: ShapeDecoration(
                                          color: KColors.surface2,
                                          shape: KShape.control(
                                              side: const BorderSide(
                                                  color: KColors.border))),
                                      child: const Text('Control · 3px')),
                                ]),
                              ]),
                              _section('Buttons and controls', [
                                Wrap(spacing: 10, runSpacing: 10, children: [
                                  KButton(
                                      label: 'Primary',
                                      primary: true,
                                      onPressed: () {}),
                                  KButton(label: 'Secondary', onPressed: () {}),
                                  const KButton(
                                      label: 'Disabled', primary: true),
                                  KButton(
                                      label: 'Loading',
                                      primary: true,
                                      loading: loading,
                                      onPressed: () =>
                                          setState(() => loading = !loading)),
                                  KButton(
                                      label: 'With icon',
                                      icon: Icons.add,
                                      onPressed: () {}),
                                ]),
                                KSegmented<String>(
                                    selected: tab,
                                    onSelected: (v) => setState(() => tab = v),
                                    options: const [
                                      (
                                        value: 'overview',
                                        label: 'Overview',
                                        icon: Icons.dashboard_outlined
                                      ),
                                      (
                                        value: 'parts',
                                        label: 'Parts',
                                        icon: Icons.view_in_ar_outlined
                                      ),
                                      (
                                        value: 'checks',
                                        label: 'Checks',
                                        icon: Icons.rule
                                      ),
                                      (
                                        value: 'logs',
                                        label: 'Logs',
                                        icon: null
                                      ),
                                    ]),
                                const TextField(
                                    decoration: InputDecoration(
                                        labelText: 'Your request',
                                        hintText:
                                            'A desk air-quality monitor…')),
                                Row(children: [
                                  Expanded(
                                      child: Slider(
                                          value: slider,
                                          onChanged: (v) =>
                                              setState(() => slider = v))),
                                  Switch(
                                      value: toggle,
                                      onChanged: (v) =>
                                          setState(() => toggle = v)),
                                  Checkbox(
                                      value: toggle,
                                      onChanged: (v) =>
                                          setState(() => toggle = v ?? false)),
                                ]),
                                const KProgress(62),
                              ]),
                              _section('Panels', [
                                const KStats(items: [
                                  (label: 'Open', value: '1', sub: null),
                                  (label: 'Waiting', value: '2', sub: null),
                                  (label: 'Accepted', value: '1', sub: null),
                                  (
                                    label: 'Linked',
                                    value: 'Not linked',
                                    sub: 'Connect it'
                                  ),
                                ]),
                                KResponsive(children: [
                                  KCard(
                                      title: 'Air Quality Monitor',
                                      trailing:
                                          const KBadge('Verify', tone: 'pass'),
                                      onTap: () {},
                                      child: const KMuted(
                                          'A pocket air-quality monitor with an e-ink display.',
                                          size: 14)),
                                  const KCard(
                                      title: 'Card with a tint',
                                      tint: KColors.accent,
                                      child: KMuted('Live work uses coral.',
                                          size: 14)),
                                ]),
                                const KNotice('A quiet in-flow message.'),
                                KErrorState(
                                    'Could not load builds. Last updated 09:14.',
                                    retry: () {}),
                                const KEmptyState('No builds yet',
                                    body: 'Describe a device to start one.'),
                              ]),
                              _section('Loading', [
                                const KLineLoader(),
                                const Row(children: [
                                  KSpinner(size: 18),
                                  SizedBox(width: 12),
                                  KStatusDot(color: KColors.accent, live: true),
                                  SizedBox(width: 12),
                                  KBadge('2 running',
                                      tone: 'accent', dot: true),
                                ]),
                                const KPulse(
                                    child:
                                        KPanel(child: KSkeletonRows(rows: 2))),
                              ]),
                              _section('Stage and 3D', [
                                SizedBox(
                                    height: 200,
                                    child: KGridBackdrop(
                                        child: Center(
                                            child: Text('FIG.01',
                                                style: KType.hud)))),
                                CadAssemblyPreview(
                                    assembly: assembly,
                                    height: 420,
                                    fallback: const Text('fallback')),
                              ]),
                            ]))))));
  }
}
