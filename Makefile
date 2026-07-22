.PHONY: download merge

PYTHON ?= .venv/bin/python
ifeq ($(wildcard $(PYTHON)),)
PYTHON := python3
endif

download:
	$(PYTHON) src/download_books.py

merge:
	$(PYTHON) src/merge_books.py
