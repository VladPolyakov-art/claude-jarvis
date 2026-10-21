"""Глобальный хоткей через Carbon RegisterEventHotKey — не требует «Универсального доступа».
Регистрировать из главного потока приложения: события приходят в его цикл (NSApplication)."""
import ctypes
import threading

from .log import log

_carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")

MODS = {"<cmd>": 256, "<shift>": 512, "<alt>": 2048, "<ctrl>": 4096}
# виртуальные коды клавиш (kVK_ANSI_*), раскладка не важна
KEYS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40,
    "n": 45, "m": 46, "<space>": 49,
}


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_Handler = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
_carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _Handler, ctypes.c_ulong,
                                        ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p, ctypes.c_void_p]
_carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotKeyID, ctypes.c_void_p,
                                        ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]

_keep = []  # колбэк и ссылки должны жить, пока живёт приложение


def parse(combo: str) -> tuple[int, int]:
    """«<ctrl>+<alt>+j» → (код клавиши, модификаторы Carbon)."""
    mods, key = 0, None
    for part in combo.lower().split("+"):
        if part in MODS:
            mods |= MODS[part]
        else:
            key = KEYS[part]
    if key is None:
        raise ValueError(f"в хоткее «{combo}» нет клавиши")
    return key, mods


def register(combo: str, callback) -> bool:
    try:
        key, mods = parse(combo)
        handler = _Handler(lambda *_: (threading.Thread(target=callback, daemon=True).start(), 0)[1])
        spec = _EventTypeSpec(int.from_bytes(b"keyb", "big"), 5)  # kEventHotKeyPressed
        target = _carbon.GetApplicationEventTarget()
        if _carbon.InstallEventHandler(target, handler, 1, ctypes.byref(spec), None, None) != 0:
            raise OSError("InstallEventHandler")
        ref = ctypes.c_void_p()
        if _carbon.RegisterEventHotKey(key, mods, _HotKeyID(int.from_bytes(b"jrvs", "big"), 1),
                                       target, 0, ctypes.byref(ref)) != 0:
            raise OSError("RegisterEventHotKey — сочетание занято другим приложением?")
        _keep.extend([handler, spec, ref])
        log(f"хоткей {combo} — заговорить первым")
        return True
    except Exception as e:
        log(f"хоткей не включился: {e}")
        return False
