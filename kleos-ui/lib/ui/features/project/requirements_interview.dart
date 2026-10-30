import 'dart:convert';
import 'package:flutter/material.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../core/widgets.dart';
import 'common.dart';

/// A focused, resumable interview over authoritative backend questions/answers.
class RequirementsInterview extends StatefulWidget {
  const RequirementsInterview(
      {super.key,
      required this.clarification,
      required this.proposal,
      required this.legacyAnswers,
      required this.canWrite,
      required this.active,
      required this.confirmed,
      required this.onAnswer,
      required this.onReview,
      required this.onConfirm,
      this.autoResume = false,
      this.acceptWhileActive = false,
      this.updatePending = false});
  final JsonMap clarification, proposal, legacyAnswers;
  final bool canWrite, active, confirmed;
  final bool autoResume, acceptWhileActive, updatePending;
  final Future<CommandResult> Function(
      String id, List<String> values, bool legacy) onAnswer;
  final Future<CommandResult> Function() onReview, onConfirm;
  @override
  State<RequirementsInterview> createState() => _RequirementsInterviewState();
}

class _RequirementsInterviewState extends State<RequirementsInterview> {
  final text = TextEditingController();
  final custom = TextEditingController();
  final acknowledged = <String, List<String>>{};
  final selected = <String>{};
  int? index;
  bool other = false;
  bool saving = false;
  bool locallyChanged = false;
  String? error;

  List<JsonMap> get questions {
    final List<JsonMap> active;
    if (widget.clarification.containsKey('questions')) {
      active = asMaps(widget.clarification['questions']);
    } else {
      final structured = asMaps(widget.proposal['clarificationQuestions']);
      active = structured.isNotEmpty
          ? structured
          : [
              for (final prompt in asList(widget.proposal['questions']))
                {
                  'id': '$prompt',
                  'prompt': '$prompt',
                  'type': 'text',
                  'required': true,
                  'legacy': true
                },
            ];
    }
    final seen = active.map((question) => question['id']).toSet();
    return [
      ...active,
      for (final question in asMaps(widget.clarification['resolvedQuestions']))
        if (seen.add(question['id'])) {...question, 'resolved': true},
    ];
  }

  JsonMap get saved => {
        ...widget.legacyAnswers,
        ...asMap(widget.clarification['answers']),
        ...acknowledged
      };
  List<String> values(JsonMap question) =>
      asList(saved[question['id']]).map((v) => '$v').toList();
  bool complete(JsonMap question) =>
      values(question).isNotEmpty ||
      (question['required'] == false && saved.containsKey(question['id']));
  bool get enabled =>
      widget.canWrite &&
      (!widget.active || widget.acceptWhileActive) &&
      !saving;
  int get completed => questions.where(complete).length;

  @override
  void initState() {
    super.initState();
    _resume();
  }

  @override
  void didUpdateWidget(covariant RequirementsInterview oldWidget) {
    super.didUpdateWidget(oldWidget);
    final authoritative = asMap(widget.clarification['answers']);
    acknowledged.removeWhere((id, values) =>
        authoritative.containsKey(id) &&
        jsonEncode(authoritative[id]) == jsonEncode(values));
    if (_signature(oldWidget) != _signature(widget) ||
        (!oldWidget.confirmed && widget.confirmed) ||
        (oldWidget.clarification['needsReview'] == true &&
            widget.clarification['needsReview'] == false)) {
      acknowledged.clear();
      locallyChanged = false;
      _resume();
    } else if (index == null) {
      _resume();
    }
  }

  String _signature(RequirementsInterview source) => jsonEncode({
        'active': source.clarification['questions'] ??
            source.proposal['clarificationQuestions'] ??
            source.proposal['questions'],
        'resolved': source.clarification['resolvedQuestions'],
      });

  bool _lastActiveQuestion(JsonMap question) =>
      question['resolved'] == true ||
      !questions.skip(index! + 1).any((next) => next['resolved'] != true);

