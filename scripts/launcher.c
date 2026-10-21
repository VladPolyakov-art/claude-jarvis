// Нативный запуск Jarvis.app: Python встроен в сам процесс, поэтому macOS выдаёт доступы
// (микрофон, универсальный доступ) именно приложению «Jarvis», а не всему python3.12.
// Собирается из scripts/make_app.sh, JARVIS_ROOT подставляется при компиляции.
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char log[1024];
    snprintf(log, sizeof log, "%s/.jarvis/app.out", getenv("HOME"));
    freopen(log, "a", stdout);
    freopen(log, "a", stderr);
    if (chdir(JARVIS_ROOT) != 0) {
        perror("chdir " JARVIS_ROOT);
        return 1;
    }

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    // executable внутри .venv — так Python находит pyvenv.cfg и пакеты окружения
    PyStatus st = PyConfig_SetBytesString(&config, &config.executable, JARVIS_ROOT "/.venv/bin/python");
    if (PyStatus_Exception(st)) goto fail;
    char *args[] = {argv[0], "-m", "jarvis.app"};
    st = PyConfig_SetBytesArgv(&config, 3, args);
    if (PyStatus_Exception(st)) goto fail;
    st = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(st)) goto fail;
    PyConfig_Clear(&config);
    return Py_RunMain();

fail:
    PyConfig_Clear(&config);
    Py_ExitStatusException(st);
}
