"""Validação do ID token do Google (Google Identity Services).

O frontend recebe um ID token (JWT assinado pelo Google) e o envia para a API. Aqui conferimos a
assinatura com as chaves públicas do Google, o público (nosso Client ID), o emissor, a validade e se
o e-mail foi verificado. Nenhum segredo do Google é necessário nesse fluxo.
"""

from dataclasses import dataclass

import jwt

from app.core.config import settings
from app.core.errors import BusinessRuleError, Unauthorized

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
_CLOCK_SKEW_SECONDS = 60

_jwks_client: jwt.PyJWKClient | None = None


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture: str | None


def is_enabled() -> bool:
    return bool(settings.google_client_id)


def _client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        # chaves em cache (o Google as rotaciona periodicamente; o kid desconhecido força nova busca)
        _jwks_client = jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, lifespan=3600, timeout=10)
    return _jwks_client


def _truthy(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def verify_id_token(token: str) -> GoogleIdentity:
    if not is_enabled():
        raise BusinessRuleError("Login com Google não está habilitado neste servidor", code="google_disabled")
    invalid = Unauthorized("Não foi possível validar o login com Google. Tente novamente.",
                           code="invalid_google_token")
    try:
        signing_key = _client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=GOOGLE_ISSUERS,
            leeway=_CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        raise invalid from None
    email = claims.get("email")
    if not email or not _truthy(claims.get("email_verified")):
        raise Unauthorized("Sua conta Google precisa ter um e-mail verificado", code="google_email_unverified")
    return GoogleIdentity(
        sub=str(claims["sub"]),
        email=str(email),
        name=claims.get("name") or claims.get("given_name"),
        picture=claims.get("picture"),
    )
