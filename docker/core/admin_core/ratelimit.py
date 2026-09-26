"""Rate-limit и базовый антиспам для входящих сообщений (фаза 2).

Скользящее окно на ключ (chat_id): не более ``limit`` сообщений за ``window``
секунд (по умолчанию 6 за 10 c — настраивается TG_RATE_LIMIT / TG_RATE_WINDOW).
Сверх лимита — причина ``rate``, счётчик ``rate_limited_total{reason}`` в
метриках и разовое предупреждение на окно (``warn=True``), дальше сообщения
молча глотаются.

Дополнительные защиты, не зависящие от времени:
  * один и тот же текст подряд («залипание»/спам-повтор) — после
    ``DUPES_ALLOWED`` одинаковых подряд отклоняем с причиной ``duplicate``;
  * слишком длинное сообщение (``MAX_TEXT_LEN``) — просим сократить.

Часы внедряются (``clock=...``) — юнит-тесты бегут без sleep. Словари растут
по числу активных чатов (единицы-тысячи) — приемлемо для фазы 2; чистка
истории — при переносе состояния в БД (фаза 4, мультиарендность).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

DEFAULT_LIMIT = 6
DEFAULT_WINDOW = 10.0
DUPES_ALLOWED = 2
MAX_TEXT_LEN = 4000  # лимит Telegram на одно сообщение

OK = "ok"
RATE = "rate"
DUPLICATE = "duplicate"
TOO_LONG = "too_long"


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = OK
    warn: bool = False  # True → можно отправить разовое «не спамьте»
    remaining: int = 0


class RateLimiter:
    """Пер-ключевой лимитер сообщений (ключ — chat_id строкой)."""

    def __init__(
        self,
        limit: int = DEFAULT_LIMIT,
        window: float = DEFAULT_WINDOW,
        *,
        clock=time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, list[float]] = {}
        self._same: dict[str, tuple[str, int]] = {}
        self._last_warn: dict[str, float] = {}
        self.rejected_total = 0

    def check(self, key: str, text: str) -> Verdict:
        now = self._clock()

        if len(text) > MAX_TEXT_LEN:
            self.rejected_total += 1
            return Verdict(False, TOO_LONG)

        prev_text, count = self._same.get(key, ("", 0))
        count = count + 1 if text == prev_text else 1
        self._same[key] = (text, count)
        if count > DUPES_ALLOWED:
            self.rejected_total += 1
            return Verdict(False, DUPLICATE)

        hits = [t for t in self._hits.get(key, ()) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            self.rejected_total += 1
            # warn один раз на окно: первый отказ в окне или после паузы
            warn = now - self._last_warn.get(key, -self.window) >= self.window
            if warn:
                self._last_warn[key] = now
            return Verdict(False, RATE, warn=warn)

        hits.append(now)
        self._hits[key] = hits
        return Verdict(True, remaining=self.limit - len(hits))
