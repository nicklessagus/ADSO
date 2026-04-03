COMPOSE := docker compose -f ~/docker/ADSO/docker-compose.yml

.PHONY: deploy stop restart logs status shell prune

deploy:
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
