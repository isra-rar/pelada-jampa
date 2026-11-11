# Pelada+ — backend

API REST (FastAPI + SQLAlchemy) do Pelada+. Documentação interativa em `http://localhost:8000/docs`.

## Rodando localmente

```sh
python -m venv .venv
.venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # ajuste as variáveis (padrão: SQLite em ./pelada.db)
alembic upgrade head
uvicorn app.main:app --reload --port 8000
pytest                            # suíte de testes
```

Com Docker: `docker compose up` sobe Postgres + API. As fotos de perfil ficam no volume `media` (em hospedagem
sem disco permanente, use um volume persistente ou elas somem a cada deploy).

## Configuração (`PELADA_*`)

| Variável | Para quê |
| --- | --- |
| `ENVIRONMENT` | `development` (padrão), `test` ou `production`. Em produção: `/docs` fechado, `JWT_SECRET` obrigatório com 32+ bytes e sem o CORS liberado para localhost |
| `DATABASE_URL` | SQLite (dev) ou Postgres (`postgresql+psycopg://...`) |
| `JWT_SECRET` | Obrigatória em produção (gere com `python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `CORS_ORIGINS` | Origens do frontend, em JSON: `["https://app.seudominio.com.br"]` |
| `CORS_ORIGIN_REGEX` | Fora de produção, o padrão aceita qualquer porta de localhost |
| `FRONTEND_URL` | Base dos links enviados por e-mail (redefinição de senha) |
| `PUBLIC_BASE_URL` | URL pública da API, usada nas fotos enviadas (padrão: URL da requisição) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Envio de e-mail. Sem `SMTP_HOST`, o e-mail é escrito no log do servidor |
| `MEDIA_DIR`, `MAX_UPLOAD_BYTES` | Onde ficam as fotos de perfil (servidas em `/media`) e tamanho máximo |
| `GOOGLE_CLIENT_ID` | Login com Google. Sem ele, o botão do Google não aparece |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Web Push. Gere com `python scripts/generate_vapid_keys.py`; sem as chaves, só há notificações internas |
| `RATE_LIMIT_ENABLED` | Limite de tentativas em login, cadastro, redefinição e troca de senha (padrão: ligado) |
| `CLIENT_IP_HEADER` | Cabeçalho com o IP real do cliente posto pelo proxy da hospedagem (`Fly-Client-IP`, `CF-Connecting-IP`...). Sem ele, o limite por IP vê o IP do proxy |

### Limite de tentativas

Contadores em memória, por janela deslizante. Responde `429` com `Retry-After`.

| O quê | Limite |
| --- | --- |
| Login com senha errada | 5 por conta e 30 por IP a cada 15 min (acertar zera o da conta) |
| Cadastro | 10 por IP por hora |
| Pedido de redefinição de senha | 3 por e-mail e 10 por IP por hora |
| Link de redefinição inválido, login Google recusado | 10 e 30 por IP a cada 15 min |
| Senha atual errada (troca de senha, exclusão de conta) | 5 por usuário a cada 15 min |

Como os contadores ficam no processo, a API deve rodar com **uma instância e um worker** (o agendador de
lembretes também roda dentro dela). Com mais réplicas, troque por Redis.

### Primeiro administrador da plataforma

Cadastre-se no app e rode, com o mesmo `PELADA_DATABASE_URL` da API:

```sh
python scripts/make_platform_admin.py voce@exemplo.com            # --remover tira o acesso
```

## Módulos

- **Grupos, convites e membros**, papéis e aprovação de entrada.
- **Peladas** avulsas e recorrentes, confirmação, lista de espera, presença efetiva. Cada recorrência mantém
  `auto_generate_ahead` ocorrências futuras criadas e publicadas pelo agendador (0 = só geração manual);
  ocorrências canceladas não contam.
- **Perfil esportivo**: atributos, média, nível em estrelas (0 a 5; com os limites padrão, a média
  arredondada), notas provisórias, histórico.
- **Quiz de nível** (`app/services/level_quiz.py`; `GET|POST /groups/{id}/self-assessment`): cinco perguntas
  simples (de linha: finalização, passe, drible, defesa, físico; ou de goleiro, em grupos com avaliação própria
  de goleiro: reflexo, posicionamento, saída do gol, reposição, um contra um) e, no fim, o nível (0 a 5
  estrelas) que o jogador acha que tem. Quem responde "no gol" passa a ser goleiro no grupo.
  - Pendente para todo membro ativo que ainda não respondeu, inclusive quem já estava no grupo
    (`GroupOut.my_self_assessment_pending`). Quem já tem perfil consolidado pelas avaliações fica dispensado.
    `MemberOut.self_assessment.status` = `not_answered | to_review | confirmed | exempt`, visível ao admin e ao
    próprio jogador.
  - As respostas viram a nota inicial provisória (a opção mais alta vale 4,5) se o perfil ainda não tem
    avaliações nem nota do admin; esse nível vale até um admin confirmar ou ajustar o nível declarado
    (`POST /groups/{id}/members/{member_id}/level-review`), que leva a nota ao nível escolhido enquanto o perfil
    é provisório (os atributos andam juntos, preservando os pontos fortes). Depois, as avaliações pós-pelada
    seguem ajustando.
  - O próprio nível de um admin só é confirmado por outro admin; se ele é o único, o nível declarado vale na
    hora.
  - Lembretes para quem não respondeu: o admin manda na hora (`POST /groups/{id}/self-assessment/remind`, no
    máximo a cada 12 horas por jogador) e o agendador lembra a cada 3 dias, até 3 vezes, das 9h às 21h.
  - O sorteio avisa quantos jogadores ainda estão sem nível confirmado. Depois de 3 peladas avaliadas,
    `self_estimate` compara o nível declarado com o atual (`over | under | accurate`).
  - O painel do admin conta quem não respondeu e quantos níveis aguardam; os admins são avisados a cada quiz
    respondido. As respostas entram na exportação de dados (LGPD) e são apagadas na exclusão da conta.
- **Avaliação pós-jogo** com janela, antimanipulação e consolidação.
- **Financeiro** (só controle: o pagamento é feito por fora do app): cobrança por pelada, quitação em lote,
  pendências, isenção/estorno; regras opcionais de cobrança para quem falta sem avisar ou desiste fora do prazo;
  aviso de "já paguei" do jogador, que o administrador confere e confirma. Lembrete periódico de pendências
  (`debt_reminder_days` do grupo, padrão 3; 0 desliga): o agendador avisa, das 9h às 21h de Brasília, quem tem
  dívida em aberto, somando tudo numa mensagem; quem já informou o pagamento não é lembrado. Exportação em CSV
  para planilha (`GET /groups/{id}/finance/export?view=payments|debtors`, mesmos filtros da lista; separador
  `;`, vírgula decimal, UTF-8 com BOM, proteção contra fórmulas; a exportação fica na auditoria).
- **Sorteio inteligente** de times, ajuste manual, comparação e publicação.
- **Juiz na pelada**: `referee_slots` (0 a 2) abre vagas de juiz fora das vagas de jogador. O membro confirma com
  `as_referee` ("Apitar"); o juiz fica fora do sorteio, da cobrança, das avaliações e do ranking, e aparece na
  escalação (aba Times).
- **Goleiro e juiz de aluguel (comércio)**: anúncio em `PUT /pros/me` (goleiro e/ou juiz, valor por pelada,
  duração máxima, descrição, cidade/bairro, modalidades, contato e agenda semanal + datas avulsas). Busca
  pública em `GET /public/pros?role=goalkeeper|referee&date=&start=&end=` (só quem tem agenda livre e não está
  contratado no horário). O admin da pelada envia o pedido (`POST /matches/{id}/hires`); o profissional aceita
  em `POST /hires/{id}/accept` e entra na pelada (goleiro vai para o sorteio; juiz ocupa a vaga de juiz). Quem
  não é do grupo entra como convidado (`guest`), sem acesso ao grupo. O valor dos contratados presentes é
  rateado entre os jogadores presentes na cobrança da pelada; o pagamento ao profissional é por fora. Pedidos
  sem resposta expiram quando a pelada começa; cancelar a pelada cancela os pedidos. O telefone de cada lado
  só aparece com o pedido aceito.
- **Calibração do sorteio** (v2.0): `GET /groups/{id}/draws/calibration` e `GET /admin/draws/calibration` medem
  com os placares registrados se os sorteios geram jogos parelhos (por até 1 gol, goleadas, favorito venceu) e
  sugerem pesos a partir da correlação entre o desequilíbrio deixado em cada critério e o saldo de gols
  (mínimo de 8 jogos; a medição pesa até 50%). Aplicar = `PATCH /groups/{id}` ou `PATCH /admin/settings`.
- **Explorar: peladas abertas e comunidades** (v3.0): o grupo escolhe aparecer em Explorar (`discoverable`,
  com cidade e bairro). Rotas públicas em `/public/discover/...` (peladas futuras com vaga, comunidades,
  cidades, detalhe) e `GET /public/invites/{token}` (prévia do convite). "Quero jogar"
  (`POST /discover/matches/{id}/join`) e "Entrar na comunidade" (`POST /discover/groups/{id}/join`) passam
  pela aprovação de um admin por padrão (`public_join_requires_approval`). Nada público mostra jogadores,
  notas ou finanças.
- **Placar e estatísticas**: jogos de cada pelada (placar, gols, assistências, gols contra) e, a partir deles,
  ranking (pontos, gols, assistências, vitórias, aproveitamento, presenças), confronto direto entre jogadores,
  premiações mensais e conquistas. As estatísticas não entram no cálculo das notas do sorteio.
- **Jogo ao vivo**: começa 0 x 0 entre dois times do sorteio, com cronômetro regressivo (padrão 10 min,
  alterável durante o jogo); cada gol é lançado na hora com o autor e o minuto vem do cronômetro. Um jogo ao
  vivo por pelada; só entra nas estatísticas depois de encerrado.
- **Ranking público** por link (opcional por grupo), sem notas nem dados pessoais.
- **Conta**: redefinição de senha por e-mail (link de uso único, encerra sessões antigas), foto de perfil.
- **Login com Google**: o frontend usa o botão do Google Identity Services e envia o ID token para
  `POST /auth/google`; a API confere assinatura (chaves públicas do Google), Client ID, emissor, validade e
  e-mail verificado. A conta é encontrada pelo identificador do Google ou pelo e-mail verificado (vincula
  contas existentes e reivindica contas criadas pelo administrador); senão, uma conta nova é criada. No
  perfil é possível conectar/desconectar o Google e criar senha para contas que só usam Google.
- **Notificações** internas + Web Push opcional (inclui aviso de resultado para quem jogou).
- **Métricas de sucesso** (PRD §21): `GET /groups/{id}/metrics` (respostas, ocupação, desistências, avaliações,
  pagamentos, equilíbrio dos sorteios, retenção) e `GET /admin/metrics` (usuários, grupos ativos, retenção).
- **Configurações globais** (`/admin/settings`, só administrador da plataforma): limites das estrelas (mudar
  reclassifica todas as notas e registra no histórico) e padrões de nota/sorteio para grupos novos.
- **Gestão de grupos pela plataforma** (`/admin/groups`): filtros por situação (ativos, suspensos,
  desativados, sem administrador), dados de suporte do grupo, suspensão com motivo (o grupo fica inativo e
  somente leitura para os membros, sem lembretes do agendador) e reativação, e definição de administradores
  para grupos que ficaram sem responsável. As ações aparecem na auditoria do próprio grupo.
- **Espaços (dono da quadra)** (`/venues`): cadastro do espaço, quadras com preço por hora, horário de
  funcionamento (horário de Brasília) e equipe (dono e funcionários). O grupo pede vínculo pelo código do
  espaço (`POST /groups/{id}/venue/request`) ou o espaço cria a pelada e define quem a administra. Peladas e
  recorrências com `court_id` reservam o horário: conflito com outra pelada, bloqueio ou horário fechado
  responde 409 `court_unavailable` (o agendador pula datas indisponíveis). Agenda por quadra com horários
  livres (`/venues/{id}/calendar`; na visão do grupo os outros aparecem como "Ocupado"), aluguel lançado
  quando a pelada começa (preço combinado no vínculo ou o da quadra × duração; "já paguei" do grupo e
  quitação pelo espaço) e painel com ocupação, cancelamentos, peladas em risco e grupos em atraso. O espaço
  nunca vê notas, avaliações nem finanças dos jogadores.
- **LGPD**: `GET /me/export` baixa os dados da conta em JSON; `POST /me/delete` exclui a conta anonimizando
  nome, e-mail, telefone e foto, libera vagas futuras e preserva o histórico do grupo sem identificar o titular.

### Configurando o login com Google

1. No [Google Cloud Console](https://console.cloud.google.com/apis/credentials), crie uma credencial
   **ID do cliente OAuth** do tipo **Aplicativo da Web** (configure antes a tela de consentimento).
2. Em **Origens JavaScript autorizadas**, adicione a URL do frontend (`http://localhost:8080` em
   desenvolvimento e o domínio de produção). Não é preciso URI de redirecionamento nem client secret.
3. Defina `PELADA_GOOGLE_CLIENT_ID` no `.env` da API e reinicie. O frontend busca o Client ID em
   `GET /auth/config`.

## Migrações

`alembic revision --autogenerate -m "descrição"` e revise o arquivo gerado (booleanos novos em tabelas
existentes precisam de `server_default`; constraints precisam de nome). `alembic check` deve terminar sem
diferenças.
