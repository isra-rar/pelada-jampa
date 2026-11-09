import pytest

from app.core import rate_limit as rl
from app.core.config import settings


@pytest.fixture(autouse=True)
def _limiter(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    rl.limiter.reset()
    yield
    rl.limiter.reset()


def _register(client, email, password="senha-segura", **headers):
    return client.post(
        "/api/v1/auth/register", json={"email": email, "name": "Jogador", "password": password}, headers=headers
    )


def _login(client, email, password, **headers):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers)


def test_login_bloqueia_conta_depois_de_falhas_e_informa_espera(client):
    assert _register(client, "alvo@example.com").status_code == 201
    for _ in range(rl.LOGIN_FAIL_PER_EMAIL.limit):
        assert _login(client, "alvo@example.com", "errada-123").status_code == 401

    r = _login(client, "ALVO@example.com", "errada-123")  # mesma conta, com outra caixa
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "too_many_attempts"
    assert 0 < int(r.headers["Retry-After"]) <= rl.LOGIN_FAIL_PER_EMAIL.window_seconds
    # nem a senha certa entra durante o bloqueio (senão o atacante saberia que acertou)
    assert _login(client, "alvo@example.com", "senha-segura").status_code == 429
    # o formato OAuth2 (/auth/token) divide o mesmo contador
    r = client.post("/api/v1/auth/token", data={"username": "alvo@example.com", "password": "senha-segura"})
    assert r.status_code == 429


def test_login_certo_zera_falhas_da_conta(client):
    _register(client, "esquece@example.com")
    for _ in range(rl.LOGIN_FAIL_PER_EMAIL.limit - 1):
        assert _login(client, "esquece@example.com", "errada-123").status_code == 401
    assert _login(client, "esquece@example.com", "senha-segura").status_code == 200
    for _ in range(rl.LOGIN_FAIL_PER_EMAIL.limit - 1):
        assert _login(client, "esquece@example.com", "errada-123").status_code == 401
    assert _login(client, "esquece@example.com", "senha-segura").status_code == 200


def test_login_limita_ip_que_testa_muitas_contas(client):
    for i in range(rl.LOGIN_FAIL_PER_IP.limit):
        assert _login(client, f"conta{i}@example.com", "chute-1234").status_code == 401
    assert _login(client, "outra@example.com", "chute-1234").status_code == 429


def test_cabecalho_de_ip_da_hospedagem_separa_clientes(client, monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "Fly-Client-IP")
    for i in range(rl.LOGIN_FAIL_PER_IP.limit):
        r = _login(client, f"conta{i}@example.com", "chute-1234", **{"Fly-Client-IP": "200.1.1.1"})
        assert r.status_code == 401
    assert _login(client, "x@example.com", "chute-1234", **{"Fly-Client-IP": "200.1.1.1"}).status_code == 429
    # outro cliente atrás do mesmo proxy não é afetado
    assert _login(client, "x@example.com", "chute-1234", **{"Fly-Client-IP": "200.2.2.2"}).status_code == 401


def test_cadastro_limitado_por_ip(client):
    for i in range(rl.REGISTER_PER_IP.limit):
        assert _register(client, f"novo{i}@example.com").status_code == 201
    r = _register(client, "mais-um@example.com")
    assert r.status_code == 429


def test_pedido_de_redefinicao_limitado_por_email(client):
    url = "/api/v1/auth/password-reset/request"
    for _ in range(rl.RESET_REQUEST_PER_EMAIL.limit):
        assert client.post(url, json={"email": "qualquer@example.com"}).status_code == 202
    assert client.post(url, json={"email": "qualquer@example.com"}).status_code == 429
    # outro e-mail do mesmo IP segue livre (até o limite por IP)
    assert client.post(url, json={"email": "outro@example.com"}).status_code == 202


def test_confirmacao_de_redefinicao_limitada_por_falhas(client):
    url = "/api/v1/auth/password-reset/confirm"
    body = {"token": "x" * 20, "new_password": "nova-senha-1"}
    for _ in range(rl.RESET_CONFIRM_FAIL_PER_IP.limit):
        assert client.post(url, json=body).status_code == 422
    assert client.post(url, json=body).status_code == 429


def test_troca_de_senha_limita_quem_tem_o_token(client):
    _register(client, "dono@example.com")
    token = _login(client, "dono@example.com", "senha-segura").json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    body = {"current_password": "chute-1234", "new_password": "nova-senha-1"}
    for _ in range(rl.PASSWORD_CHECK_FAIL_PER_USER.limit):
        assert client.patch("/api/v1/me", json=body, headers=auth).status_code == 422
    assert client.patch("/api/v1/me", json=body, headers=auth).status_code == 429
    # mudar só o nome não pede senha e continua liberado
    assert client.patch("/api/v1/me", json={"name": "Dono"}, headers=auth).status_code == 200


def test_janela_expira():
    agora = [1000.0]
    limiter = rl.RateLimiter(clock=lambda: agora[0])
    regra = rl.Rule("teste", 2, 60)
    limiter.consume(regra, "k")
    limiter.consume(regra, "k")
    with pytest.raises(rl.TooManyRequests):
        limiter.check(regra, "k")
    agora[0] += 61
    limiter.check(regra, "k")


def test_desligado_nao_limita(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    for _ in range(rl.LOGIN_FAIL_PER_EMAIL.limit + 2):
        assert _login(client, "x@example.com", "errada-123").status_code == 401
