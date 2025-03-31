#!/usr/bin/env bash

include .env
export $(shell sed 's/=.*//' .env)

DOCKER_COMPOSE = docker compose -p $(ROOT_PROJECT_NAME)

CONTAINER := $(shell docker container ls -f "name=$(ROOT_PROJECT_NAME)-subtitle-incrustator" -q)

SE := docker exec -ti $(CONTAINER)

fix:
	$(SE) ruff check --fix --exclude src/Protobuf --unsafe-fixes
	$(SE) black src --exclude "src/Protobuf"