  void _resume() {
    final first = questions.indexWhere(
        (question) => question['resolved'] != true && !complete(question));
    _open(widget.confirmed || first < 0 ? questions.length : first);
  }

  void _open(int next) {
    index = next;
    selected.clear();
    text.clear();
    custom.clear();
    other = false;
    error = null;
    if (next >= questions.length) return;
    final question = questions[next];
    final current = values(question);
    final choices =
        asMaps(question['options']).map((o) => '${o['value']}').toSet();
    if (question['type'] == 'single_select' ||
        question['type'] == 'multi_select') {
      selected.addAll(current.where(choices.contains));
      final unknown =
          current.where((value) => !choices.contains(value)).toList();
      if (unknown.isNotEmpty) {
        other = true;
        custom.text = unknown.join(', ');
      }
    } else {
      text.text = current.join(', ');
    }
  }

  @override
  void dispose() {
    text.dispose();
    custom.dispose();
    super.dispose();
  }

  List<String> _draft(JsonMap question) {
    if (question['type'] == 'single_select' ||
        question['type'] == 'multi_select') {
      return [
        ...selected,
        if (other && custom.text.trim().isNotEmpty) custom.text.trim()
      ];
    }
    return text.text.trim().isEmpty ? [] : [text.text.trim()];
  }

  String? _validation(JsonMap question, List<String> answer) {
    if (question['required'] != false && answer.isEmpty) {
      return 'Choose an answer to continue.';
    }
    if (question['type'] == 'number' && answer.isNotEmpty) {
      final value = num.tryParse(answer.single);
      if (value == null || !value.isFinite) return 'Enter a valid number.';
      if (question['min'] is num && value < (question['min'] as num)) {
        return 'Enter ${question['min']} or more.';
      }
      if (question['max'] is num && value > (question['max'] as num)) {
        return 'Enter ${question['max']} or less.';
      }
    }
    return null;
  }

