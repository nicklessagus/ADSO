COMPOSE := docker compose -f ~/docker/ADSO/docker-compose.yml
DEPLOY_DIR := ~/docker/ADSO

.PHONY: deploy stop restart logs status shell prune llm-baseline llm-check

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
