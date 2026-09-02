from __future__ import annotations

from aiogram.types import (
    ChatAdministratorRights,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
)

from groupguard.models import ModerationCase


def dashboard_keyboard(notifications_enabled: bool = True) -> InlineKeyboardMarkup:
    notification_label = "🔔 Уведомления: вкл" if notifications_enabled else "🔕 Уведомления: выкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚠️ На проверке", callback_data="panel:cases"),
                InlineKeyboardButton(text="🔇 Ограниченные", callback_data="panel:sanctions"),
            ],
            [
                InlineKeyboardButton(text="🛡 Белый список", callback_data="panel:allowlist"),
                InlineKeyboardButton(text="👥 Владельцы", callback_data="panel:owners"),
            ],
            [
                InlineKeyboardButton(text="🔎 Найти человека", callback_data="panel:search"),
                InlineKeyboardButton(text="📋 История", callback_data="panel:audit"),
            ],
            [InlineKeyboardButton(text=notification_label, callback_data="panel:notifications")],
            [InlineKeyboardButton(text="➕ Подключить группу", callback_data="panel:connect")],
            [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="panel:help")],
        ]
    )


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="panel:home")],
        ]
    )


def case_keyboard(case: ModerationCase) -> InlineKeyboardMarkup | None:
    if case.status != "open":
        rows = [
            [
                InlineKeyboardButton(
                    text="👤 История пользователя",
                    callback_data=f"case:{case.id}:history",
                )
            ]
        ]
        if case.resolution in {"auto_muted", "muted", "auto_partial"}:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔊 Снять мут",
                        callback_data=f"case:{case.id}:unmute",
                    )
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Оставить", callback_data=f"case:{case.id}:keep"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"case:{case.id}:delete"),
            ],
            [
                InlineKeyboardButton(
                    text="🔇 Удалить и мут 7 дней",
                    callback_data=f"case:{case.id}:mute",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡 В белый список",
                    callback_data=f"case:{case.id}:allow",
                ),
                InlineKeyboardButton(
                    text="👤 История",
                    callback_data=f"case:{case.id}:history",
                ),
            ],
        ]
    )


def case_list_keyboard(cases: list[ModerationCase]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"⚠️ {case.reason} · {case.target_user_id}",
                callback_data=f"case:{case.id}:show",
            )
        ]
        for case in cases
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="panel:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def owner_list_keyboard(owner_ids: list[int]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"➖ Удалить {user_id}", callback_data=f"owner:remove:{user_id}"
            )
        ]
        for user_id in owner_ids
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text="➕ Добавить владельца", callback_data="owner:add")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="panel:home")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sanction_list_keyboard(sanctions: list[tuple[str, int]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🔊 Снять мут {user_id}", callback_data=f"sanction:unmute:{sid}"
            )
        ]
        for sid, user_id in sanctions
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="panel:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def allowlist_keyboard(user_ids: list[int]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"➖ Удалить {user_id}", callback_data=f"allow:remove:{user_id}"
            )
        ]
        for user_id in user_ids
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="panel:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_keyboard(user_id: int, *, allowlisted: bool) -> InlineKeyboardMarkup:
    allow_button = (
        InlineKeyboardButton(
            text="➖ Убрать из белого списка",
            callback_data=f"profile:unallow:{user_id}",
        )
        if allowlisted
        else InlineKeyboardButton(
            text="🛡 В белый список",
            callback_data=f"profile:allow:{user_id}",
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [allow_button],
            [
                InlineKeyboardButton(
                    text="🔊 Снять активный мут",
                    callback_data=f"profile:unmute:{user_id}",
                )
            ],
        ]
    )


def group_request_keyboard() -> ReplyKeyboardMarkup:
    rights = ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=False,
        can_restrict_members=True,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=False,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
    )
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="Выбрать группу",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=1,
                        chat_is_channel=False,
                        user_administrator_rights=rights,
                        bot_administrator_rights=rights,
                        bot_is_member=True,
                    ),
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
