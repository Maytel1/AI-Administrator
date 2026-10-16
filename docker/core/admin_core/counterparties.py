"""Реестр контрагентов: подключённые клиенты, которым выставляют документы.

Вместо карточки плательщика «на салон» (``client_legal``) — отдельный
справочник на всю команду. Его данные питают счёт/акт и списки выбора: в
документах участвуют только контрагенты отсюда, поэтому реквизиты вводятся
один раз и не дублируются.

Валидация и набор полей по типу (самозанятый / ИП / ООО) переиспользуются из
``billing_legal``: проверка контрольных сумм ИНН/ОГРН/ОГРНИП нужна и здесь —
ошибка в цифре отвергается банком, и платёж теряется.
"""

from __future__ import annotations

import json
from typing import Any

from admin_core.billing_legal import (
    CLIENT_TYPES,
    digits,
)
from admin_core.billing_legal import (
    validate as validate_legal,
)


def parse_row(row: Any) -> dict[str, Any]:
    """Строка БД → словарь с разобранным jsonb ``details``."""
    data = dict(row)
    if isinstance(data.get("details"), str):
        try:
            data["details"] = json.loads(data["details"])
        except json.JSONDecodeError:
            data["details"] = {}
    if not isinstance(data.get("details"), dict):
        data["details"] = {}
    return data


async def list_all(pool: Any, *, active_only: bool = False) -> list[dict[str, Any]]:
    """Все контрагенты (по алфавиту). ``active_only`` — только для списков выбора."""
    query = (
        "SELECT id, title, client_type, legal_name, inn, details, contract_no, "
        "is_active, created_at, updated_at FROM counterparties"
    )
    if active_only:
        query += " WHERE is_active"
    query += " ORDER BY lower(title), title"
    rows = await pool.fetch(query)
    return [parse_row(r) for r in rows]


async def get(pool: Any, counterparty_id: str) -> dict[str, Any] | None:
    """Один контрагент по id (или None)."""
    row = await pool.fetchrow(
        "SELECT id, title, client_type, legal_name, inn, details, contract_no, "
        "is_active, created_at, updated_at FROM counterparties WHERE id = $1",
        counterparty_id,
    )
    return parse_row(row) if row else None


async def active_options(pool: Any) -> list[dict[str, Any]]:
    """Короткий список активных контрагентов для выпадающих списков документов."""
    rows = await pool.fetch(
        "SELECT id, title, legal_name, inn FROM counterparties "
        "WHERE is_active ORDER BY lower(title), title"
    )
    return [dict(r) for r in rows]


def validate(client_type: str, title: str, legal_name: str, inn: str,
             details: dict[str, Any]) -> list[str]:
    """Собрать все ошибки сразу. Тот же набор полей/проверок, что у плательщика."""
    errors: list[str] = []
    if client_type not in CLIENT_TYPES:
        errors.append("неизвестный тип контрагента")
    if not str(title or "").strip():
        errors.append("заполните «Название»")
    # Валидатор карточки плательщика принимает legal_name и inn — переиспользуем.
    errors.extend(validate_legal(client_type, legal_name, inn, details))
    return errors


async def save(
    pool: Any,
    *,
    counterparty_id: str | None = None,
    title: str,
    client_type: str,
    legal_name: str,
    inn: str,
    details: dict[str, Any],
    contract_no: str = "",
) -> dict[str, Any]:
    """Создать или обновить контрагента. Правки полей — по тому же принципу,
    что и карточка плательщика: тип определяет набор полей, сервер проверяет
    только их."""
    title = str(title or "").strip()
    errors = validate(client_type, title, legal_name, inn, details)
    if errors:
        return {"ok": False, "errors": errors}
    clean_contract = str(contract_no or "").strip() or None
    if counterparty_id:
        row = await pool.fetchrow(
            "UPDATE counterparties SET title = $2, client_type = $3, "
            "legal_name = $4, inn = $5, details = $6::jsonb, contract_no = $7, "
            "updated_at = now() WHERE id = $1 "
            "RETURNING id, title",
            counterparty_id, title, client_type, str(legal_name).strip(),
            digits(inn), json.dumps(details, ensure_ascii=False), clean_contract,
        )
        if not row:
            return {"ok": False, "errors": ["контрагент не найден"]}
        saved_id = str(row["id"])
    else:
        row = await pool.fetchrow(
            "INSERT INTO counterparties (title, client_type, legal_name, inn, "
            "details, contract_no) VALUES ($1, $2, $3, $4, $5::jsonb, $6) "
            "RETURNING id, title",
            title, client_type, str(legal_name).strip(), digits(inn),
            json.dumps(details, ensure_ascii=False), clean_contract,
        )
        saved_id = str(row["id"])
    await pool.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES "
        "('team:counterparties', 'counterparty_saved', $1)",
        json.dumps({"id": saved_id, "title": title, "inn": digits(inn)},
                   ensure_ascii=False),
    )
    return {"ok": True, "errors": [], "id": saved_id, "title": title}


async def set_active(pool: Any, counterparty_id: str, active: bool) -> dict[str, Any]:
    """Включить/архивировать контрагента. Архив — не удаление: документы,
    выставленные ранее, остаются валидными."""
    row = await pool.fetchrow(
        "UPDATE counterparties SET is_active = $2, updated_at = now() "
        "WHERE id = $1 RETURNING id, title",
        counterparty_id, bool(active),
    )
    if not row:
        return {"ok": False, "error": "not_found"}
    await pool.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES "
        "('team:counterparties', 'counterparty_archived', $1)",
        json.dumps({"id": counterparty_id, "active": bool(active)},
                   ensure_ascii=False),
    )
    return {"ok": True, "title": str(row["title"])}
