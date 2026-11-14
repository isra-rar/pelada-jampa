"""código curto no convite do grupo

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-14 10:00:00

- Os convites de grupo criados antes (token longo) passam a ter código curto de 8 caracteres.
  Links longos já compartilhados deixam de valer; o novo aparece em "Copiar convite".
"""
import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, invite_token FROM football_groups")).fetchall()
    taken = {r[1] for r in rows if len(r[1]) <= 8}
    for gid, token in rows:
        if len(token) <= 8:
            continue
        while True:
            code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
            if code not in taken:
                break
        taken.add(code)
        conn.execute(sa.text("UPDATE football_groups SET invite_token = :c WHERE id = :i"), {"c": code, "i": gid})


def downgrade() -> None:
    pass
