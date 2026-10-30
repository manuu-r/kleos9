import 'dart:math';

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
    final text = draft.text.trim();
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
      final result = await widget.onSend(text, identity);
      if (!mounted) return;
      if (result.ok) {
        draft.clear();
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
          color: user
              ? KColors.accent.withValues(alpha: .1)
              : KColors.surface2.withValues(alpha: .6),
          border: Border.all(
              color: user
                  ? KColors.accent.withValues(alpha: .24)
                  : KColors.border),
          borderRadius: BorderRadius.only(
              topLeft: Radius.circular(user ? 16 : 4),
              topRight: Radius.circular(user ? 4 : 16),
              bottomLeft: const Radius.circular(16),
              bottomRight: const Radius.circular(16)),
        ),
        child: pColumn([
          pText(user ? 'You' : 'Kleos', muted: true, small: true),
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
                size: 16, color: KColors.accent),
            const SizedBox(width: 9),
            Expanded(child: pText(activity, muted: true, small: true)),
          ]),
        ),
      if (widget.question != null) widget.question!,
      if (widget.selectedPartLabel != null)
        InputChip(
          avatar: const Icon(Icons.view_in_ar_outlined, size: 16),
          label:
              Text(widget.selectedPartLabel!, overflow: TextOverflow.ellipsis),
          onDeleted: sending ? null : widget.onClearPart,
        ),
      CallbackShortcuts(
        bindings: {
          const SingleActivator(LogicalKeyboardKey.enter): send,
          const SingleActivator(LogicalKeyboardKey.enter, control: true): send,
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
                : 'What would you like to change about this part?',
            counterText: '',
            suffixIcon: IconButton(
              key: const ValueKey('planning-chat-send'),
              tooltip: 'Send message',
              onPressed: canSend && draft.text.trim().isNotEmpty ? send : null,
              icon: Icon(sending ? Icons.hourglass_top : Icons.arrow_upward),
            ),
          ),
        ),
      ),
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
