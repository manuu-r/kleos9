import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/theme_gallery.dart';

void main() {
  // The gallery is the fast way to iterate on the theme; it must always render
  // every token and component at phone, tablet and desktop widths.
  for (final width in [320.0, 390.0, 768.0, 1280.0]) {
    testWidgets('theme gallery renders at $width without overflow',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(const ThemeGalleryApp());
      // The page holds running loaders, so pump a bounded time.
      for (var i = 0; i < 6; i++) {
        await tester.pump(const Duration(milliseconds: 100));
      }
      expect(find.text('Everything in one page'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}
