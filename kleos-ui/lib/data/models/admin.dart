import 'package:flutter/foundation.dart';
import 'json.dart';

const adminRoles = ['owner', 'admin', 'member', 'viewer'];

@immutable
class MemberRecord {
  const MemberRecord(
      {required this.subject,
      required this.email,
      required this.name,
      required this.role,
      required this.you,
      required this.locked});
  factory MemberRecord.fromJson(JsonMap json) => MemberRecord(
      subject: json['subject'] as String,
      email: json['email'] as String?,
      name: json['name'] as String? ?? '',
      role: json['role'] as String,
      you: json['you'] == true,
      locked: json['locked'] == true);
  final String subject;
  final String? email;
  final String name;
  final String role;
  final bool you;
  final bool locked;
  String get label => name.isNotEmpty ? name : (email ?? subject);
}

@immutable
class InviteRecord {
  const InviteRecord(
      {required this.id,
      required this.role,
      required this.email,
      required this.status,
      required this.expiresAt});
  factory InviteRecord.fromJson(JsonMap json) => InviteRecord(
      id: json['id'] as String,
      role: json['role'] as String,
      email: json['email'] as String?,
      status: json['status'] as String,
      expiresAt: DateTime.parse(json['expiresAt'] as String).toLocal());
  final String id;
  final String role;
  final String? email;
  final String status;
  final DateTime expiresAt;
}

/// The one-time result of creating an invite; the code is never retrievable again.
@immutable
class CreatedInvite {
  const CreatedInvite(this.code, this.role, this.email, this.expiresInHours);
  factory CreatedInvite.fromJson(JsonMap json) => CreatedInvite(
      json['code'] as String,
      json['role'] as String,
      json['email'] as String?,
      json['expiresInHours'] as int);
  final String code;
  final String role;
  final String? email;
  final int expiresInHours;
}
