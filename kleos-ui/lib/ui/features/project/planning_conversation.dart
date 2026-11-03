import 'dart:math';
import '../../../data/services/reference_images.dart';
import '../../core/reference_image_attachments.dart';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../core/widgets.dart';
import 'common.dart';

/// A bounded conversation over saved project messages. Sending waits only for
/// command acceptance; model work continues through workspace events.
class PlanningConversation extends StatefulWidget {
  const PlanningConversation({
    super.key,
    required this.messages,
    required this.onSend,
    required this.canWrite,
    required this.busy,
    this.onUploadImage,
    this.onSendWithReferences,
    this.loadImage,
    this.pending = false,
    this.allowSendWhileBusy = true,
    this.phase = 'capture',
    this.selectedPartLabel,
    this.selectedPartId,
    this.onClearPart,
    this.question,
    this.emptyMessage =
        'Tell me what you want to make. I’ll shape a first concept and ask only what I need.',
  });

  final List<JsonMap> messages;
  final Future<CommandResult> Function(String text, String clientMessageId)
      onSend;
  final Future<String> Function(ReferenceImageDraft image)? onUploadImage;
  final Future<CommandResult> Function(
          String text, String clientMessageId, List<String> imageIds)?
      onSendWithReferences;
  final Future<Uint8List> Function(String id)? loadImage;
  final bool canWrite, busy, pending, allowSendWhileBusy;
  final String phase;
  final String? selectedPartLabel, selectedPartId;
  final VoidCallback? onClearPart;
  final Widget? question;
  final String emptyMessage;

  @override
  State<PlanningConversation> createState() => _PlanningConversationState();
}

class _PlanningConversationState extends State<PlanningConversation> {
  final draft = TextEditingController();
  final history = ScrollController();
  bool sending = false;
  bool showEarlier = false;
  String? messageId;
  String? error;
  List<ReferenceImageDraft> images = [];
  final imageLoads = <String, Future<Uint8List>>{};

  @override
  void initState() {
    super.initState();
    _showLatest();
  }

