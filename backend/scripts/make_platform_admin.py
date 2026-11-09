"""Dá (ou tira) o acesso de administrador da plataforma a uma conta já cadastrada.

Uso:  python scripts/make_platform_admin.py email@exemplo.com
      python scripts/make_platform_admin.py email@exemplo.com --remover
Usa o mesmo PELADA_DATABASE_URL da API (em produção, rode dentro do container/console da hospedagem).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: E402,F401  (registra todos os modelos)
from app.core.db import SessionLocal  # noqa: E402
from app.services.users import get_by_email  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--remover", action="store_true", help="tira o acesso em vez de dar")
    args = parser.parse_args()

    with SessionLocal() as db:
        user = get_by_email(db, args.email)
        if user is None or not user.is_active:
            print(f"Nenhuma conta ativa com o e-mail {args.email}. Cadastre-se no app primeiro.", file=sys.stderr)
            return 1
        user.is_platform_admin = not args.remover
        db.commit()
        estado = "não é mais" if args.remover else "agora é"
        print(f"{user.name} <{user.email}> {estado} administrador da plataforma.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
