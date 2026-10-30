import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/widgets.dart';

class IsolatedAppFrame extends StatelessWidget {
  const IsolatedAppFrame(
      {super.key, required this.url, this.onLoaded, this.onError});
  final Uri url;
  final VoidCallback? onLoaded;
  final VoidCallback? onError;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.open_in_browser, color: KColors.muted, size: 30),
            const SizedBox(height: 12),
            const Text('Open the built companion app in your browser.',
                textAlign: TextAlign.center),
            const SizedBox(height: 12),
            KButton(
                label: 'Open built app',
                compact: true,
                onPressed: () async {
                  try {
                    final opened = await launchUrl(url,
                        mode: LaunchMode.externalApplication);
                    if (opened) {
                      onLoaded?.call();
                    } else {
                      onError?.call();
                    }
                  } catch (_) {
                    onError?.call();
                  }
                }),
          ]),
        ),
      );
}
