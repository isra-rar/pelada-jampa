from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import DB, CurrentUser
from app.core import rate_limit as rl
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth import (
    AccountDeleteIn,
    AuthConfigOut,
    GoogleCredentialIn,
    LoginIn,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterIn,
    TokenOut,
    UserOut,
    UserUpdate,
)
from app.schemas.common import Message
from app.schemas.misc import HomeOut
from app.services import account, dashboard, google_auth, media, users

router = APIRouter(tags=["Autenticação e usuário"])


def _token(user_id: int) -> TokenOut:
    return TokenOut(access_token=create_access_token(user_id), expires_in=settings.access_token_expire_minutes * 60)


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, request: Request, db: DB):
    """Cadastro. Se o administrador já adicionou o e-mail, a conta é reivindicada."""
    rl.limiter.consume(rl.REGISTER_PER_IP, rl.client_ip(request))
    return users.register(db, data)


def _login(request: Request, db: DB, email: str, password: str) -> TokenOut:
    """Conta só as falhas, por e-mail (protege a conta) e por IP (freia quem testa muitas contas)."""
    email_key = users.normalize_email(email)
    email_rule = (rl.LOGIN_FAIL_PER_EMAIL, email_key)
    with rl.count_failures(email_rule, (rl.LOGIN_FAIL_PER_IP, rl.client_ip(request)), success_clears=email_rule):
        user = users.authenticate(db, email, password)
    return _token(user.id)


@router.post("/auth/login", response_model=TokenOut)
def login(data: LoginIn, request: Request, db: DB):
    return _login(request, db, data.email, data.password)


@router.post("/auth/token", response_model=TokenOut, include_in_schema=True)
def token(form: Annotated[OAuth2PasswordRequestForm, Depends()], request: Request, db: DB):
    """Login no formato OAuth2 (usado pelo botão Authorize do Swagger). `username` = e-mail."""
    return _login(request, db, form.username, form.password)


@router.get("/auth/config", response_model=AuthConfigOut)
def auth_config():
    """Métodos de login disponíveis (o Client ID do Google é público por natureza)."""
    return {"google_client_id": settings.google_client_id if google_auth.is_enabled() else None}


@router.post("/auth/google", response_model=TokenOut)
def login_google(data: GoogleCredentialIn, request: Request, db: DB):
    """Entrar (ou criar conta) com Google a partir do ID token do Google Identity Services."""
    with rl.count_failures((rl.GOOGLE_FAIL_PER_IP, rl.client_ip(request))):
        identity = google_auth.verify_id_token(data.credential)
        user = users.login_with_google(db, identity)
    return _token(user.id)


@router.post("/me/google", response_model=UserOut)
def link_google(data: GoogleCredentialIn, user: CurrentUser, db: DB):
    """Liga uma conta Google à conta atual (para entrar também pelo Google)."""
    return users.link_google(db, user, google_auth.verify_id_token(data.credential))


@router.delete("/me/google", response_model=UserOut)
def unlink_google(user: CurrentUser, db: DB):
    return users.unlink_google(db, user)


@router.post("/auth/password-reset/request", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(data: PasswordResetRequest, request: Request, db: DB):
    """Envia um link de redefinição por e-mail. A resposta é a mesma exista ou não a conta."""
    rl.limiter.check(rl.RESET_REQUEST_PER_EMAIL, users.normalize_email(data.email))
    rl.limiter.consume(rl.RESET_REQUEST_PER_IP, rl.client_ip(request))
    rl.limiter.hit(rl.RESET_REQUEST_PER_EMAIL, users.normalize_email(data.email))
    users.request_password_reset(db, data.email)
    return {"message": "Se o e-mail estiver cadastrado, você receberá um link para redefinir a senha."}


@router.post("/auth/password-reset/confirm", response_model=TokenOut)
def password_reset_confirm(data: PasswordResetConfirm, request: Request, db: DB):
    """Define a nova senha (link de uso único), encerra sessões antigas e já devolve um novo acesso."""
    with rl.count_failures((rl.RESET_CONFIRM_FAIL_PER_IP, rl.client_ip(request))):
        user = users.confirm_password_reset(db, data.token, data.new_password)
    return _token(user.id)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(data: UserUpdate, user: CurrentUser, db: DB):
    if not data.new_password:
        return users.update_me(db, user, data)
    with rl.count_failures((rl.PASSWORD_CHECK_FAIL_PER_USER, str(user.id))):
        return users.update_me(db, user, data)


@router.post("/me/photo", response_model=UserOut)
async def upload_photo(request: Request, user: CurrentUser, db: DB, file: UploadFile = File(...)):
    """Envia a foto de perfil (JPG, PNG ou WEBP; tamanho máximo configurável)."""
    data = await file.read(settings.max_upload_bytes + 1)
    rel = media.save_avatar(user.id, data)
    old = user.photo_url
    user.photo_url = media.public_url(str(request.base_url), rel)
    db.commit()
    media.delete_if_local(old)
    db.refresh(user)
    return user


@router.delete("/me/photo", response_model=UserOut)
def delete_photo(user: CurrentUser, db: DB):
    old = user.photo_url
    user.photo_url = None
    db.commit()
    media.delete_if_local(old)
    db.refresh(user)
    return user


@router.get("/me/home", response_model=HomeOut)
def home(user: CurrentUser, db: DB):
    """Tela inicial: próxima pelada por grupo, estado da confirmação, pendências e atividades."""
    return dashboard.home(db, user)


# ------------------------------------------------------------------ LGPD


@router.get("/me/export")
def export_my_data(user: CurrentUser, db: DB):
    """Baixa todos os dados pessoais da conta em JSON (LGPD, art. 18): conta, grupos, notas, presenças,
    pagamentos, avaliações feitas e notificações. Avaliações recebidas aparecem consolidadas por pelada,
    sem identificar quem avaliou."""
    data = jsonable_encoder(account.export_data(db, user))
    return JSONResponse(
        data, headers={"Content-Disposition": f'attachment; filename="pelada-meus-dados-{user.id}.json"'}
    )


@router.post("/me/delete", response_model=Message)
def delete_my_account(data: AccountDeleteIn, user: CurrentUser, db: DB):
    """Exclui a conta: nome, e-mail, telefone, foto, acesso Google e notificações são apagados; as vagas em
    peladas futuras são liberadas. Registros do grupo (pagamentos, presenças, placares, notas) ficam
    anonimizados. Irreversível."""
    with rl.count_failures((rl.PASSWORD_CHECK_FAIL_PER_USER, str(user.id))):
        account.delete_account(db, user, password=data.password, confirm=data.confirm)
    return {"message": "Conta excluída. Seus dados pessoais foram removidos."}
