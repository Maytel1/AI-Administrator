"""InternalCRM — наш внутренний календарь (SALON_WITHOUT_CRM.md, сценарий A).

Полностью самодостаточный адаптер: слоты нарезаются из working_hours мастеров,
накладки исключаются exclusion-констрейнтом bookings (ошибка 23P01 →
SlotTakenError). Каждый значимый шаг пишется в audit_log.
"""

import json
import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from .base import CRMProvider, SlotTakenError


def parse_hhmm(text: str) -> tuple[int, int]:
    m = re.search(r"(\d{1,2}):(\d{2})", str(text))  # только HH:MM — «20.09» не время
    if not m:
        raise ValueError(f"не могу разобрать время: {text!r}")
    return int(m.group(1)) % 24, int(m.group(2)) % 60


class InternalCRM(CRMProvider):
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def _salon_tz(self, salon_id: str) -> str:
        return await self.pool.fetchval("SELECT timezone FROM salons WHERE id = $1", salon_id)

    async def _audit(self, salon_id, actor, action, details) -> None:
        await self.pool.execute(
            "INSERT INTO audit_log (salon_id, actor, action, details) VALUES ($1,$2,$3,$4)",
            salon_id, actor, action, json.dumps(details, ensure_ascii=False),
        )

    # ── клиенты ──────────────────────────────────────────────────────────
    async def get_client_by_phone(self, salon_id: str, phone: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM clients WHERE salon_id = $1 AND phone = $2", salon_id, phone
        )
        return dict(row) if row else None

    async def ensure_client(self, salon_id: str, phone: str, name: str | None = None) -> dict:
        row = await self.get_client_by_phone(salon_id, phone)
        if row:
            return row
        row = await self.pool.fetchrow(
            "INSERT INTO clients (salon_id, phone, name) VALUES ($1, $2, $3) RETURNING *",
            salon_id, phone, name,
        )
        await self._audit(salon_id, "system", "create_client", {"phone": phone})
        return dict(row)

    # ── справочники ──────────────────────────────────────────────────────
    async def get_services(self, salon_id: str) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT id, title, duration_min, price FROM services "
            "WHERE salon_id = $1 AND is_active ORDER BY title",
            salon_id,
        )
        return [dict(r) for r in rows]

    async def get_masters(self, salon_id: str) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT id, name FROM masters WHERE salon_id = $1 AND is_active ORDER BY name",
            salon_id,
        )
        return [dict(r) for r in rows]

    # ── слоты ────────────────────────────────────────────────────────────
    async def get_free_slots(
        self, salon_id: str, service_title: str, days_ahead: int = 2
    ) -> list[dict]:
        svc = await self.pool.fetchrow(
            "SELECT id, title, duration_min FROM services WHERE salon_id = $1 "
            "AND title ILIKE $2 AND is_active LIMIT 1",
            salon_id, f"%{service_title}%",
        )
        if not svc:
            return []
        tz = ZoneInfo(await self._salon_tz(salon_id))
        now = datetime.now(tz)
        slots: list[dict] = []
        for day in range(days_ahead):
            date = (now + timedelta(days=day)).date()
            weekday = date.weekday()  # 0=Пн
            whs = await self.pool.fetch(
                "SELECT m.id AS master_id, m.name AS master_name, wh.start_time, wh.end_time "
                "FROM working_hours wh JOIN masters m ON m.id = wh.master_id "
                "JOIN master_services ms ON ms.master_id = m.id "
                "JOIN services s ON s.id = ms.service_id "
                "WHERE m.salon_id = $1 AND s.id = $2 AND wh.weekday = $3 AND m.is_active",
                salon_id, svc["id"], weekday,
            )
            if not whs:
                continue
            day_start = datetime.combine(date, datetime.min.time(), tz)
            day_end = day_start + timedelta(days=1)
            busy = await self.pool.fetch(
                "SELECT master_id, starts_at, ends_at FROM bookings "
                "WHERE master_id = ANY($1::uuid[]) AND status IN ('booked','confirmed') "
                "AND starts_at < $2 AND ends_at > $3",
                [w["master_id"] for w in whs], day_end, day_start,
            )
            for wh in whs:
                t = datetime.combine(date, wh["start_time"], tz)
                end_day = datetime.combine(date, wh["end_time"], tz)
                while (
                    t + timedelta(minutes=svc["duration_min"]) <= end_day
                    and len(slots) < 12
                ):
                    free = t >= now + timedelta(minutes=10) and not any(
                        b["master_id"] == wh["master_id"]
                        and b["starts_at"] < t + timedelta(minutes=svc["duration_min"])
                        and b["ends_at"] > t
                        for b in busy
                    )
                    if free:
                        slots.append(
                            {
                                "master_id": str(wh["master_id"]),
                                "master_name": wh["master_name"],
                                "service_title": svc["title"],
                                "start_iso": t.isoformat(),
                                "start_text": t.strftime("%d.%m %H:%M"),
                            }
                        )
                    t += timedelta(minutes=30)
            if len(slots) >= 12:
                break
        return slots

    # ── записи ───────────────────────────────────────────────────────────
    async def create_booking(
        self, salon_id, client_id, service_title, time_text, offered=None, created_by="bot"
    ) -> dict:
        svc = await self.pool.fetchrow(
            "SELECT id, title, duration_min FROM services WHERE salon_id = $1 "
            "AND title ILIKE $2 AND is_active LIMIT 1",
            salon_id, f"%{service_title}%",
        )
        if not svc:
            return {"ok": False, "error": "service_not_found", "service_title": service_title}

        hh, mm = parse_hhmm(time_text)
        needle = f"{hh:02d}:{mm:02d}"
        target = next(
            (s for s in (offered or []) if needle in s["start_text"]), None
        )
        if target is None:
            slots = await self.get_free_slots(salon_id, service_title)
            target = next((s for s in slots if needle in s["start_text"]), None)
        if target is None:
            return {"ok": False, "error": "slot_taken", "attempted": time_text}

        starts_at = datetime.fromisoformat(target["start_iso"])
        ends_at = starts_at + timedelta(minutes=svc["duration_min"])
        client = await self.pool.fetchrow(
            "SELECT requires_prepayment FROM clients WHERE id = $1", client_id
        )
        prepay = bool(client and client["requires_prepayment"])
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        "INSERT INTO bookings (salon_id, client_id, master_id, service_id, "
                        "starts_at, ends_at, prepayment_status, source, created_by) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,'bot',$8) RETURNING id",
                        salon_id, client_id, target["master_id"], svc["id"],
                        starts_at, ends_at,
                        "offered" if prepay else "not_required", created_by,
                    )
                    await conn.execute(
                        "INSERT INTO audit_log (salon_id, actor, action, details) "
                        "VALUES ($1, $2, 'create_booking', $3)",
                        salon_id, created_by,
                        json.dumps({"booking_id": str(row["id"]),
                                    "service": svc["title"],
                                    "when": target["start_text"]}, ensure_ascii=False),
                    )
        except asyncpg.exceptions.ExclusionViolationError as exc:
            # exclusion-констрейнт бросает собственный класс (не UniqueViolation)
            raise SlotTakenError() from exc
        except asyncpg.exceptions.UniqueViolationError as exc:
            if "bookings_master_id_tstzrange_excl" in str(exc):
                raise SlotTakenError() from exc
            raise
        return {
            "ok": True,
            "booking_id": str(row["id"]),
            "service_title": svc["title"],
            "when": target["start_text"],
            "master_name": target["master_name"],
            "prepayment_required": prepay,
        }

    async def cancel_my_last_booking(self, salon_id: str, client_id: str) -> dict:
        row = await self.pool.fetchrow(
            "UPDATE bookings SET status = 'cancelled' WHERE id = ("
            "  SELECT id FROM bookings WHERE salon_id = $1 AND client_id = $2 "
            "  AND status IN ('booked','confirmed') ORDER BY starts_at LIMIT 1"
            ") RETURNING id",
            salon_id, client_id,
        )
        if not row:
            return {"ok": False, "error": "not_found"}
        await self._audit(salon_id, "bot", "cancel_booking", {"booking_id": str(row["id"])})
        return {"ok": True, "booking_id": str(row["id"])}

    async def get_client_profile(self, salon_id: str, client_id: str) -> dict:
        row = await self.pool.fetchrow(
            "SELECT name, phone, notes, preferred_master_id, requires_prepayment "
            "FROM clients WHERE id = $1 AND salon_id = $2",
            client_id, salon_id,
        )
        if not row:
            return {}
        visits = await self.pool.fetchval(
            "SELECT count(*) FROM bookings WHERE client_id = $1 AND status = 'completed'",
            client_id,
        )
        upcoming = await self.pool.fetch(
            "SELECT starts_at FROM bookings WHERE client_id = $1 "
            "AND status IN ('booked','confirmed') ORDER BY starts_at LIMIT 1",
            client_id,
        )
        return {
            **dict(row),
            "completed_visits": visits,
            "upcoming_iso": upcoming[0]["starts_at"].isoformat() if upcoming else None,
        }
