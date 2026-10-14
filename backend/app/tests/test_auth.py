"""Tests: Auth & onboarding (build plan Day 4, PRD ON-1 and ON-2).

Done-when from the plan: a new user registers by phone, verifies the OTP,
consents and sees /auth/me. Covers token expiry and refresh.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.core.audit import AuditLog
from app.core.permissions import UserRole, require_roles
from app.core.security import create_access_token, hash_token
from app.domains.auth.dependencies import require_consent
from app.domains.auth.models import ConsentType, RefreshToken, User

PHONE = "+2348031234567"


async def signup(api, latest_code, phone="08031234567", role="patient", name="Ada Obi"):
    """Register + verify; returns the token response body."""
    r = await api.post("/auth/register", json={"phone": phone, "full_name": name, "role": role})
    assert r.status_code == 201, r.text
    e164 = r.json()["user"]["phone"]
    r = await api.post("/auth/otp/verify", json={"phone": e164, "code": latest_code(e164)})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- done-when
async def test_full_onboarding_flow(api, latest_code):
    """Register -> verify OTP -> consent -> /auth/me."""
    r = await api.post(
        "/auth/register",
        json={"phone": "0803 123 4567", "full_name": "  Ada   Obi ", "role": "guardian"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["phone"] == PHONE
    assert body["user"]["full_name"] == "Ada Obi"
    assert body["user"]["phone_verified"] is False
    assert body["expires_in"] == 300
    assert body["debug_code"] == latest_code(PHONE)  # test env exposes it

    r = await api.post("/auth/otp/verify", json={"phone": PHONE, "code": latest_code(PHONE)})
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["user"]["phone_verified"] is True
    assert tokens["expires_in"] == 30 * 60

    r = await api.get("/auth/me", headers=bearer(tokens["access_token"]))
    assert r.status_code == 200
    me = r.json()
    assert me["role"] == "guardian"
    assert me["consents"]["missing_required"] == ["terms", "health_data"]

    for consent_type in ("terms", "health_data"):
        r = await api.post(
            "/auth/consent",
            json={"consent_type": consent_type, "granted": True},
            headers=bearer(tokens["access_token"]),
        )
        assert r.status_code == 201, r.text

    me = (await api.get("/auth/me", headers=bearer(tokens["access_token"]))).json()
    assert me["consents"]["terms"] is True
    assert me["consents"]["health_data"] is True
    assert me["consents"]["missing_required"] == []


# --------------------------------------------------------------- register
async def test_register_rejects_verified_duplicate(api, latest_code):
    await signup(api, latest_code)
    r = await api.post(
        "/auth/register", json={"phone": PHONE, "full_name": "Someone Else", "role": "patient"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "phone_taken"


async def test_register_again_before_verifying_replaces_details(api, db, sms):
    await api.post("/auth/register", json={"phone": PHONE, "full_name": "Typo Nmae", "role": "patient"})
    await api.post("/auth/otp/send", json={"phone": PHONE})  # cooldown blocks this; fine
    from app.core.redis import get_redis_client

    await get_redis_client().delete(f"otp:cooldown:{PHONE}")
    r = await api.post(
        "/auth/register", json={"phone": PHONE, "full_name": "Real Name", "role": "guardian"}
    )
    assert r.status_code == 201
    assert r.json()["user"]["full_name"] == "Real Name"
    assert await db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("role", ["ops", "family_abroad", "admin"])
async def test_register_rejects_non_self_service_roles(api, role):
    r = await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": role})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("phone", ["12345", "0603123456", "+234123", "080312345678", "abc"])
async def test_register_rejects_bad_phone(api, phone):
    r = await api.post("/auth/register", json={"phone": phone, "full_name": "Ada Obi", "role": "patient"})
    assert r.status_code == 422


async def test_register_rejects_unknown_fields(api):
    r = await api.post(
        "/auth/register",
        json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient", "is_active": False},
    )
    assert r.status_code == 422


async def test_validation_errors_do_not_echo_input(api):
    r = await api.post("/auth/otp/verify", json={"phone": PHONE, "code": "12ab"})
    assert r.status_code == 422
    assert "12ab" not in r.text


# -------------------------------------------------------------------- OTP
async def test_otp_send_unknown_number_is_404(api):
    r = await api.post("/auth/otp/send", json={"phone": PHONE})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "account_not_found"


async def test_otp_resend_cooldown(api, latest_code):
    await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient"})
    r = await api.post("/auth/otp/send", json={"phone": PHONE})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "otp_cooldown"
    assert int(r.headers["Retry-After"]) > 0


async def test_otp_hourly_limit(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "OTP_RESEND_COOLDOWN_SECONDS", 0)
    await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient"})
    for _ in range(get_settings().OTP_MAX_SENDS_PER_HOUR - 1):
        assert (await api.post("/auth/otp/send", json={"phone": PHONE})).status_code == 202
    r = await api.post("/auth/otp/send", json={"phone": PHONE})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "otp_rate_limited"


async def test_new_code_replaces_old_code(api, latest_code, monkeypatch):
    monkeypatch.setattr(get_settings(), "OTP_RESEND_COOLDOWN_SECONDS", 0)
    await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient"})
    first = latest_code(PHONE)
    await api.post("/auth/otp/send", json={"phone": PHONE})
    second = latest_code(PHONE)
    if first != second:  # 1-in-a-million chance they collide
        r = await api.post("/auth/otp/verify", json={"phone": PHONE, "code": first})
        assert r.status_code == 400
    r = await api.post("/auth/otp/verify", json={"phone": PHONE, "code": second})
    assert r.status_code == 200


async def test_wrong_code_counts_down_then_locks(api, latest_code):
    await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient"})
    good = latest_code(PHONE)
    wrong = "000000" if good != "000000" else "111111"
    max_attempts = get_settings().OTP_MAX_ATTEMPTS
    for i in range(1, max_attempts):
        r = await api.post("/auth/otp/verify", json={"phone": PHONE, "code": wrong})
        assert r.status_code == 400
        assert r.json()["error"]["details"]["attempts_remaining"] == max_attempts - i
    r = await api.post("/auth/otp/verify", json={"phone": PHONE, "code": wrong})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "otp_locked"
    # The real code is burned too.
    r = await api.post("/auth/otp/verify", json={"phone": PHONE, "code": good})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "otp_expired"


async def test_code_is_single_use(api, latest_code):
    await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient"})
    code = latest_code(PHONE)
    assert (await api.post("/auth/otp/verify", json={"phone": PHONE, "code": code})).status_code == 200
    r = await api.post("/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "otp_expired"


async def test_sms_body_never_logged(api, caplog):
    caplog.set_level("INFO")
    r = await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient"})
    code = r.json()["debug_code"]
    assert code not in caplog.text
    assert PHONE not in caplog.text  # numbers are masked


async def test_debug_code_hidden_outside_local_dev(api, monkeypatch):
    monkeypatch.setattr(type(get_settings()), "expose_otp_in_response", property(lambda s: False))
    r = await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient"})
    assert r.status_code == 201
    assert r.json()["debug_code"] is None


# ------------------------------------------------------------------ login
async def test_login_with_otp(api, latest_code, db):
    await signup(api, latest_code)
    from app.core.redis import get_redis_client

    await get_redis_client().delete(f"otp:cooldown:{PHONE}")
    assert (await api.post("/auth/otp/send", json={"phone": "+234 803 123 4567"})).status_code == 202
    r = await api.post("/auth/login", json={"phone": PHONE, "code": latest_code(PHONE)})
    assert r.status_code == 200
    assert r.json()["user"]["phone"] == PHONE
    actions = (await db.scalars(select(AuditLog.action).order_by(AuditLog.id))).all()
    assert actions.count("auth.login") == 2
    assert "auth.phone_verified" in actions


async def test_login_requires_verified_phone(api, latest_code):
    await api.post("/auth/register", json={"phone": PHONE, "full_name": "Ada Obi", "role": "patient"})
    r = await api.post("/auth/login", json={"phone": PHONE, "code": latest_code(PHONE)})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "phone_not_verified"


async def test_login_unknown_number(api):
    r = await api.post("/auth/login", json={"phone": PHONE, "code": "123456"})
    assert r.status_code == 404


async def test_disabled_account_cannot_get_code_or_use_tokens(api, latest_code, db):
    tokens = await signup(api, latest_code)
    user = await db.scalar(select(User).where(User.phone == PHONE))
    user.is_active = False
    await db.commit()

    from app.core.redis import get_redis_client

    await get_redis_client().delete(f"otp:cooldown:{PHONE}")
    r = await api.post("/auth/otp/send", json={"phone": PHONE})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "account_disabled"
    r = await api.get("/auth/me", headers=bearer(tokens["access_token"]))
    assert r.status_code == 401
    r = await api.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


# ----------------------------------------------------------------- tokens
async def test_me_requires_token(api):
    r = await api.get("/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_authenticated"
    assert r.headers["WWW-Authenticate"] == "Bearer"


async def test_expired_access_token_is_rejected(api, latest_code, db):
    await signup(api, latest_code)
    user = await db.scalar(select(User).where(User.phone == PHONE))
    expired, _ = create_access_token(user.id, user.role.value, expires_delta=timedelta(seconds=-5))
    r = await api.get("/auth/me", headers=bearer(expired))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "token_expired"


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c"])
async def test_garbage_token_is_rejected(api, token):
    r = await api.get("/auth/me", headers=bearer(token))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "token_invalid"


async def test_token_signed_with_other_key_is_rejected(api, latest_code, db):
    import jwt

    await signup(api, latest_code)
    user = await db.scalar(select(User).where(User.phone == PHONE))
    forged = jwt.encode(
        {"sub": str(user.id), "role": "ops", "type": "access", "iss": "carekin-api",
         "iat": 1, "exp": 4102444800},
        "attacker-key-attacker-key-attacker-key",
        algorithm="HS256",
    )
    r = await api.get("/auth/me", headers=bearer(forged))
    assert r.status_code == 401


async def test_refresh_rotates_tokens(api, latest_code, db):
    tokens = await signup(api, latest_code)
    r = await api.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    assert (await api.get("/auth/me", headers=bearer(new["access_token"]))).status_code == 200

    old_row = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(tokens["refresh_token"]))
    )
    assert old_row.revoked_at is not None
    # Raw tokens are never stored.
    stored = (await db.scalars(select(RefreshToken.token_hash))).all()
    assert tokens["refresh_token"] not in stored


async def test_refresh_reuse_revokes_every_session(api, latest_code, db):
    tokens = await signup(api, latest_code)
    rotated = (await api.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).json()

    # Replaying the old token: treated as theft.
    r = await api.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "refresh_reused"
    # The legitimate newer token is now dead too.
    r = await api.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert r.status_code == 401
    active = await db.scalar(
        select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None))
    )
    assert active == 0


async def test_expired_refresh_token(api, latest_code, db):
    tokens = await signup(api, latest_code)
    row = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(tokens["refresh_token"]))
    )
    row.expires_at = row.expires_at - timedelta(days=30)
    await db.commit()
    r = await api.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "refresh_expired"


async def test_unknown_refresh_token(api):
    r = await api.post("/auth/refresh", json={"refresh_token": "x" * 64})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "refresh_invalid"


async def test_logout_revokes_refresh_token(api, latest_code):
    tokens = await signup(api, latest_code)
    r = await api.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    # Idempotent.
    assert (await api.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})).status_code == 200
    r = await api.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


# ---------------------------------------------------------------- consent
async def test_consent_withdraw_and_history(api, latest_code, db):
    tokens = await signup(api, latest_code)
    h = bearer(tokens["access_token"])
    await api.post("/auth/consent", json={"consent_type": "health_data", "granted": True}, headers=h)
    r = await api.post("/auth/consent", json={"consent_type": "health_data", "granted": False}, headers=h)
    assert r.status_code == 201
    assert r.json()["granted"] is False

    me = (await api.get("/auth/me", headers=h)).json()
    assert me["consents"]["health_data"] is False
    actions = (await db.scalars(select(AuditLog.action).order_by(AuditLog.id))).all()
    assert "consent.grant" in actions and "consent.withdraw" in actions


async def test_consent_grant_is_idempotent(api, latest_code):
    tokens = await signup(api, latest_code)
    h = bearer(tokens["access_token"])
    a = await api.post("/auth/consent", json={"consent_type": "terms", "granted": True}, headers=h)
    b = await api.post("/auth/consent", json={"consent_type": "terms", "granted": True}, headers=h)
    assert a.json()["id"] == b.json()["id"]


async def test_consent_rejects_stale_policy_version(api, latest_code):
    tokens = await signup(api, latest_code)
    r = await api.post(
        "/auth/consent",
        json={"consent_type": "terms", "granted": True, "policy_version": "2020-01"},
        headers=bearer(tokens["access_token"]),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "policy_outdated"


async def test_policy_bump_requires_new_consent(api, latest_code, monkeypatch):
    tokens = await signup(api, latest_code)
    h = bearer(tokens["access_token"])
    await api.post("/auth/consent", json={"consent_type": "terms", "granted": True}, headers=h)
    monkeypatch.setattr(get_settings(), "CONSENT_POLICY_VERSION", "2027-01-v2")
    me = (await api.get("/auth/me", headers=h)).json()
    assert me["consents"]["terms"] is False
    assert "terms" in me["consents"]["missing_required"]


async def test_passport_share_consent_not_given_here(api, latest_code):
    tokens = await signup(api, latest_code)
    r = await api.post(
        "/auth/consent",
        json={"consent_type": "passport_share", "granted": True},
        headers=bearer(tokens["access_token"]),
    )
    assert r.status_code == 400


async def test_consent_requires_login(api):
    r = await api.post("/auth/consent", json={"consent_type": "terms", "granted": True})
    assert r.status_code == 401


async def test_caregiver_only_needs_terms(api, latest_code):
    tokens = await signup(api, latest_code, role="caregiver")
    me = (await api.get("/auth/me", headers=bearer(tokens["access_token"]))).json()
    assert me["consents"]["missing_required"] == ["terms"]


# -------------------------------------------------------------------- me
async def test_update_me(api, latest_code):
    tokens = await signup(api, latest_code)
    h = bearer(tokens["access_token"])
    r = await api.patch(
        "/auth/me",
        json={"full_name": "Ada N. Obi", "email": "Ada@Example.com", "preferred_language": "ig"},
        headers=h,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["full_name"] == "Ada N. Obi"
    assert body["email"] == "ada@example.com"
    assert body["preferred_language"] == "ig"

    r = await api.patch("/auth/me", json={"email": None}, headers=h)
    assert r.json()["email"] is None
    assert r.json()["full_name"] == "Ada N. Obi"


@pytest.mark.parametrize("field", [{"role": "ops"}, {"phone": "+2348099999999"}, {"is_active": True}])
async def test_update_me_cannot_change_protected_fields(api, latest_code, field):
    tokens = await signup(api, latest_code)
    r = await api.patch("/auth/me", json=field, headers=bearer(tokens["access_token"]))
    assert r.status_code == 422


# ------------------------------------------------ dependencies for other days
async def test_require_roles_and_require_consent(api, latest_code):
    """The guards later domains use: role checks and the ON-2 consent gate."""
    import httpx
    from fastapi import FastAPI

    from app.core.exceptions import register_exception_handlers

    probe = FastAPI()
    register_exception_handlers(probe)

    @probe.get("/ops", dependencies=[require_roles(UserRole.OPS)])
    async def ops_only() -> dict:
        return {"ok": True}

    @probe.get("/health-data", dependencies=[require_consent(ConsentType.HEALTH_DATA)])
    async def needs_consent() -> dict:
        return {"ok": True}

    tokens = await signup(api, latest_code, role="patient")
    h = bearer(tokens["access_token"])
    transport = httpx.ASGITransport(app=probe)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as client:
        r = await client.get("/ops", headers=h)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "role_forbidden"

        r = await client.get("/health-data", headers=h)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "consent_required"

        await api.post(
            "/auth/consent", json={"consent_type": "health_data", "granted": True}, headers=h
        )
        assert (await client.get("/health-data", headers=h)).status_code == 200
        assert (await client.get("/ops")).status_code == 401
