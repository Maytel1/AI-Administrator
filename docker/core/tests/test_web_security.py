"""Тесты HTTPS/HSTS и управления учётными записями из панели."""

from admin_core.web.security import (
    CSP,
    HSTS_MAX_AGE,
    cookie_secure_flag,
    https_redirect_target,
    is_secure_request,
    security_headers,
)


class _Req:
    """Минимальный запрос: secure=True — как за TLS, иначе обычный http."""

    def __init__(self, *, secure: bool = False, headers: dict | None = None,
                 host: str = "panel.example.ru", path: str = "/today"):
        self.secure = secure
        self.headers = headers or {}
        self.host = host
        self.raw_path = path


# ═══ HTTPS И HSTS ═══════════════════════════════════════════════════════


def test_security_headers_always_present():
    headers = security_headers(secure=False)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Content-Security-Policy"] == CSP
    assert headers["Cache-Control"] == "no-store"  # панель показывает ПДн


def test_hsts_only_over_https():
    assert "Strict-Transport-Security" not in security_headers(secure=False)
    hsts = security_headers(secure=True)["Strict-Transport-Security"]
    assert f"max-age={HSTS_MAX_AGE}" in hsts
    assert "includeSubDomains" in hsts


def test_csp_has_no_external_sources():
    """Внешних скриптов и шрифтов быть не должно — панель работает в контуре."""
    assert "cdn" not in CSP.lower()
    assert "http://" not in CSP and "https://" not in CSP
    assert "frame-ancestors 'none'" in CSP  # защита от кликджекинга
    assert "object-src 'none'" in CSP


def test_is_secure_request_respects_proxy_header():
    # без TLS и без доверия прокси заголовок игнорируется
    spoofed = _Req(headers={"X-Forwarded-Proto": "https"})
    assert is_secure_request(spoofed, tls_enabled=False, trust_proxy=False) is False
    assert is_secure_request(spoofed, tls_enabled=False, trust_proxy=True) is True
    direct = _Req(secure=True)
    assert is_secure_request(direct, tls_enabled=True, trust_proxy=False) is True
    plain = _Req(secure=False)
    assert is_secure_request(plain, tls_enabled=True, trust_proxy=False) is False


def test_http_redirects_to_https_when_tls_enabled():
    target = https_redirect_target(_Req(secure=False, path="/services"), tls_enabled=True)
    assert target == "https://panel.example.ru/services"
    # уже по https — редиректа нет, и выключенном TLS тоже
    assert https_redirect_target(_Req(secure=True), tls_enabled=True) is None
    assert https_redirect_target(_Req(secure=False), tls_enabled=False) is None


def test_cookie_secure_flag_follows_transport():
    """Secure у cookie под http поставил бы браузер в тупик — флаг только под HTTPS."""
    assert cookie_secure_flag(secure=True) is True
    assert cookie_secure_flag(secure=False) is False


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
