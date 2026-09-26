"""Тесты rate-limiter: скользящее окно, дубли, длинный текст, warn."""

from admin_core.ratelimit import (
    DUPES_ALLOWED,
    DUPLICATE,
    MAX_TEXT_LEN,
    RATE,
    TOO_LONG,
    RateLimiter,
)


class Clock:
    """Управляемые часы: t двигаем сами, без sleep."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_passes_up_to_limit_then_rate_warn_once():
    clock = Clock()
    rl = RateLimiter(limit=3, window=10, clock=clock)
    for i in range(3):
        v = rl.check("1", f"msg {i}")
        assert v.allowed, f"msg {i} должна проходить"
    v = rl.check("1", "msg 4")
    assert not v.allowed
    assert v.reason == RATE
    assert v.warn  # первое отклонение в окне — просим не спамить
    v = rl.check("1", "msg 5")
    assert not v.allowed
    assert not v.warn  # дальше молча глотаем
    assert rl.rejected_total == 2


def test_window_slides():
    clock = Clock()
    rl = RateLimiter(limit=2, window=10, clock=clock)
    assert rl.check("1", "a").allowed
    assert rl.check("1", "b").allowed
    assert not rl.check("1", "c").allowed
    clock.t += 11  # окно истекло
    assert rl.check("1", "d").allowed


def test_duplicate_spam_rejected():
    rl = RateLimiter(clock=Clock())
    for _ in range(DUPES_ALLOWED):
        assert rl.check("1", "same").allowed
    v = rl.check("1", "same")  # третий одинаковый подряд
    assert not v.allowed
    assert v.reason == DUPLICATE


def test_too_long_rejected():
    rl = RateLimiter(clock=Clock())
    v = rl.check("1", "x" * (MAX_TEXT_LEN + 1))
    assert not v.allowed
    assert v.reason == TOO_LONG


def test_keys_are_isolated():
    clock = Clock()
    rl = RateLimiter(limit=1, window=10, clock=clock)
    assert rl.check("a", "one").allowed
    assert rl.check("b", "two").allowed  # другой chat_id — своя корзина
    assert not rl.check("a", "three").allowed
