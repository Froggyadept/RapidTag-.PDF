# -*- coding: utf-8 -*-
"""
RapidTag — кликовый набор бирок кабелей из PDF-схемы.

Логика: оператор кликает по словам на изображении страницы PDF.
Каждый клик добавляет "бит" в текущую сторону бирки (сторона 1 или 2),
биты внутри стороны склеиваются через настраиваемый разделитель, стороны —
через свой. По кнопке (или двойному клику по пустому месту) фиксируется
пара A/B и её зеркало B/A — либо, если набрана только сторона 1, одиночное
значение. Есть пакетный режим — генерация серии бирок по диапазону чисел.

Запуск:
    python -m streamlit run app.py
    (или через start.bat)
"""

import io
import re
import json
import time
import hashlib
import inspect
from pathlib import Path

import pandas as pd
import streamlit as st
import pdfplumber
import fitz  # PyMuPDF
from streamlit.errors import StreamlitAPIException
from streamlit_image_coordinates import streamlit_image_coordinates
from PIL import Image

from rapidtag_text import clean_word as clean_pdf_word

# ------------------------------------------------------------------
# Бренд / версия / контакты
# ------------------------------------------------------------------
APP_NAME = "RapidTag"
APP_VERSION = "2.1.0"

# Служебные маркеры page/reserve отфильтровываются из хвоста обозначения.
# Исходная строка никогда не отбрасывается целиком только из-за такого
# маркера: ``201U1page 3`` превращается в ``201U1``.
PERMANENT_BAN_WORDS = {"page", "reserve"}
TELEGRAM_HANDLE = "@protonfrog"
TELEGRAM_URL = "https://t.me/protonfrog"
CONTACT_EMAIL = "ilhaferrari@yandex.ru"

# ------------------------------------------------------------------
# Константы поведения
# ------------------------------------------------------------------
EMPTY_CLICK_DIST_PTS = 15      # дальше этого (в pt PDF) — клик считается "по пустому месту"
DOUBLE_CLICK_WINDOW_SEC = 0.6  # окно для распознавания двойного клика
VIEW_W_PX = 850                # ширина окна просмотра чертежа (px) — уменьшено под левую колонку
VIEW_H_PX = 1300               # высота окна просмотра чертежа (px) — почти до низа страницы
DEFAULT_ZOOM = 1.0
MAX_BATCH_RANGE = 500          # защита от случайного огромного диапазона
PAGE_SUFFIX_RE = re.compile(r"(?:\s*(?:page|reserve)\s*\d*\s*)+$", re.IGNORECASE)
TRAILING_NUM_RE = re.compile(r"^(.*?)(\d+)$")

APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "mask": {
        "inside_delimiter": ":",
        "between_delimiter": "/",
        "auto_suggest_enabled": True,
        "ignore_patterns": [],
    },
    "printer": {
        "model": "Elegir RT230",
        "label_width_mm": 40,
        "label_height_mm": 20,
        "dpi": 203,
        "font_size": 30,
    },
}


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = json.loads(json.dumps(DEFAULT_SETTINGS))  # глубокая копия дефолтов
            for section, vals in data.items():
                if section in merged and isinstance(vals, dict):
                    merged[section].update(vals)
                else:
                    merged[section] = vals
            return merged
        except Exception:
            return json.loads(json.dumps(DEFAULT_SETTINGS))
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    save_settings(settings)
    return settings


