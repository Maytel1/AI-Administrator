"""Вынос резервных копий из папки сервера в объектное хранилище.

Зачем: копии на том же диске, что и сервер, — это не резерв. Погибнет диск
(пожар, кража, битая файловая система) — пропадут и копии, и восстанавливаться
будет нечем.

Схема работы:

    backup_db.ps1 (локальный дамп) → upload_backup.py (S3) → проверка

Проверка после загрузки обязательна: «файл загрузился» не значит «файл
читается». Скрипт сверяет размер и заголовок объекта: если это не дамп
PostgreSQL, копия в облаке бесполезна, а обнаружится это в момент аварии.

Хранилище любое S3-совместимое: Yandex Object Storage, Selectel, Timeweb.
Настройки в окружении (или в docker/.env):

    S3_ENDPOINT      https://storage.yandexcloud.net
    S3_BUCKET        backups-aiadmin
    S3_REGION        ru-central1
    S3_ACCESS_KEY    ...
    S3_SECRET_KEY    ...

Код возврата: 0 — загружено и проверено, 1 — ошибка, 2 — не настроено.
Не настроенное хранилище не считается ошибкой: скрипт честно об этом
говорит и не притворяется, что копии сохраняются в двух местах.

    python scripts/upload_backup.py                # свежий дамп
    python scripts/upload_backup.py --dry-run      # только показать план
    python scripts/upload_backup.py --file путь    # конкретный файл
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_CONFIGURED = 2

# Байты, с которых начинается поток custom-формата PostgreSQL: "PGDMP".
# Если после загрузки их нет — перед нами не тот файл, и восстановить его нельзя.
PGDMP_MAGIC = b"PGDMP"
REQUIRED_KEYS = ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")


def load_settings() -> dict[str, str]:
    """Настройки хранилища из окружения."""
    return {key: os.getenv(key, "") for key in REQUIRED_KEYS}


def missing(settings: dict[str, str]) -> list[str]:
    """Какие из обязательных настроек не заданы.

    Сверяем с REQUIRED_KEYS, а не с ключами словаря: пустой словарь дал бы
    «всё настроено» и упал бы позже на KeyError вместо понятного отказа.
    """
    return [key for key in REQUIRED_KEYS if not settings.get(key)]


def newest_dump(backup_dir: str) -> str:
    """Самый свежий локальный дамп: тот, что только что сделал backup_db.ps1."""
    candidates = glob.glob(str(Path(backup_dir) / "aiadmin-*.dump"))
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def _remote_size(head: object) -> int:
    """Размер объекта из ответа boto3.

    В boto3 v3 ``Object.head()`` отдаёт словарь с ключом ``ContentLength``,
    а в v2 — объект с полем ``content_length``. Поддерживаем оба: иначе
    проверка копии падала бы на AttributeError ровно после аварии.
    """
    if isinstance(head, dict):
        return int(head.get("ContentLength") or 0)
    return int(getattr(head, "content_length", 0) or 0)


def verify_remotely(client, bucket: str, key: str, local_size: int) -> str:
    """Скачать начало объекта и убедиться, что это дамп PostgreSQL.

    Возвращает пустую строку при успехе, иначе — причину.
    """
    obj = client.Object(bucket, key)
    remote_size = _remote_size(obj.head())
    if remote_size != local_size:
        return f"размер не совпал: локально {local_size}, в хранилище {remote_size}"

    # get() без Range отдаёт весь файл: берём только начало.
    response = obj.get(Range="bytes=0-1023")
    body = response["Body"].read()
    if not body.startswith(PGDMP_MAGIC):
        return f"начало файла не начинается с магии {PGDMP_MAGIC!r}: {body[:8]!r}"
    return ""


def upload(path: str, settings: dict[str, str], *, dry_run: bool = False) -> int:
    """Залить дамп в хранилище и проверить, что он читается."""
    missing_keys = missing(settings)
    if missing_keys:
        print("хранилище не настроено, копия осталась только на сервере:")
        for key in missing_keys:
            print(f"  не задано: {key}")
        print("  задайте их в docker/.env — см. .env.example")
        return EXIT_NOT_CONFIGURED

    if not os.path.isfile(path):
        print(f"ОШИБКА: нет файла {path}")
        return EXIT_FAILED

    size = os.path.getsize(path)
    if size < 1024:
        print(f"ОШИБКА: дамп подозрительно мал ({size} байт) — выгружать нечего")
        return EXIT_FAILED

    key = (
        f"{os.getenv('S3_PREFIX') or 'db'}/"
        f"{datetime.now(timezone.utc):%Y/%m/%d}/{Path(path).name}"
    )
    print(f"файл:  {path} ({size} байт)")
    print(f"куда:  s3://{settings['S3_BUCKET']}/{key}")

    if dry_run:
        print("режим --dry-run: загрузка не выполнялась")
        return EXIT_OK

    try:
        import boto3  # noqa: PLC0415 — тяжёлая зависимость, нужна только тут
    except ImportError:
        print("ОШИБКА: нет boto3 — pip install boto3")
        return EXIT_FAILED

    client = boto3.client(
        "s3",
        endpoint_url=settings["S3_ENDPOINT"],
        aws_access_key_id=settings["S3_ACCESS_KEY"],
        aws_secret_access_key=settings["S3_SECRET_KEY"],
        region_name=os.getenv("S3_REGION") or None,
    )
    try:
        client.upload_file(path, settings["S3_BUCKET"], key)
    except Exception as exc:  # noqa: BLE001 — причина в тексте exc, тип не важен
        print(f"ОШИБКА: загрузка не удалась: {type(exc).__name__}: {exc}")
        return EXIT_FAILED

    problem = verify_remotely(client, settings["S3_BUCKET"], key, size)
    if problem:
        # Копия в облаке есть, но ею нельзя восстановиться — это хуже, чем
        # отсутствия копии: о ней будут думать, что она годная.
        print(f"ОШИБКА: копия загружена, но непригодна: {problem}")
        return EXIT_FAILED

    print("OK: загружено и проверено — размер совпал, заголовок PGDMP на месте")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description="Вынос бэкапа в объектное хранилище")
    default_dir = str(Path(__file__).resolve().parents[1] / "backups")
    parser.add_argument("--file", default="", help="какой дамп грузить (по умолчанию свежий)")
    parser.add_argument("--backup-dir", default=default_dir, help="папка с локальными дампами")
    parser.add_argument("--dry-run", action="store_true", help="показать план и выйти")
    args = parser.parse_args()

    target = args.file or newest_dump(args.backup_dir)
    if not target:
        print(f"ОШИБКА: в {args.backup_dir} нет ни одного aiadmin-*.dump")
        print("  сначала: powershell -ExecutionPolicy Bypass -File scripts/backup_db.ps1")
        return EXIT_FAILED

    code = upload(target, load_settings(), dry_run=args.dry_run)
    if code == EXIT_OK and not args.dry_run:
        print("\nТеперь копия в двух местах: на сервере и в хранилище.")
    return code


if __name__ == "__main__":
    sys.exit(main())
