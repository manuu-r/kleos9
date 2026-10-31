import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../data/models/admin.dart';
import '../../../data/services/production_api.dart';
import '../../../state/admin_store.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';

class AdminPage extends ConsumerWidget {
  const AdminPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(accountProvider).asData?.value;
    if (account == null || !const {'owner', 'admin'}.contains(account.role)) {
      return const Padding(
          padding: EdgeInsets.all(32),
          child: KMuted(
              'Only organization owners and admins can open this page.'));
    }
    final organization = account.organizations
        .where((org) => org.id == account.tenantId)
        .map((org) => org.name)
        .firstOrNull;
    final owner = account.role == 'owner';
    return KPage(children: [
      KSectionHeader(
          title: 'Team access',
          description:
              'Invite people to ${organization ?? 'your organization'} and manage who can use it.'),
      _InviteForm(roles: owner ? adminRoles : const ['member', 'viewer']),
      const _InviteList(),
      _MemberList(isOwner: owner),
    ]);
  }
}

/// Runs an admin action and reports failures instead of leaving the page silent.
Future<T?> _attempt<T>(
    BuildContext context, Future<T> Function() action) async {
  try {
    return await action();
  } on ProductionApiException catch (error) {
    if (context.mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
    return null;
  }
}

Future<void> _showSecret(BuildContext context,
    {required String title, required String body, required String secret}) {
  return showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialog) => AlertDialog(
              title: Text(title),
              content: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(body),
                    const SizedBox(height: 14),
                    KCode(secret),
                  ]),
              actions: [
                TextButton(
                    key: const ValueKey('admin-copy-secret'),
                    onPressed: () =>
                        Clipboard.setData(ClipboardData(text: secret)),
                    child: const Text('Copy')),
                FilledButton(
                    onPressed: () => Navigator.pop(dialog),
                    child: const Text('Done')),
              ]));
}

class _InviteForm extends ConsumerStatefulWidget {
  const _InviteForm({required this.roles});
  final List<String> roles;
  @override
  ConsumerState<_InviteForm> createState() => _InviteFormState();
}

class _InviteFormState extends ConsumerState<_InviteForm> {
  final email = TextEditingController();
  String role = 'member';
  int hours = 168;
  bool busy = false;

  @override
  void dispose() {
    email.dispose();
    super.dispose();
  }

  Future<void> create() async {
    setState(() => busy = true);
    final created = await _attempt(
        context,
        () => ref.read(adminActionsProvider).createInvite(
            role: role, email: email.text.trim(), expiresHours: hours));
    if (!mounted) return;
    setState(() => busy = false);
    if (created == null) return;
    email.clear();
    ref.invalidate(adminInvitesProvider);
    await _showSecret(context,
        title: 'Invite code',
        body:
            'Share this code${created.email == null ? '' : ' with ${created.email}'}. '
            'It can be used once, expires in ${created.expiresInHours ~/ 24 >= 1 ? '${created.expiresInHours ~/ 24} day(s)' : '${created.expiresInHours} hour(s)'}, '
            'and will not be shown again. They choose "I have an invite code" on the sign-in page and create a '
            '${created.role} account.',
        secret: created.code);
  }

  @override
  Widget build(BuildContext context) => KCard(
      title: 'Invite someone',
      child: kColumn([
        const KMuted(
            'Create a single-use code. The person signs up with it and picks their own password.'),
        Wrap(
            spacing: 12,
            runSpacing: 12,
            crossAxisAlignment: WrapCrossAlignment.end,
            children: [
              SizedBox(
                  width: 260,
                  child: TextField(
                      key: const ValueKey('admin-invite-email'),
                      controller: email,
                      keyboardType: TextInputType.emailAddress,
                      decoration: const InputDecoration(
                          labelText: 'Restrict to email (optional)'))),
              SizedBox(
                  width: 180,
                  child: DropdownButtonFormField<String>(
                      key: const ValueKey('admin-invite-role'),
                      initialValue: role,
                      decoration: const InputDecoration(labelText: 'Role'),
                      items: [
                        for (final r in widget.roles)
                          DropdownMenuItem(value: r, child: Text(r))
                      ],
                      onChanged: (v) => setState(() => role = v ?? role))),
              SizedBox(
                  width: 180,
                  child: DropdownButtonFormField<int>(
                      initialValue: hours,
                      decoration:
                          const InputDecoration(labelText: 'Expires in'),
                      items: const [
                        DropdownMenuItem(value: 24, child: Text('1 day')),
                        DropdownMenuItem(value: 168, child: Text('7 days')),
                        DropdownMenuItem(value: 720, child: Text('30 days')),
                      ],
                      onChanged: (v) => setState(() => hours = v ?? hours))),
              KButton(
                  key: const ValueKey('admin-create-invite'),
                  label: busy ? 'Creating…' : 'Create invite code',
                  primary: true,
                  onPressed: busy ? null : create),
            ]),
      ], gap: 14));
}

