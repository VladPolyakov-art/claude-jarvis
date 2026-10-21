#!/bin/bash
# Точка входа: ./jarvis.sh start | on | off | status | say ТЕКСТ | test | install-hooks | uninstall-hooks
cd "$(dirname "$0")" && exec .venv/bin/python -m jarvis.cli "$@"
