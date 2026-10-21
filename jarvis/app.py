"""Jarvis в меню-баре: иконка состояния, вкл/выкл, голос, чаты. Внутри — тот же фоновый сервис."""
import socket
import subprocess
import sys
import threading

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

from . import config, daemon, hotkey, sessions, status
from .log import log

ICONS = {"loading": "⏳", "idle": "🎙", "speaking": "🗣", "listening": "👂", "thinking": "💭"}
VOICE_TITLES = {
    "aidar": "Айдар", "eugene": "Юджин", "aidar-low": "Айдар, низкий", "eugene-low": "Юджин, низкий",
    "baya": "Бая", "kseniya": "Ксения", "xenia": "Ксюша",
    "piper:dmitri": "Дмитрий (Piper)", "piper:denis": "Денис (Piper)", "piper:ruslan": "Руслан (Piper)",
}


def _port_busy() -> bool:
    with socket.socket() as s:
        return s.connect_ex((config.HOST, config.PORT)) == 0


class JarvisApp(rumps.App):
    def __init__(self):
        super().__init__("Jarvis", title="⏳", quit_button=None)
        self.toggle = rumps.MenuItem("Включён", callback=self.on_toggle)
        self.talk = rumps.MenuItem("Сказать первым  ⌃⌥J", callback=self.on_talk)
        self.chats = rumps.MenuItem("Чаты")
        self.voices = rumps.MenuItem("Голос")
        for key, title in VOICE_TITLES.items():
            item = rumps.MenuItem(title, callback=self.on_voice)
            item.voice = key
            self.voices.add(item)
        self.now = rumps.MenuItem("Загружаюсь…")
        self.menu = [self.now, None, self.toggle, self.talk, None, self.chats, self.voices, None,
                     rumps.MenuItem("Открыть лог", callback=lambda _: subprocess.run(["open", str(config.LOG_FILE)])),
                     rumps.MenuItem("Выйти", callback=lambda _: rumps.quit_application())]
        self._chats_sig = None
        rumps.Timer(self.refresh, 0.5).start()

    def refresh(self, _):
        off = config.OFF_FLAG.exists()
        chats = sessions.recent()
        queued = sum(len(c.pending) for c in chats) + sum(c.waiting for c in chats)
        icon = "🔇" if off and status.mode == "idle" else ICONS.get(status.mode, "🎙")
        self.title = icon + (f"{queued}" if queued else "")
        self.toggle.state = not off
        mode_text = {"loading": "Загружаю модели…", "idle": "Жду", "speaking": "Говорю",
                     "listening": "Слушаю", "thinking": "Думаю"}.get(status.mode, status.mode)
        self.now.title = ("Выключен — хуки молчат" if off else mode_text) + (f" · {status.chat}" if status.chat else "")
        for item in self.voices.values():
            item.state = item.voice == config.speaker()
        sig = [(c.title, c.waiting, len(c.pending)) for c in chats]
        if sig != self._chats_sig:
            self._chats_sig = sig
            if self.chats._menu is not None:  # rumps падает на clear() у ещё не созданного подменю
                self.chats.clear()
            if not chats:
                self.chats.add(rumps.MenuItem("Пока пусто"))
            for c in chats:
                mark = (" · в очереди" if c.waiting else "") + (f" · ✉️ {len(c.pending)}" if c.pending else "")
                self.chats.add(rumps.MenuItem(c.title + mark))

    def on_toggle(self, _):
        if config.OFF_FLAG.exists():
            config.OFF_FLAG.unlink()
        else:
            config.OFF_FLAG.touch()

    def on_talk(self, _):
        threading.Thread(target=daemon.on_hotkey, daemon=True).start()

    def on_voice(self, item):
        config.VOICE_FILE.write_text(item.voice)
        threading.Thread(target=daemon.on_say, args=({"text": "Теперь говорю так."},), daemon=True).start()


def main() -> None:
    if _port_busy():
        log("Jarvis уже запущен — второй экземпляр не нужен")
        subprocess.run(["terminal-notifier", "-title", "Jarvis", "-message", "Уже запущен"], check=False)
        sys.exit(0)
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    hotkey.register(config.HOTKEY, daemon.on_hotkey)  # Carbon: без «Универсального доступа»
    threading.Thread(target=daemon.serve, kwargs={"hotkey": False}, daemon=True).start()
    JarvisApp().run()


if __name__ == "__main__":
    main()
