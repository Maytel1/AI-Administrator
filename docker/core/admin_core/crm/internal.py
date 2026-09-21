"""InternalCRM — наш внутренний календарь (SALON_WITHOUT_CRM.md, сценарий A).

Полностью самодостаточный адаптер: слоты нарезаются из working_hours мастеров,
накладки исключаются exclusion-констрейнтом bookings (ошибка 23P01 →
SlotTakenError). Каждый значимый шаг пишется в audit_log.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg

from .base import CRMProvider, SlotTakenError, audit


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

