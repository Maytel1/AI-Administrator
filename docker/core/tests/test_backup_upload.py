"""Вынос бэкапа в объектное хранилище: проверки без сети.

Сеть здесь не нужна и была бы вредна: тест, который ходит в реальное
хранилище, зависит от ключей и падает не по делу. Проверяем решения —
выбор файла, ключ в бакете, отказ при неполных настройках, и главное
проверку содержимого копии.
"""

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest

_REPO_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "upload_backup.py"
# В образе скрипты лежат в /app/scripts, в репозитории — рядом с core.
_SCRIPT = next(
    (path for path in (_REPO_SCRIPT, Path("/app/scripts/upload_backup.py")) if path.is_file()),
    _REPO_SCRIPT,
)
_spec = importlib.util.spec_from_file_location("upload_backup", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _dump(path: Path, size: int = 2048) -> Path:
    path.write_bytes(mod.PGDMP_MAGIC + b"x" * size)
    return path


def test_reports_not_configured_instead_of_pretending(tmp_path) -> None:
    """Без ключей скрипт честно говорит об этом, а не делает вид, что копия есть.

    Молчаливый «успех» здесь опаснее отсутствия выноса: о копиях в облаке
    будут думать, что они есть, ровно тогда, когда они не понадобятся.
    """
    dump = _dump(tmp_path / "aiadmin-20260101-0000.dump")
    assert mod.upload(str(dump), {}) == mod.EXIT_NOT_CONFIGURED


def test_reports_partial_settings_instead_of_pretending(tmp_path) -> None:
    """Частично заданные настройки — тоже «не настроено», а не «получилось»."""
    dump = _dump(tmp_path / "aiadmin-20260101-0000.dump")
    partial = _settings(S3_SECRET_KEY="")
    assert mod.upload(str(dump), partial) == mod.EXIT_NOT_CONFIGURED


def test_rejects_tiny_dump(tmp_path) -> None:
    """Дамп меньше килобайта — почти наверняка пустая база или оборванная выгрузка."""
    dump = _dump(tmp_path / "aiadmin-20260101-0000.dump", size=10)
    assert mod.upload(str(dump), _settings()) == mod.EXIT_FAILED


def test_rejects_missing_file() -> None:
    assert mod.upload("нет-такого.dump", _settings()) == mod.EXIT_FAILED


def test_dry_run_does_not_touch_network(tmp_path, fake_boto3) -> None:
    """--dry-run не должен даже пытаться подключаться."""
    fake_boto3(None)  # модуль есть, но любой вызов client() упал бы
    dump = _dump(tmp_path / "aiadmin-20260101-0000.dump")
    assert mod.upload(str(dump), _settings(), dry_run=True) == mod.EXIT_OK


def test_key_includes_date_and_prefix(tmp_path, fake_boto3, monkeypatch) -> None:
    """Ключ сортирует копии по датам: год/месяц/день, префикс из настроек."""
    sink: dict = {}
    fake_boto3(_client(2048 + 5, mod.PGDMP_MAGIC, sink))
    monkeypatch.setenv("S3_PREFIX", "my-prefix")
    dump = _dump(tmp_path / "aiadmin-20260101-0000.dump", size=2048)
    assert mod.upload(str(dump), _settings()) == mod.EXIT_OK
    assert sink["key"].startswith("my-prefix/")
    assert sink["key"].endswith("/aiadmin-20260101-0000.dump")


def test_upload_verifies_readability(tmp_path, fake_boto3) -> None:
    """Копия без заголовка PGDMP — ошибка, а не «загрузилось и ладно»."""
    fake_boto3(_client(4096, b"not a dump at all"))
    dump = _dump(tmp_path / "aiadmin-20260101-0000.dump", size=4096)
    assert mod.upload(str(dump), _settings()) == mod.EXIT_FAILED


def _settings(**overrides: str) -> dict[str, str]:
    """Полный набор настроек; overrides — для проверки частичных."""
    base = {
        "S3_ENDPOINT": "https://example.storage.yandexcloud.net",
        "S3_BUCKET": "backups-aiadmin",
        "S3_ACCESS_KEY": "key",
        "S3_SECRET_KEY": "secret",
    }
    base.update(overrides)
    return base


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeObject:
    def __init__(self, head_size: int, head_bytes: bytes) -> None:
        self._head_size = head_size
        self._head_bytes = head_bytes

    def head(self):
        # boto3 v3 отдаёт словарь — тест повторяет именно его форму.
        return {"ContentLength": self._head_size}

    def get(self, Range=None):  # noqa: N803 — так в boto3
        return {"Body": _FakeBody(self._head_bytes)}


def _client(head_size: int, head_bytes: bytes, sink: dict | None = None):
    """Клиент-boto3: отдаёт заранее заданный объект и пишет ключ в sink."""

    class _Client:
        def Object(self, _bucket, _key):  # noqa: N802 — так в boto3
            return _FakeObject(head_size, head_bytes)

        def upload_file(self, _path, _bucket, key):
            if sink is not None:
                sink["key"] = key

    return _Client()


@pytest.fixture()
def fake_boto3(monkeypatch):
    """Подменяем boto3: сеть в тестах не нужна и была бы вредна."""
    import sys
    from types import SimpleNamespace

    holder = {"client": None}

    def _install(client):
        holder["client"] = client
        module = SimpleNamespace(client=lambda *_a, **_k: client)
        monkeypatch.setitem(sys.modules, "boto3", module)
        return module

    return _install


def test_verify_passes_for_real_dump_header() -> None:
    """Настоящий заголовок PGDMP и совпавший размер — копия годна."""
    client = _client(4096, mod.PGDMP_MAGIC + b"\x01\x02")
    assert mod.verify_remotely(client, "b", "k", 4096) == ""


def test_verify_rejects_size_mismatch() -> None:
    """Обрезанная копия размером не совпадёт — это ловим до аварии."""
    problem = mod.verify_remotely(_client(100, mod.PGDMP_MAGIC), "b", "k", 4096)
    assert "размер" in problem


def test_verify_rejects_foreign_content() -> None:
    """Объект без заголовка PGDMP — не дамп: восстановить его нельзя."""
    problem = mod.verify_remotely(_client(4096, b"just a text file"), "b", "k", 4096)
    assert "PGDMP" in problem


def test_newest_dump_picks_latest(tmp_path) -> None:
    """Берётся самый свежий дамп: старые копии выгружать бессмысленно."""
    import time

    old = _dump(tmp_path / "aiadmin-20250101-0000.dump")
    new = _dump(tmp_path / "aiadmin-20260101-0000.dump")
    os.utime(old, (1000, 1000))
    os.utime(new, (time.time(), time.time()))
    assert mod.newest_dump(str(tmp_path)) == str(new)


def test_newest_dump_returns_empty_without_dumps(tmp_path) -> None:
    assert mod.newest_dump(str(tmp_path)) == ""
