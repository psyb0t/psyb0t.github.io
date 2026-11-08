.PHONY: build test lint preview clean help

# Checkout of github.com/psyb0t/psyb0t, whose scripts/update.py and
# categories.json supply the repo list.
PROFILE_DIR ?= ../psyb0t
OUT ?= _site
PREVIEW_PORT ?= 8000

all: build

build:
	GITHUB_TOKEN="$${GITHUB_TOKEN:-$$(gh auth token 2>/dev/null)}" python3 scripts/build.py --profile-dir $(PROFILE_DIR) --out $(OUT)

test:
	python3 -m pytest -q tests

lint:
	python3 -m ruff check scripts tests
	python3 -m ruff format --check scripts tests
	python3 -m mypy --ignore-missing-imports scripts/build.py

preview: build
	python3 -m http.server --directory $(OUT) $(PREVIEW_PORT)

clean:
	rm -rf $(OUT)

help:
	@echo "build    build the site into $(OUT) (uses GITHUB_TOKEN or gh auth token)"
	@echo "test     run the unit tests"
	@echo "lint     ruff and mypy"
	@echo "preview  build, then serve $(OUT) on http://localhost:$(PREVIEW_PORT)"
	@echo "clean    delete $(OUT)"
