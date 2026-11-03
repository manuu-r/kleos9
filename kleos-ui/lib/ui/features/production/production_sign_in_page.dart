import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../data/models/json.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import 'production_widgets.dart';

class ProductionSignInPage extends ConsumerStatefulWidget {
  const ProductionSignInPage({super.key, required this.loading, this.error});
  final bool loading;
  final String? error;
  @override
  ConsumerState<ProductionSignInPage> createState() => _SignInPageState();
}

class _SignInPageState extends ConsumerState<ProductionSignInPage> {
  final token = TextEditingController();
  final email = TextEditingController();
  final password = TextEditingController();
  final inviteCode = TextEditingController();
  final name = TextEditingController();
  final setupToken = TextEditingController();
  final organization = TextEditingController();
  bool creating = false;
  late Future<JsonMap> config = ref.read(productionAuthProvider).config();
  @override
  void dispose() {
    token.dispose();
    email.dispose();
    password.dispose();
    inviteCode.dispose();
    name.dispose();
    setupToken.dispose();
    organization.dispose();
    super.dispose();
  }

  Future<void> signIn({required bool development, bool setup = false}) {
    FocusManager.instance.primaryFocus?.unfocus();
    final account = ref.read(accountProvider.notifier);
    if (development) return account.signIn(developmentToken: token.text);
    if (setup) {
      return account.setUp(
          setupToken: setupToken.text,
          organization: organization.text,
          email: email.text,
          password: password.text,
          name: name.text);
    }
    if (creating) {
      return account.signUp(
          inviteCode: inviteCode.text,
          email: email.text,
          password: password.text,
          name: name.text);
    }
    return account.signIn(email: email.text, password: password.text);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      backgroundColor: KColors.bg,
      body: Stack(children: [
        const Positioned.fill(child: KAmbient()),
        SafeArea(
            child: Center(
          child: SingleChildScrollView(
            padding: EdgeInsets.all(KSpace.gutter(context)),
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 440),
                child: KCard(
                    padding: EdgeInsets.all(KSpace.phone(context) ? 20 : 28),
                    child: kColumn([
                      Row(children: [
                        Container(
                            width: 34,
                            height: 34,
                            alignment: Alignment.center,
                            decoration: BoxDecoration(
                                color: KColors.surface3,
                                border: Border.all(color: KColors.borderStrong),
                                borderRadius:
                                    BorderRadius.circular(KRadius.control)),
                            child: const Text('K9',
                                style: TextStyle(
                                    fontFamily: 'GeistMono',
                                    color: KColors.text,
                                    fontSize: 12,
                                    letterSpacing: -.4,
                                    fontWeight: FontWeight.w700))),
                        const SizedBox(width: 12),
                        const KBadge('Kleos9 private beta'),
                      ]),
                      Text('Your engineering workspace.',
                          style: Theme.of(context)
                              .textTheme
                              .headlineLarge
                              ?.copyWith(fontSize: 24)),
                      const KMuted(
                          'Sign in with your invited account to design, inspect, and revise your builds.'),
                      if (widget.error != null)
                        ProductionNotice(widget.error!, error: true),
                      FutureBuilder<JsonMap>(
                          future: config,
                          builder: (context, snapshot) {
                            if (snapshot.data?['loginRequired'] == false) {
                              return KButton(
                                  label: 'Retry workspace',
                                  onPressed: () =>
                                      ref.invalidate(accountProvider));
                            }
                            final dev =
                                snapshot.data?['authMode'] == 'development';
                            final setup =
                                snapshot.data?['setupRequired'] == true;
                            final signup = creating && !setup;
                            return kColumn([
                              if (dev)
                                TextField(
                                    key: const ValueKey('production-dev-token'),
                                    controller: token,
                                    obscureText: true,
                                    enableSuggestions: false,
                                    autocorrect: false,
                                    decoration: const InputDecoration(
                                        labelText: 'Development access token'),
                                    onSubmitted: (_) =>
                                        signIn(development: true)),
                              if (!dev && snapshot.hasData) ...[
                                if (setup)
                                  const ProductionNotice(
                                      'No account exists yet. Create the first owner with the setup token from your AWS Secrets Manager.'),
                                if (setup)
                                  TextField(
                                      key: const ValueKey(
                                          'production-setup-token'),
                                      controller: setupToken,
                                      obscureText: true,
                                      enableSuggestions: false,
                                      autocorrect: false,
                                      decoration: const InputDecoration(
                                          labelText: 'Setup token')),
                                if (setup)
                                  TextField(
                                      key: const ValueKey(
                                          'production-organization'),
                                      controller: organization,
                                      textCapitalization:
                                          TextCapitalization.words,
                                      decoration: const InputDecoration(
                                          labelText: 'Organization name')),
                                if (signup)
                                  TextField(
                                      key: const ValueKey(
                                          'production-invite-code'),
                                      controller: inviteCode,
                                      enableSuggestions: false,
                                      autocorrect: false,
                                      decoration: const InputDecoration(
                                          labelText: 'Invite code')),
                                if (creating || setup)
                                  TextField(
                                      key: const ValueKey('production-name'),
                                      controller: name,
                                      textCapitalization:
                                          TextCapitalization.words,
                                      autofillHints: const [AutofillHints.name],
                                      decoration: const InputDecoration(
                                          labelText: 'Name (optional)')),
                                TextField(
                                    key: const ValueKey('production-email'),
                                    controller: email,
                                    keyboardType: TextInputType.emailAddress,
                                    autofillHints: const [AutofillHints.email],
                                    autocorrect: false,
                                    decoration: const InputDecoration(
                                        labelText: 'Email')),
                                TextField(
                                    key: const ValueKey('production-password'),
                                    controller: password,
                                    obscureText: true,
                                    enableSuggestions: false,
                                    autocorrect: false,
                                    autofillHints: [
                                      creating || setup
                                          ? AutofillHints.newPassword
                                          : AutofillHints.password
                                    ],
                                    decoration: InputDecoration(
                                        labelText: creating || setup
                                            ? 'Choose a password (12+ characters)'
                                            : 'Password'),
                                    onSubmitted: (_) => signIn(
                                        development: false, setup: setup)),
                              ],
                              if (snapshot.hasError)
                                ProductionNotice(snapshot.error.toString(),
                                    error: true),
                              if (snapshot.hasError)
                                KButton(
                                    label: 'Retry connection',
                                    onPressed: () {
                                      setState(() => config = ref
                                          .read(productionAuthProvider)
                                          .config());
                                      ref.invalidate(accountProvider);
                                    }),
                              KButton(
                                  key: const ValueKey('production-sign-in'),
                                  expand: true,
                                  label: widget.loading
                                      ? 'Connecting…'
                                      : setup
                                          ? 'Create owner account'
                                          : creating
                                              ? 'Create account'
                                              : 'Sign in',
                                  primary: true,
                                  onPressed: widget.loading || !snapshot.hasData
                                      ? null
                                      : () => signIn(
                                          development: dev, setup: setup)),
                              if (!dev && snapshot.hasData && !setup)
                                KButton(
                                    key: const ValueKey(
                                        'production-toggle-signup'),
                                    label: creating
                                        ? 'I already have an account'
                                        : 'I have an invite code',
                                    onPressed: widget.loading
                                        ? null
                                        : () => setState(
                                            () => creating = !creating)),
                            ]);
                          }),
                      const KMuted(
                          'Engineering runs use real models and tools. Physical fulfillment remains simulated in this release.'),
                    ], gap: 20))),
          ),
        ))
      ]));
}