def save_settings(settings: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.warning(f"Не удалось сохранить settings.json: {e}")


# ------------------------------------------------------------------
# Настройка страницы + тема (тёмный фон, пастельные акценты + терминальный флёр)
# ------------------------------------------------------------------
st.set_page_config(page_title=f"{APP_NAME}", layout="wide")

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');

.stApp { background-color: #1a1d23; color: #e6e6e6; }
.block-container {padding-top: 1.2rem;}
h1, h2, h3 { color: #cdd6f4; }

.stApp::before {
    content: "";
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
        to bottom,
        rgba(120,255,170,0.018) 0px,
        rgba(120,255,170,0.018) 1px,
        transparent 1px,
        transparent 3px
    );
    pointer-events: none;
    z-index: 9999;
}

/* левая живая панель — реальные кнопки Streamlit внутри одного и того же
   fragment (через st.container(key=...)), прилипает при скролле */
.st-key-left_panel {
    position: sticky;
    top: 8px;
    z-index: 998;
    background-color: #20242c;
    border: 1px solid #2c313c;
    border-radius: 12px;
    padding: 14px 14px 16px 14px;
}

/* правая колонка (чертёж) — тоже прилипает, синхронно с левой. Без этого
   короткая картинка чертежа "заканчивалась" раньше, чем длинная левая
   панель, и при скролле справа оставалось пустое место (разметка
   "уезжала"). padding-top чуть опускает сам чертёж — чтобы он начинался
   не впритык к верху экрана, а примерно на уровне середины левой панели */
.st-key-right_panel {
    position: sticky;
    top: 8px;
    padding-top: 130px;
    z-index: 997;
}

.label-box {
    background-color: #20242c;
    border: 1px solid #2e7d4f;
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 21px;
    font-family: 'Share Tech Mono', 'Consolas', monospace;
    letter-spacing: 0.5px;
    box-shadow: 0 0 14px rgba(60, 220, 130, 0.14), inset 0 0 24px rgba(60, 220, 130, 0.03);
}
.hl {
    color: #9be89b;
    font-weight: 700;
    text-shadow: 0 0 6px rgba(155,232,155,0.55);
    background-color: rgba(155, 232, 155, 0.10);
    border-radius: 4px;
    padding: 0 4px;
}
.cursor {
    display:inline-block;
    color:#7fe0a0;
    margin-left: 3px;
    animation: blink 1s steps(1) infinite;
}
@keyframes blink { 50% { opacity: 0; } }

/* поле инлайн-правки бирки (п.6) — тот же вид, что и .label-box, чтобы
   переключение display -> edit выглядело как "клик в тот же текст" */
.st-key-label_edit_input input {
    background-color: #20242c !important;
    border: 1px solid #2e7d4f !important;
    border-radius: 10px !important;
    color: #9be89b !important;
    font-size: 21px !important;
    font-family: 'Share Tech Mono', 'Consolas', monospace !important;
    letter-spacing: 0.5px !important;
    padding: 14px 18px !important;
    box-shadow: 0 0 14px rgba(60, 220, 130, 0.14) !important;
}

.side-tag {
    display:inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 12px;
    margin-bottom: 6px;
    font-family: 'Share Tech Mono', monospace;
}
.side-1 { background-color: #b8c6f7; color:#1a1d23; }
.side-2 { background-color: #f7d6a8; color:#1a1d23; }
.mode-batch { background-color: #f7a8a8; color:#1a1d23; }

.stButton>button {
    background-color: #262b34;
    color: #d8dce3;
    border: 1px solid #3a4150;
    border-radius: 8px;
    padding: 6px 14px;
}
.stButton>button:hover {
    background-color: #303744;
    border-color: #6fdb9a;
    box-shadow: 0 0 8px rgba(111,219,154,0.3);
}
div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] { border-radius: 10px; overflow: hidden; }

/* Заставка / шпаргалка */
.splash-wrap { text-align:center; padding: 40px 0 10px 0; }
.splash-title {
    font-family: 'Share Tech Mono', monospace;
    font-size: 64px;
    font-weight: 700;
    color: #cdd6f4;
    letter-spacing: 4px;
    text-shadow: 0 0 18px rgba(111,219,154,0.45);
}
.splash-title .accent { color: #6fdb9a; text-shadow: 0 0 22px rgba(111,219,154,0.75); }
.splash-sub {
    font-family: 'Share Tech Mono', monospace;
    color: #8a93a6;
    font-size: 16px;
    margin-top: 6px;
}

/* ---- гейт-экран (вход по Ебаш ID) ---- */
.gate-ascii {
    font-family: 'Share Tech Mono', monospace;
    color: #4fae72;
    font-size: 13px;
    line-height: 1.4;
    opacity: 0.8;
    margin-bottom: 18px;
    white-space: pre;
}
.gate-prompt {
    font-family: 'Share Tech Mono', monospace;
    color: #9be89b;
    font-size: 16px;
    margin: 6px 0 8px 2px;
}
.gate-prompt::before { content: "> "; color: #4fae72; }
.gate-error {
    font-family: 'Share Tech Mono', monospace;
    color: #f28b8b;
    text-align: center;
    margin-top: 8px;
}

.cheat-scroll {
    max-height: 560px;
    overflow-y: auto;
    background-color: #20242c;
    border: 1px solid #2c313c;
    border-radius: 10px;
    padding: 20px 24px;
    font-family: 'Share Tech Mono', monospace;
    line-height: 1.5;
}
.cheat-scroll h4 { color: #6fdb9a; margin-top: 18px; margin-bottom: 4px; }
.cheat-scroll .footnote {
    color: #7a8290;
    font-size: 13px;
    font-style: italic;
    border-left: 2px solid #3a4150;
    padding-left: 10px;
    margin: 6px 0 10px 0;
}

.footer-card {
    margin-top: 26px;
    padding-top: 12px;
    border-top: 1px solid #2c313c;
    text-align: center;
    font-family: 'Share Tech Mono', monospace;
    color: #6a7180;
    font-size: 13px;
}
.footer-version { color: #6fdb9a; margin-bottom: 4px; }
.footer-contacts a {
    color: #8fb8e0;
    text-decoration: none;
}
.footer-contacts a:hover { color: #6fdb9a; text-shadow: 0 0 6px rgba(111,219,154,0.5); }

/* ---- лендинг "Е-Монтаж": крупный заголовок с градиентом и тенью ---- */
.brand-title {
    font-family: 'Share Tech Mono', monospace;
    font-size: 54px;
    font-weight: 700;
    letter-spacing: 2px;
    line-height: 1.15;
    background: linear-gradient(90deg, #cdd6f4 0%, #6fdb9a 55%, #8fb8e0 100%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    color: transparent;
    text-shadow: 0 0 26px rgba(111,219,154,0.22);
}
.lozung-frame {
    max-width: 680px;
    margin: 22px auto 0 auto;
    padding: 18px 26px;
    border: 1px solid #2e7d4f;
    border-radius: 12px;
    background: rgba(46, 125, 79, 0.08);
    box-shadow: 0 0 18px rgba(60, 220, 130, 0.10), inset 0 0 26px rgba(60, 220, 130, 0.04);
    font-family: 'Share Tech Mono', monospace;
    font-size: 15px;
    color: #c9d3e0;
    letter-spacing: 0.3px;
    line-height: 1.6;
}

/* большая кнопка "Начать работу" на втором экране заставки */
.st-key-splash_start button {
    font-size: 26px !important;
    padding: 22px 40px !important;
    letter-spacing: 2px !important;
    border: 1px solid #6fdb9a !important;
    background-color: #1f3a2b !important;
    color: #9be89b !important;
    box-shadow: 0 0 20px rgba(111,219,154,0.35) !important;
}
.st-key-splash_start button:hover {
    background-color: #2a4f38 !important;
    box-shadow: 0 0 28px rgba(111,219,154,0.6) !important;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def render_footer():
    st.markdown(
        f'''<div class="footer-card">
            <div class="footer-version">{APP_NAME} v{APP_VERSION}</div>
            <div class="footer-contacts">
                <a href="{TELEGRAM_URL}" target="_blank">Telegram: {TELEGRAM_HANDLE}</a>
                &nbsp;·&nbsp;
                <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>
            </div>
        </div>''',
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------
# Session state
# ------------------------------------------------------------------
DEFAULTS = {
    "screen": "splash",               # splash | cheatsheet | workspace
    "settings": None,                # заполняется load_settings() ниже
    "pairs": [],
    "print_selected": [],            # чекбоксы 🖨️ — параллельно pairs, для "Выделить всё"/"Удалить выбранные"
    "pair_cabinets": [],             # шкаф каждой бирки (для печати разделителей в ленте)
    "pairs_editor_epoch": 0,         # бампается при массовых операциях с таблицей, чтобы пересоздать data_editor
    "export_filename": "goryachie_birki",  # имя файла для скачивания — своё имя вписываешь прямо перед скачиванием
    "side1": [],
    "side2": [],
    "active_side": 1,
    "batch_mode": False,
    "save_mode": "pair",             # "single" | "pair" — переключатель "Одиночное / Пара"
    "last_touched_side": None,
    "last_unix_time": None,
    "last_empty_click_time": None,
    "last_word_click_time": None,    # время последнего клика по слову (для двойного клика)
    "last_word_click_key": None,     # позиция этого слова (для распознавания двойного)
    "batch_anchor_bit": None,        # последний бит стороны 1, от которого взят автостарт "С"
    "click_counter": 0,
    "ban_list": None,                # заполняется из settings при первой загрузке
    "last_skip_msg": None,
    "suggestion_candidates": [],
    "label_edit_mode": False,        # инлайн-редактирование окошка бирки (п.6)
    "label_edit_text": "",
    "file_id": None,
    "file_bytes": None,               # байты текущего PDF — чтобы не дёргать uploader из других блоков
    "cabinet": "",                    # метка шкафа, извлечённая из имени PDF
    "words_cache": {},
    "dims_cache": {},
    "viewport_cache": {},
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.settings is None:
    st.session_state.settings = load_settings()
if st.session_state.ban_list is None:
    st.session_state.ban_list = set(st.session_state.settings["mask"]["ignore_patterns"])


def D_IN() -> str:
    return st.session_state.settings["mask"]["inside_delimiter"] or ":"


def D_BETWEEN() -> str:
    return st.session_state.settings["mask"]["between_delimiter"] or "/"


_SEGMENTED_SUPPORTS_REQUIRED = "required" in inspect.signature(st.segmented_control).parameters


def segmented_toggle(label: str, options: list, key: str, current_value):
    """Двухпозиционный переключатель на базе st.segmented_control с
    сохранением текущего значения между рерунами. required=True (там, где
    поддерживается) не даёт кликом снять выделение и уйти в None; там, где
    параметра нет — просто держим предыдущее значение вместо диска на
    первый вариант."""
    kwargs = {"required": True} if _SEGMENTED_SUPPORTS_REQUIRED else {}
    result = st.segmented_control(
        label, options, key=key, default=current_value,
        label_visibility="collapsed", width="stretch", **kwargs,
    )
    return result if result is not None else current_value


def rerun_fast():
    """Пытаемся перерисовать только фрагмент (быстро). Streamlit разрешает
    scope='fragment' только когда текущий прогон УЖЕ сам является
    фрагмент-реруном — в остальных случаях откатываемся на обычный полный
    rerun, чтобы не упасть."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun(scope="app")


def clean_word(text: str) -> str:
    """Оставляем полезное обозначение и просеиваем служебный хвост.

    Нельзя банить всё исходное слово только потому, что рядом встречается
    ``page``/``reserve``: ``201U1page 3`` должно дать ``201U1``.
    """
    return clean_pdf_word(text)


def is_banned(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    if t in PERMANENT_BAN_WORDS:
        return True
    # page/reserve уже отсечены PAGE_SUFFIX_RE в clean_word; в бан попадает только
    # пустой остаток (page7, reserve2) и игнор-лист, а осмысленный хвост (XT, 76) — бит.
    return t in {b.lower() for b in st.session_state.ban_list}


def reset_current_label():
    st.session_state.side1 = []
    st.session_state.side2 = []
    st.session_state.active_side = 1
    st.session_state.last_touched_side = None


def smart_trim_after_save():
    """После сохранения остаётся набранным только ПЕРВЫЙ бит с каждой
    стороны (например, FTA101) — для следующей клеммы того же блока
    просто добираешь остальное кликами. Раньше стирался только последний
    бит, а не всё после первого — если на стороне случайно накапливалось
    3+ бита (глюк разбора слова на чертеже, инлайн-правка), лишний хвост
    (например ":XS1") оставался и просачивался в следующую бирку."""
    st.session_state.side1 = st.session_state.side1[:1] if st.session_state.side1 else []
    st.session_state.side2 = st.session_state.side2[:1] if st.session_state.side2 else []
    st.session_state.active_side = 1
    st.session_state.last_touched_side = None


def undo_last_bit():
    if st.session_state.active_side == 2 and st.session_state.side2:
        st.session_state.side2.pop()
        st.session_state.last_touched_side = 2 if st.session_state.side2 else 1
    elif st.session_state.active_side == 2 and not st.session_state.side2:
        st.session_state.active_side = 1
        st.session_state.last_touched_side = 1
    elif st.session_state.side1:
        st.session_state.side1.pop()
        st.session_state.last_touched_side = 1


def switch_side():
    if st.session_state.side1:
        st.session_state.active_side = 2


def save_pair() -> bool:
    s1 = D_IN().join(st.session_state.side1)
    s2 = D_IN().join(st.session_state.side2)
    if not s1 or not s2:
        return False
    st.session_state.pairs.append(f"{s1}{D_BETWEEN()}{s2}")
    st.session_state.pairs.append(f"{s2}{D_BETWEEN()}{s1}")
    st.session_state.pair_cabinets.append(st.session_state.cabinet or "")
    st.session_state.pair_cabinets.append(st.session_state.cabinet or "")
    smart_trim_after_save()
    return True


def save_single() -> bool:
    s1 = D_IN().join(st.session_state.side1)
    if not s1:
        return False
    st.session_state.pairs.append(s1)
    st.session_state.pair_cabinets.append(st.session_state.cabinet or "")
    smart_trim_after_save()
    return True


def try_auto_save():
    """Поведение двойного клика по пустому месту диктует переключатель
    Одиночное/Пара (тот же, что и на кнопке сохранения): в режиме
    "Одиночное" сохраняем только сторону 1, даже если набрана и сторона 2 —
    не блокируем пользователя, он поправит в таблице сам при необходимости;
    в режиме "Пара" нужны обе стороны."""
    if st.session_state.save_mode == "single":
        if st.session_state.side1:
            save_single()
    else:
        if st.session_state.side1 and st.session_state.side2:
            save_pair()


def add_bit(text: str):
    side = st.session_state.active_side
    if side == 1:
        st.session_state.side1.append(text)
    else:
        st.session_state.side2.append(text)
    st.session_state.last_touched_side = side
    st.session_state.click_counter += 1


def find_auto_suggestion(words, source_text: str):
    exact, partial = [], []
    needle = source_text.lower()
    for w in words:
        raw = w["text"]
        if not raw.startswith("-"):
            continue
        candidate = clean_word(raw)
        if not candidate:
            continue
        if candidate.lower() == needle:
            exact.append(candidate)
        elif needle in candidate.lower():
            partial.append(candidate)
    return list(dict.fromkeys(exact + partial))


# ------------------------------------------------------------------
# Пакетный режим (диапазон)
# ------------------------------------------------------------------
def extract_trailing_number(bit: str):
    """('XT101' -> ('XT', 101, 0, 3)), ('01' -> ('', 1, 2, 2)), ('+' -> None).
    4-й элемент кортежа — разрядность хвоста (число цифр), по которой пакетная
    генерация молча режет диапазон: 1 знак → до 9, 2 → до 99, 3 → до 999."""
    m = TRAILING_NUM_RE.match(bit)
    if not m:
        return None
    prefix, num_str = m.groups()
    pad_width = len(num_str) if num_str.startswith("0") else 0
    return prefix, int(num_str), pad_width, len(num_str)


def format_number(n: int, pad_width: int) -> str:
    s = str(n)
    if pad_width and len(s) < pad_width:
        s = s.zfill(pad_width)
    return s


def generate_batch_rows(side1, side2, start: int, end: int):
    """Возвращает (список_строк, ошибка_или_None). Меняет только последнюю
    числовую часть последнего бита с каждой стороны. Диапазон молча режется
    по разрядности хвоста (1 знак → до 9, 2 → до 99, 3 → до 999) — берётся
    минимум из ограничений двух сторон, чтобы ни одна не вышла за свои цифры."""
    if not side1 or not side2:
        return None, "Наберите обе стороны, прежде чем генерировать диапазон."
    p1 = extract_trailing_number(side1[-1])
    p2 = extract_trailing_number(side2[-1])
    if p1 is None or p2 is None:
        return None, ('Последний бит одной из сторон не заканчивается цифрой — '
                       'диапазон пока поддерживает только такой формат (например, "1", "XT101").')
    prefix1, _, pad1, digits1 = p1
    prefix2, _, pad2, digits2 = p2
    if start > end:
        start, end = end, start
    # молча режем по разрядности (граница каждой стороны — её собственные цифры)
    max_val = min(10 ** digits1 - 1, 10 ** digits2 - 1)
    start = min(start, max_val)
    end = min(end, max_val)
    if start > end:
        start, end = end, start
    if end - start + 1 > MAX_BATCH_RANGE:
        return None, f"Диапазон больше {MAX_BATCH_RANGE} — проверь, не опечатка ли это."

    rows = []
    for n in range(start, end + 1):
        b1 = side1[:-1] + [f"{prefix1}{format_number(n, pad1)}"]
        b2 = side2[:-1] + [f"{prefix2}{format_number(n, pad2)}"]
        s1 = D_IN().join(b1)
        s2 = D_IN().join(b2)
        rows.append(f"{s1}{D_BETWEEN()}{s2}")
        rows.append(f"{s2}{D_BETWEEN()}{s1}")
    return rows, None


def generate_batch_rows_single(side1, start: int, end: int):
    """Диапазон ОДИНОЧНЫХ бирок (без зеркала) — только по стороне 1.
    Меняет последнюю числовую часть последнего бита. Пример: FTA101,
    FTA102, FTA103... (просто название прибора, без пары). Диапазон молча
    режется по разрядности хвоста (1 знак → до 9, 2 → до 99, 3 → до 999)."""
    if not side1:
        return None, "Наберите сторону 1, прежде чем генерировать диапазон."
    p1 = extract_trailing_number(side1[-1])
    if p1 is None:
        return None, ('Последний бит стороны 1 не заканчивается цифрой — '
                       'диапазон пока поддерживает только такой формат (например, "1", "FTA101").')
    prefix1, _, pad1, digits1 = p1
    if start > end:
        start, end = end, start
    max_val = 10 ** digits1 - 1
    start = min(start, max_val)
    end = min(end, max_val)
    if start > end:
        start, end = end, start
    if end - start + 1 > MAX_BATCH_RANGE:
        return None, f"Диапазон больше {MAX_BATCH_RANGE} — проверь, не опечатка ли это."

    rows = []
    for n in range(start, end + 1):
        b1 = side1[:-1] + [f"{prefix1}{format_number(n, pad1)}"]
        rows.append(D_IN().join(b1))
    return rows, None


def generate_batch_rows_unified(side1, side2, start: int, end: int, save_mode: str):
    """Обёртка, которая решает пара/одиночное так же, как переключатель
    Одиночное/Пара на кнопке сохранения (п.1/2/3): в режиме "Одиночное"
    генерируем диапазон только по стороне 1 (сторона 2 игнорируется, не
    блокируем пользователя); в режиме "Пара" нужны обе стороны."""
    if save_mode == "single":
        return generate_batch_rows_single(side1, start, end)
    return generate_batch_rows(side1, side2, start, end)


# ------------------------------------------------------------------
# ZPL (Elegir RT230)
# ------------------------------------------------------------------
def build_zpl_label(text: str, printer_cfg: dict) -> str:
    dpi = printer_cfg.get("dpi", 203)
    w_mm = printer_cfg.get("label_width_mm", 40)
    h_mm = printer_cfg.get("label_height_mm", 20)
    font = printer_cfg.get("font_size", 30)
    w_dots = max(1, int(w_mm / 25.4 * dpi))
    h_dots = max(1, int(h_mm / 25.4 * dpi))
    safe_text = text.replace("^", "").replace("~", "")
    return (
        "^XA\n"
        f"^PW{w_dots}\n"
        f"^LL{h_dots}\n"
        f"^FO20,20^A0N,{font},{font}^FD{safe_text}^FS\n"
        "^XZ\n"
    )


def group_entries_by_cabinet(entries):
    """Сгруппировать (text, cabinet) по шкафу. Порядок шкафов — по первому
    появлению в списке, внутри шкафа — порядок списка (ничего не сортируем).
    Бирки с пустым шкафом идут отдельной группой ('' — без заголовка).
    Возвращает список [(cabinet, [text, ...]), ...]."""
    groups = {}
    order = []
    for text, cab in entries:
        cab = (cab or "").strip()
        if cab not in groups:
            groups[cab] = []
            order.append(cab)
        groups[cab].append(text)
    return [(cab, groups[cab]) for cab in order]


def build_zpl_batch(entries, printer_cfg: dict) -> str:
    """Лента из бирок, сгруппированная по шкафам: сначала бирка-заголовок с
    названием шкафа, потом все бирки этого шкафа, дальше следующий шкаф и его
    бирки — границу видно прямо на ленте и легко разорвать по группам."""
    parts = []
    for cab, texts in group_entries_by_cabinet(entries):
        if cab:
            parts.append(build_zpl_label(cab, printer_cfg))
        for text in texts:
            parts.append(build_zpl_label(text, printer_cfg))
    return "".join(parts)


# ------------------------------------------------------------------
# Данные PDF: свой кэш в session_state (без пере-хеширования всего файла
# на каждый клик, как это делает декоратор st.cache_data с байтами)
# ------------------------------------------------------------------
def get_file_id(file_bytes: bytes) -> str:
    return hashlib.md5(file_bytes).hexdigest()


def get_page_count(file_bytes: bytes) -> int:
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        return len(pdf.pages)


# LRU-лимиты на кэши в session_state. Раньше эти словари росли всю сессию
# без остановки (виброзов картинки чертежа — самый тяжёлый, ~850x1300px
# на каждую уникальную комбинацию зум/панорама/страница) — за много часов
# работы это реальный кандидат на утечку памяти, особенно на слабом VPS.
MAX_VIEWPORT_CACHE = 12   # картинок чертежа одновременно в памяти на сессию
MAX_WORDS_CACHE = 20      # списков слов (по страницам) на сессию
MAX_DIMS_CACHE = 20


def _cache_get(cache: dict, key):
    """LRU: если ключ есть — переносим его в конец словаря (отмечаем как
    недавно использованный), иначе None."""
    if key in cache:
        cache[key] = cache.pop(key)
        return cache[key]
    return None


def _cache_put(cache: dict, key, value, max_items: int):
    cache[key] = value
    while len(cache) > max_items:
        cache.pop(next(iter(cache)))  # вышвыриваем самый старый (LRU)


def ensure_words(file_bytes: bytes, file_id: str, page_index: int):
    key = (file_id, page_index)
    cached = _cache_get(st.session_state.words_cache, key)
    if cached is not None:
        return cached
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        words = pdf.pages[page_index].extract_words(use_text_flow=False, keep_blank_chars=False)
    _cache_put(st.session_state.words_cache, key, words, MAX_WORDS_CACHE)
    return words


def ensure_page_dims(file_id: str, page_index: int, doc):
    key = (file_id, page_index)
    cached = _cache_get(st.session_state.dims_cache, key)
    if cached is not None:
        return cached
    r = doc[page_index].rect
    dims = (r.width, r.height)
    _cache_put(st.session_state.dims_cache, key, dims, MAX_DIMS_CACHE)
    return dims


def render_viewport(file_bytes: bytes, file_id: str, page_index: int,
                     zoom: float, pan_x_pct: float, pan_y_pct: float):
    """Рендерим ТОЛЬКО видимое окно страницы через PyMuPDF (clip), а не всю
    страницу целиком — так рендер остаётся быстрым независимо от зума."""
    zoom_r = round(zoom, 2)
    key = (file_id, page_index, zoom_r, round(pan_x_pct), round(pan_y_pct))
    cached = _cache_get(st.session_state.viewport_cache, key)
    if cached is not None:
        return cached

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page = doc[page_index]
    page_w, page_h = ensure_page_dims(file_id, page_index, doc)

    view_w_pt = VIEW_W_PX / zoom
    view_h_pt = VIEW_H_PX / zoom
    max_off_x = max(0.0, page_w - view_w_pt)
    max_off_y = max(0.0, page_h - view_h_pt)
    off_x = max_off_x * (pan_x_pct / 100)
    off_y = max_off_y * (pan_y_pct / 100)

    clip = fitz.Rect(off_x, off_y, off_x + view_w_pt, off_y + view_h_pt)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=clip)
    img = Image.open(io.BytesIO(pix.tobytes("png")))

    result = (img, off_x, off_y)
    _cache_put(st.session_state.viewport_cache, key, result, MAX_VIEWPORT_CACHE)
    return result



def find_nearest_word_pt(words, pt_x, pt_y):
    if not words:
        return None, None
    best, best_dist = None, None
    for w in words:
        cx = (w["x0"] + w["x1"]) / 2
        cy = (w["top"] + w["bottom"]) / 2
        d = ((cx - pt_x) ** 2 + (cy - pt_y) ** 2) ** 0.5
        if best_dist is None or d < best_dist:
            best_dist = d
            best = w
    return best, best_dist


# ------------------------------------------------------------------
# Рендер строки бирки (с подсветкой последнего бита + мигающий курсор)
# ------------------------------------------------------------------
def render_label_html():
    def join_side(tokens, side_num):
        if not tokens:
            return ""
        highlight = st.session_state.last_touched_side == side_num
        parts = []
        for i, t in enumerate(tokens):
            if highlight and i == len(tokens) - 1:
                parts.append(f'<span class="hl">{t}</span>')
            else:
                parts.append(t)
        return D_IN().join(parts)

    s1_html = join_side(st.session_state.side1, 1)
    s2_html = join_side(st.session_state.side2, 2)

    if st.session_state.active_side == 2 or st.session_state.side2:
        content = f"{s1_html}<span style='color:#7a8290;'>{D_BETWEEN()}</span>{s2_html}"
    else:
        content = s1_html

    if not content:
        content = '<span style="color:#5b6270;">кликните по чертежу, чтобы начать набор…</span>'
    else:
        content += '<span class="cursor">_</span>'
    return content


def cabinet_from_filename(filename: str) -> str:
    """Метка шкафа из имени PDF-файла ('' — если имени нет).

    Правило: выводим '...' + хвост имени файла (там имя шкафа, впереди общее
    вступление). Если всё имя влезает в 18 символов — пишем целиком; если
    длинное — режем НАЧАЛО, оставляя последние 15, чтобы уложиться.
    Служебный суффикс '_markup' из имени отбрасываем.
    """
    if not filename:
        return ""
    stem = Path(filename).stem  # отрезаем расширение (.pdf)
    stem = re.sub(r"_markup\s*$", "", stem, flags=re.IGNORECASE).strip()
    budget = 18 - len("...")
    if len(stem) <= budget:
        return f"...{stem}"
    return f"...{stem[-budget:]}"


def build_current_label_text() -> str:
    """Текущая бирка одной строкой — то, что уходит в поле инлайн-правки."""
    s1 = D_IN().join(st.session_state.side1)
    s2 = D_IN().join(st.session_state.side2)
    if st.session_state.active_side == 2 or st.session_state.side2:
        return f"{s1}{D_BETWEEN()}{s2}"
    return s1


def apply_label_edit(text: str):
    """Разбирает вручную отредактированную строку бирки обратно в side1/
    side2, используя те же разделители маски."""
    text = text.strip()
    if not text:
        reset_current_label()
        return
    d_bt = D_BETWEEN()
    if d_bt and d_bt in text:
        left, right = text.split(d_bt, 1)
    else:
        left, right = text, ""
    d_in = D_IN()
    st.session_state.side1 = [p.strip() for p in left.split(d_in) if p.strip() != ""] if left else []
    st.session_state.side2 = [p.strip() for p in right.split(d_in) if p.strip() != ""] if right else []
    st.session_state.active_side = 2 if st.session_state.side2 else 1
    st.session_state.last_touched_side = None


# ------------------------------------------------------------------
# Шпаргалка (общий контент для отдельного экрана и вкладки в настройках)
# ------------------------------------------------------------------
def render_cheatsheet_html() -> str:
    d_in, d_bt = D_IN(), D_BETWEEN()
    html = f'''
    <div class="cheat-scroll">
        <h4>Как набирается бирка</h4>
        Клик по слову на чертеже = один "бит". Биты внутри одной стороны
        склеиваются через <b>{d_in}</b>, стороны между собой — через
        <b>{d_bt}</b>. Пример: клики FTA101 → XT1 → 1.1 дают
        <b>FTA101{d_in}XT1{d_in}1.1</b>.
        <div class="footnote">* Разделители настраиваются в панели "Настройки → Маска".</div>

        <h4>Двойной клик по слову</h4>
        Одиночный клик по слову добавляет бит в активную сторону. Двойной клик
        по слову добавляет бит один раз и сразу переключает на сторону 2 —
        вместо кнопки "➡️ К стороне 2".

        <h4>Переключатель "Одиночное / Пара"</h4>
        Решает, что именно сохраняется — кнопкой "💾 Сохранить" и двойным
        кликом по пустому месту чертежа одинаково. "Пара" — нужны обе
        стороны, сохраняется сразу пара И её зеркало (два физических конца
        кабеля). "Одиночное" — сохраняется только сторона 1, даже если
        сторона 2 тоже набрана (её просто не трогаем, ничего не блокируем).

        <h4>Умный сброс после сохранения</h4>
        После сохранения остаётся набранным только ПЕРВЫЙ бит с каждой
        стороны (например, FTA101) — для следующей клеммы того же блока
        просто добираешь остальное кликами, начало печатать заново не
        нужно.

        <h4>Пакетный режим</h4>
        Переключатель "🎯 Снайперский / 📦 Пакетный" на главной панели.
        В пакетном режиме набираешь общий "хвост" бирки один раз, задаёшь
        диапазон "с" / "по" — генерируется вся серия сразу. Поле "С"
        подставляется автоматически из числа последнего бита стороны 1
        (FTA105 → 105). Диапазон тоже слушается переключателя
        "Одиночное / Пара".
        <div class="footnote">* Меняется только последняя числовая часть последнего бита
        (FTA105 → FTA106 → FTA107…). Разрядность соблюдается: однозначное число
        растёт до 9, двузначное — до 99, трёхзначное — до 999; при выходе за
        границу диапазон молча срезается. Ведущие нули сохраняются (01 → 02 → 03…).</div>

        <h4>Автоподсказка второго конца</h4>
        Если на чертеже рядом уже есть ссылка вида "-FTA102:XT2:C page7" —
        кнопка "Найти второй конец" сама её находит и подставляет.

        <h4>Правка бирки на лету</h4>
        Кнопка "✏️ Править бирку" под текущей биркой превращает её в
        обычное текстовое поле — правишь всю строку целиком (с учётом
        разделителей маски) и жмёшь "✅ Готово".

        <h4>Игнор-лист</h4>
        Слова, которые не должны попадать в бирку (мусорный текст на
        чертеже) — настраиваются в "Настройки → Маска". Слова "page" и
        "reserve" (в любом виде, даже приклеенные к соседним символам)
        банятся всегда, номера листов (pageNN) отсекаются автоматически.

        <h4>Зум и панорама</h4>
        Три ползунка в левой колонке, вместе с остальными органами
        управления — для плотных участков схемы.

        <h4>Таблица сохранённых бирок</h4>
        Строки редактируются и удаляются прямо в таблице (двойной клик по
        ячейке "Бирка", крестик слева — удалить строку). Кнопки сверху:
        "☑️ Выделить всё" / "🗑️ Удалить выбранные" / "🗑️ Удалить всё".

        <h4>Печать</h4>
        Чекбоксы 🖨️ у каждой строки таблицы. Отмечаешь нужные →
        "Печать выбранных" → скачивается файл с командами для принтера
        {st.session_state.settings["printer"]["model"]}.
        <div class="footnote">* Печатает только выбранные строки, не всё скопом. Сам файл нужно
        передать на принтер отдельно — прямой отправки по сети пока нет.</div>
    </div>
    '''
    return "\n".join(line.strip() for line in html.strip().split("\n"))


# ------------------------------------------------------------------
# Экраны: заставка / шпаргалка
# ------------------------------------------------------------------
def render_splash():
    st.markdown(
        '''<div class="splash-wrap">
            <div class="splash-title">RAPID<span class="accent">TAG</span></div>
            <div class="splash-sub">Кликовый набор бирок кабелей из PDF-схемы</div>
            <div class="lozung-frame">наша цель создание инновационных решений по взаимодействию человека и клеммной колодки</div>
        </div>''',
        unsafe_allow_html=True,
    )
    st.write("")
    _, mid_col, _ = st.columns([1, 1.6, 1])
    with mid_col:
        if st.button("🚀 Начать работу", width="stretch", key="splash_start"):
            st.session_state.screen = "workspace"
            st.rerun()
        if st.button("📖 Шпаргалка", width="stretch", key="splash_cheatsheet"):
            st.session_state.screen = "cheatsheet"
            st.rerun()
    st.write("")
    render_footer()


def render_cheatsheet_screen():
    st.markdown('<div class="splash-title" style="font-size:34px;">Шпаргалка</div>',
                unsafe_allow_html=True)
    if st.button("🚀 Начать работу", key="cheatsheet_start_top"):
        st.session_state.screen = "workspace"
        st.rerun()
    st.write("")
    st.markdown(render_cheatsheet_html(), unsafe_allow_html=True)
    st.write("")
    if st.button("🚀 Начать работу", key="cheatsheet_start_bottom"):
        st.session_state.screen = "workspace"
        st.rerun()
    render_footer()


# ------------------------------------------------------------------
# Панель настроек (сайдбар) — видна только в рабочем режиме
# ------------------------------------------------------------------
def render_settings_sidebar():
    with st.sidebar:
        st.markdown(f"### ⚙️ {APP_NAME} — настройки")
        tab_mask, tab_printer, tab_cheat = st.tabs(["Маска", "Принтер", "Шпаргалка"])

        with tab_mask:
            new_in = st.text_input(
                "Разделитель внутри стороны",
                value=st.session_state.settings["mask"]["inside_delimiter"],
                max_chars=3, key="cfg_inside_delim",
            )
            new_between = st.text_input(
                "Разделитель между сторонами",
                value=st.session_state.settings["mask"]["between_delimiter"],
                max_chars=3, key="cfg_between_delim",
            )
            auto_on = st.checkbox(
                "Автоподсказка второго конца включена",
                value=st.session_state.settings["mask"]["auto_suggest_enabled"],
                key="cfg_auto_on",
            )
            st.caption("Игнор-лист — слова, которые не должны попадать в бирку")
            new_ig = st.text_input("Добавить в игнор-лист", key="cfg_new_ignore")
            if st.button("Добавить", key="cfg_add_ignore") and new_ig.strip():
                st.session_state.ban_list.add(new_ig.strip())
                st.rerun()
            for item in sorted(st.session_state.ban_list):
                ic1, ic2 = st.columns([4, 1])
                ic1.write(item)
                if ic2.button("✕", key=f"cfg_del_{item}"):
                    st.session_state.ban_list.discard(item)
                    st.rerun()
            st.caption('"pageNN" и ведущий "-" отсекаются автоматически всегда, без настройки.')

            if st.button("💾 Применить и сохранить", key="cfg_save_mask", width="stretch"):
                st.session_state.settings["mask"]["inside_delimiter"] = new_in or ":"
                st.session_state.settings["mask"]["between_delimiter"] = new_between or "/"
                st.session_state.settings["mask"]["auto_suggest_enabled"] = auto_on
                st.session_state.settings["mask"]["ignore_patterns"] = sorted(st.session_state.ban_list)
                save_settings(st.session_state.settings)
                st.success("Сохранено в settings.json")
                st.rerun()

        with tab_printer:
            p = st.session_state.settings["printer"]
            model = st.text_input("Модель принтера", value=p["model"], key="cfg_printer_model")
            w_mm = st.number_input("Ширина этикетки, мм", value=float(p["label_width_mm"]),
                                    min_value=5.0, max_value=200.0, key="cfg_w")
            h_mm = st.number_input("Высота этикетки, мм", value=float(p["label_height_mm"]),
                                    min_value=5.0, max_value=200.0, key="cfg_h")
            dpi = st.number_input("DPI принтера", value=int(p["dpi"]),
                                   min_value=100, max_value=600, key="cfg_dpi")
            font = st.number_input("Размер шрифта, точек", value=int(p["font_size"]),
                                    min_value=8, max_value=120, key="cfg_font")
            st.caption("Формат вывода — ZPL (файл для передачи на принтер), под "
                       "Elegir RT230. Отправка по сети пока не делается сознательно.")

            if st.button("💾 Применить и сохранить", key="cfg_save_printer", width="stretch"):
                st.session_state.settings["printer"].update({
                    "model": model, "label_width_mm": w_mm, "label_height_mm": h_mm,
                    "dpi": int(dpi), "font_size": int(font),
                })
                save_settings(st.session_state.settings)
                st.success("Сохранено в settings.json")
                st.rerun()

        with tab_cheat:
            st.markdown(render_cheatsheet_html(), unsafe_allow_html=True)



# ------------------------------------------------------------------
# Рабочий экран
# ------------------------------------------------------------------
def render_workspace():
    st.title(f"🏷️ {APP_NAME}")
    st.caption("Кликовый набор бирок кабелей из PDF-схемы")

    render_settings_sidebar()

    if st.session_state.file_id is None:
        # файла ещё нет — просто узкий загрузчик в ширину левой колонки
        # (та же пропорция 1:2.4, что и рабочая зона ниже), чтобы верх
        # экрана не "разъезжался" шире рабочей области (п.5)
        gate_col, _ = st.columns([1, 2.4])
        with gate_col:
            uploaded = st.file_uploader("Загрузи PDF схемы", type=["pdf"], key="pdf_uploader")
        if uploaded is None:
            st.info("Загрузи PDF-файл схемы, чтобы начать.")
            render_footer()
            return
        file_bytes = uploaded.getvalue()
        st.session_state.file_id = get_file_id(file_bytes)
        st.session_state.page_count = get_page_count(file_bytes)
        st.rerun(scope="app")
        return

    # ------------------------------------------------------------
    # Рабочая зона в st.fragment — обычный клик по чертежу перерисовывает
    # ТОЛЬКО этот блок, а не всё приложение целиком. Загрузчик, номер
    # страницы и "Найти второй конец" теперь тоже внутри — все живут в
    # левой узкой колонке, а правая целиком занята чертежом (п.5).
    # ------------------------------------------------------------
    @st.fragment
    def workspace_fragment():
        left_col, right_col = st.columns([1, 2.4])

        with left_col:
            panel = st.container(key="left_panel")
            with panel:
                uploaded = st.file_uploader("Загрузи PDF схемы", type=["pdf"], key="pdf_uploader")
                if uploaded is None:
                    # файл убрали кнопкой "x" — возвращаемся к узкому гейту
                    st.session_state.file_id = None
                    st.rerun(scope="app")
                    return
                file_bytes = uploaded.getvalue()
                file_id = get_file_id(file_bytes)
                if st.session_state.file_id != file_id:
                    st.session_state.file_id = file_id
                    st.session_state.page_count = get_page_count(file_bytes)
                st.session_state.file_bytes = file_bytes
                st.session_state.cabinet = cabinet_from_filename(uploaded.name)
                n_pages = st.session_state.page_count
                if st.session_state.cabinet:
                    st.caption(f"🏢 Шкаф: {st.session_state.cabinet}")

                page_num_1based = st.number_input(
                    "Страница (сторона активная сейчас)",
                    min_value=1, max_value=n_pages, value=1, step=1, key="page_input",
                )
                cross_page = st.checkbox(
                    "Сторона 2 — на другой странице",
                    value=False, key="cross_page_checkbox",
                    help="Включи, если второй конец связи находится на другом листе схемы",
                )
                page_index = page_num_1based - 1
                if cross_page and st.session_state.active_side == 2:
                    page_num_2based = st.number_input(
                        "Страница для стороны 2", min_value=1, max_value=n_pages,
                        value=page_num_1based, step=1, key="page_input_2",
                    )
                    active_page_index = page_num_2based - 1
                else:
                    active_page_index = page_index

                words = ensure_words(file_bytes, file_id, active_page_index)

                # ---------- автоподсказка второго конца (п.5: переехала
                # левее, из правой колонки) ----------
                if st.session_state.settings["mask"]["auto_suggest_enabled"]:
                    source_side_text = None
                    if st.session_state.side1 and not st.session_state.side2:
                        source_side_text = D_IN().join(st.session_state.side1)
                    elif st.session_state.side2 and not st.session_state.side1:
                        source_side_text = D_IN().join(st.session_state.side2)

                    with st.expander("🔍 Найти второй конец по подписи на чертеже"):
                        if not source_side_text:
                            st.caption("Набери одну сторону (без второй), чтобы искать подсказку.")
                        else:
                            if st.button("🔍 Искать"):
                                st.session_state.suggestion_candidates = find_auto_suggestion(words, source_side_text)
                            candidates = st.session_state.suggestion_candidates
                            if candidates:
                                chosen = st.selectbox("Найдено на странице:", candidates, key="suggestion_pick")
                                if st.button("✅ Подставить"):
                                    target_side = st.session_state.side2 if st.session_state.side1 else st.session_state.side1
                                    target_side.clear()
                                    target_side.extend(chosen.split(D_IN()))
                                    st.session_state.last_touched_side = 2 if st.session_state.side1 else 1
                                    st.session_state.suggestion_candidates = []
                                    st.rerun(scope="app")

                st.divider()

                # ---------- зум + панорама ----------
                zoom = st.slider("🔍 Зум", 0.5, 3.5, DEFAULT_ZOOM, 0.1, key="zoom_slider")
                pan_x = st.slider("↔️ Панорама (гориз.)", 0, 100, 0, 1, key="pan_x_slider")
                pan_y = st.slider("↕️ Панорама (вертик.)", 0, 100, 0, 1, key="pan_y_slider")

                st.divider()

                # ---------- строка с текущей биркой — всегда перед переключателем
                # режима, чтобы она оставалась видимой при работе с чертежом. ----------
                mode_badge = (
                    '<span class="side-tag mode-batch">📦 Пакетный режим</span>'
                    if st.session_state.batch_mode else ""
                )
                side_badge = (
                    '<span class="side-tag side-1">Сторона 1</span>'
                    if st.session_state.active_side == 1
                    else '<span class="side-tag side-2">Сторона 2</span>'
                )
                st.markdown(f'{mode_badge}{side_badge}', unsafe_allow_html=True)

                # ---------- кнопки «Отменить бит» / «Полный сброс» подняты выше (п.7),
                # чтобы не елозить вниз за биркой. ----------
                undo_col, reset_col = st.columns(2)
                with undo_col:
                    if st.button("↩️ Отменить бит", width="stretch"):
                        undo_last_bit()
                        st.rerun(scope="app")
                with reset_col:
                    if st.button("🗑️ Полный сброс", width="stretch"):
                        reset_current_label()
                        st.rerun(scope="app")

                label_wrap = st.container(key="label_edit_wrap")
                with label_wrap:
                    if not st.session_state.label_edit_mode:
                        st.markdown(f'<div class="label-box">{render_label_html()}</div>',
                                    unsafe_allow_html=True)
                        if st.button("✏️ Править бирку", key="edit_label_toggle", width="stretch"):
                            st.session_state.label_edit_text = build_current_label_text()
                            st.session_state.label_edit_mode = True
                            st.rerun(scope="app")
                    else:
                        edited_label_text = st.text_input(
                            "Правка бирки", value=st.session_state.label_edit_text,
                            key="label_edit_input", label_visibility="collapsed",
                        )
                        apply_col, cancel_col = st.columns(2)
                        with apply_col:
                            if st.button("✅ Готово", key="edit_label_apply", width="stretch"):
                                apply_label_edit(edited_label_text)
                                st.session_state.label_edit_mode = False
                                st.rerun(scope="app")
                        with cancel_col:
                            if st.button("✖️ Отмена", key="edit_label_cancel", width="stretch"):
                                st.session_state.label_edit_mode = False
                                st.rerun(scope="app")

                if st.session_state.last_skip_msg:
                    st.caption(st.session_state.last_skip_msg)
                    st.session_state.last_skip_msg = None

                if st.session_state.pairs:
                    st.caption(f"✅ Последняя сохранённая: «{st.session_state.pairs[-1]}»")

                # ---------- переключатели (п.1): режим набора и тип сохранения ----------
                mode_options = ["🎯 Снайперский", "📦 Пакетный"]
                selected_mode = segmented_toggle(
                    "Режим", mode_options, "mode_toggle",
                    mode_options[1] if st.session_state.batch_mode else mode_options[0],
                )
                st.session_state.batch_mode = (selected_mode == mode_options[1])

                save_mode_options = ["Одиночное", "Пара"]
                selected_save_mode = segmented_toggle(
                    "Что сохраняем", save_mode_options, "save_mode_toggle",
                    "Пара" if st.session_state.save_mode == "pair" else "Одиночное",
                )
                st.session_state.save_mode = "pair" if selected_save_mode == "Пара" else "single"

                if st.button("➡️ К стороне 2", width="stretch",
                              disabled=not st.session_state.side1 or st.session_state.active_side == 2):
                    switch_side()
                    st.rerun(scope="app")

                if st.session_state.batch_mode:
                    # автостарт: "С" подставляется из числа последнего бита
                    # стороны 1 (FTA105 → 105), "По" — туда же (одна бирка по умолчанию).
                    anchor = st.session_state.side1[-1] if st.session_state.side1 else None
                    if anchor is not None and anchor != st.session_state.batch_anchor_bit:
                        st.session_state.batch_anchor_bit = anchor
                        p = extract_trailing_number(anchor)
                        if p is not None:
                            st.session_state["batch_from"] = p[1]
                            st.session_state["batch_to"] = p[1]
                    elif anchor is None:
                        st.session_state.batch_anchor_bit = None
                    range_from = st.number_input("С", min_value=0, value=1, step=1, key="batch_from")
                    range_to = st.number_input("По", min_value=0, value=10, step=1, key="batch_to")
                    gen_disabled = (
                        not st.session_state.side1 if st.session_state.save_mode == "single"
                        else not (st.session_state.side1 and st.session_state.side2)
                    )
                    if st.button("🚀 Сгенерировать серию", width="stretch", disabled=gen_disabled):
                        rows, err = generate_batch_rows_unified(
                            st.session_state.side1, st.session_state.side2,
                            range_from, range_to, st.session_state.save_mode,
                        )
                        if err:
                            st.session_state.last_skip_msg = f"⚠️ {err}"
                        else:
                            st.session_state.pairs.extend(rows)
                            st.session_state.print_selected.extend([False] * len(rows))
                            st.session_state.pair_cabinets.extend(
                                [st.session_state.cabinet or ""] * len(rows)
                            )
                            smart_trim_after_save()
                        st.rerun(scope="app")
                else:
                    save_disabled = (
                        not st.session_state.side1 if st.session_state.save_mode == "single"
                        else not (st.session_state.side1 and st.session_state.side2)
                    )
                    if st.button("💾 Сохранить", width="stretch", disabled=save_disabled):
                        n_before = len(st.session_state.pairs)
                        if st.session_state.save_mode == "single":
                            save_single()
                        else:
                            save_pair()
                        n_added = len(st.session_state.pairs) - n_before
                        st.session_state.print_selected.extend([False] * n_added)
                        st.rerun(scope="app")

                save_mode_caption = "«Как одиночное»" if st.session_state.save_mode == "single" else "«Сохранить пару»"
                st.caption(f"Двойной клик по слову = переход на сторону 2. "
                           f"Двойной клик по пустому месту = {save_mode_caption} "
                           f"(зависит от переключателя «Одиночное / Пара» выше).")

        with right_col:
            right_panel = st.container(key="right_panel")
            with right_panel:
                img, off_x_pt, off_y_pt = render_viewport(
                    file_bytes, file_id, active_page_index, zoom, pan_x, pan_y
                )

                st.divider()
                st.caption("Кликни по нужному слову на чертеже ниже")

                coords = streamlit_image_coordinates(
                    img, key=f"img_{active_page_index}_{st.session_state.active_side}_{zoom}_{pan_x}_{pan_y}"
                )

                if coords is not None and coords.get("unix_time") != st.session_state.last_unix_time:
                    st.session_state.last_unix_time = coords["unix_time"]

                    pt_x = off_x_pt + coords["x"] / zoom
                    pt_y = off_y_pt + coords["y"] / zoom
                    word, dist_pt = find_nearest_word_pt(words, pt_x, pt_y)
                    is_empty_click = word is None or dist_pt is None or dist_pt > EMPTY_CLICK_DIST_PTS

                    if is_empty_click:
                        # клик по пустому месту — сбрасываем детектор двойного клика по слову
                        st.session_state.last_word_click_time = None
                        st.session_state.last_word_click_key = None
                        now = time.time()
                        last_empty = st.session_state.last_empty_click_time
                        if last_empty is not None and (now - last_empty) <= DOUBLE_CLICK_WINDOW_SEC:
                            try_auto_save()
                            st.session_state.last_empty_click_time = None
                            st.rerun(scope="app")
                        else:
                            st.session_state.last_empty_click_time = now
                    else:
                        st.session_state.last_empty_click_time = None
                        raw_text = word["text"]
                        cleaned = clean_word(raw_text)
                        if not cleaned:
                            st.session_state.last_skip_msg = f'Пропущено (ссылка на лист): "{word["text"]}"'
                            rerun_fast()
                        elif is_banned(cleaned):
                            st.session_state.last_skip_msg = f'Пропущено (игнор-лист): "{cleaned}"'
                            rerun_fast()
                        else:
                            now = time.time()
                            word_key = (
                                active_page_index,
                                round(word["x0"], 1), round(word["top"], 1),
                                round(word["x1"], 1), round(word["bottom"], 1),
                            )
                            last_key = st.session_state.last_word_click_key
                            last_t = st.session_state.last_word_click_time
                            is_double = (
                                last_key == word_key and last_t is not None
                                and (now - last_t) <= DOUBLE_CLICK_WINDOW_SEC
                            )
                            if is_double:
                                # Двойной клик по слову: бит уже добавлен первым
                                # кликом, повторно не добавляем. На стороне 1 —
                                # переключаемся на сторону 2 (вместо кнопки).
                                # На стороне 2 — просто одиночный бит.
                                st.session_state.last_word_click_time = None
                                st.session_state.last_word_click_key = None
                                if st.session_state.active_side == 1 and st.session_state.side1:
                                    switch_side()
                                    st.rerun(scope="app")
                            else:
                                add_bit(cleaned)
                                st.session_state.last_word_click_time = now
                                st.session_state.last_word_click_key = word_key
                                rerun_fast()

    workspace_fragment()

    # ---------- таблица сохранённых пар: правка/удаление строк прямо в
    # таблице (п.7) + чекбоксы 🖨️ для печати + массовые операции ----------
    st.divider()
    st.subheader("Сохранённые бирки")
    if st.session_state.pairs:
        n = len(st.session_state.pairs)
        if len(st.session_state.print_selected) != n:
            # чекбоксы рассинхронились с pairs (после ручного добавления/
            # удаления строк прямо в таблице) — просто подгоняем длину
            st.session_state.print_selected = (st.session_state.print_selected + [False] * n)[:n]
        if len(st.session_state.pair_cabinets) != n:
            st.session_state.pair_cabinets = (st.session_state.pair_cabinets + [""] * n)[:n]

        df = pd.DataFrame({
            "🖨️": st.session_state.print_selected,
            "Бирка": st.session_state.pairs,
            "Шкаф": st.session_state.pair_cabinets,
        })
        editor_key = f"pairs_editor_{st.session_state.pairs_editor_epoch}"
        edited = st.data_editor(
            df,
            column_config={
                "🖨️": st.column_config.CheckboxColumn("🖨️", width="small"),
                "Бирка": st.column_config.TextColumn("Бирка"),
                "Шкаф": st.column_config.TextColumn("Шкаф"),
            },
            hide_index=True,
            width="stretch",
            height=300,
            num_rows="fixed",  # нельзя добавлять строки бесплатно в обход счётчика
            key=editor_key,
        )
        # синхронизируем обратно в session_state: пустые строки (например,
        # недописанная новая строка после "+") выкидываем
        cleaned_pairs, cleaned_selected, cleaned_cabs = [], [], []
        for tag, checked, cab in zip(
            edited["Бирка"].fillna(""),
            edited["🖨️"].fillna(False),
            edited["Шкаф"].fillna(""),
        ):
            if tag.strip() != "":
                cleaned_pairs.append(tag)
                cleaned_selected.append(bool(checked))
                cleaned_cabs.append(str(cab).strip())
        st.session_state.pairs = cleaned_pairs
        st.session_state.print_selected = cleaned_selected
        st.session_state.pair_cabinets = cleaned_cabs
        selected = [t for t, s in zip(st.session_state.pairs, st.session_state.print_selected) if s]

        sel_col1, sel_col2, sel_col3 = st.columns(3)
        with sel_col1:
            if st.button("☑️ Выделить всё", width="stretch"):
                st.session_state.print_selected = [True] * len(st.session_state.pairs)
                st.session_state.pairs_editor_epoch += 1
                st.rerun(scope="app")
        with sel_col2:
            if st.button(f"🗑️ Удалить выбранные ({len(selected)})", width="stretch", disabled=not selected):
                keep_pairs, keep_selected, keep_cabs = [], [], []
                for tag, checked, cab in zip(
                    st.session_state.pairs,
                    st.session_state.print_selected,
                    st.session_state.pair_cabinets,
                ):
                    if not checked:
                        keep_pairs.append(tag)
                        keep_selected.append(False)
                        keep_cabs.append(cab)
                st.session_state.pairs = keep_pairs
                st.session_state.print_selected = keep_selected
                st.session_state.pair_cabinets = keep_cabs
                st.session_state.pairs_editor_epoch += 1
                st.rerun(scope="app")
        with sel_col3:
            if st.button("🗑️ Удалить всё", width="stretch"):
                st.session_state.pairs = []
                st.session_state.print_selected = []
                st.session_state.pair_cabinets = []
                st.session_state.pairs_editor_epoch += 1
                st.rerun(scope="app")

        raw_name = st.text_input(
            "Имя файла для скачивания (без расширения)",
            value=st.session_state.export_filename,
            key="export_filename",
        )
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", raw_name).strip() or "goryachie_birki"

        # Одна вертикальная структура для TXT/CSV/Excel: строка с названием
        # шкафа, потом все его бирки (порядок — как накликано).
        export_rows = []
        for cab, texts in group_entries_by_cabinet(
            zip(st.session_state.pairs, st.session_state.pair_cabinets)
        ):
            if cab:
                export_rows.append(cab)
            export_rows.extend(texts)

        col_x, col_y, col_v, col_z = st.columns(4)
        with col_x:
            st.download_button(
                "⬇️ TXT",
                data="\n".join(export_rows),
                file_name=f"{safe_name}.txt",
                mime="text/plain",
                width="stretch",
            )
        with col_y:
            st.download_button(
                "⬇️ CSV",
                data=pd.Series(export_rows).to_csv(index=False, header=False),
                file_name=f"{safe_name}.csv",
                mime="text/csv",
                width="stretch",
            )
        with col_v:
            xlsx_buf = io.BytesIO()
            pd.DataFrame({"Бирка": export_rows}).to_excel(
                xlsx_buf, index=False, engine="openpyxl"
            )
            st.download_button(
                "⬇️ Excel",
                data=xlsx_buf.getvalue(),
                file_name=f"{safe_name}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
        with col_z:
            entries = [
                (t, c)
                for t, s, c in zip(
                    st.session_state.pairs,
                    st.session_state.print_selected,
                    st.session_state.pair_cabinets,
                )
                if s
            ]
            zpl_data = build_zpl_batch(entries, st.session_state.settings["printer"]) if entries else ""
            st.download_button(
                f"🖨️ Печать выбранных ({len(selected)})",
                data=zpl_data,
                file_name=f"{safe_name}.zpl",
                mime="text/plain",
                disabled=not selected,
                width="stretch",
                help=f'ZPL-файл под {st.session_state.settings["printer"]["model"]}',
            )
    else:
        st.caption("Пока пусто — набери и сохрани первую пару.")

    render_footer()


# ------------------------------------------------------------------
# Роутинг экранов
# ------------------------------------------------------------------
if st.session_state.screen == "splash":
    render_splash()
elif st.session_state.screen == "cheatsheet":
    render_cheatsheet_screen()
else:
    render_workspace()
