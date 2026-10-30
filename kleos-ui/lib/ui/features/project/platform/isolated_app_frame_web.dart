import 'dart:js_interop';

import 'package:flutter/material.dart';
import 'package:web/web.dart' as web;

/// The generated build has an opaque origin and receives no Kleos credentials.
class IsolatedAppFrame extends StatelessWidget {
  const IsolatedAppFrame(
      {super.key, required this.url, this.onLoaded, this.onError});
  final Uri url;
  final VoidCallback? onLoaded;
  final VoidCallback? onError;

  @override
  Widget build(BuildContext context) => HtmlElementView.fromTagName(
        key: ValueKey(url.toString()),
        tagName: 'iframe',
        onElementCreated: (element) {
          final frame = element as web.HTMLIFrameElement;
          frame
            ..setAttribute('title', 'Built companion app')
            ..setAttribute('sandbox', 'allow-scripts')
            ..setAttribute('credentialless', '')
            ..setAttribute('referrerpolicy', 'no-referrer')
            ..setAttribute('allow',
                "camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; payment 'none'; usb 'none'; serial 'none'; bluetooth 'none'")
            ..setAttribute('loading', 'eager')
            ..style.width = '100%';
          frame.style
            ..height = '100%'
            ..border = '0'
            ..backgroundColor = '#09090b';
          frame.addEventListener(
              'load', ((web.Event _) => onLoaded?.call()).toJS);
          frame.addEventListener(
              'error', ((web.Event _) => onError?.call()).toJS);
          // Restrictions are set before navigation; never set srcdoc or inject code.
          frame.src = url.toString();
        },
      );
}
