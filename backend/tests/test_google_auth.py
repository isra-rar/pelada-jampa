"""Login com Google: validação do ID token e regras de vínculo de contas."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings
from app.services import google_auth
from tests.test_api_flows import G

CLIENT_ID = "1234567890-teste.apps.googleusercontent.com"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJwks:
    """Substitui a busca das chaves públicas do Google pela chave de teste."""

    def get_signing_key_from_jwt(self, token):
        return type("Key", (), {"key": _KEY.public_key()})()


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(google_auth, "_client", lambda: _FakeJwks())


def id_token(sub="g-1", email="craque@gmail.com", *, key=None, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": "Craque da Silva",
        "picture": "https://lh3.googleusercontent.com/a/foto",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, key or _KEY, algorithm="RS256", headers={"kid": "teste"})


def google_login(api, token, expected=200):
    return api.post("/auth/google", {}, expected, json={"credential": token})


def bearer(out: dict) -> dict:
    return {"Authorization": f"Bearer {out['access_token']}"}


def test_disabled_without_client_id(apiclient, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", None)
    assert apiclient.get("/auth/config", {}) == {"google_client_id": None}
    r = google_login(apiclient, "x" * 40, 422)
    assert r["error"]["code"] == "google_disabled"


def test_new_account_via_google(apiclient, google):
    api = apiclient
    assert api.get("/auth/config", {})["google_client_id"] == CLIENT_ID
    me = api.get("/me", bearer(google_login(api, id_token())))
    assert me["email"] == "craque@gmail.com" and me["name"] == "Craque da Silva"
    assert me["google_linked"] is True and me["has_password"] is False
    assert me["photo_url"].startswith("https://lh3.googleusercontent.com/")
    # segundo login cai na mesma conta (pelo "sub"), mesmo que o e-mail do Google mude
    again = api.get("/me", bearer(google_login(api, id_token(email="novo@gmail.com"))))
    assert again["id"] == me["id"]
    # conta só-Google não pode ser "tomada" por cadastro com senha
    r = api.post("/auth/register", {}, 409, json={"email": "craque@gmail.com", "name": "Intruso",
                                                   "password": "senha-qualquer"})
    assert r["error"]["code"] == "email_taken"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"aud": "outro-app.apps.googleusercontent.com"}, "invalid_google_token"),
        ({"iss": "https://evil.example.com"}, "invalid_google_token"),
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "invalid_google_token"),
        ({"email_verified": False}, "google_email_unverified"),
        ({"email_verified": "false"}, "google_email_unverified"),
    ],
)
def test_rejects_invalid_tokens(apiclient, google, overrides, code):
    r = google_login(apiclient, id_token(**overrides), 401)
    assert r["error"]["code"] == code


def test_rejects_token_signed_by_another_key(apiclient, google):
    r = google_login(apiclient, id_token(key=_OTHER_KEY), 401)
    assert r["error"]["code"] == "invalid_google_token"


def test_links_existing_password_account_by_verified_email(apiclient, google):
    api = apiclient
    user = api.user("Ze")  # ze@example.com com senha
    me = api.get("/me", bearer(google_login(api, id_token(sub="g-ze", email="ze@example.com"))))
    assert me["id"] == api.get("/me", user)["id"]
    assert me["google_linked"] and me["has_password"]
    api.post("/auth/login", {}, json={"email": "ze@example.com", "password": "senha-segura"})


def test_claims_account_created_by_admin(apiclient, google):
    api = apiclient
    admin = api.user("Admin")
    group = api.post(G, admin, 201, json={"name": "Pelada"})
    member = api.post(f"{G}/{group['id']}/members", admin, 201, json={"email": "goleiro@gmail.com", "name": "Goleiro"})
    out = google_login(api, id_token(sub="g-gk", email="goleiro@gmail.com", name="Fulano Goleiro"))
    view = api.get(f"{G}/{group['id']}", bearer(out))
    assert view["my_member_id"] == member["id"]
    assert api.get("/me", bearer(out))["name"] == "Goleiro"  # mantém o nome dado pelo admin


def test_google_only_user_can_create_password_then_unlink(apiclient, google):
    api = apiclient
    h = bearer(google_login(api, id_token(sub="g-2", email="semsenha@gmail.com")))
    r = api.call("DELETE", "/me/google", h, 422)
    assert r["error"]["code"] == "password_required"
    me = api.patch("/me", h, json={"new_password": "minha-senha-1"})  # sem senha atual
    assert me["has_password"] is True
    api.post("/auth/login", {}, json={"email": "semsenha@gmail.com", "password": "minha-senha-1"})
    assert api.call("DELETE", "/me/google", h)["google_linked"] is False
    # depois de desvincular, um novo login Google com outro "sub" e o mesmo e-mail religa a conta
    again = api.get("/me", bearer(google_login(api, id_token(sub="g-3", email="semsenha@gmail.com"))))
    assert again["google_linked"] is True


def test_link_google_from_profile_and_conflicts(apiclient, google):
    api = apiclient
    a = api.user("Ana")
    b = api.user("Bia")
    linked = api.post("/me/google", a, json={"credential": id_token(sub="g-ana", email="ana.pessoal@gmail.com")})
    assert linked["google_linked"] is True
    r = api.post("/me/google", b, 409, json={"credential": id_token(sub="g-ana", email="ana.pessoal@gmail.com")})
    assert r["error"]["code"] == "google_conflict"
    # login pelo Google vinculado entra na conta da Ana, não cria outra
    me = api.get("/me", bearer(google_login(api, id_token(sub="g-ana", email="ana.pessoal@gmail.com"))))
    assert me["email"] == "ana@example.com"
    # e-mail da Bia com outra conta Google, mas a Bia já tem outro Google vinculado: conflito
    api.post("/me/google", b, json={"credential": id_token(sub="g-bia", email="bia.google@gmail.com")})
    r = google_login(api, id_token(sub="g-intruso", email="bia@example.com"), 409)
    assert r["error"]["code"] == "google_conflict"


def test_inactive_user_cannot_login_with_google(apiclient, google, db):
    from app.models import User

    api = apiclient
    api.user("Banido")
    user = db.query(User).filter(User.email == "banido@example.com").one()
    user.is_active = False
    db.commit()
    r = google_login(api, id_token(sub="g-ban", email="banido@example.com"), 401)
    assert r["error"]["code"] == "invalid_credentials"