String _date(DateTime value) =>
    '${value.year}-${value.month.toString().padLeft(2, '0')}-${value.day.toString().padLeft(2, '0')}';

class _InviteList extends ConsumerWidget {
  const _InviteList();
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final invites = ref.watch(adminInvitesProvider);
    return KCard(
        title: 'Invites',
        child: invites.when(
            loading: () => const KLineLoader(),
            error: (error, _) => KMuted('Could not load invites: $error'),
            data: (items) => items.isEmpty
                ? const KMuted('No invites yet.')
                : kColumn([
                    for (final invite in items)
                      Row(key: ValueKey('invite-${invite.id}'), children: [
                        Expanded(
                            child: Text(
                                '${invite.role}${invite.email == null ? '' : ' · ${invite.email}'}')),
                        KBadge(invite.status == 'pending'
                            ? 'Pending until ${_date(invite.expiresAt)}'
                            : invite.status == 'redeemed'
                                ? 'Used'
                                : 'Expired'),
                        if (invite.status != 'redeemed')
                          IconButton(
                              tooltip: 'Revoke invite',
                              icon: const Icon(Icons.delete_outline, size: 18),
                              onPressed: () async {
                                final done = await _attempt(
                                    context,
                                    () => ref
                                        .read(adminActionsProvider)
                                        .revokeInvite(invite.id)
                                        .then((_) => true));
                                if (done ?? false) {
                                  ref.invalidate(adminInvitesProvider);
                                }
                              })
                        else
                          const SizedBox(width: 40),
                      ])
                  ], gap: 6)));
  }
}

class _MemberList extends ConsumerWidget {
  const _MemberList({required this.isOwner});
  final bool isOwner;

  bool _canManage(MemberRecord member) =>
      isOwner || !const {'owner', 'admin'}.contains(member.role);

  Future<void> _act(BuildContext context, WidgetRef ref, MemberRecord member,
      String action) async {
    final actions = ref.read(adminActionsProvider);
    if (action == 'reset') {
      final temporary =
          await _attempt(context, () => actions.resetPassword(member.subject));
      if (temporary != null && context.mounted) {
        await _showSecret(context,
            title: 'Temporary password',
            body:
                '${member.label} was signed out everywhere. Give them this password; '
                'it is shown only once. Tell them to sign in with it.',
            secret: temporary);
      }
    } else if (action == 'remove') {
      final confirmed = await showDialog<bool>(
          context: context,
          builder: (dialog) => AlertDialog(
                  title: Text('Remove ${member.label}?'),
                  content: const Text(
                      'They lose access to this organization immediately. Their projects stay.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(dialog, false),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: () => Navigator.pop(dialog, true),
                        child: const Text('Remove')),
                  ]));
      if (confirmed != true || !context.mounted) return;
      await _attempt(context, () => actions.removeMember(member.subject));
    } else if (action.startsWith('role:')) {
      await _attempt(context,
          () => actions.changeRole(member.subject, action.substring(5)));
    }
    ref.invalidate(adminMembersProvider);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final members = ref.watch(adminMembersProvider);
    return KCard(
        title: 'Members',
        child: members.when(
            loading: () => const KLineLoader(),
            error: (error, _) => KMuted('Could not load members: $error'),
            data: (items) => kColumn([
                  for (final member in items)
                    Row(key: ValueKey('member-${member.subject}'), children: [
                      Expanded(
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                            Text(member.you
                                ? '${member.label} (you)'
                                : member.label),
                            if (member.email != null &&
                                member.email != member.label)
                              KMuted(member.email!),
                          ])),
                      if (member.locked) const KBadge('Locked'),
                      const SizedBox(width: 8),
                      KBadge(member.role),
                      if (_canManage(member))
                        PopupMenuButton<String>(
                            tooltip: 'Manage ${member.label}',
                            onSelected: (value) =>
                                _act(context, ref, member, value),
                            itemBuilder: (_) => [
                                  for (final role in isOwner
                                      ? adminRoles
                                      : const ['member', 'viewer'])
                                    if (role != member.role)
                                      PopupMenuItem(
                                          value: 'role:$role',
                                          child: Text('Make $role')),
                                  const PopupMenuItem(
                                      value: 'reset',
                                      child: Text('Reset password')),
                                  if (!member.you)
                                    const PopupMenuItem(
                                        value: 'remove', child: Text('Remove')),
                                ])
                      else
                        const SizedBox(width: 40),
                    ])
                ], gap: 10)));
  }
}
