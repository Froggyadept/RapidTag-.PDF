"""Нормализация и фильтрация текста, извлечённого из чертежа RapidTag."""

import re


PERMANENT_BAN_WORDS = frozenset({"page", "reserve"})

# Служебные подписи в PDF встречаются как отдельными словами, так и
# приклеенными к обозначению: ``208u2 page7`` и ``208u2page7``. Маркер нужно
# удалять из строки, а не использовать как повод выбросить всю строку: перед
# ``page``/``reserve`` может находиться полезное обозначение прибора или
# клеммы.
_PAGE_MARKER_RE = re.compile(
    r"page(?:\s*(?:\d+|n+))?(?=$|[^\w])",
    re.IGNORECASE,
)
_RESERVE_MARKER_RE = re.compile(
    r"reserve(?:\s*(?:\d+|n+))?(?=$|[^\w])",
    re.IGNORECASE,
)


def contains_permanent_ban_marker(text: str) -> str | None:
    """Возвращает найденный служебный маркер для диагностики или ``None``.

    ``page`` допускается с номером или буквенным placeholder-ом ``NN``:
    ``page``, ``page7``, ``page 7``, ``pageNN``. Поиск намеренно работает и
    внутри строки, чтобы обнаружить служебный фрагмент в ``208u2page7`` или
    ``208u2 reserve``. Саму строку эта функция не должна отбрасывать: для
    фильтрации используется ``clean_word``.
    """
    normalized = " ".join(str(text).replace("\u00a0", " ").split())
    if _PAGE_MARKER_RE.search(normalized):
        return "page"
    if _RESERVE_MARKER_RE.search(normalized):
        return "reserve"
    return None


def is_permanently_banned(text: str) -> bool:
    """Совместимый диагностический ответ о наличии служебного маркера.

    При обработке клика результат не используется как бан всей строки.
    """
    return contains_permanent_ban_marker(text) is not None


def clean_word(text: str) -> str:
    """Возвращает полезную часть слова после просеивания служебного хвоста.

    ``page`` и ``reserve`` являются служебными маркерами, а не причиной
    отбрасывать всю строку. Поэтому ``201U1page 3`` превращается в ``201U1``,
    ``XT21:1 page reserve`` — в ``XT21:1``, а чистое ``page 3`` — в пустую
    строку и уже затем может быть пропущено вызывающим кодом.
    """
    value = str(text).replace("\u00a0", " ").lstrip("-").strip()
    return re.sub(
        r"(?:\s*(?:page|reserve)\s*(?:\d+|n+)?\s*)+$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()