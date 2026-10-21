#!/bin/bash
# Установка Jarvis на macOS (Apple Silicon): зависимости, модели, хуки Claude Code, приложение в меню-баре.
set -e
cd "$(dirname "$0")"

say_step() { printf "\n\033[1m▸ %s\033[0m\n" "$1"; }

[ "$(uname -m)" = "arm64" ] || { echo "Нужен Mac на Apple Silicon (Whisper крутится на MLX)"; exit 1; }
command -v brew >/dev/null || { echo "Нужен Homebrew: https://brew.sh"; exit 1; }
command -v clang >/dev/null || { echo "Нужны Command Line Tools: xcode-select --install"; exit 1; }
command -v claude >/dev/null || echo "⚠️  Claude Code не найден — хуки поставлю, но без него Jarvis бесполезен"

say_step "Инструменты (uv, terminal-notifier)"
brew list uv >/dev/null 2>&1 || brew install uv
brew list terminal-notifier >/dev/null 2>&1 || brew install terminal-notifier

say_step "Ollama и модель для выжимок"
if ! command -v ollama >/dev/null; then
  echo "Поставь Ollama (https://ollama.com/download), запусти её и перезапусти install.sh"; exit 1
fi
ollama pull "${JARVIS_MODEL:-qwen3:4b-instruct}"

say_step "Python-окружение"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

say_step "Голоса Piper (по желанию, ~190 МБ)"
read -r -p "Скачать мужские голоса Piper? [y/N] " yn
[[ "$yn" =~ ^[YyДд] ]] && ./jarvis.sh get-piper

say_step "Хуки Claude Code (~/.claude/settings.json, с бэкапом)"
./jarvis.sh install-hooks

say_step "Приложение в меню-баре + автозапуск"
./scripts/make_app.sh
open "$HOME/Applications/Jarvis.app"

cat <<'TXT'

Готово. Первый запуск качает модели (~2 ГБ) — подожди, пока иконка ⏳ сменится на 🎙.
Дай доступы в «Системные настройки → Конфиденциальность и безопасность»:
  • Микрофон → Jarvis
  • Универсальный доступ и Мониторинг ввода → Jarvis (для хоткея ⌃⌥J)
TXT
