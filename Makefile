PYTHON ?= python3
IMAGE  ?= ghcr.io/osm-fulda/panoramax-exporter
TAG    ?= dev

.PHONY: help venv deps lint fmt test build image run clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

deps:  ## Install runtime + dev dependencies
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:  ## Ruff lint + format check
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt:  ## Auto-format
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

test:  ## Run the test suite
	$(PYTHON) -m pytest

build:  ## Byte-compile the exporter (cheap syntax/import gate) and build the image
	$(PYTHON) -m compileall -q exporter.py
	docker build -t $(IMAGE):$(TAG) .

image: build  ## Alias for build

run:  ## Run locally against PANORAMAX_API
	$(PYTHON) exporter.py

clean:
	rm -rf .pytest_cache .ruff_cache __pycache__ dist