# ═══ ПРАВКИ ИЗ ПАНЕЛИ (фаза 4) ═══════════════════════════════════════════
    # Владелец точки правит расписание, цены и статусы из личного кабинета.
    # Каждое действие проверяет принадлежность салону (изоляция по tenant_id)
    # и пишется в audit_log: по журналу потом разбирают споры (LIABILITY_OPTIONS).

    BOOKING_STATUSES = ("booked", "confirmed", "completed", "cancelled", "no_show")

    async def set_booking_status(
        self, salon_id: str, booking_id: str, status: str, actor: str = "owner:panel"
    ) -> dict:
        """Смена статуса записи: подтвердить / выполнить / отменить / не пришёл.

        Статусы 'booked'/'confirmed' держат слот в exclusion-констрейнте,
        остальные его освобождают — поэтому отмена сразу открывает время
        другим клиентам.
        """
        if status not in self.BOOKING_STATUSES:
            return {"ok": False, "error": "bad_status"}
        row = await self.pool.fetchrow(
            "UPDATE bookings SET status = $3 "
            "WHERE id = $1 AND salon_id = $2 "
            "RETURNING id, starts_at, service_id, client_id, salon_id",
            booking_id, salon_id, status,
        )
        if not row:
            return {"ok": False, "error": "not_found"}
        await self._audit(
            salon_id, actor, "set_booking_status",
            {"booking_id": str(booking_id), "status": status},
        )
        result: dict[str, Any] = {"ok": True, "status": status}
        if status in ("cancelled", "no_show"):
            # Слот освободился — отдаём его клиенту из листа ожидания.
            # Если желающих нет, поле просто остаётся не заполненным: клиент сам
            # увидит время в слотах при следующем обращении к боту.
            from ..waitlist import take_waitlist_offer  # noqa: PLC0415

            tz_name = await self.pool.fetchval(
                "SELECT timezone FROM salons WHERE id = $1", salon_id
            )
            from zoneinfo import ZoneInfo  # noqa: PLC0415

            offer = await take_waitlist_offer(
                self.pool, dict(row), tz=ZoneInfo(tz_name or "Asia/Yekaterinburg"),
                salon_name=await self.pool.fetchval(
                    "SELECT name FROM salons WHERE id = $1", salon_id
                ),
            )
            result["waitlist_offer"] = offer
        return result

    async def update_service(
        self, salon_id: str, service_id: str, *, price: Any, duration_min: int,
        is_active: bool, actor: str = "owner:panel",
    ) -> dict:
        """Правка услуги: цена, длительность, активность — то, что бот озвучивает."""
        row = await self.pool.fetchrow(
            "SELECT title FROM services WHERE id = $1 AND salon_id = $2", service_id, salon_id
        )
        if not row:
            return {"ok": False, "error": "not_found"}
        await self.pool.execute(
            "UPDATE services SET price = $3, duration_min = $4, is_active = $5 "
            "WHERE id = $1 AND salon_id = $2",
            service_id, salon_id, price, duration_min, is_active,
        )
        await self._audit(
            salon_id, actor, "update_service",
            {"service": row["title"], "price": str(price), "duration_min": duration_min,
             "is_active": is_active},
        )
        return {"ok": True, "title": row["title"]}

    async def set_client_flags(
        self, salon_id: str, client_id: str, *, requires_prepayment: bool | None = None,
        notes: str | None = None, actor: str = "owner:panel",
    ) -> dict:
        """Флаг предоплаты и заметки клиента (аллергии, пожелания — идут в контекст бота)."""
        row = await self.pool.fetchrow(
            "SELECT phone FROM clients WHERE id = $1 AND salon_id = $2", client_id, salon_id
        )
        if not row:
            return {"ok": False, "error": "not_found"}
        sets: list[str] = []
        args: list[Any] = [client_id, salon_id]
        if requires_prepayment is not None:
            args.append(requires_prepayment)
            sets.append(f"requires_prepayment = ${len(args)}")
        if notes is not None:
            args.append(notes[:500])
            sets.append(f"notes = ${len(args)}")
        if not sets:
            return {"ok": False, "error": "nothing_to_update"}
        await self.pool.execute(
            f"UPDATE clients SET {', '.join(sets)} WHERE id = $1 AND salon_id = $2", *args
        )
        await self._audit(
            salon_id, actor, "set_client_flags",
            {"phone": row["phone"], "requires_prepayment": requires_prepayment,
             "notes": (notes or "")[:200]},
        )
        return {"ok": True, "phone": row["phone"]}

    async def set_working_hours(
        self, salon_id: str, master_id: str, weekday: int, *, start: time | None = None,
        end: time | None = None, enabled: bool = True, actor: str = "owner:panel",
    ) -> dict:
        """Рабочий день мастера: график, из которого нарезаются слоты для записей."""
        if not 0 <= weekday <= 6:
            return {"ok": False, "error": "bad_weekday"}
        row = await self.pool.fetchval(
            "SELECT name FROM masters WHERE id = $1 AND salon_id = $2", master_id, salon_id
        )
        if not row:
            return {"ok": False, "error": "not_found"}
        if enabled and start and end:
            await self.pool.execute(
                "INSERT INTO working_hours (master_id, weekday, start_time, end_time) "
                "VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (master_id, weekday) DO UPDATE SET start_time = $3, end_time = $4",
                master_id, weekday, start, end,
            )
        else:
            await self.pool.execute(
                "DELETE FROM working_hours WHERE master_id = $1 AND weekday = $2",
                master_id, weekday,
            )
        await self._audit(
            salon_id, actor, "set_working_hours",
            {"master": row, "weekday": weekday, "enabled": enabled,
             "window": f"{start or '—'}–{end or '—'}"},
        )
        return {"ok": True, "master": row, "weekday": weekday}

    async def count_bookings_outside_hours(
        self, salon_id: str, master_id: str, weekday: int, start: time, end: time,
        days_ahead: int = 14,
    ) -> int:
        """Сколько будущих записей мастера в этот день выпадут из нового окна."""
        return await self.pool.fetchval(
            """
            SELECT count(*) FROM bookings b
            WHERE b.salon_id = $1 AND b.master_id = $2
              AND b.status IN ('booked','confirmed')
              AND b.starts_at >= now() AND b.starts_at < now() + ($3 || ' days')::interval
              AND EXTRACT(ISODOW FROM b.starts_at AT TIME ZONE 'Asia/Yekaterinburg')::int - 1 = $4
              AND (b.starts_at::time < $5::time OR b.ends_at::time > $6::time)
            """,
            salon_id, master_id, str(days_ahead), weekday, start, end,
        )
