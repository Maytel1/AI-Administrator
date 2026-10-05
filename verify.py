"""Проверка проекта одной командой — ровно то, что делает CI.

Зачем: CI был красным на пяти коммитах подряд, и никто этого не заметил.
Причина не в самих проверках, а в том, что локально они запускались иначе:
в контейнере `services/web` и каталог миграций есть, в репозитории — нет,
а `build_html_docs.py` подставлял сегодняшнюю дату. Один скрипт убирает
этот разрыв: что он проверяет, то же проверяет и GitHub Actions.

    python verify.py            # линтеры + документы + тесты
    python verify.py --fast     # без тестов: только линтеры и документы
    python verify.py --ci       # плюс статус последнего прогона GitHub Actions

Каждая проверка печатает «OK»/«СБОЙ» и, если упала, — последние строки вывода:
больше двух экранов лога не помогает, мешает найти причину.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCKER_DIR = os.path.join(ROOT, "docker")

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"


@dataclass
class Step:
    """Одна проверка: название, команда и признак того, что нужен контейнер."""

    name: str
    cmd: list[str]
    cwd: str = ROOT
    docker: bool = False
    hint: str = ""


def extract_summary(output: str) -> str:
    """Короткая сводка из вывода: «313 passed», «All checks passed»."""
    patterns = (
        r"\d+ passed[^\n]*",
        r"\d+ failed[^\n]*",
        r"Success: [^\n]+",
        r"All checks passed",
        r"Found \d+ error[^\n]*",
        r"RESULT: \w+",
    )
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(0).strip()
    return ""


def tail_lines(output: str, limit: int = 12) -> list[str]:
    """Последние значащие строки вывода — причину обычно видно в конце."""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return lines[-limit:]


def run(step: Step, verbose: bool) -> tuple[bool, str]:
    """Выполнить шаг и вернуть (успех, вывод)."""
    if step.docker and not shutil.which("docker"):
        return False, "docker не найден: запустите Docker Desktop или используйте --fast"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            step.cmd,
            cwd=step.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return False, "шаг не уложился в 30 минут"
    took = time.monotonic() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    if verbose:
        print(f"{DIM}{output}{RESET}")
    print(f"    {DIM}{took:.1f} c{RESET}")
    return proc.returncode == 0, output
def report(steps: list[Step], verbose: bool) -> int:
    """Прогнать шаги по очереди, остановиться на первом падении."""
    for index, step in enumerate(steps, 1):
        print(f"{BOLD}[{index}/{len(steps)}] {step.name}{RESET}")
        ok, output = run(step, verbose)
        if ok:
            summary = extract_summary(output)
            suffix = f" {DIM}{summary}{RESET}" if summary else ""
            print(f"  {GREEN}OK{RESET}{suffix}")
            continue
        print(f"  {RED}СБОЙ{RESET}")
        for line in tail_lines(output):
            print(f"    {RED}{line}{RESET}")
        if step.hint:
            print(f"  {YELLOW}подсказка:{RESET} {step.hint}")
        print(f"\n{RED}Остановились на первом падении — дальше идти смысла нет.{RESET}")
        return 1
    return 0


def promtool_command(mount: str, *args: str) -> list[str]:
    """Команда promtool в одноразовом контейнере.

    Отдельный контейнер нужен, чтобы не тащить prom/prometheus к себе на
    машину: образ весит сотни мегабайт и ради проверки конфигов не нужен.
    """
    return [
        "docker", "run", "--rm",
        "--entrypoint", "promtool",
        "-v", f"{DOCKER_DIR}{os.sep}monitoring:/etc/prometheus",
        "prom/prometheus:latest",
        *args,
    ]


def steps_for(fast: bool) -> list[Step]:
    """Шаги в том же порядке, что в .github/workflows/ci.yml."""
    steps = [
        Step("ruff: стиль и ошибки", [sys.executable, "-m", "ruff", "check", "docker"]),
        Step(
            "mypy: типы ядра admin_core",
            [sys.executable, "-m", "mypy", "docker/core/admin_core", "--ignore-missing-imports"],
        ),
        Step(
            "compileall: синтаксис всего кода",
            [sys.executable, "-m", "compileall", "-q", "docker/core", "docker/services"],
        ),
        Step(
            "документы: сборка HTML",
            [sys.executable, "build_html_docs.py"],
            hint="После правки .md запустите build_html_docs.py и закоммитьте HTML вместе с ним.",
        ),
        Step("документы: валидация", [sys.executable, "validate_plan_html.py"]),
        Step(
            "документы: HTML совпадает с репозиторием",
            ["git", "diff", "--exit-code", "--", "*.html"],
            hint="Этот шаг падал в CI из-за текущей даты в HTML; теперь дата берётся из .md.",
        ),
    ]
    if fast:
        return steps
    # Правила алертов: без этой проверки молчащее правило выглядит как рабочее,
    # а ошибочное выражение не срабатывает ровно тогда, когда всё сломалось.
    steps.append(
        Step(
            "алерты: promtool check config",
            promtool_command("", "check", "config", "/etc/prometheus/prometheus.yml"),
            cwd=DOCKER_DIR,
            docker=True,
        )
    )
    steps.append(
        Step(
            "алерты: правила срабатывают (unit-тесты)",
            promtool_command("", "test", "rules", "/etc/prometheus/alerts_test.yml"),
            cwd=DOCKER_DIR,
            docker=True,
            hint=(
                "Правило, которое не срабатывает, хуже ненаписанного: "
                "оно молчит при аварии. Проверьте выражение и for: в alerts.yml."
            ),
        )
    )
    # Образ cli копирует тесты и код внутрь себя при сборке. Без пересборки
    # pytest запускает ПРОШЛУЮ версию: правки тестов молча не попадают в
    # прогон, и зелёный результат ничего не значит (ловилось на практике).
    steps.append(
        Step(
            "docker: пересборка образа cli",
            ["docker", "compose", "build", "cli"],
            cwd=DOCKER_DIR,
            docker=True,
            hint="Без этого тесты гоняются на устаревшем коде из образа",
        )
    )
    steps.append(
        Step(
            "pytest: тесты с настоящей БД",
            [
                "docker", "compose", "run", "--rm", "-T", "cli",
                "python", "-m", "pytest", "tests", "-q",
            ],
            cwd=DOCKER_DIR,
            docker=True,
            hint="Нужен запущенный postgres: docker compose up -d postgres",
        )
    )
    return steps
def ci_status() -> int:
    """Показать последний прогон GitHub Actions — «зелёное ли у мастера»."""
    if not shutil.which("gh"):
        print(f"{YELLOW}gh не найден — статус CI не проверить (поставьте GitHub CLI).{RESET}")
        return 0
    try:
        proc = subprocess.run(
            ["gh", "run", "list", "--limit", "1", "--json", "conclusion,status,headSha"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if proc.returncode != 0:
            print(f"{YELLOW}gh не смог получить статус: {proc.stderr.strip()[:120]}{RESET}")
            return 0
        runs = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"{YELLOW}статус CI недоступен: {type(exc).__name__}{RESET}")
        return 0
    if not runs:
        print(f"{YELLOW}прогонов CI пока нет{RESET}")
        return 0
    latest = runs[0]
    conclusion = latest.get("conclusion") or latest.get("status") or "?"
    colour = GREEN if conclusion == "success" else RED
    print(f"{BOLD}Последний прогон CI:{RESET} {colour}{conclusion}{RESET} "
          f"{DIM}({latest.get('headSha', '')[:7]}){RESET}")
    return 0 if conclusion == "success" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка проекта, как в CI")
    parser.add_argument("--fast", action="store_true", help="без тестов (они требуют БД)")
    parser.add_argument("--ci", action="store_true", help="плюс статус GitHub Actions")
    parser.add_argument("--verbose", "-v", action="store_true", help="полный вывод каждого шага")
    args = parser.parse_args()

    print(f"{BOLD}Проверка проекта (те же шаги, что в CI){RESET}\n")
    code = report(steps_for(args.fast), args.verbose)
    if code == 0 and args.ci:
        code = ci_status()
    print()
    if code == 0:
        print(f"{GREEN}{BOLD}Все проверки пройдены.{RESET}")
    else:
        print(f"{RED}{BOLD}Есть падения — причина выше.{RESET}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
