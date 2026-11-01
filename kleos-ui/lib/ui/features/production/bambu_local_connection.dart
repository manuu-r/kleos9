import 'package:flutter/material.dart';

import '../../../data/models/json.dart';
import '../../../data/services/production_api.dart';
import '../../core/widgets.dart';
import '../project/common.dart';

class BambuLocalConnectionForm extends StatefulWidget {
  const BambuLocalConnectionForm(
      {super.key,
      required this.modelId,
      required this.state,
      required this.onCertificate,
      required this.onConnect,
      required this.onRefresh,
      required this.onForget});
  final String? modelId;
  final JsonMap state;
  final Future<JsonMap> Function(JsonMap) onCertificate, onConnect;
  final Future<JsonMap> Function() onRefresh, onForget;

  @override
  State<BambuLocalConnectionForm> createState() =>
      _BambuLocalConnectionFormState();
}

class _BambuLocalConnectionFormState extends State<BambuLocalConnectionForm> {
  late final address =
      TextEditingController(text: '${widget.state['address'] ?? ''}');
  late final serial =
      TextEditingController(text: '${widget.state['serial'] ?? ''}');
  final code = TextEditingController(), fingerprint = TextEditingController();
  String? observedFingerprint, message;
  bool verified = false, busy = false;
  JsonMap? latest;

  @override
  void dispose() {
    for (final controller in [address, serial, code, fingerprint]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> run(Future<JsonMap> Function() operation,
      {bool inspection = false}) async {
    setState(() {
      busy = true;
      message = null;
    });
    try {
      final response = await operation();
      if (mounted) {
        setState(() {
          if (inspection) {
            observedFingerprint = '${response['certificateSha256']}';
            message =
                'Certificate observed. It is not verified; compare it with your trusted printer identity before connecting.';
          } else {
            latest = response;
            message = '${response['message']}';
          }
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() => message = error is ProductionApiException
            ? error.message
            : 'Connection check failed. Check the printer details and LAN access.');
      }
    } finally {
      if (mounted) {
        code.clear();
        setState(() => busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final status = latest ?? widget.state;
    return pColumn([
      pText('Connect locally · read-only', strong: true),
      pText(
          'This test reads status only. No slicing profile is needed. It cannot start a print, heat the printer or move it.',
          small: true),
      TextField(
          controller: address,
          enabled: !busy,
          maxLength: 15,
          decoration: const InputDecoration(labelText: 'Printer IP address'),
          onChanged: (_) => setState(() {
                verified = false;
                observedFingerprint = null;
              })),
      TextField(
          controller: serial,
          enabled: !busy,
          maxLength: 64,
          decoration: const InputDecoration(labelText: 'Printer serial number'),
          onChanged: (_) => setState(() => verified = false)),
      KButton(
          label: 'Inspect certificate (no access code sent)',
          onPressed: busy || address.text.trim().isEmpty
              ? null
              : () => run(
                  () => widget.onCertificate({'address': address.text.trim()}),
                  inspection: true)),
      if (observedFingerprint != null)
        SelectableText('Observed certificate SHA-256: $observedFingerprint'),
      TextField(
          controller: fingerprint,
          enabled: !busy,
          maxLength: 64,
          decoration: const InputDecoration(
              labelText: 'Independently verified certificate SHA-256'),
          onChanged: (_) => setState(() => verified = false)),
      CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          value: verified,
          title:
              const Text('I verified this fingerprint belongs to my printer'),
          onChanged: busy ||
                  !RegExp(r'^[a-f0-9]{64}$').hasMatch(fingerprint.text.trim())
              ? null
              : (value) => setState(() => verified = value == true)),
      TextField(
          controller: code,
          enabled: !busy,
          obscureText: true,
          autocorrect: false,
          enableSuggestions: false,
          maxLength: 64,
          decoration: const InputDecoration(
              labelText: 'LAN access code (kept on this computer)'),
          onChanged: (_) => setState(() {})),
      KButton(
          label: 'Test read-only connection',
          loading: busy,
          onPressed: busy ||
                  !verified ||
                  widget.modelId == null ||
                  code.text.isEmpty ||
                  serial.text.trim().isEmpty
              ? null
              : () async {
                  final body = <String, dynamic>{
                    'address': address.text.trim(),
                    'serial': serial.text.trim(),
                    'accessCode': code.text,
                    'certificateSha256': fingerprint.text.trim(),
                    'certificateVerified': verified,
                    'modelId': widget.modelId
                  };
                  await run(() => widget.onConnect(body));
                }),
      if (status['credentialsStored'] == true)
        KButton(
            label: 'Refresh status',
            onPressed: busy ? null : () => run(widget.onRefresh)),
      if (status['address'] != null)
        KButton(
            label: 'Forget connection',
            onPressed: busy ? null : () => run(widget.onForget)),
      if (message != null) pText(message, small: true),
      if (status['connected'] == true)
        pText(
            'Printer reports: ${asMap(status['observation'])['state'] ?? 'unknown'}',
            small: true),
      pText(
          'Use the printer’s LAN access mode when required by its firmware. The access code is held in local process memory for up to 30 minutes, never saved to a project or sent to a hosted Kleos server. Closing or restarting the local API clears it.',
          small: true,
          muted: true),
    ]);
  }
}
