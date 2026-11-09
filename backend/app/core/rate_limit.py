"""Limite de tentativas em memória (janela deslizante), contra força bruta em login, cadastro e senha.

Os contadores ficam no processo da API. Isso basta porque o deploy roda uma única instância com um
worker (o agendador também roda dentro do processo). Com várias réplicas, cada uma contaria à parte e
o limite efetivo seria multiplicado; aí o certo é trocar por Redis.
"""

import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import Request

from app.core.config import settings
from app.core.errors import AppError, TooManyRequests


@dataclass(frozen=True)
class Rule:
    scope: str
    limit: int
    window_seconds: int


# login: conta só as falhas (quem acerta a senha não gasta tentativa)
LOGIN_FAIL_PER_EMAIL = Rule("login:email", 5, 15 * 60)
# por IP é mais folgado: operadoras móveis põem muita gente atrás do mesmo IP (CGNAT)
LOGIN_FAIL_PER_IP = Rule("login:ip", 30, 15 * 60)
REGISTER_PER_IP = Rule("register:ip", 10, 60 * 60)
RESET_REQUEST_PER_EMAIL = Rule("reset:email", 3, 60 * 60)
RESET_REQUEST_PER_IP = Rule("reset:ip", 10, 60 * 60)
RESET_CONFIRM_FAIL_PER_IP = Rule("reset-confirm:ip", 10, 15 * 60)
GOOGLE_FAIL_PER_IP = Rule("google:ip", 30, 15 * 60)
# senha pedida com a sessão aberta (troca de senha, exclusão de conta): limita quem roubou um token
PASSWORD_CHECK_FAIL_PER_USER = Rule("password:user", 5, 15 * 60)


class RateLimiter:
    _SWEEP_EVERY = 1000

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def _bucket(self, rule: Rule, key: str, now: float) -> deque[float]:
        bucket = self._hits.setdefault((rule.scope, key), deque())
        cutoff = now - rule.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def _sweep(self, now: float) -> None:
        # descarta chaves sem tentativas recentes para a memória não crescer sem limite
        longest = max(r.window_seconds for r in _ALL_RULES)
        stale = [k for k, b in self._hits.items() if not b or b[-1] <= now - longest]
        for k in stale:
            del self._hits[k]

    def check(self, rule: Rule, key: str | None) -> None:
        """Recusa com 429 se a chave já gastou o limite da janela. Não registra tentativa."""
        if not settings.rate_limit_enabled or not key:
            return
        with self._lock:
            now = self._clock()
            bucket = self._bucket(rule, key, now)
            if len(bucket) >= rule.limit:
                retry_after = max(1, math.ceil(bucket[0] + rule.window_seconds - now))
                minutes = math.ceil(retry_after / 60)
                raise TooManyRequests(
                    f"Muitas tentativas. Tente de novo em {minutes} minuto{'s' if minutes > 1 else ''}.",
                    details={"retry_after_seconds": retry_after},
                    headers={"Retry-After": str(retry_after)},
                )

    def hit(self, rule: Rule, key: str | None) -> None:
        """Registra uma tentativa."""
        if not settings.rate_limit_enabled or not key:
            return
        with self._lock:
            now = self._clock()
            self._bucket(rule, key, now).append(now)
            self._calls += 1
            if self._calls % self._SWEEP_EVERY == 0:
                self._sweep(now)

    def consume(self, rule: Rule, key: str | None) -> None:
        """Checa e registra de uma vez (para limites que contam toda tentativa, não só falhas)."""
        self.check(rule, key)
        self.hit(rule, key)

    def clear(self, rule: Rule, key: str | None) -> None:
        if not key:
            return
        with self._lock:
            self._hits.pop((rule.scope, key), None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._calls = 0


_ALL_RULES = (
    LOGIN_FAIL_PER_EMAIL,
    LOGIN_FAIL_PER_IP,
    REGISTER_PER_IP,
    RESET_REQUEST_PER_EMAIL,
    RESET_REQUEST_PER_IP,
    RESET_CONFIRM_FAIL_PER_IP,
    GOOGLE_FAIL_PER_IP,
    PASSWORD_CHECK_FAIL_PER_USER,
)

limiter = RateLimiter()


@contextmanager
def count_failures(*rules: tuple[Rule, str | None], success_clears: tuple[Rule, str | None] | None = None):
    """Recusa se alguma chave já estourou o limite; registra tentativa em todas se o bloco falhar."""
    for rule, key in rules:
        limiter.check(rule, key)
    try:
        yield
    except AppError:
        for rule, key in rules:
            limiter.hit(rule, key)
        raise
    if success_clears:
        limiter.clear(*success_clears)


def client_ip(request: Request) -> str | None:
    """IP de quem fez a requisição.

    Atrás do proxy da hospedagem, `request.client.host` é o IP do proxy, a menos que o uvicorn confie
    nele (`FORWARDED_ALLOW_IPS`). Se a plataforma manda o IP real num cabeçalho próprio
    (`Fly-Client-IP`, `CF-Connecting-IP`...), configure `PELADA_CLIENT_IP_HEADER`: esse cabeçalho é
    escrito pelo proxy, então o cliente não consegue forjar.
    """
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else None
