DEPLOY_DIR := $(HOME)/docker/ADSO

# El `docker-compose.yml` del repo es la base y manda en todo lo compartido
# (healthcheck, hardening, logging, volúmenes). `$(DEPLOY_DIR)/local.yml` agrega
# solo lo propio de esta máquina: dónde está el código y las llaves SSH.
#
# Antes esto era `-f $(DEPLOY_DIR)/docker-compose.yml`, una COPIA adaptada a
# mano: nada la sincronizaba con el repo, así que los cambios al compose no
# llegaban nunca a producción. El fix del healthcheck (B4 de la auditoría
# 2026-07-31) estuvo semanas commiteado y documentado como hecho mientras
# producción seguía corriendo el roto.
#
# `--project-directory` mantiene `.env`, `./config.yaml` y `./credentials`
# resolviendo contra DEPLOY_DIR (hay un `.env` distinto en el repo: sin esto
# cargaría el equivocado). `-p adso` fija el nombre de proyecto para que el
# volumen siga siendo `adso_adso-data`, donde viven ChromaDB y whisper.
COMPOSE := docker compose -p adso --project-directory $(DEPLOY_DIR) \
	-f docker-compose.yml -f $(DEPLOY_DIR)/local.yml

.PHONY: deploy stop restart logs status shell prune check-sync llm-baseline llm-check

deploy:
	cp config.yaml $(DEPLOY_DIR)/config.yaml
	$(COMPOSE) up --build -d

stop:
	$(COMPOSE) stop

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f

status:
	$(COMPOSE) ps

shell:
	$(COMPOSE) exec adso-bot bash

# Reconcilia el indice de ChromaDB contra las notas en disco. Read-only: sirve
# para confirmar que el VaultWatcher proceso los borrados/ediciones hechos a
# mano en Obsidian. Corre dentro del contenedor, que es donde estan montados el
# vault y el volumen de Chroma.
check-sync:
	$(COMPOSE) exec -T -e PYTHONPATH=/app -w /app adso-bot \
		python /app/scripts/check_vault_sync.py

prune:
	docker image prune -f

# Harness de regresión de modelo — pega contra la API real, ver
# tests/llm_regression/README.md. Correr llm-baseline ANTES de evaluar candidatos.
llm-baseline:
	python scripts/llm_regression.py --save

# make llm-check MODEL=gemini-3.7-flash BASE=gemini-3.5-flash-lite
llm-check:
	python scripts/llm_regression.py --model $(MODEL) \
		--compare tests/llm_regression/baselines/$(BASE).json
