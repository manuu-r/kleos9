from datetime import timedelta

from kleos.db import Account, Invite, Membership, Organization, now

PASSWORD = "correct horse battery"
TOKEN = "setup-token-for-tests-only"


def join(auth, role, email):
    """Sign a new person up with a fresh invite and leave the client logged in as them."""
    auth.client.cookies.clear()
    response = auth.client.post("/api/v1/auth/signup", json={
        "inviteCode": auth.invite(role), "email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["subject"]


def csrf(auth):
    return {"X-CSRF-Token": auth.client.cookies["kleos_csrf"]}


def test_first_run_setup_creates_the_owner_once(auth):
    assert auth.client.get("/api/v1/auth/config").json()["setupRequired"] is True
    form = {"setupToken": TOKEN, "organization": " Acme ", "email": "boss@example.com", "password": PASSWORD}
    assert auth.client.post("/api/v1/auth/setup", json={**form, "setupToken": "wrong" * 4}).status_code == 403
    done = auth.client.post("/api/v1/auth/setup", json=form)
    assert done.status_code == 200
    session = auth.client.get("/api/v1/auth/session").json()
    assert session["role"] == "owner"
    with auth.db.transaction() as db:
        assert db.query(Organization).filter(Organization.name == "Acme").count() == 1
    # The portal is closed for good once any account exists, even with the right token.
    again = auth.client.post("/api/v1/auth/setup", json={**form, "email": "other@example.com"})
    assert (again.status_code, again.json()["code"]) == (403, "setup_unavailable")
    assert auth.client.get("/api/v1/auth/config").json()["setupRequired"] is False


def test_setup_is_unavailable_without_a_configured_token(auth):
    auth.app.state.settings.setup_token = None
    assert auth.client.get("/api/v1/auth/config").json()["setupRequired"] is False
    assert auth.client.post("/api/v1/auth/setup", json={
        "setupToken": TOKEN, "organization": "X", "email": "a@example.com", "password": PASSWORD}).status_code == 403


def test_only_admins_see_the_portal(auth):
    join(auth, "member", "member@example.com")
    for path in ("/api/v1/auth/members", "/api/v1/auth/invites"):
        assert auth.client.get(path).status_code == 403
    auth.client.cookies.clear()
    assert auth.client.get("/api/v1/auth/members").status_code == 401


def test_member_listing_shows_people_in_this_organization_only(auth):
    join(auth, "member", "member@example.com")
    join(auth, "owner", "owner@example.com")
    with auth.db.transaction() as session:
        session.add(Organization(id="org-b", name="B"))
        session.flush()
        session.add(Membership(subject="outsider", tenant_id="org-b", role="member"))
    listed = auth.client.get("/api/v1/auth/members").json()["members"]
    assert {m["email"] for m in listed} == {"member@example.com", "owner@example.com"}
    assert [m["email"] for m in listed if m["you"]] == ["owner@example.com"]


def test_invite_list_status_and_revoke(auth):
    join(auth, "owner", "owner@example.com")
    minted = auth.client.post("/api/v1/auth/invites", json={"role": "member"}, headers=csrf(auth)).json()
    auth.invite("viewer")
    with auth.db.transaction() as session:
        old = session.query(Invite).filter(Invite.role == "viewer").one()
        old.expires_at = now() - timedelta(hours=1)
    listed = auth.client.get("/api/v1/auth/invites").json()["invites"]
    assert sorted(i["status"] for i in listed) == ["expired", "pending", "redeemed"]
    assert all("code" not in i and "codeHash" not in i for i in listed)
    pending = next(i for i in listed if i["status"] == "pending")
    assert auth.client.delete(f"/api/v1/auth/invites/{pending['id']}", headers=csrf(auth)).status_code == 200
    # A revoked code no longer works.
    auth.client.cookies.clear()
    revoked = auth.client.post("/api/v1/auth/signup", json={
        "inviteCode": minted["code"], "email": "late@example.com", "password": PASSWORD})
    assert revoked.json()["code"] == "invite_invalid"


def test_redeemed_invites_cannot_be_revoked(auth):
    join(auth, "owner", "owner@example.com")
    redeemed = auth.client.get("/api/v1/auth/invites").json()["invites"][0]
    result = auth.client.delete(f"/api/v1/auth/invites/{redeemed['id']}", headers=csrf(auth))
    assert (result.status_code, result.json()["code"]) == (409, "invite_redeemed")


def test_admins_manage_only_members_and_viewers(auth):
    member = join(auth, "member", "member@example.com")
    owner = join(auth, "owner", "owner@example.com")
    join(auth, "admin", "admin@example.com")
    headers = csrf(auth)
    assert auth.client.post(f"/api/v1/auth/members/{member}/role", json={"role": "viewer"}, headers=headers).status_code == 200
    assert auth.client.post(f"/api/v1/auth/members/{member}/role", json={"role": "admin"}, headers=headers).status_code == 403
    assert auth.client.post(f"/api/v1/auth/members/{owner}/role", json={"role": "member"}, headers=headers).status_code == 403
    assert auth.client.delete(f"/api/v1/auth/members/{owner}", headers=headers).status_code == 403
    assert auth.client.post(f"/api/v1/auth/members/{owner}/reset-password", headers=headers).status_code == 403
    assert auth.client.post("/api/v1/auth/invites", json={"role": "admin"}, headers=headers).status_code == 403
    assert auth.client.delete(f"/api/v1/auth/members/{member}", headers=headers).status_code == 200
    assert auth.client.post("/api/v1/auth/members/nobody/role", json={"role": "viewer"}, headers=headers).status_code == 404


def test_last_owner_cannot_be_demoted_or_removed_and_self_removal_is_blocked(auth):
    owner = join(auth, "owner", "owner@example.com")
    headers = csrf(auth)
    demote = auth.client.post(f"/api/v1/auth/members/{owner}/role", json={"role": "admin"}, headers=headers)
    assert (demote.status_code, demote.json()["code"]) == (409, "last_owner")
    assert auth.client.delete(f"/api/v1/auth/members/{owner}", headers=headers).json()["code"] == "self_removal"
    second = join(auth, "owner", "second@example.com")
    assert auth.client.post(f"/api/v1/auth/members/{owner}/role", json={"role": "admin"}, headers=csrf(auth)).status_code == 200
    # The remaining owner is now the last one.
    assert auth.client.post(f"/api/v1/auth/members/{second}/role", json={"role": "member"}, headers=csrf(auth)).json()["code"] == "last_owner"


def test_removing_a_member_ends_their_access(auth):
    member = join(auth, "member", "member@example.com")
    member_client_cookies = dict(auth.client.cookies)
    join(auth, "owner", "owner@example.com")
    assert auth.client.delete(f"/api/v1/auth/members/{member}", headers=csrf(auth)).status_code == 200
    auth.client.cookies.clear()
    for name, value in member_client_cookies.items():
        auth.client.cookies.set(name, value)
    assert auth.client.get("/api/v1/auth/session").status_code == 401


def test_admin_password_reset_returns_a_temporary_password(auth):
    member = join(auth, "member", "member@example.com")
    join(auth, "owner", "owner@example.com")
    reset = auth.client.post(f"/api/v1/auth/members/{member}/reset-password", headers=csrf(auth)).json()
    assert reset["email"] == "member@example.com"
    auth.client.cookies.clear()
    assert auth.client.post("/api/v1/auth/login", json={
        "email": "member@example.com", "password": PASSWORD}).status_code == 401
    assert auth.client.post("/api/v1/auth/login", json={
        "email": "member@example.com", "password": reset["temporaryPassword"]}).status_code == 200
    with auth.db.transaction() as session:
        assert session.query(Account).count() == 2
