"""Dá (ou tira) o acesso de Super-Admin (administrador da plataforma) a uma conta.

Uso:  python scripts/make_platform_admin.py email@exemplo.com
      python scripts/make_platform_admin.py email@exemplo.com --remover
      python scripts/make_platform_admin.py email@exemplo.com --criar --nome "Seu Nome"

Com --criar, a conta é criada se não existir (ou tem a senha trocada se já existir) e uma senha aleatória é
mostrada uma única vez no terminal. Troque-a no Perfil depois de entrar.
Usa o mesmo PELADA_DATABASE_URL da API (em produção, rode na VPS dentro de backend/).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: E402,F401  (registra todos os modelos)
from app.core.db import SessionLocal  # noqa: E402
from app.services.users import ensure_super_admin, get_by_email  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--remover", action="store_true", help="tira o acesso em vez de dar")
    parser.add_argument("--criar", action="store_true", help="cria a conta (ou troca a senha) e mostra a senha")
    parser.add_argument("--nome", default="Super-Admin", help="nome da conta criada com --criar")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.criar:
            user, password, created = ensure_super_admin(db, args.email, args.nome)
            print(f"{user.name} <{user.email}> {'criada' if created else 'já existia; senha trocada'} "
                  "e agora é Super-Admin.")
            print(f"Senha: {password}")
            return 0
        user = get_by_email(db, args.email)
        if user is None or not user.is_active:
            print(f"Nenhuma conta ativa com o e-mail {args.email}. Cadastre-se no app ou use --criar.",
                  file=sys.stderr)
            return 1
        user.is_platform_admin = not args.remover
        db.commit()
        estado = "não é mais" if args.remover else "agora é"
        print(f"{user.name} <{user.email}> {estado} Super-Admin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
