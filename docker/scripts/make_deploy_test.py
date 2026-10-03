"""Собирает копию deploy.ps1 с принудительным провалом health-проверки.

Нужна, чтобы проверить автооткат вживую: без подмены невозможно увидеть, что
скрипт действительно возвращает прошлые образы, а не просто падает с ошибкой.
"""

import pathlib

SRC = pathlib.Path("docker/scripts/deploy.ps1")
DST = pathlib.Path("docker/scripts/deploy_test.ps1")
MARKER = "function Test-Health {"
INJECT = (
    MARKER + "\n" +
    '    if ($env:DEPLOY_TEST_FAIL) '
    '{ Write-Host "[deploy] TEST: считаем выкатку проваленной"; return $false }'
)


def main() -> int:
    source = SRC.read_text(encoding="utf-8")
    if MARKER not in source:
        print("не найдена функция Test-Health — скрипт изменился")
        return 1
    DST.write_text(source.replace(MARKER, INJECT, 1), encoding="utf-8")
    print("тестовая копия готова:", DST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