# ═══ СПРАВОЧНИКИ ИЗ ПАНЕЛИ (фаза 4) ═══════════════════════════════════
    # Владелец точки заводит услуги и мастеров прямо в панели; удалять нельзя —
    # на них ссылаются прошлые записи, поэтому только архивация (is_active).

    async def create_service(
        self, salon_id: str, *, title: str, duration_min: int, price: float,
        actor: str = "owner:panel",
    ) -> dict:
        """Новая услуга точки: сразу попадает в прайс, который слышит бот."""
        title = (title or "").strip()
        if not title:
            return {"ok": False, "error": "bad_title",
                    "message": "Введите название услуги"}
        # Дубль проверяем раньше лимита тарифа: сообщение должно быть о настоящей
        # причине, а не о первой попавшейся проверке.
        if await self.pool.fetchval(
            "SELECT 1 FROM services WHERE salon_id = $1 AND title = $2", salon_id, title
        ):
            return {"ok": False, "error": "exists",
                    "message": f"Услуга «{title}» уже есть"}
        from ..plans import check_limit  # noqa: PLC0415 — лимиты тарифа

        salon = await self.pool.fetchrow(
            "SELECT id, plan, timezone FROM salons WHERE id = $1", salon_id
        )
        limit = await check_limit(self.pool, dict(salon or {}), "services")
        if not limit["ok"]:
            return limit
        try:
            row = await self.pool.fetchrow(
                "INSERT INTO services (salon_id, title, duration_min, price) "
                "VALUES ($1,$2,$3,$4) RETURNING id, title",
                salon_id, title[:120], duration_min, price,
            )
        except Exception as exc:  # noqa: BLE001 — уникальность (salon_id, title)
            if "unique" in str(exc).lower():
                return {"ok": False, "error": "exists",
                        "message": f"Услуга «{title}» уже есть"}
            raise
        await self._audit(
            salon_id, actor, "create_service",
            {"title": title, "duration_min": duration_min, "price": str(price)},
        )
        return {"ok": True, "id": str(row["id"]), "title": row["title"]}

    async def set_service_active(
        self, salon_id: str, service_id: str, *, active: bool, actor: str = "owner:panel"
    ) -> dict:
        """Архивация услуги: исчезает из прайса бота, но остаётся в истории записей."""
        row = await self.pool.fetchrow(
            "UPDATE services SET is_active = $3 WHERE id = $1 AND salon_id = $2 "
            "RETURNING title",
            service_id, salon_id, active,
        )
        if not row:
            return {"ok": False, "error": "not_found"}
        await self._audit(
            salon_id, actor, "archive_service" if not active else "restore_service",
            {"service": row["title"], "is_active": active},
        )
        return {"ok": True, "title": row["title"], "is_active": active}

    async def create_master(
        self, salon_id: str, *, name: str, actor: str = "owner:panel"
    ) -> dict:
        """Новый мастер точки; рабочие дни заполняются отдельно (set_working_hours)."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "bad_name", "message": "Введите имя мастера"}
        # Сначала дубль, потом лимит тарифа: иначе владелец триала получил бы
        # «превышен лимит» вместо «такой мастер уже есть» — это вводит в заблуждение.
        if await self.pool.fetchval(
            "SELECT 1 FROM masters WHERE salon_id = $1 AND name = $2", salon_id, name
        ):
            return {"ok": False, "error": "exists",
                    "message": f"Мастер «{name}» уже есть"}
        from ..plans import check_limit  # noqa: PLC0415 — лимиты тарифа

        salon = await self.pool.fetchrow(
            "SELECT id, plan, timezone FROM salons WHERE id = $1", salon_id
        )
        limit = await check_limit(self.pool, dict(salon or {}), "masters")
        if not limit["ok"]:
            return limit
        try:
            row = await self.pool.fetchrow(
                "INSERT INTO masters (salon_id, name) VALUES ($1,$2) RETURNING id, name",
                salon_id, name[:120],
            )
        except Exception as exc:  # noqa: BLE001 — уникальность (salon_id, name)
            if "unique" in str(exc).lower():
                return {"ok": False, "error": "exists",
                        "message": f"Мастер «{name}» уже есть"}
            raise
        await self._audit(salon_id, actor, "create_master", {"master": name})
        return {"ok": True, "id": str(row["id"]), "name": row["name"]}

    async def set_master_active(
        self, salon_id: str, master_id: str, *, active: bool, actor: str = "owner:panel"
    ) -> dict:
        """Архивация мастера. Сначала проверяем будущие записи — они останутся без мастера."""
        if not active:
            upcoming = await self.pool.fetchval(
                "SELECT count(*) FROM bookings WHERE salon_id = $1 AND master_id = $2 "
                "AND status IN ('booked','confirmed') AND starts_at >= now()",
                salon_id, master_id,
            )
            if upcoming:
                return {
                    "ok": False, "error": "has_bookings",
                    "message": f"У мастера {upcoming} будущих записей — сначала перенесите их",
                }
        row = await self.pool.fetchrow(
            "UPDATE masters SET is_active = $3 WHERE id = $1 AND salon_id = $2 RETURNING name",
            master_id, salon_id, active,
        )
        if not row:
            return {"ok": False, "error": "not_found"}
        await self._audit(
            salon_id, actor, "archive_master" if not active else "restore_master",
            {"master": row["name"], "is_active": active},
        )
        return {"ok": True, "name": row["name"], "is_active": active}

    async def create_client_manual(
        self, salon_id: str, *, phone: str, name: str | None = None,
        notes: str | None = None, requires_prepayment: bool = False,
        actor: str = "owner:panel",
    ) -> dict:
        """Ручной ввод клиента: пришёл в салон или позвонил, пока бота не было.

        Тот же телефон, что у бота, — значит карточка одна: «свои» не заводятся дважды.
        """
        from ..plans import check_limit  # noqa: PLC0415 — лимиты тарифа

        existing = await self.pool.fetchval(
            "SELECT 1 FROM clients WHERE salon_id = $1 AND phone = $2",
            salon_id, phone,
        )
        if not existing:  # лимит считаем только для новых карточек
            salon = await self.pool.fetchrow(
                "SELECT id, plan, timezone FROM salons WHERE id = $1", salon_id
            )
            limit = await check_limit(self.pool, dict(salon or {}), "clients")
            if not limit["ok"]:
                return limit
        client = await self.ensure_client(salon_id, phone, name)
        if notes or requires_prepayment:
            await self.set_client_flags(
                salon_id, str(client["id"]),
                requires_prepayment=requires_prepayment or None,
                notes=notes, actor=actor,
            )
        return {"ok": True, "id": str(client["id"]), "phone": client["phone"],
                "name": client.get("name")}
