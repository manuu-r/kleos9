import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/json.dart';
import '../../../data/services/production_api.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import 'platform/isolated_app_frame.dart';

typedef CompanionPreviewRequest = ({
  String projectId,
  String artifactId,
  String sha256
});

class CompanionPreviewLease {
  const CompanionPreviewLease(
      {required this.url,
      required this.expiresAt,
      required this.artifactId,
      required this.sha256});
  final Uri url;
  final DateTime expiresAt;
  final String artifactId;
  final String sha256;

  factory CompanionPreviewLease.fromJson(
      JsonMap json, CompanionPreviewRequest request,
      {required Uri apiOrigin, required Uri appOrigin}) {
    final url = Uri.tryParse('${json['url'] ?? ''}');
    final expiresAt = DateTime.tryParse('${json['expiresAt'] ?? ''}');
    final sha256 = '${json['sha256'] ?? ''}';
    bool sameOrigin(Uri a, Uri b) =>
        const {'http', 'https'}.contains(b.scheme) && a.origin == b.origin;
    if (url == null ||
        !const {'http', 'https'}.contains(url.scheme) ||
        !url.hasAuthority ||
        url.userInfo.isNotEmpty ||
        sameOrigin(url, apiOrigin) ||
        sameOrigin(url, appOrigin) ||
        expiresAt == null ||
        json['artifactId'] != request.artifactId ||
        json['mode'] != 'isolated-web-build' ||
        !RegExp(r'^[a-f0-9]{64}$').hasMatch(sha256) ||
        (request.sha256.isNotEmpty && request.sha256 != sha256)) {
      throw const ProductionApiException(
          'The built app preview could not be verified. Refresh the workspace and try again.');
    }
    return CompanionPreviewLease(
        url: url,
        expiresAt: expiresAt,
        artifactId: request.artifactId,
        sha256: sha256);
  }
}

final companionPreviewProvider = FutureProvider.autoDispose
    .family<CompanionPreviewLease, CompanionPreviewRequest>(
        (ref, request) async {
  final repository = ref.watch(productionRepositoryProvider);
  final data = await repository.api.companionPreview(request.projectId,
      artifactId: request.artifactId,
      organizationId: repository.organizationId);
  return CompanionPreviewLease.fromJson(data, request,
      apiOrigin: Uri.parse(repository.api.baseUrl), appOrigin: Uri.base);
}, retry: (_, __) => null);

typedef CompanionFrameBuilder = Widget Function(
    Uri url, VoidCallback onLoaded, VoidCallback onError);
final companionFrameBuilderProvider = Provider<CompanionFrameBuilder>((ref) =>
    (url, onLoaded, onError) =>
        IsolatedAppFrame(url: url, onLoaded: onLoaded, onError: onError));

class CompanionAppPreview extends ConsumerStatefulWidget {
  const CompanionAppPreview(
      {super.key,
      required this.projectId,
      required this.artifactId,
      this.sha256 = ''});
  final String projectId, artifactId, sha256;
  @override
  ConsumerState<CompanionAppPreview> createState() =>
      _CompanionAppPreviewState();
}

class _CompanionAppPreviewState extends ConsumerState<CompanionAppPreview> {
  Timer? expiry;
  DateTime? scheduledExpiry;
  bool expired = false, failed = false;

  @override
  void didUpdateWidget(covariant CompanionAppPreview oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.artifactId != widget.artifactId ||
        oldWidget.projectId != widget.projectId) {
      expiry?.cancel();
      scheduledExpiry = null;
      expired = false;
      failed = false;
    }
  }

  @override
  void dispose() {
    expiry?.cancel();
    super.dispose();
  }

  void schedule(DateTime time) {
    if (scheduledExpiry == time) return;
    expiry?.cancel();
    scheduledExpiry = time;
    final remaining = time.difference(DateTime.now());
    expired = !remaining.isNegative && remaining > Duration.zero ? false : true;
    if (!expired) {
      expiry = Timer(remaining, () {
        if (mounted) setState(() => expired = true);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final request = (
      projectId: widget.projectId,
      artifactId: widget.artifactId,
      sha256: widget.sha256
    );
    final provider = companionPreviewProvider(request);
    final result = ref.watch(provider);
    final lease = result.asData?.value;
    if (lease != null) schedule(lease.expiresAt);
    void refresh() {
      expiry?.cancel();
      scheduledExpiry = null;
      setState(() {
        expired = false;
        failed = false;
      });
      ref.invalidate(provider);
    }

    if (result.hasError || expired || failed) {
      return Center(
          child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Icon(
                    expired
                        ? Icons.timer_outlined
                        : Icons.app_blocking_outlined,
                    color: KColors.muted,
                    size: 30),
                const SizedBox(height: 12),
                Text(
                    expired
                        ? 'This preview link has expired.'
                        : 'The built app preview could not be opened.',
                    textAlign: TextAlign.center),
                const SizedBox(height: 12),
                KButton(
                    label: expired ? 'Refresh preview link' : 'Retry built app',
                    compact: true,
                    onPressed: refresh),
              ])));
    }
    if (lease == null) {
      return const Center(
          child: Text('Opening built companion app…',
              style: TextStyle(color: KColors.muted)));
    }
    return Semantics(
        label: 'Interactive companion app from the generated web build',
        child: ref.watch(companionFrameBuilderProvider)(lease.url, () {}, () {
          if (mounted) setState(() => failed = true);
        }));
  }
}
