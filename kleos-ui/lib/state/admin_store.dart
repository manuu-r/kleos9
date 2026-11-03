import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/models/admin.dart';
import '../data/repositories/admin_repository.dart';
import 'production_store.dart';

final adminRepositoryProvider = Provider<AdminRepository>(
    (ref) => AdminRepository(ref.watch(productionApiProvider)));

/// Reloads when the signed-in organization changes so one organization's roster is never shown for another.
final adminMembersProvider =
    FutureProvider.autoDispose<List<MemberRecord>>((ref) {
  ref.watch(accountProvider.select((a) => a.asData?.value?.tenantId));
  return ref.watch(adminRepositoryProvider).members();
});

final adminInvitesProvider =
    FutureProvider.autoDispose<List<InviteRecord>>((ref) {
  ref.watch(accountProvider.select((a) => a.asData?.value?.tenantId));
  return ref.watch(adminRepositoryProvider).invites();
});

/// The administration commands the UI may issue.
typedef AdminActions = AdminRepository;

final adminActionsProvider =
    Provider<AdminActions>((ref) => ref.watch(adminRepositoryProvider));