  Future<void> _continue() async {
    final question = questions[index!];
    final signature = _signature(widget);
    final answer = _draft(question);
    final invalid = _validation(question, answer);
    if (invalid != null) {
      setState(() => error = invalid);
      return;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      saving = true;
      error = null;
    });
    try {
      final result = await widget.onAnswer(
          '${question['id']}', answer, question['legacy'] == true);
      if (!mounted) return;
      if (signature != _signature(widget)) {
        setState(() => error =
            'The questions changed. Please review the current question.');
        return;
      }
      if (!result.ok) {
        setState(
            () => error = 'Your answer could not be saved. Please try again.');
        return;
      }
      acknowledged['${question['id']}'] = answer;
      locallyChanged = true;
      setState(() => _open(_lastActiveQuestion(question)
          ? questions.length
          : (index! + 1).clamp(0, questions.length)));
    } catch (_) {
      if (mounted) {
        setState(
            () => error = 'Your answer could not be saved. Please try again.');
      }
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Future<void> _run(Future<CommandResult> Function() callback) async {
    setState(() {
      saving = true;
      error = null;
    });
    try {
      final result = await callback();
      if (mounted && !result.ok) {
        setState(() =>
            error = 'This update could not be started. Please try again.');
      }
    } catch (_) {
      if (mounted) {
        setState(() => error = 'Connection interrupted. Please try again.');
      }
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Widget _choice(String value, String label, String? description, bool single) {
    final checked = selected.contains(value);
    return Semantics(
        checked: checked,
        enabled: enabled,
        inMutuallyExclusiveGroup: single,
        button: true,
        child: InkWell(
            key: ValueKey('requirement-option-$value'),
            borderRadius: BorderRadius.circular(12),
            onTap: !enabled
                ? null
                : () => setState(() {
                      if (single) {
                        selected.clear();
                        other = false;
                      }
                      if (!checked) {
                        selected.add(value);
                      } else {
                        selected.remove(value);
                      }
                      error = null;
                    }),
            child: Container(
                width: double.infinity,
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                    color: checked
                        ? KColors.accent.withValues(alpha: .08)
                        : KColors.surface,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                        color: checked ? KColors.accent : KColors.border)),
                child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                          single
                              ? (checked
                                  ? Icons.radio_button_checked
                                  : Icons.radio_button_unchecked)
                              : (checked
                                  ? Icons.check_box
                                  : Icons.check_box_outline_blank),
                          size: 20,
                          color: checked ? KColors.accent : KColors.faint),
                      const SizedBox(width: 12),
                      Expanded(
                          child: pColumn([
                        Text(label,
                            style: const TextStyle(
                                fontSize: 14, fontWeight: FontWeight.w600)),
                        if (description != null && description.isNotEmpty)
                          pText(description, muted: true, small: true),
                      ], gap: 5)),
                    ]))));
  }

  Widget _question(JsonMap question) {
    final multiple = question['type'] == 'multi_select';
    final choice = multiple || question['type'] == 'single_select';
    final answer = _draft(question);
    return pColumn([
      Text('${question['prompt']}',
          style: Theme.of(context).textTheme.headlineSmall),
      if ('${question['helpText'] ?? ''}'.isNotEmpty)
        pText(question['helpText'], muted: true),
      if (choice) ...[
        pText(multiple ? 'Choose all that apply.' : 'Choose one.',
            muted: true, small: true),
        for (final option in asMaps(question['options']))
          _choice('${option['value']}', '${option['label']}',
              option['description']?.toString(), !multiple),
        if (question['allowCustom'] == true) ...[
          InkWell(
              onTap: !enabled
                  ? null
                  : () => setState(() {
                        other = !other;
                        if (other && !multiple) selected.clear();
                      }),
              child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Row(children: [
                    Icon(other ? Icons.check_circle : Icons.add_circle_outline,
                        size: 20, color: KColors.muted),
                    const SizedBox(width: 12),
                    const Expanded(child: Text('Something else')),
                  ]))),
          if (other)
            TextField(
                controller: custom,
                enabled: enabled,
                minLines: 1,
                maxLines: 3,
                decoration: const InputDecoration(
                    labelText: 'Tell us what you have in mind'),
                onChanged: (_) => setState(() {})),
        ],
      ] else
        TextField(
            key: ValueKey('requirement-text-${question['id']}'),
            controller: text,
            enabled: enabled,
            minLines: question['type'] == 'number' ? 1 : 2,
            maxLines: question['type'] == 'number' ? 1 : 4,
            keyboardType: question['type'] == 'number'
                ? const TextInputType.numberWithOptions(decimal: true)
                : TextInputType.multiline,
            decoration: InputDecoration(
                labelText: question['type'] == 'number'
                    ? 'Your number'
                    : 'Your answer',
                suffixText: question['type'] == 'number'
                    ? question['unit']?.toString()
                    : null,
                helperText: question['type'] == 'number' &&
                        (question['min'] != null || question['max'] != null)
                    ? 'Range: ${question['min'] ?? 'any'}–${question['max'] ?? 'any'} ${question['unit'] ?? ''}'
                    : null,
                hintText: question['type'] == 'number'
                    ? null
                    : 'A few words are enough.'),
            onChanged: (_) => setState(() {})),
      if (error != null)
        pCallout(Text(error!, style: const TextStyle(color: KColors.fail))),
      pActions([
        if (index! > 0)
          KButton(
              label: 'Back',
              compact: true,
              onPressed:
                  enabled ? () => setState(() => _open(index! - 1)) : null),
        KButton(
            key: const ValueKey('requirement-continue'),
            label: saving
                ? 'Saving…'
                : widget.autoResume
                    ? 'Send answer'
                    : _lastActiveQuestion(question)
                        ? 'Review answers'
                        : 'Continue',
            primary: true,
            icon: Icons.arrow_forward,
            onPressed:
                enabled && (question['required'] == false || answer.isNotEmpty)
                    ? _continue
                    : null),
      ]),
      pText(
          widget.autoResume
              ? 'Your answer is saved and the plan continues automatically.'
              : 'Your answers are saved as you continue.',
          muted: true,
          small: true),
    ], gap: 16);
  }

  String _answerLabel(JsonMap question) {
    final options = {
      for (final o in asMaps(question['options']))
        '${o['value']}': '${o['label']}'
    };
    return values(question).map((value) => options[value] ?? value).join(', ');
  }

  Widget _summary() {
    final needsReview = locallyChanged ||
        widget.clarification['needsReview'] == true ||
        (widget.clarification.isEmpty && saved.isNotEmpty);
    final ready = widget.clarification['readyToConfirm'] == true ||
        (widget.clarification.isEmpty && questions.isEmpty && saved.isEmpty);
    return pColumn([
      Text(
          widget.confirmed
              ? 'Your requirements'
              : 'Here’s what we’ll build around',
          style: Theme.of(context).textTheme.headlineSmall),
      if (questions.isNotEmpty) ...[
        for (var i = 0; i < questions.length; i++)
          Container(
              padding: const EdgeInsets.symmetric(vertical: 12),
              decoration: const BoxDecoration(
                  border: Border(bottom: BorderSide(color: KColors.border))),
              child:
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Padding(
                    padding: EdgeInsets.only(top: 2),
                    child: Icon(Icons.check_circle_outline,
                        size: 18, color: KColors.pass)),
                const SizedBox(width: 12),
                Expanded(
                    child: pColumn([
                  pText(questions[i]['prompt'], muted: true, small: true),
                  pText(
                      _answerLabel(questions[i]).isEmpty
                          ? 'No preference'
                          : _answerLabel(questions[i]),
                      strong: true),
                ], gap: 4)),
                IconButton(
                    tooltip: 'Edit answer ${i + 1}',
                    icon: const Icon(Icons.edit_outlined, size: 18),
                    onPressed: enabled ? () => setState(() => _open(i)) : null),
              ])),
      ],
      if (widget.active || widget.updatePending) ...[
        const LinearProgressIndicator(minHeight: 3),
        pText('Updating your requirements…', strong: true),
        pText(
            'Your choices are being worked into the concept. Any new questions will appear here.',
            muted: true),
      ] else if (!widget.confirmed) ...[
        pText(
            needsReview
                ? widget.autoResume
                    ? 'Your choices are saved. The plan will update automatically.'
                    : 'Your choices are saved. Let Kleos update the concept before you confirm.'
                : ready
                    ? 'The concept is ready for your review.'
                    : 'Complete the remaining questions before continuing.',
            muted: true),
        if (needsReview && !widget.autoResume)
          KButton(
              label: saving ? 'Starting review…' : 'Review my requirements',
              primary: true,
              icon: Icons.auto_awesome_outlined,
              onPressed: enabled ? () => _run(widget.onReview) : null),
        if (ready && !needsReview)
          KButton(
              label: 'Confirm and start design',
              primary: true,
              icon: Icons.check,
              onPressed: enabled ? () => _run(widget.onConfirm) : null),
      ],
      if (error != null)
        pCallout(Text(error!, style: const TextStyle(color: KColors.fail))),
    ], gap: 16);
  }

  @override
  Widget build(BuildContext context) {
    final all = questions;
    final current = (index ?? all.length).clamp(0, all.length);
    return pColumn([
      Row(children: [
        Expanded(
            child: pText(
                current < all.length
                    ? 'Let’s shape your device'
                    : 'Requirements summary',
                strong: true)),
        if (all.isNotEmpty) KBadge('$completed of ${all.length} saved'),
      ]),
      if (all.isNotEmpty)
        LinearProgressIndicator(
            value: completed / all.length,
            minHeight: 3,
            backgroundColor: KColors.border,
            color: KColors.accent),
      if (widget.active && current < all.length)
        pText(
            widget.acceptWhileActive
                ? 'I’m updating the concept. You can keep answering.'
                : 'A requirements update is running. Your saved choices will appear here when it finishes.',
            muted: true,
            small: true),
      if (current < all.length) _question(all[current]) else _summary(),
    ], gap: 20);
  }
}
