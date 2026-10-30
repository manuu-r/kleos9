import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/models/admin.dart';
import '../data/models/json.dart';
import '../data/services/production_api.dart';
import 'production_store.dart';

/// Reloads when the signed-in organization changes so one organization's roster is never shown for another.
final adminMembersProvider =
    FutureProvider.autoDispose<List<MemberRecord>>((ref) async {
  ref.watch(accountProvider.select((a) => a.asData?.value?.tenantId));
  final result = await ref.watch(productionApiProvider).get('/auth/members');
  return asMaps(result['members']).map(MemberRecord.fromJson).toList();
});

final adminInvitesProvider =
    FutureProvider.autoDispose<List<InviteRecord>>((ref) async {
  ref.watch(accountProvider.select((a) => a.asData?.value?.tenantId));
  final result = await ref.watch(productionApiProvider).get('/auth/invites');
  return asMaps(result['invites']).map(InviteRecord.fromJson).toList();
});

class AdminActions {
  const AdminActions(this._api);
  final ProductionApi _api;

  Future<CreatedInvite> createInvite(
          {required String role,
          String? email,
          required int expiresHours}) async =>
      CreatedInvite.fromJson(await _api.post('/auth/invites', {
        'role': role,
        if (email != null && email.isNotEmpty) 'email': email,
        'expiresHours': expiresHours,
      }));

  Future<void> revokeInvite(String id) =>
      _api.delete('/auth/invites/${Uri.encodeComponent(id)}');

  Future<void> changeRole(String subject, String role) => _api.post(
      '/auth/members/${Uri.encodeComponent(subject)}/role', {'role': role});

  Future<void> removeMember(String subject) =>
      _api.delete('/auth/members/${Uri.encodeComponent(subject)}');

  /// Returns the temporary password, which is shown once.
  Future<String> resetPassword(String subject) async => (await _api.post(
      '/auth/members/${Uri.encodeComponent(subject)}/reset-password',
      {}))['temporaryPassword'] as String;
}

final adminActionsProvider = Provider<AdminActions>(
    (ref) => AdminActions(ref.watch(productionApiProvider)));
