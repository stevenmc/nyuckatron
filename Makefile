PYTHON        ?= python3
MIN_PYTHON    := 3.10
PREFERRED_PYTHON := 3.13  # what's actually been verified end-to-end (2026-09) -- prefer this when installing fresh
VENV          := .venv
VENV_PY       := $(VENV)/bin/python3
VENV_PIP      := $(VENV)/bin/pip
PROJECT_DIR   := $(shell pwd)

# Overridable: make cron-install CRON_SCHEDULE="*/15 * * * *"
# Default is hourly, 8am-8pm -- adjust to taste.
CRON_SCHEDULE ?= 0 8-20 * * *
CRON_MARKER   := \# newry-bot
CRON_CMD      := cd $(PROJECT_DIR) && $(VENV_PY) bot.py >> $(PROJECT_DIR)/cron.log 2>&1

.PHONY: help check venv install install-dev test run run-events env cron-install cron-remove cron-status clean reset-db

help:
	@echo "newry-bot -- available targets:"
	@echo "  make check         Verify Python version and sqlite3 support"
	@echo "  make install       Create venv and install runtime dependencies"
	@echo "  make install-dev   Create venv and install runtime + test dependencies"
	@echo "  make test          Run the unit test suite (offline, no Reddit credentials needed)"
	@echo "  make env           Copy .env.example to .env if .env doesn't exist yet"
	@echo "  make run           Run the bot once (one-off execution -- news + events + calendar)"
	@echo "  make run-events    Google Calendar sync only -- no Reddit interaction at all"
	@echo "  make cron-install  Add a cron entry that runs the bot hourly, 8am-8pm (see CRON_SCHEDULE)"
	@echo "  make cron-remove   Remove the cron entry"
	@echo "  make cron-status   Show whether the cron entry is currently installed"
	@echo "  make clean         Remove the venv and Python cache directories (keeps state.db, logs)"
	@echo "  make reset-db      Delete state.db (dedup history) -- asks first unless FORCE=1"

# Fails clearly rather than letting a too-old interpreter produce a
# confusing error partway through pip install or a test run.
check:
	@command -v $(PYTHON) >/dev/null 2>&1 || { \
		echo "ERROR: '$(PYTHON)' not found on PATH."; \
		echo "Install Python $(MIN_PYTHON)+ ($(PREFERRED_PYTHON) preferred) -- run './run.sh check' for"; \
		echo "OS-specific install commands (Amazon Linux 2023/2, Ubuntu/Debian)."; \
		exit 1; }
	@$(PYTHON) -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" || { \
		echo "ERROR: $(PYTHON) is $$($(PYTHON) --version 2>&1), need $(MIN_PYTHON)+."; \
		echo "Install Python $(MIN_PYTHON)+ ($(PREFERRED_PYTHON) preferred) -- run './run.sh check' for"; \
		echo "OS-specific install commands (Amazon Linux 2023/2, Ubuntu/Debian)."; \
		exit 1; }
	@$(PYTHON) -c "import sqlite3" 2>/dev/null || { \
		echo "ERROR: $(PYTHON) was built without sqlite3 support (used for local dedup state)."; \
		echo "Reinstall Python with sqlite3 support -- most standard distributions have this by default."; exit 1; }
	@$(PYTHON) -c "import venv" 2>/dev/null || { \
		echo "ERROR: the 'venv' standard-library module isn't available."; \
		echo "On Debian/Ubuntu: sudo apt-get install python3-venv"; exit 1; }
	@echo "OK: $$($(PYTHON) --version) with sqlite3 and venv support."

venv: check
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)

install: venv
	$(VENV_PIP) install -q --upgrade pip
	$(VENV_PIP) install -q -r requirements.txt
	@echo "Installed. Next: 'make env' then edit .env, then 'make run'."

install-dev: venv
	$(VENV_PIP) install -q --upgrade pip
	$(VENV_PIP) install -q -r requirements-dev.txt

test: install-dev
	$(VENV_PY) -m pytest -q

env:
	@if [ -f .env ]; then \
		echo ".env already exists, left untouched."; \
	else \
		cp .env.example .env && echo "Created .env from .env.example -- edit it now with your Reddit app credentials."; \
	fi

run: install env
	$(VENV_PY) bot.py

# Google Calendar sync only -- no Reddit interaction at all (doesn't even
# need Reddit credentials). Skips both the news pipeline and
# events-to-Reddit posting entirely. Useful for testing calendar_sync.py
# in isolation.
run-events: install env
	$(VENV_PY) bot.py calendar

reset-db:
	@if [ ! -f state.db ]; then echo "No state.db found -- nothing to reset."; exit 0; fi
	@if [ "$(FORCE)" != "1" ]; then \
		read -r -p "This deletes state.db permanently -- dedup history starts from scratch on the next run (may re-post anything currently in a feed's window). Continue? [y/N] " reply; \
		case "$$reply" in [yY]|[yY][eE][sS]) ;; *) echo "Aborted."; exit 1;; esac; \
	fi
	rm -f state.db
	@echo "Removed state.db -- it's recreated automatically (empty) on the next run."

cron-install:
	@( crontab -l 2>/dev/null | grep -v -F '$(CRON_MARKER)' ; echo '$(CRON_SCHEDULE) $(CRON_CMD) $(CRON_MARKER)' ) | crontab -
	@echo "Cron entry installed (schedule: $(CRON_SCHEDULE)). Output goes to $(PROJECT_DIR)/cron.log"
	@echo "Change the schedule later with: make cron-install CRON_SCHEDULE=\"*/15 * * * *\""

cron-remove:
	@crontab -l 2>/dev/null | grep -v -F '$(CRON_MARKER)' | crontab - || true
	@echo "Cron entry removed (if it existed)."

cron-status:
	@crontab -l 2>/dev/null | grep -F '$(CRON_MARKER)' || echo "No newry-bot cron entry installed."

clean:
	rm -rf $(VENV) __pycache__ tests/__pycache__ .pytest_cache
	find . -name "*.pyc" -delete
