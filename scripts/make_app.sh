#!/bin/bash
# Собирает ~/Applications/Jarvis.app (иконка в меню-баре) и включает автозапуск при входе в систему.
# Удалить: scripts/make_app.sh --remove
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/Jarvis.app"
AGENT="$HOME/Library/LaunchAgents/local.jarvis.voice.plist"

if [ "$1" = "--remove" ]; then
  launchctl unload "$AGENT" 2>/dev/null || true
  rm -rf "$APP" "$AGENT"
  echo "Jarvis.app и автозапуск удалены"
  exit 0
fi

mkdir -p "$APP/Contents/MacOS" "$HOME/Library/LaunchAgents"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Jarvis</string>
  <key>CFBundleIdentifier</key><string>local.jarvis.voice</string>
  <key>CFBundleExecutable</key><string>Jarvis</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.3</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Jarvis слушает твои ответы для Claude.</string>
</dict></plist>
PLIST
# Нативный запуск со встроенным Python — доступы macOS получает «Jarvis», а не python3.12
PYCFG="$("$ROOT/.venv/bin/python" -c "import sysconfig, os; print(os.path.join(sysconfig.get_config_var('BINDIR'), 'python3.12-config'))")"
clang -O2 -o "$APP/Contents/MacOS/Jarvis" "$ROOT/scripts/launcher.c" -DJARVIS_ROOT="\"$ROOT\"" \
  $("$PYCFG" --cflags) $("$PYCFG" --ldflags --embed) 2>/dev/null
codesign --force --deep -s - "$APP" 2>/dev/null || true

cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.jarvis.voice</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>$APP</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
PLIST
echo "Готово: $APP (автозапуск при входе — $AGENT)"
