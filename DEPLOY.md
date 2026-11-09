# Deploy do Pelada+

- **API + Postgres:** Railway (este repositório, pasta `backend/`).
- **Frontend:** Cloudflare Workers (repositório `pelada-equilibrada`, build `cloudflare-module`).

Ordem: suba a API primeiro, porque o build do front precisa da URL dela.

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

## Checklist rápido

- [ ] `PELADA_JWT_SECRET` aleatório com 32+ bytes. Sem isso a API nem sobe em produção.
- [ ] Volume em `/app/media` e `RAILWAY_RUN_UID=0`
- [ ] Backups do Postgres ativos
- [ ] `FORWARDED_ALLOW_IPS=*`, com o IP real aparecendo nos logs
- [ ] CORS e `FRONTEND_URL` com o domínio final do front
- [ ] `VITE_API_URL` de produção no build do front
- [ ] SMTP configurado
- [ ] Administrador da plataforma criado
