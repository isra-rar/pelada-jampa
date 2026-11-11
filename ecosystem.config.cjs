// PM2 na VPS própria: API (FastAPI/uvicorn) + frontend (TanStack Start, build para Node).
// O Caddy (deploy/Caddyfile) recebe o HTTPS e repassa para as portas abaixo, só em 127.0.0.1.
// Guia completo: DEPLOY.md, seção 6. Atualizar a VPS: ./deploy/atualizar.sh
//
//   pm2 start ecosystem.config.cjs     # primeira vez
//   pm2 status | pm2 logs | pm2 monit
//
// Layout esperado (dois repositórios):
//   pelada/                             <- este repositório
//   pelada/frontend/pelada-equilibrada/ <- repositório do frontend
const path = require("node:path");

const BACKEND = path.join(__dirname, "backend");
const FRONTEND = path.join(__dirname, "frontend", "pelada-equilibrada");

module.exports = {
  apps: [
    {
      name: "pelada-api",
      cwd: BACKEND, // a API lê backend/.env a partir daqui
      script: path.join(BACKEND, ".venv", "bin", "python"),
      args: "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --workers 1",
      interpreter: "none",
      // 1 processo, sempre: o agendador (lembretes, encerramento de avaliações, cobranças) e o limite de
      // tentativas de login vivem na memória. Duas cópias mandariam lembrete em dobro.
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      exp_backoff_restart_delay: 200, // banco fora do ar: tenta de novo com intervalo crescente
      max_memory_restart: "512M",
      kill_timeout: 10000, // tempo para o uvicorn fechar as requisições e o agendador terminar o ciclo
      time: true,
      env: {
        PYTHONUNBUFFERED: "1",
        // o Caddy roda na mesma máquina: só dele a API aceita X-Forwarded-For/Proto (IP real e https)
        FORWARDED_ALLOW_IPS: "127.0.0.1",
      },
    },
    {
      name: "pelada-web",
      cwd: FRONTEND,
      script: ".output/server/index.mjs", // gerado por: NITRO_PRESET=node-server bun run build
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      exp_backoff_restart_delay: 200,
      max_memory_restart: "384M",
      kill_timeout: 5000,
      time: true,
      env: {
        NODE_ENV: "production",
        HOST: "127.0.0.1",
        PORT: "3000",
      },
    },
  ],
};
