# -*- mode: python ; coding: utf-8 -*-
# RapidTag.spec — однопапочная (onedir) сборка PyInstaller.
#
#   pip install pyinstaller
#   pyinstaller RapidTag.spec
#
# Результат: dist/RapidTag/RapidTag.exe + app.py.
# Запускать потом можно двойным кликом по portable_start.vbs (скрытый запуск)
# либо напрямую RapidTag.exe.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Streamlit и тяжёлые зависимости app.py собираем целиком (их импортирует
# сам скрипт app.py, PyInstaller их не увидит через лаунчер).
for pkg in [
    "streamlit",
    "pandas",
    "openpyxl",
    "pdfplumber",
    "fitz",                      # PyMuPDF
    "PIL",                       # Pillow
    "streamlit_image_coordinates",
]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Явный список на случай, если collect_all чего-то недобрал или лаунчер
# импортирует streamlit лениво (внутри main()), и статический анализ его не видит.
hiddenimports += [
    "streamlit",
    "streamlit.web.cli",
    "streamlit.web.bootstrap",
    "streamlit.runtime.scriptrunner.script_run_context",
    "pandas",
    "openpyxl",
    "pdfplumber",
    "fitz",
    "PIL",
    "streamlit_image_coordinates",
]

# app.py идёт как данные рядом с exe, чтобы Streamlit запускал его из папки
# публичного комплекта.
datas += [
    ("launcher.py", "."),
    ("app.py", "."),
]
if __import__("os").path.exists("settings.json"):
    datas += [("settings.json", ".")]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RapidTag",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,      # скрытый запуск: без окна консоли
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RapidTag",
)