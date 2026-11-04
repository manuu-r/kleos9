import '../models/admin.dart';
import '../models/json.dart';
import '../services/api_routes.dart';
import '../services/production_api.dart';

/// Organization administration over the authenticated session. UI and state code never build auth paths.
class AdminRepository {
  const AdminRepository(this.api);
  final ProductionApi api;

  Future<List<MemberRecord>> members() async =>
      asMaps((await api.get(ApiRoutes.authMembers))['members'])
          .map(MemberRecord.fromJson)
          .toList();

  Future<List<InviteRecord>> invites() async =>
      asMaps((await api.get(ApiRoutes.authInvites))['invites'])
          .map(InviteRecord.fromJson)
          .toList();

  Future<CreatedInvite> createInvite(
          {required String role,
          String? email,
          required int expiresHours}) async =>
      CreatedInvite.fromJson(await api.post(ApiRoutes.authInvites, {
        'role': role,
        if (email != null && email.isNotEmpty) 'email': email,
        'expiresHours': expiresHours,
      }));

  Future<void> revokeInvite(String id) => api.delete(ApiRoutes.authInvite(id));

  Future<void> changeRole(String subject, String role) =>
      api.post(ApiRoutes.authMemberRole(subject), {'role': role});

  Future<void> removeMember(String subject) =>
      api.delete(ApiRoutes.authMember(subject));

  /// Returns the temporary password, which is shown once.
  Future<String> resetPassword(String subject) async => (await api.post(
          ApiRoutes.authMemberResetPassword(subject), {}))['temporaryPassword']
      as String;
}
