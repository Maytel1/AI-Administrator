"""Роли панели и права на действия (DEVELOPMENT_PLAN.md §8.2 — RBAC).

Три роли, как и договаривались по плану:
  * ``owner`` — владелец точки: всё в своём салоне + управление доступом;
  * ``staff`` — администратор точки: рутина (статусы записей) и просмотр;
  * ``team``  — наша команда: все салоны и настройки.

Права описаны явно (``ROLE_PERMISSIONS``), а не «если роль не staff — можно всё»:
новое действие не должно молча открываться всем.
"""

from __future__ import annotations

ROLES = ("owner", "staff", "team")

ROLE_RU = {
    "owner": "Владелец точки",
    "staff": "Администратор",
    "team": "Наша команда",
}

# Права: что роль может делать в панели.
P_READ = "read"                      # любой просмотр своего салона
P_BOOKING_STATUS = "booking_status"  # подтвердить/выполнить/отмен��ть запись
P_PRICING = "pricing"                # цены, длительность, активность услуг
P_CLIENT_FLAGS = "client_flags"      # предоплата и заметки клиента
P_SCHEDULE = "schedule"              # рабочие дни мастера
P_USERS = "users"                    # учётные записи точки
P_ALL_SALONS = "all_salons"          # видеть и переключать все точки

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {P_READ, P_BOOKING_STATUS, P_PRICING, P_CLIENT_FLAGS, P_SCHEDULE, P_USERS},
    "staff": {P_READ, P_BOOKING_STATUS, P_CLIENT_FLAGS},
    "team": {
        P_READ, P_BOOKING_STATUS, P_PRICING, P_CLIENT_FLAGS, P_SCHEDULE,
        P_USERS, P_ALL_SALONS,
    },
}

# Право, требуемое для каждого действия формы в web.actions.
ACTION_PERMISSION = {
    "booking_status": P_BOOKING_STATUS,
    "service_save": P_PRICING,
    "client_save": P_CLIENT_FLAGS,
    "hours_save": P_SCHEDULE,
    "user_create": P_USERS,
    "user_totp": P_USERS,
    "user_disable": P_USERS,
}

# Роли, которые владелец точки может выдать из панели. Роль `team` — наша
# внутренняя, её создают из CLI: иначе любой владелец выдал бы себе все точки.
SELF_ISSUE_ROLES = ("owner", "staff")


def can(role: str, permission: str) -> bool:
    """Есть ли у роли право; неизвестная роль не имеет ничего."""
    return permission in ROLE_PERMISSIONS.get(role, set())


def can_action(role: str, action: str) -> bool:
    """Право на конкретное действие формы."""
    return can(role, ACTION_PERMISSION.get(action, ""))


def role_label(role: str) -> str:
    return ROLE_RU.get(role, role)


def permissions_of(role: str) -> list[str]:
    """Список прав роли в читаемом виде — показываем в интерфейсе и в логах."""
    names = {
        P_READ: "просмотр",
        P_BOOKING_STATUS: "статусы записей",
        P_PRICING: "цены и услуги",
        P_CLIENT_FLAGS: "клиенты: предоплата и заметки",
        P_SCHEDULE: "расписание мастеров",
        P_USERS: "учётные записи точки",
        P_ALL_SALONS: "все точки",
    }
    return sorted(names[p] for p in ROLE_PERMISSIONS.get(role, set()) if p in names)


def audit_actor(role: str, login: str) -> str:
    """Актор для audit_log: было 'owner:panel', стало 'owner:login@точка'."""
    prefix = {"owner": "owner", "staff": "staff", "team": "admin"}[role]
    return f"{prefix}:{login}"
