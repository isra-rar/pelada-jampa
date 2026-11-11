# Atalhos do Pelada+ (rode na raiz deste repositório; precisa de make e bash, como o Git Bash).
#
#   make deploy      envia os dois repositórios ao GitHub e atualiza a VPS (o comando do dia a dia)
#   make push        só envia ao GitHub
#   make atualizar   so atualiza a VPS (ela baixa do GitHub)
#   make status | logs | logs-web | health | ssh | backup
#
# Antes do deploy, faça commit nos dois repositórios. O make não faz commit por você.

SHELL := bash
export MSYS_NO_PATHCONV := 1

# atalho do ~/.ssh/config (root@143.95.184.202, porta 22022)
VPS     ?= vps
REMOTO  ?= /srv/pelada
FRONT   := frontend/pelada-equilibrada
DOMINIO ?= https://pelada.rodricode.com.br

.PHONY: help deploy push atualizar checar status logs logs-web health ssh backup

help:
	@echo "make deploy    push dos 2 repos + atualiza a VPS"
	@echo "make push      so push"
	@echo "make atualizar so atualiza a VPS"
	@echo "make status    processos do PM2"
	@echo "make logs      logs da API (logs-web: do site)"
	@echo "make health    testa /health e o site pelo dominio"
	@echo "make ssh       abre terminal na VPS"
	@echo "make backup    roda o backup do banco e fotos agora"

deploy: push atualizar health

# Falha se houver alteração sem commit: o deploy mandaria uma versão diferente da que está no seu PC.
checar:
	@dirty=0; \
	for d in . $(FRONT); do \
	  if [ -n "$$(git -C $$d status --porcelain --untracked-files=no)" ]; then \
	    echo "Há alterações sem commit em $$d:"; git -C $$d status -s --untracked-files=no; dirty=1; \
	  fi; \
	done; \
	[ $$dirty -eq 0 ] || { echo "Faça commit antes (ou use: make deploy FORCE=1 para ignorar)."; [ -n "$(FORCE)" ]; }

push: checar
	exec git push origin main
	exec git -C $(FRONT) push origin main

atualizar:
	exec ssh $(VPS) 'cd $(REMOTO) && bash deploy/atualizar.sh'

status:
	exec ssh $(VPS) 'pm2 status --no-color'

logs:
	exec ssh $(VPS) 'pm2 logs pelada-api --lines 100 --nostream --no-color'

logs-web:
	exec ssh $(VPS) 'pm2 logs pelada-web --lines 100 --nostream --no-color'

health:
	@echo "API: $$(curl -fsS -m 20 $(DOMINIO)/health)"
	@echo "Site: HTTP $$(curl -s -m 20 -o /dev/null -w '%{http_code}' $(DOMINIO)/)"

ssh:
	exec ssh $(VPS)

backup:
	exec ssh $(VPS) '/usr/local/bin/backup-pelada.sh && ls -lh /root/backups | tail -4'