  void _showLatest() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && history.hasClients) {
        history.jumpTo(history.position.maxScrollExtent);
      }
    });
  }

  bool get canSend =>
      widget.canWrite &&
      !sending &&
      (!widget.busy || widget.allowSendWhileBusy);

  @override
  void didUpdateWidget(covariant PlanningConversation oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!sending && oldWidget.selectedPartId != widget.selectedPartId) {
      messageId = null;
    }
    if (oldWidget.messages.lastOrNull?['id'] !=
        widget.messages.lastOrNull?['id']) {
      _showLatest();
    }
  }

  @override
  void dispose() {
    draft.dispose();
    history.dispose();
    super.dispose();
  }

  Future<void> send() async {
    final text = draft.text.trim().isEmpty && images.isNotEmpty
        ? 'Use these images to refine the concept.'
        : draft.text.trim();
    if (!canSend || text.isEmpty || !draft.value.composing.isCollapsed) return;
    messageId ??=
        'message-${DateTime.now().microsecondsSinceEpoch}-${Random.secure().nextInt(0x7fffffff)}';
    final identity = messageId!;
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      sending = true;
      error = null;
    });
    try {
      final sendWithReferences = widget.onSendWithReferences;
      final sendMessage = widget.onSend;
      final uploadImage = widget.onUploadImage;
      final ids = <String>[];
      for (final image in images) {
        ids.add(await uploadImage!(image));
      }
      final result = ids.isNotEmpty && sendWithReferences != null
          ? await sendWithReferences(text, identity, ids)
          : await sendMessage(text, identity);
      if (!mounted) return;
      if (result.ok) {
        draft.clear();
        images = [];
        messageId = null;
        widget.onClearPart?.call();
      } else {
        error =
            'Your message could not be sent. Your draft is kept. Try again.';
      }
    } catch (_) {
      if (mounted) {
        error = 'Connection interrupted. Your draft is kept. Try again.';
      }
    } finally {
      if (mounted) setState(() => sending = false);
    }
  }

  String get activity => switch (widget.phase) {
        'research' ||
        'researching' ||
        'feasibility' =>
          'Checking feasibility and adjusting the concept…',
        'capture' => 'Shaping your first concept…',
        _ => 'Updating the plan and concept…',
      };

  Widget bubble(JsonMap message) {
    final user = message['role'] == 'user';
    return Align(
      alignment: user ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        key: ValueKey('planning-message-${message['id']}'),
        constraints: const BoxConstraints(maxWidth: 580),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        decoration: BoxDecoration(
          color: user ? KColors.surface3 : KColors.surface,
          borderRadius: BorderRadius.only(
              topLeft: Radius.circular(user ? 3 : 0),
              topRight: Radius.circular(user ? 0 : 3),
              bottomLeft: const Radius.circular(3),
              bottomRight: const Radius.circular(3)),
        ),
        child: pColumn([
          pText(user ? 'You' : 'Kleos', muted: true, small: true),
          if (widget.loadImage != null &&
              asList(message['referenceImageIds']).isNotEmpty)
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final id
                  in asList(message['referenceImageIds']).whereType<String>())
                FutureBuilder<Uint8List>(
                    future:
                        imageLoads.putIfAbsent(id, () => widget.loadImage!(id)),
                    builder: (context, snapshot) => snapshot.hasError
                        ? TextButton.icon(
                            onPressed: () => setState(() {
                                  imageLoads.remove(id);
                                }),
                            icon: const Icon(Icons.refresh),
                            label: const Text('Retry image'))
                        : snapshot.hasData
                            ? Image.memory(snapshot.data!,
                                width: 100,
                                height: 80,
                                fit: BoxFit.cover,
                                semanticLabel: 'Reference image',
                                errorBuilder: (_, __, ___) =>
                                    const Icon(Icons.broken_image_outlined))
                            : const SizedBox(
                                width: 100,
                                height: 80,
                                child: Icon(Icons.image_outlined))),
            ]),
          Text('${message['text'] ?? ''}',
              style: const TextStyle(fontSize: 14, height: 1.5)),
        ], gap: 5),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final messages = widget.messages
        .where((message) =>
            const {'user', 'assistant'}.contains(message['role']) &&
            '${message['text'] ?? ''}'.trim().isNotEmpty)
        .toList();
    final visible = showEarlier || messages.length <= 4
        ? messages
        : messages.sublist(messages.length - 4);
    return pColumn([
      if (messages.length > 4)
        TextButton(
          onPressed: () {
            setState(() => showEarlier = !showEarlier);
            WidgetsBinding.instance.addPostFrameCallback((_) {
              if (mounted && history.hasClients) {
                history
                    .jumpTo(showEarlier ? 0 : history.position.maxScrollExtent);
              }
            });
          },
          child: Text(showEarlier
              ? 'Show recent messages'
              : 'Show earlier messages (${messages.length - 4})'),
        ),
      ConstrainedBox(
        constraints: BoxConstraints(
            maxHeight: MediaQuery.sizeOf(context).width < 600 ? 280 : 340),
        child: Scrollbar(
          controller: history,
          child: SingleChildScrollView(
            key: const ValueKey('planning-message-history'),
            controller: history,
            child: pColumn([
              if (messages.isEmpty)
                bubble({
                  'id': 'welcome',
                  'role': 'assistant',
                  'text': widget.emptyMessage
                })
              else
                for (final message in visible) bubble(message),
            ], gap: 16),
          ),
        ),
      ),
      if (widget.busy || widget.pending)
        Semantics(
          liveRegion: true,
          child: Row(children: [
            const Icon(Icons.auto_awesome_outlined,
                size: 16, color: KColors.text),
            const SizedBox(width: 9),
            Expanded(child: pText(activity, muted: true, small: true)),
          ]),
        ),
      if (widget.question != null) widget.question!,
      if (widget.selectedPartLabel != null)
        InputChip(
          avatar: const Icon(Icons.view_in_ar_outlined, size: 16),
          label: Text('Redo ${widget.selectedPartLabel}',
              overflow: TextOverflow.ellipsis),
          onDeleted: sending ? null : widget.onClearPart,
        ),
      if (widget.selectedPartLabel != null)
        pText(
            'Only this part will be rebuilt from scratch. The other parts stay unchanged.',
            muted: true,
            small: true),
      ReferenceImageAttachments(
          images: images,
          enabled: canSend,
          supported: widget.onUploadImage != null,
          onChanged: (value) => setState(() {
                images = value;
                messageId = null;
                error = null;
              }),
          input: CallbackShortcuts(
            bindings: {
              const SingleActivator(LogicalKeyboardKey.enter): send,
              const SingleActivator(LogicalKeyboardKey.enter, control: true):
                  send,
              const SingleActivator(LogicalKeyboardKey.enter, meta: true): send,
            },
            child: TextField(
              key: const ValueKey('planning-chat-input'),
              controller: draft,
              enabled: canSend,
              minLines: 1,
              maxLines: 5,
              maxLength: 2000,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => send(),
              onChanged: (_) => setState(() {
                messageId = null;
                error = null;
              }),
              decoration: InputDecoration(
                labelText: 'Tell Kleos what you’d like',
                hintText: widget.selectedPartLabel == null
                    ? 'Make it smaller, change the shape, add a feature…'
                    : 'Describe the replacement for this part…',
                counterText: '',
                suffixIcon: IconButton(
                  key: const ValueKey('planning-chat-send'),
                  tooltip: 'Send message',
                  onPressed: canSend &&
                          (draft.text.trim().isNotEmpty || images.isNotEmpty)
                      ? send
                      : null,
                  icon:
                      Icon(sending ? Icons.hourglass_top : Icons.arrow_upward),
                ),
              ),
            ),
          )),
      if (error != null) pText(error, color: KColors.fail, small: true),
      pText(
          widget.canWrite
              ? widget.busy && !widget.allowSendWhileBusy
                  ? 'Wait for engineering work to finish before changing requirements.'
                  : 'Enter to send · Shift+Enter for a new line. Your plan resumes automatically.'
              : 'You have read-only access to this build.',
          muted: true,
          small: true),
    ], gap: 16);
  }
}
