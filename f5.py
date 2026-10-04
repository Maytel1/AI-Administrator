import pathlib

p = pathlib.Path(r"docker/core/tests/test_web_panel.py")
t = p.read_text(encoding="utf-8")
OLD = '''def test_status_buttons_hidden_without_permission():
    """Кнопок статуса нет ни без csrf, ни у роли без права."""
    assert "Подтвердить" not in page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], None)
    guest = PageCtx(csrf="g" * 32, role="guest")
    assert "Подтвердить" not in page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], guest)
    owner_html = page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], OWNER_CTX)
    assert "Подтвердить" in owner_html and "Отмена" in owner_html
    staff_html = page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], STAFF_CTX)
    assert "Подтвердить" in staff_html  # у администратора это как раз его рутина'''
NEW = '''def test_action_menu_hidden_without_permission():
    """Меню действий есть только у роли с правом и при реальном csrf."""
    def html(ctx):
        return page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], ctx)

    # Без csrf и у гостя кнопок нет: пункт «Действия» без права — это
    # приглашение нажать, после которого ничего не происходит.
    assert "menu-pop" not in html(None)
    assert "menu-pop" not in html(PageCtx(csrf="g" * 32, role="guest"))
    owner_html = html(OWNER_CTX)
    assert "menu-pop" in owner_html
    assert "Выполнена" in owner_html and "Отменена" in owner_html
    # Перенос открывает второе мини-окно: дата и время, а не кнопка «куда?».
    assert "booking_reschedule" in owner_html and "new_start" in owner_html
    staff_html = html(STAFF_CTX)
    assert "menu-pop" in staff_html  # у администратора это как раз его рутина


def test_closed_booking_offers_no_actions():
    """Выполненная и отменённая запись закрыта: меню действий не предлагается."""
    for closed in ("completed", "cancelled"):
        row = {**BOOKING, "status": closed}
        html = page_bookings([row], TZ, "", SALON, [SALON], OWNER_CTX)
        assert "menu-pop" not in html, closed
        assert "закрыта" in html, closed


def test_period_picker_shows_chosen_window():
    """Подпись периода соответствует выбранному окну, а не всегда «Месяц»."""
    week = page_bookings([dict(BOOKING)], TZ, "", SALON, [SALON], OWNER_CTX, days=7)
    assert "Период: Неделя" in week
    assert "Период: Месяц" not in week
    # Ссылка сохраняет выбранный статус: иначе смена периода сбрасывала фильтр.
    assert "days=90&amp;status=confirmed" in page_bookings(
        [dict(BOOKING)], TZ, "confirmed", SALON, [SALON], OWNER_CTX
    )'''
assert OLD in t, "anchor missing"
p.write_text(t.replace(OLD, NEW), encoding="utf-8")
import ast; ast.parse(t)
print("tests updated")
