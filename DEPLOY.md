# Deploy do Pelada+

- **API + Postgres:** Railway (este repositório, pasta `backend/`).
- **Frontend:** Cloudflare Workers (repositório `pelada-equilibrada`, build `cloudflare-module`).

Ordem: suba a API primeiro, porque o build do front precisa da URL dela.

Alternativa: tudo numa VPS própria, com PM2 + Caddy (seção 6).

## 1. API no Railway

1. **New Project → Deploy from GitHub repo →** `isra-rar/pelada-jampa`.
2. No serviço criado, em **Settings**:
   - **Root Directory:** `/backend`
   - **Config as code → Railway Config File:** `/backend/railway.json`. O Railway não procura esse arquivo
     dentro do Root Directory, por isso o caminho é absoluto.
   - **Wait for CI:** ligado. Assim só faz deploy se o workflow `backend` (pytest no Postgres) passar.
3. **New → Database → PostgreSQL.** Na aba **Backups** do Postgres, ative os backups agendados (plano pago).
4. No serviço da API, clique com o botão direito → **Attach Volume**, com mount path `/app/media`. É onde ficam
   as fotos de perfil; sem o volume elas somem a cada deploy.
5. **Variables** do serviço da API:

   | Variável | Valor |
   | --- | --- |
   | `PELADA_ENVIRONMENT` | `production` |
   | `PELADA_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (a API troca `postgresql://` pelo driver psycopg sozinha) |
   | `PELADA_JWT_SECRET` | saída de `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `PELADA_CORS_ORIGINS` | `["https://URL-DO-FRONT"]` |
   | `PELADA_FRONTEND_URL` | `https://URL-DO-FRONT` |
   | `PELADA_PUBLIC_BASE_URL` | `https://URL-DA-API` |
   | `FORWARDED_ALLOW_IPS` | `*` (o uvicorn passa a ler o IP real e o `https` do proxy do Railway) |
   | `RAILWAY_RUN_UID` | `0` (o Railway monta o volume como root e a imagem roda com usuário comum) |
   | `PELADA_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` | provedor de e-mail. Sem isso, o link de redefinição de senha só sai no log |
   | `PELADA_VAPID_PUBLIC_KEY`, `PELADA_VAPID_PRIVATE_KEY`, `PELADA_VAPID_SUBJECT` | `python scripts/generate_vapid_keys.py` |
   | `PELADA_GOOGLE_CLIENT_ID` | opcional (ver passo 3) |

6. **Settings → Networking → Generate Domain** (gera `*.up.railway.app`), ou **Custom Domain** com um CNAME
   tipo `api.seudominio.com.br`. Volte em `PELADA_PUBLIC_BASE_URL` e preencha com essa URL.
7. Deploy. O container roda `alembic upgrade head` antes de subir. O Railway só libera o tráfego quando
   `/health` responde 200, e esse endpoint também testa o banco.

O `railway.json` fixa **1 réplica**. Não aumente: o agendador de lembretes e o limite de tentativas vivem
dentro do processo. Como há volume, cada deploy tem alguns segundos fora do ar (o Railway não sobe duas
cópias com o mesmo volume).

## 2. Frontend no Cloudflare Workers

O endereço da API entra no build (`VITE_API_URL`). O `.env` do repositório aponta para `localhost`.

**Pelo painel (recomendado):** Cloudflare → **Workers & Pages → Create → Import a repository** →
`isra-rar/pelada-equilibrada`.
- Build command: `npm run build`
- Deploy command: `npx wrangler deploy`
- Em **Build variables**, crie `VITE_API_URL=https://URL-DA-API/api/v1`. Uma variável já definida no
  ambiente tem prioridade sobre o `.env`.

**Pela sua máquina:**
```sh
cd frontend/pelada-equilibrada
VITE_API_URL=https://URL-DA-API/api/v1 npm run build
npx wrangler deploy        # usa o .output/server/wrangler.json gerado pelo build
```

Domínio próprio: **Settings → Domains & Routes → Custom domain** no Worker (ex.: `app.seudominio.com.br`).
Se trocar o domínio, atualize `PELADA_CORS_ORIGINS` e `PELADA_FRONTEND_URL` no Railway.

O botão **Publish** do Lovable gera o build com o `.env`, ou seja, com a API em `localhost`. Para usar o
Lovable, crie `.env.production` com a URL da API. O Vite usa esse arquivo no build e mantém o `.env` no
`npm run dev`.

## 3. Login com Google (opcional)

