#!/usr/bin/env bash
# Atualiza a VPS: baixa o código dos dois repositórios, instala dependências, roda as migrações,
# gera o build Node do frontend e reinicia os processos do PM2 (ecosystem.config.cjs).
# Uso, na raiz deste repositório:  bash deploy/atualizar.sh
# Também serve para a primeira subida, depois de criar backend/.venv, backend/.env e o
# .env.production.local do frontend (DEPLOY.md, seção 6).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACK="$ROOT/backend"
FRONT="$ROOT/frontend/pelada-equilibrada"

[ -f "$BACK/.env" ] || { echo "Falta backend/.env (variáveis PELADA_*)"; exit 1; }
[ -x "$BACK/.venv/bin/python" ] || { echo "Falta backend/.venv: python3 -m venv backend/.venv"; exit 1; }
grep -q '^VITE_API_URL=' "$FRONT/.env.production.local" 2>/dev/null || {
  echo "Falta VITE_API_URL em frontend/pelada-equilibrada/.env.production.local"
  echo "  ex.: VITE_API_URL=https://pelada.seudominio.com.br/api/v1"
  exit 1
}

echo "==> Código"
git -C "$ROOT" pull --ff-only
git -C "$FRONT" pull --ff-only

echo "==> API: dependências e migrações"
cd "$BACK"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet .
.venv/bin/alembic upgrade head

echo "==> Frontend: build para Node"
cd "$FRONT"
bun install --frozen-lockfile
NITRO_PRESET=node-server bun run build

echo "==> PM2"
cd "$ROOT"
pm2 startOrReload ecosystem.config.cjs --update-env
pm2 save

echo "==> Conferindo a API"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health; then echo; exit 0; fi
  sleep 1
done
echo "A API não respondeu em /health. Veja: pm2 logs pelada-api --lines 100"
exit 1
