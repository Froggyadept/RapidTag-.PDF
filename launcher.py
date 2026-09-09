# -*- coding: utf-8 -*-
"""Скрытый переносимый лаунчер RapidTag (без окна консоли).

Запускает Streamlit-сервер и сам открывает браузер. Работает и из исходников
(``pythonw launcher.py``), и из собранного PyInstaller-дистрибутива
(``dist\\RapidTag\\RapidTag.exe``).

Переменная окружения:
    RAPIDTAG_PORT    — порт (по умолчанию 8501)
"""

import os
import sys
import time
import threading
import webbrowser
from pathlib import Path


def _fix_stdio_for_windowless() -> None:
    """pythonw / PyInstaller console=False оставляют stdout/stderr = None.
    Streamlit пишет лог в stdout, поэтому подставляем заглушку в devnull."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _app_path() -> Path:
    return _base_dir() / "app.py"


def _open_browser(port: int, delay: float = 3.0) -> None:
    def _go() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def main() -> int:
    _fix_stdio_for_windowless()

    port = int(os.environ.get("RAPIDTAG_PORT", "8501"))
    app = _app_path()

    if not app.exists():
        # Без консоли сообщить об ошибке негде — пишем флаг-файл рядом.
        (_base_dir() / "RAPIDTAG_ERROR.txt").write_text(
            f"Не найден {app.name}", encoding="utf-8"
        )
        return 1

    # Streamlit и пользовательский код могут разрешать относительные пути
    # относительно cwd. Не доверяем ярлыку/PowerShell: рабочая папка всегда
    # должна совпадать с папкой конкретного комплекта RapidTag.
    base_dir = _base_dir().resolve()
    os.chdir(base_dir)

    _open_browser(port)

    # headless=true: streamlit не пытается сам открывать окно/браузер и не
    # требует консоли; браузер открываем сами выше.
    sys.argv = [
        "streamlit",
        "run",
        str(app.resolve()),
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--browser.gatherUsageStats=false",
    ]

    try:
        from streamlit.web import cli as stcli
    except ImportError:  # старые/нестандартные сборки streamlit
        import streamlit.web.cli as stcli

    sys.exit(stcli.main())


if __name__ == "__main__":
    main()