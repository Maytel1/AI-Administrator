# 🌐 vibe-audit livecheck: http://localhost:8083/health

**5 находок — 🔴 0 / 🟡 2 / 🔵 3**

_Только внешние GET-запросы. Это не пентест — внутреннюю логику так не проверить._

## 🟡 Нет Content-Security-Policy
- URL: http://localhost:8083/health
- Как чинить: Защита от XSS/инъекций. Добавь CSP-заголовок.

## 🟡 Нет HSTS
- URL: http://localhost:8083/health
- Как чинить: Strict-Transport-Security заставляет браузер всегда ходить по HTTPS.

## 🔵 Нет X-Frame-Options
- URL: http://localhost:8083/health
- Как чинить: Защита от кликджекинга (встраивание твоего сайта в чужой iframe).

## 🔵 Нет X-Content-Type-Options
- URL: http://localhost:8083/health
- Как чинить: nosniff мешает браузеру угадывать MIME-типы.

## 🔵 Заголовок server раскрывает стек: Python/3.12 aiohttp/3.14.5
- URL: http://localhost:8083/health
- Как чинить: Скрой версию сервера/фреймворка — она подсказывает известные уязвимости.