No [Google Cloud Console](https://console.cloud.google.com/apis/credentials), em **Origens JavaScript
autorizadas** da credencial OAuth, adicione `https://URL-DO-FRONT`. Coloque o Client ID em
`PELADA_GOOGLE_CLIENT_ID` no Railway.

## 4. Depois do primeiro deploy

1. Cadastre-se no app e torne sua conta administradora da plataforma:
   ```sh
   railway ssh            # entra no container da API (CLI do Railway, com o projeto linkado)
   python scripts/make_platform_admin.py voce@exemplo.com
   ```
2. Confira o IP real nos logs do Railway. Cada requisição aparece como `INFO: 189.x.x.x:porta - "GET ..."`.
   Se aparecer sempre o mesmo IP `100.x.x.x`, o `FORWARDED_ALLOW_IPS` não foi aplicado. Nesse caso todos os
   usuários dividem o limite por IP do login, e 30 erros de senha em 15 minutos bloqueiam todo mundo.
3. Envie uma foto de perfil e confira se a URL dela começa com `https://`.
4. Peça "esqueci a senha" e confira se o e-mail chega.

## 5. Monitor de disponibilidade (UptimeRobot)

O `/health` responde **503** se o banco não responde ou se o agendador parou. O agendador cuida de lembretes,
encerramento das avaliações e cobranças de aluguel. Exemplo de resposta normal:
`{"status":"ok","database":"ok","scheduler":"ok"}`.

1. Crie uma conta grátis em [uptimerobot.com](https://uptimerobot.com) (50 monitores, checagem a cada 5 min).
2. **Add New Monitor**, tipo **HTTP(s)**:
   - **API:** `https://URL-DA-API/health`
   - **Front:** `https://URL-DO-FRONT/`
3. Em **Alert Contacts**, use o e-mail ou o app (push no celular). O Telegram também funciona no plano grátis.
4. Opcional: **Status Pages**. Gera uma página pública de status para mandar aos grupos quando algo cair.

No Railway, em **Project Settings → Notifications**, ligue também os e-mails de deploy com falha.

## 6. Alternativa: VPS própria com PM2

Tudo numa máquina: o PM2 mantém a API e o frontend no ar (reinicia se cair e sobe junto com a VPS), o Caddy
cuida do HTTPS e o Postgres roda na própria VPS. Substitui as seções 1 e 2. As seções 3, 4 e 5 continuam valendo.

```text
navegador ─https─> Caddy :443 ─┬─ /api, /media, /health ─> pelada-api  127.0.0.1:8000  (uvicorn, 1 processo)
                               └─ o resto ───────────────> pelada-web  127.0.0.1:3000  (node .output/server)
```

App e API ficam no mesmo domínio, então não há CORS e basta um certificado. Arquivos: `ecosystem.config.cjs`
(PM2), `deploy/Caddyfile` e `deploy/atualizar.sh`.

**Máquina:** Ubuntu 24.04 com 2 GB de RAM. O build do frontend passa de 1 GB; numa VPS de 1 GB, crie 2 GB de
swap. Antes de começar, aponte o domínio (registro A) para o IP da VPS. Deixe o DNS sem o proxy da Cloudflare
(nuvem cinza). Com o proxy ligado, a API veria o IP da Cloudflare e seria preciso definir
`PELADA_CLIENT_IP_HEADER=CF-Connecting-IP`.

1. **Pacotes:**
   ```sh
   sudo apt update && sudo apt install -y git curl python3 python3-venv postgresql
   curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
   sudo npm install -g pm2 bun
   sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
   ```
   Instale o Caddy pelo repositório oficial: <https://caddyserver.com/docs/install#debian-ubuntu-raspbian>.
2. **Banco:**
   ```sh
   sudo -u postgres createuser --pwprompt pelada
   sudo -u postgres createdb -O pelada pelada
   ```
3. **Código** (o mesmo layout da sua máquina, com o frontend dentro de `frontend/`):
   ```sh
   git clone https://github.com/isra-rar/pelada-jampa.git ~/pelada
   git clone https://github.com/isra-rar/pelada-equilibrada.git ~/pelada/frontend/pelada-equilibrada
   cd ~/pelada && python3 -m venv backend/.venv
   ```
4. **`backend/.env`**: copie de `backend/.env.example` e rode `chmod 600 backend/.env`. Preencha:

   | Variável | Valor |
   | --- | --- |
   | `PELADA_ENVIRONMENT` | `production` |
   | `PELADA_DATABASE_URL` | `postgresql+psycopg://pelada:SENHA@localhost:5432/pelada` |
   | `PELADA_JWT_SECRET` | saída de `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `PELADA_CORS_ORIGINS` | `["https://pelada.seudominio.com.br"]` |
   | `PELADA_FRONTEND_URL` e `PELADA_PUBLIC_BASE_URL` | `https://pelada.seudominio.com.br` (o mesmo domínio) |
   | SMTP, VAPID e Google | como na seção 1 |

   `FORWARDED_ALLOW_IPS` e `RAILWAY_RUN_UID` não são necessários: o `ecosystem.config.cjs` já define o
   primeiro (só o Caddy local é confiável) e as fotos ficam no disco da VPS, em `backend/media`.
5. **`frontend/pelada-equilibrada/.env.production.local`**, com a URL da API que entra no build:
   ```sh
   VITE_API_URL=https://pelada.seudominio.com.br/api/v1
   ```
   O `.gitignore` do front já ignora `*.local`. O `.env` com `localhost` continua valendo no `npm run dev`.
6. **Caddy:** troque o domínio em `deploy/Caddyfile` e rode
   `sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`.
7. **Primeira subida e início automático:**
   ```sh
   bash deploy/atualizar.sh     # dependências, migrações, build Node do front e pm2 start
   pm2 startup systemd          # mostra um comando com sudo; rode esse comando
   pm2 save
   pm2 install pm2-logrotate
   pm2 set pm2-logrotate:max_size 10M && pm2 set pm2-logrotate:retain 14
   ```
   Depois crie o administrador (seção 4) com
   `cd ~/pelada/backend && .venv/bin/python scripts/make_platform_admin.py voce@exemplo.com`.
8. **Backup diário** do banco e das fotos. Crie `~/.pgpass` com `localhost:5432:pelada:pelada:SENHA` e
   `chmod 600 ~/.pgpass`, crie a pasta com `mkdir ~/backups` e, em `crontab -e`, adicione:
   ```sh
   0 3 * * * pg_dump -Fc -h localhost -U pelada pelada > ~/backups/pelada-$(date +\%F).dump && tar czf ~/backups/media-$(date +\%F).tgz -C ~/pelada/backend media && find ~/backups -mtime +14 -delete
   ```
   Copie `~/backups` para fora da VPS de tempos em tempos. Um backup que fica só na VPS some junto com ela.

**Para atualizar:** `bash deploy/atualizar.sh`. O script faz `git pull` nos dois repositórios, instala as
dependências, roda `alembic upgrade head`, gera o build do front, roda `pm2 startOrReload` e confere o `/health`.
A API fica alguns segundos fora enquanto reinicia, porque roda em 1 processo de propósito (ver seção 1). Quem
estiver com o app aberto recebe o aviso "Nova versão do app disponível".

**No dia a dia:**

- `pm2 status`: mostra os dois processos, com uso de memória e número de reinícios.
- `pm2 logs pelada-api --lines 100` e `pm2 logs pelada-web`: mostram os logs, com data e hora.
- `pm2 restart pelada-api`: reinicia a API, por exemplo depois de mudar o `backend/.env`.
- `pm2 monit`: mostra CPU e memória ao vivo.

O PM2 reinicia um processo quando ele cai ou passa do limite de memória (512 MB na API e 384 MB no front). No
UptimeRobot (seção 5), monitore `https://pelada.seudominio.com.br/health` e `https://pelada.seudominio.com.br/`.

## Checklist rápido

- [ ] Dados do responsável preenchidos em `frontend/pelada-equilibrada/src/lib/legal.ts`. Enquanto houver campo
      entre colchetes, Termos e Privacidade mostram o aviso "Documento incompleto"

- [ ] `PELADA_JWT_SECRET` aleatório com 32+ bytes. Sem isso a API nem sobe em produção.
- [ ] Volume em `/app/media` e `RAILWAY_RUN_UID=0`
- [ ] Backups do Postgres ativos
- [ ] `FORWARDED_ALLOW_IPS=*`, com o IP real aparecendo nos logs
- [ ] CORS e `FRONTEND_URL` com o domínio final do front
- [ ] `VITE_API_URL` de produção no build do front
- [ ] SMTP configurado
- [ ] Administrador da plataforma criado
- [ ] UptimeRobot vigiando `/health` e o front
- [ ] Na VPS (seção 6): `pm2 startup` + `pm2 save` feitos, `pm2-logrotate` instalado e backup no `crontab`
