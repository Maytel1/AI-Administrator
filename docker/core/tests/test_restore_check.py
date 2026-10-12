"""Сверка восстановленной БД: проверки без docker и без сети.

Проверяем решения — как именно сверяются счётчики таблиц и что считается
расхождением. Логика (mismatch/report) намеренно чистые функции, поэтому тест
не требует ни БД, ни docker: он ловит регрессию ровно там, где «успешное»
восстановление могло бы оказаться пустым или неполным.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore_check.py"
_spec = importlib.util.spec_from_file_location("restore_check", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_matching_counts_have_no_problems() -> None:
    """Совпавшие счётчики — расхождений нет."""
    prod = {"salons": 137, "clients": 40, "bookings": 12}
    assert mod.mismatch(prod, dict(prod)) == []


def test_count_difference_is_a_problem() -> None:
    """Недостача строк в восстановленной таблице — расхождение с числами."""
    prod = {"salons": 137}
    restored = {"salons": 10}
    problems = mod.mismatch(prod, restored)
    assert len(problems) == 1
    assert "salons" in problems[0]
    assert "137" in problems[0] and "10" in problems[0]


def test_missing_table_is_a_problem() -> None:
    """Таблицы нет в восстановленной БД (None) — это расхождение, а не «ноль»."""
    problems = mod.mismatch({"clients": 5}, {"clients": None})
    assert len(problems) == 1
    assert "отсутствует" in problems[0]


def test_extra_table_in_restore_is_ignored() -> None:
    """Лишняя таблица в копии не ошибка: сверяем только то, что есть в рабочей."""
    prod = {"salons": 3}
    restored = {"salons": 3, "junk": 999}
    assert mod.mismatch(prod, restored) == []


def test_report_ok_when_counts_match(capsys) -> None:
    """Совпадение и непустая база — код 0."""
    prod = {"salons": 2, "clients": 5}
    assert mod.report(prod, dict(prod)) == mod.EXIT_OK


def test_report_fails_on_empty_source(capsys) -> None:
    """Пустая рабочая база — сверять нечего, бэкап бесполезен."""
    assert mod.report({"salons": 0, "clients": 0}, {"salons": 0}) == mod.EXIT_MISMATCH


def test_report_fails_on_mismatch(capsys) -> None:
    """Любое расхождение счётчиков — код 1, даже если база непустая."""
    prod = {"salons": 5, "clients": 1}
    restored = {"salons": 5, "clients": 0}
    assert mod.report(prod, restored) == mod.EXIT_MISMATCH


def test_report_fails_when_table_missing(capsys) -> None:
    """Отсутствие ключевой таблицы в копии — провал, даже при совпадении прочих."""
    prod = {"salons": 5, "bookings": 3}
    restored = {"salons": 5, "bookings": None}
    assert mod.report(prod, restored) == mod.EXIT_MISMATCH
