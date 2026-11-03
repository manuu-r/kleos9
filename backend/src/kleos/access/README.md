# Access authority

`AccessService` uses the shared PostgreSQL schema/session factory, with disposable
SQLite databases for focused tests. Import its models before schema initialization.
Production schema creation belongs to migrations.

Accounts require one-time setup with an operator token, followed by single-use
invitations. Passwords use salted scrypt. Session and invite secrets are stored
only as SHA-256 hashes. Browser sessions use HttpOnly cookies and a session-bound
HMAC CSRF token; native bearer sessions cannot be exchanged for browser cookies.
Membership is rechecked on every authenticated operation. Ten failed login
attempts per normalized email block further attempts until the 15-minute window
expires, including across process restarts.

Organization admins manage members/viewers; owners manage privileged roles. The
last owner cannot be demoted/removed. Password reset revokes all existing account
sessions and returns a one-time temporary password. No email service is invoked.

Development no-login creates a persistent local account only when explicitly
enabled and only in development/test. It does not grant access to other tenants
or rescue invalid credentials. Production rejects that configuration.

The HTTP adapter receives `AuthResult` cookie instructions, while the application
facade delegates membership queries/actions explicitly. This module does not
approve engineering output, external spending or project artifacts.
