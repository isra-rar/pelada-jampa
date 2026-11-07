"""Armazenamento local de imagens enviadas pelos usuários (fotos de perfil)."""

import secrets
from pathlib import Path

from app.core.config import settings
from app.core.errors import BusinessRuleError

MEDIA_PREFIX = "/media/"

# assinatura (magic bytes) -> extensão; o tipo é decidido pelo conteúdo, nunca pelo nome enviado
_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
]


def media_root() -> Path:
    root = Path(settings.media_dir).resolve()
    (root / "avatars").mkdir(parents=True, exist_ok=True)
    return root


def detect_image_ext(data: bytes) -> str | None:
    for sig, ext in _SIGNATURES:
        if data.startswith(sig):
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_avatar(user_id: int, data: bytes) -> str:
    """Grava a imagem e devolve o caminho relativo servido em /media."""
    if not data:
        raise BusinessRuleError("Arquivo vazio", code="invalid_file")
    if len(data) > settings.max_upload_bytes:
        mb = settings.max_upload_bytes / (1024 * 1024)
        raise BusinessRuleError(f"A imagem deve ter no máximo {mb:.0f} MB", code="file_too_large")
    ext = detect_image_ext(data)
    if ext is None:
        raise BusinessRuleError("Envie uma imagem JPG, PNG ou WEBP", code="invalid_file_type")
    name = f"avatars/{user_id}-{secrets.token_hex(8)}.{ext}"
    (media_root() / name).write_bytes(data)
    return name


def delete_if_local(url: str | None) -> None:
    """Remove o arquivo anterior quando ele foi enviado para este servidor."""
    if not url or MEDIA_PREFIX not in url:
        return
    rel = url.split(MEDIA_PREFIX, 1)[1]
    root = media_root()
    path = (root / rel).resolve()
    if root in path.parents and path.is_file():
        path.unlink(missing_ok=True)


def public_url(base_url: str, rel_path: str) -> str:
    base = (settings.public_base_url or base_url).rstrip("/")
    return f"{base}{MEDIA_PREFIX}{rel_path}"
