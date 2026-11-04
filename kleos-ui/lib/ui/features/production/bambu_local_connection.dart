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
      this.onCertificate,
      required this.onConnect,
      required this.onRefresh,
      required this.onForget,
      this.onDiscover,
      this.onModelSelected});
  final Future<JsonMap> Function()? onDiscover;
  final ValueChanged<String>? onModelSelected;
  final String? modelId;
  final JsonMap state;
  final Future<JsonMap> Function(JsonMap)? onCertificate;
  final Future<JsonMap> Function(JsonMap) onConnect;
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
  late final firmware =
      TextEditingController(text: '${widget.state['firmwareVersion'] ?? ''}');
  final code = TextEditingController();
  String? selectedName, selectedModel, message;
  bool directPrinting = false, busy = false, manual = false;
  JsonMap? latest;
  List<JsonMap> discovered = [];
  bool searched = false;

  @override
  void didUpdateWidget(covariant BambuLocalConnectionForm oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.modelId != widget.modelId) {
      if (selectedModel != widget.modelId) {
        firmware.clear();
        directPrinting = false;
      }
      selectedModel = widget.modelId;
    }
  }

  @override
  void dispose() {
    for (final controller in [address, serial, code, firmware]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> discover() async {
    setState(() {
      busy = true;
      message = null;
    });
    try {
      final result = await widget.onDiscover!();
      if (mounted) {
        setState(() {
          discovered = asMaps(result['devices']);
          searched = true;
          if (discovered.isEmpty) manual = true;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          manual = true;
          message = error is ProductionApiException
              ? error.message
              : 'Could not find printers. Retry or enter the IP address manually.';
        });
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void selectPrinter(JsonMap device) {
    setState(() {
      if (address.text.isNotEmpty &&
          (address.text != device['address'] ||
              serial.text != device['serial'])) {
        code.clear();
      }
      address.text = '${device['address']}';
      serial.text = '${device['serial'] ?? ''}';
      firmware.text = '${device['firmwareVersion'] ?? ''}';
      selectedName = '${device['name'] ?? 'Bambu printer'}';
      selectedModel = device['modelId'] as String?;
      directPrinting = false;
      manual = false;
      latest = {};
      message = 'Enter the access code shown on this printer, then connect.';
    });
    if (selectedModel != null) widget.onModelSelected?.call(selectedModel!);
  }

  Future<void> run(Future<JsonMap> Function() operation,
      {bool clearCode = false}) async {
    setState(() {
      busy = true;
      message = null;
    });
    try {
      final response = await operation();
      if (mounted) {
        setState(() {
          latest = response;
          message = response['message'] as String?;
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
        if (clearCode) code.clear();
        setState(() => busy = false);
      }
    }
  }

  void changeIdentity() {
    code.clear();
    selectedName = null;
    latest = {};
    message = null;
    directPrinting = false;
  }

  @override
  Widget build(BuildContext context) {
    final status = latest ?? widget.state;
    final paired =
        status['paired'] == true || status['credentialsStored'] == true;
    final model = selectedModel ?? widget.modelId;
    final ready = address.text.trim().isNotEmpty &&
        serial.text.trim().isNotEmpty &&
        model != null;
    final statusMessage = message ??
        (status['address'] != null ||
                status['status'] == 'expired' ||
                status['errorCode'] != null
            ? status['message'] as String?
            : null);
    return pColumn([
      pText('Connect your printer', strong: true),
      pText('Choose your printer, enter its LAN access code, and connect.',
          small: true),
      if (paired)
        pText(
            status['connected'] == true
                ? 'Printer paired · connected'
                : 'Printer paired · refresh to get its latest status',
            strong: true),
      if (widget.onDiscover != null) ...[
        KButton(
            label: 'Find printers',
            loading: busy,
            icon: Icons.search,
            onPressed: busy ? null : discover),
        if (searched && discovered.isEmpty)
          pText(
              'No printers found. Enter the IP address and serial number from your printer.',
              small: true),
        for (final device in discovered)
          ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text('${device['name'] ?? 'Bambu printer'}'),
              subtitle: Text('${device['address']}'),
              trailing: Icon(address.text == device['address'] &&
                      serial.text == device['serial']
                  ? Icons.check_circle_outline
                  : Icons.chevron_right),
              onTap: busy ? null : () => selectPrinter(device)),
      ],
      if (!manual && address.text.isNotEmpty)
        pText('${selectedName ?? 'Selected printer'} · ${address.text}',
            small: true),
      TextButton(
          onPressed: busy ? null : () => setState(() => manual = !manual),
          child: Text(manual
              ? 'Hide printer details'
              : 'Enter manually / edit details')),
      if (manual || widget.onDiscover == null) ...[
        TextField(
            controller: address,
            enabled: !busy,
            maxLength: 15,
            decoration: const InputDecoration(labelText: 'Printer IP address'),
            onChanged: (_) => setState(changeIdentity)),
        TextField(
            controller: serial,
            enabled: !busy,
            maxLength: 64,
            decoration:
                const InputDecoration(labelText: 'Printer serial number'),
            onChanged: (_) => setState(changeIdentity)),
      ],
      TextField(
          controller: code,
          enabled: !busy,
          obscureText: true,
          autocorrect: false,
          enableSuggestions: false,
          maxLength: 64,
          decoration: const InputDecoration(
              labelText: 'LAN access code',
              helperText: 'From the printer’s network / LAN mode settings'),
          onChanged: (_) => setState(() {})),
      KButton(
          label: 'Connect',
          loading: busy,
          onPressed: busy ||
                  !ready ||
                  code.text.trim().isEmpty ||
                  (directPrinting && firmware.text.trim().isEmpty)
              ? null
              : () => run(
                  () => widget.onConnect({
                        'address': address.text.trim(),
                        'serial': serial.text.trim(),
                        'accessCode': code.text.trim(),
                        'modelId': model,
                        if (firmware.text.trim().isNotEmpty)
                          'firmwareVersion': firmware.text.trim(),
                        if (directPrinting) 'lanDeveloperMode': true,
                      }),
                  clearCode: true)),
      if (statusMessage != null) pText(statusMessage, small: true),
      if (status['connected'] == true)
        pText(
            'Printer reports: ${asMap(status['observation'])['state'] ?? 'unknown'}',
            small: true),
      if (paired)
        KButton(
            label: 'Refresh status',
            onPressed: busy ? null : () => run(widget.onRefresh)),
      if (status['address'] != null)
        KButton(
            label: 'Forget connection',
            onPressed:
                busy ? null : () => run(widget.onForget, clearCode: true)),
      ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text('Advanced · direct LAN printing'),
          children: [
            SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Enable direct LAN printing'),
                subtitle: const Text(
                    'Experimental for A1 mini, A1, P1S and X1 Carbon. Enable Developer Mode on the printer first. Printing still needs a separate review; external spool only.'),
                value: directPrinting,
                onChanged: busy ||
                        !const {'N1', 'N2S', 'C12', 'BL-P001'}.contains(model)
                    ? null
                    : (value) => setState(() => directPrinting = value)),
            if (directPrinting)
              TextField(
                  controller: firmware,
                  enabled: !busy,
                  maxLength: 80,
                  decoration: const InputDecoration(
                      labelText:
                          'Printer firmware version (from printer settings)'),
                  onChanged: (_) => setState(() {})),
          ]),
      pText(
          'Kleos verifies the printer’s identity automatically. The access code stays on this computer for up to 30 minutes. Connecting only reads status and never starts a print.',
          small: true,
          muted: true),
    ]);
  }
}
