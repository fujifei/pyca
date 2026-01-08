.PHONY: help install install-dev build wheel clean test

help:
	@echo "PCA (Python Coverage Agent) - Makefile"
	@echo ""
	@echo "Available commands:"
	@echo "  make install      - Install PCA in normal mode"
	@echo "  make install-dev  - Install PCA in development mode"
	@echo "  make build        - Build source and wheel distributions"
	@echo "  make wheel        - Build wheel distribution only"
	@echo "  make clean        - Clean build artifacts"
	@echo "  make test         - Run tests"

install:
	pip install .

install-dev:
	pip install -e .

build:
	pip install build wheel
	python -m build

wheel:
	pip install build wheel
	python -m build --wheel

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	find . -type d -name __pycache__ -exec rm -r {} +
	find . -type f -name "*.pyc" -delete

test:
	python -m pytest tests/ -v

