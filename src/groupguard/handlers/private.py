from __future__ import annotations

import hmac
import html
import re
import secrets
from datetime import UTC, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ChatMemberAdministrator,
    ChatMemberOwner,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from groupguard.config import Settings
from groupguard.domain import format_local_datetime, normalize_username
from groupguard.keyboards import (
    allowlist_keyboard,
    case_list_keyboard,
    dashboard_keyboard,
    group_request_keyboard,
    help_keyboard,
    owner_list_keyboard,
    profile_keyboard,
    sanction_list_keyboard,
)
from groupguard.models import (
    AllowlistEntry,
    AppState,
    AuditEvent,
    ManagedChat,
    ModerationCase,
    Owner,
    PendingOwnerInvite,
    Sanction,
    UsernameAlias,
    UserProfile,
    uuid_str,
)
from groupguard.presentation import (
    reason_label,
    resolution_label,
    user_button_label,
    user_profile_url,
    user_reference,
)
from groupguard.repositories import (
    add_audit,
    find_user,
    get_managed_chat,
    is_owner,
    list_owners,
    owner_count,
    token_hash,
    upsert_user,
)
from groupguard.services.cases import CaseActionError, CaseActionService
from groupguard.services.notifications import NotificationService
from groupguard.services.owners import invite_validation_error

router = Router(name="private")
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{5,32}$")

OWNER_HELP_TEXT_TEMPLATE = """<b>Помощь GroupGuard</b>

<b>Основные команды</b>
• /menu — открыть панель управления
• /help — показать эту справку

<b>Как подключить группу</b>
1. Добавьте бота администратором группы.
2. Разрешите удаление сообщений и блокировку пользователей.
3. В @BotFather отключите Group Privacy для этого бота.
4. Нажмите «Подключить группу», затем «Выбрать группу».

Если Telegram скрыл кнопку «Выбрать группу», нажмите значок клавиатуры из четырёх
квадратов рядом со строкой ввода.

<b>Как добавить человека в белый список</b>
1. Человек должен хотя бы один раз написать в подключённой группе.
2. Нажмите «Найти человека».
3. Отправьте его @username или Telegram ID.
4. В карточке нажмите «В белый список».

Владельцы защищены автоматически — добавлять их в белый список не нужно. Раздел
«Белый список» показывает уже добавленных людей и позволяет удалить их оттуда.

<b>Что бот делает автоматически</b>
Если один автор в тот же календарный день часового пояса <code>{timezone}</code>
повторит одинаковый текст с хотя бы одним тем же номером телефона, бот выдаст мут
на 7 дней и удалит повтор. Первое сообщение останется.

Похожие тексты, одинаковые номера у разных аккаунтов и повторные фотографии не
наказываются автоматически — они попадают в раздел «На проверке».

<b>Если что-то не работает</b>
• Пользователь не найден — попросите его написать сообщение в группе.
• Группа не выбирается — сохраните права администратора и откройте скрытую клавиатуру.
• Бот не видит сообщения — отключите Group Privacy в @BotFather.
• Старая панель не обновилась — отправьте /menu.

Во всех разделах нажмите имя человека или кнопку 👤, чтобы открыть его профиль. Если
у него нет публичного @username, бот использует внутреннюю Telegram-ссылку по
user_id и явно показывает пометку «username отсутствует».
"""

GUEST_HELP_TEXT = """<b>GroupGuard</b>

Это личная панель владельцев бота-модератора. Для доступа попросите действующего
владельца добавить ваш @username и отправить одноразовую ссылку-приглашение.
"""


def owner_help_text(settings: Settings) -> str:
    return OWNER_HELP_TEXT_TEMPLATE.format(timezone=html.escape(settings.timezone))


class OwnerInput(StatesGroup):
    username = State()
    search = State()


async def require_owner(session: AsyncSession, user_id: int) -> bool:
    return await is_owner(session, user_id)


async def profiles_by_id(
    session: AsyncSession,
    user_ids: list[int],
) -> dict[int, UserProfile]:
    if not user_ids:
        return {}
    profiles = list(
        (
            await session.scalars(
                select(UserProfile).where(UserProfile.user_id.in_(set(user_ids)))
            )
        ).all()
    )
    return {profile.user_id: profile for profile in profiles}


async def send_dashboard(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    owner = await session.get(Owner, message.from_user.id)
    if owner is None:
        await message.answer("Доступ закрыт. Попросите владельца прислать ссылку-приглашение.")
        return
    managed = await get_managed_chat(session)
    group_line = (
        f"Подключена группа: <b>{html.escape(managed.title)}</b>"
        if managed
        else "Группа пока не подключена."
    )
    await message.answer(
        f"<b>GroupGuard</b>\n{group_line}\n\nВыберите действие:",
        reply_markup=dashboard_keyboard(owner.notifications_enabled),
    )


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    settings: Settings,
    notifier: NotificationService,
) -> None:
    if message.from_user is None:
        return
    user = message.from_user
    profile = await upsert_user(session, user.id, user.username, user.full_name)
    args = command.args or ""

    if args.startswith("bootstrap_"):
        supplied = args.removeprefix("bootstrap_")
        await session.execute(sql_text("SELECT pg_advisory_xact_lock(711000)"))
        state = await session.get(AppState, 1)
        if state is None:
            state = AppState(id=1)
            session.add(state)
        if (
            await owner_count(session) == 0
            and state.bootstrap_consumed_at is None
            and hmac.compare_digest(supplied, settings.bootstrap_token.get_secret_value())
        ):
            session.add(Owner(user_id=user.id, private_chat_id=message.chat.id))
            state.bootstrap_consumed_at = datetime.now(UTC)
            await add_audit(session, "bootstrap_owner_created", actor_user_id=user.id)
            await session.commit()
            await message.answer("✅ Вы стали первым владельцем GroupGuard.")
        elif not await is_owner(session, user.id):
            await message.answer(
                "Ссылка первоначальной настройки недействительна или уже использована."
            )
            return

    elif args.startswith("owner_"):
        token = args.removeprefix("owner_")
        invite = await session.scalar(
            select(PendingOwnerInvite)
            .where(PendingOwnerInvite.token_hash == token_hash(token))
            .with_for_update()
        )
        now = datetime.now(UTC)
        validation_error = invite_validation_error(invite, user.username, now)
        if validation_error in {"missing", "consumed", "expired"}:
            await message.answer("Приглашение недействительно или уже использовано.")
            return
        if validation_error in {"username_missing", "username_mismatch"}:
            await message.answer(
                "Username не совпадает с приглашением. Проверьте аккаунт и обратитесь к владельцу."
            )
            return
        assert invite is not None
        existing = await session.get(Owner, user.id)
        if existing is None:
            session.add(
                Owner(
                    user_id=user.id,
                    private_chat_id=message.chat.id,
                    added_by_user_id=invite.created_by_user_id,
                )
            )
        else:
            existing.private_chat_id = message.chat.id
        invite.consumed_at = now
        invite.consumed_by_user_id = user.id
        await add_audit(
            session,
            "owner_added",
            actor_user_id=invite.created_by_user_id,
            target_user_id=user.id,
        )
        await session.commit()
        await notifier.critical(
            session,
            f"Добавлен новый владелец:\n{user_reference(user.id, profile)}",
        )
        await session.commit()

    await send_dashboard(message, session)


@router.message(Command("menu"))
async def menu(message: Message, session: AsyncSession) -> None:
    await send_dashboard(message, session)


@router.message(Command("help"))
async def help_command(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None:
        return
    if await is_owner(session, message.from_user.id):
        await message.answer(owner_help_text(settings), reply_markup=help_keyboard())
    else:
        await message.answer(GUEST_HELP_TEXT)


async def edit_panel(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


@router.callback_query(F.data == "panel:home")
async def panel_home(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.from_user is None or not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    owner = await session.get(Owner, callback.from_user.id)
    assert owner is not None
    managed = await get_managed_chat(session)
    group_line = (
        f"Подключена группа: <b>{html.escape(managed.title)}</b>"
        if managed
        else "Группа пока не подключена."
    )
    await edit_panel(
        callback,
        f"<b>GroupGuard</b>\n{group_line}\n\nВыберите действие:",
        dashboard_keyboard(owner.notifications_enabled),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:cases")
async def panel_cases(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    cases = list(
        (
            await session.scalars(
                select(ModerationCase)
                .where(ModerationCase.status == "open")
                .order_by(ModerationCase.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    profiles = await profiles_by_id(session, [case.target_user_id for case in cases])
    text = "<b>На проверке</b>\n\nВыберите случай." if cases else "Очередь проверки пуста."
    await edit_panel(
        callback,
        text,
        case_list_keyboard(
            [
                (
                    case,
                    user_button_label(case.target_user_id, profiles.get(case.target_user_id)),
                    user_profile_url(case.target_user_id, profiles.get(case.target_user_id)),
                )
                for case in cases
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:sanctions")
async def panel_sanctions(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    sanctions = list(
        (
            await session.scalars(
                select(Sanction)
                .where(Sanction.active.is_(True), Sanction.until_at > datetime.now(UTC))
                .order_by(Sanction.until_at)
                .limit(30)
            )
        ).all()
    )
    profiles = await profiles_by_id(session, [item.user_id for item in sanctions])
    lines = [
        f"• {user_reference(item.user_id, profiles.get(item.user_id))}\n"
        f"  Мут до "
        f"{format_local_datetime(item.until_at, settings.zoneinfo, '%d.%m %H:%M')}"
        for item in sanctions
    ]
    text = (
        f"<b>Активные ограничения</b>\n"
        f"Время: <code>{html.escape(settings.timezone)}</code>\n\n"
        + ("\n".join(lines) if lines else "Список пуст.")
    )
    await edit_panel(
        callback,
        text,
        sanction_list_keyboard(
            [
                (
                    item.id,
                    user_button_label(item.user_id, profiles.get(item.user_id)),
                    user_profile_url(item.user_id, profiles.get(item.user_id)),
                )
                for item in sanctions
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:allowlist")
async def panel_allowlist(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    entries = list((await session.scalars(select(AllowlistEntry).limit(30))).all())
    profiles = await profiles_by_id(session, [entry.user_id for entry in entries])
    text = "<b>Белый список</b>\n\n" + (
        "\n".join(
            f"• {user_reference(entry.user_id, profiles.get(entry.user_id))}"
            for entry in entries
        )
        if entries
        else "Список пуст."
    )
    await edit_panel(
        callback,
        text,
        allowlist_keyboard(
            [
                (
                    entry.user_id,
                    user_button_label(entry.user_id, profiles.get(entry.user_id)),
                    user_profile_url(entry.user_id, profiles.get(entry.user_id)),
                )
                for entry in entries
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:owners")
async def panel_owners(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    owners = await list_owners(session)
    profiles = [await session.get(UserProfile, owner.user_id) for owner in owners]
    lines = [
        f"• {user_reference(owner.user_id, profile)}"
        for owner, profile in zip(owners, profiles, strict=True)
    ]
    await edit_panel(
        callback,
        "<b>Владельцы</b>\n\n" + "\n".join(lines),
        owner_list_keyboard(
            [
                (
                    owner.user_id,
                    user_button_label(owner.user_id, profile),
                    user_profile_url(owner.user_id, profile),
                )
                for owner, profile in zip(owners, profiles, strict=True)
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:audit")
async def panel_audit(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    events = list(
        (
            await session.scalars(
                select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(20)
            )
        ).all()
    )
    profiles = await profiles_by_id(
        session,
        [event.target_user_id for event in events if event.target_user_id is not None],
    )
    lines = [
        f"• {format_local_datetime(event.created_at, settings.zoneinfo, '%d.%m %H:%M')} — "
        f"{event.action}"
        + (
            f"\n  {user_reference(event.target_user_id, profiles.get(event.target_user_id))}"
            if event.target_user_id is not None
            else ""
        )
        for event in events
    ]
    owner = await session.get(Owner, callback.from_user.id)
    assert owner is not None
    await edit_panel(
        callback,
        f"<b>История действий</b>\n"
        f"Время: <code>{html.escape(settings.timezone)}</code>\n\n"
        + ("\n".join(lines) if lines else "История пуста."),
        dashboard_keyboard(owner.notifications_enabled),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:notifications")
async def toggle_notifications(callback: CallbackQuery, session: AsyncSession) -> None:
    owner = await session.get(Owner, callback.from_user.id)
    if owner is None:
        await callback.answer("Нет доступа", show_alert=True)
        return
    owner.notifications_enabled = not owner.notifications_enabled
    await add_audit(
        session,
        "owner_notifications_changed",
        actor_user_id=callback.from_user.id,
        details={"enabled": owner.notifications_enabled},
    )
    await session.commit()
    await panel_home(callback, session)


@router.callback_query(F.data == "panel:connect")
async def connect_group(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await require_owner(session, callback.from_user.id) or callback.message is None:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.answer(
        "Добавьте бота администратором группы с правами удаления сообщений и ограничения "
        "участников, затем нажмите кнопку ниже. Если Telegram спрятал кнопку, нажмите "
        "значок клавиатуры из четырёх квадратов рядом со строкой ввода.",
        reply_markup=group_request_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:help")
async def panel_help(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await require_owner(session, callback.from_user.id) or callback.message is None:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await edit_panel(callback, owner_help_text(settings), help_keyboard())
    await callback.answer()


@router.message(F.chat_shared)
async def group_shared(message: Message, session: AsyncSession, bot: Bot) -> None:
    if message.from_user is None or message.chat_shared is None:
        return
    if not await require_owner(session, message.from_user.id):
        await message.answer("Нет доступа.", reply_markup=ReplyKeyboardRemove())
        return
    await session.execute(sql_text("SELECT pg_advisory_xact_lock(711002)"))
    existing = await get_managed_chat(session)
    if existing is not None and existing.chat_id != message.chat_shared.chat_id:
        await message.answer(
            "К этому экземпляру уже подключена другая группа.", reply_markup=ReplyKeyboardRemove()
        )
        return
    chat_id = message.chat_shared.chat_id
    me = await bot.get_me()
    try:
        owner_member = await bot.get_chat_member(chat_id, message.from_user.id)
        bot_member = await bot.get_chat_member(chat_id, me.id)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(
            "Telegram не дал проверить группу. Сначала добавьте бота администратором.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    owner_admin = isinstance(owner_member, ChatMemberAdministrator | ChatMemberOwner)
    bot_ready = isinstance(bot_member, ChatMemberAdministrator) and bool(
        bot_member.can_delete_messages and bot_member.can_restrict_members
    )
    if not owner_admin or not bot_ready:
        await message.answer(
            "Проверка прав не пройдена. Владелец и бот должны быть администраторами, а боту "
            "нужны права удаления и ограничения участников.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    try:
        chat = await bot.get_chat(chat_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(
            "Telegram не дал получить сведения о группе. Проверьте права бота.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if existing is None:
        session.add(
            ManagedChat(
                chat_id=chat_id,
                title=chat.title or str(chat_id),
                username=chat.username,
                connected_by_user_id=message.from_user.id,
            )
        )
    else:
        existing.title = chat.title or str(chat_id)
        existing.username = chat.username
    await add_audit(session, "group_connected", actor_user_id=message.from_user.id)
    await session.commit()
    await message.answer(
        f"✅ Подключена группа: <b>{html.escape(chat.title or str(chat_id))}</b>",
        reply_markup=ReplyKeyboardRemove(),
    )
    await send_dashboard(message, session)


@router.callback_query(F.data == "owner:add")
async def owner_add(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await require_owner(session, callback.from_user.id) or callback.message is None:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(OwnerInput.username)
    await callback.message.answer("Отправьте @username нового владельца.")
    await callback.answer()


@router.message(OwnerInput.username)
async def owner_username(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
) -> None:
    if message.from_user is None or not await require_owner(session, message.from_user.id):
        await state.clear()
        return
    username = normalize_username(message.text or "")
    if not USERNAME_RE.fullmatch(username):
        await message.answer("Некорректный username. Пример: <code>@example_user</code>.")
        return
    token = secrets.token_urlsafe(18)
    invite = PendingOwnerInvite(
        id=uuid_str(),
        username=username,
        token_hash=token_hash(token),
        created_by_user_id=message.from_user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    session.add(invite)
    await session.commit()
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=owner_{token}"
    await message.answer(
        f"Приглашение для <b>@{username}</b> действует 24 часа:\n{link}\n\n"
        "Отправьте эту ссылку только нужному человеку."
    )
    await state.clear()


@router.callback_query(F.data.startswith("owner:remove:"))
async def owner_remove(
    callback: CallbackQuery,
    session: AsyncSession,
    notifier: NotificationService,
) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await session.execute(sql_text("SELECT pg_advisory_xact_lock(711001)"))
    target_id = int((callback.data or "").rsplit(":", 1)[-1])
    if await owner_count(session) <= 1:
        await callback.answer("Нельзя удалить последнего владельца.", show_alert=True)
        return
    owner = await session.get(Owner, target_id)
    if owner is None:
        await callback.answer("Владелец уже удалён.", show_alert=True)
        return
    profile = await session.get(UserProfile, target_id)
    await session.delete(owner)
    await add_audit(
        session,
        "owner_removed",
        actor_user_id=callback.from_user.id,
        target_user_id=target_id,
    )
    await session.commit()
    await notifier.critical(
        session,
        f"Удалён владелец:\n{user_reference(target_id, profile)}",
    )
    await session.commit()
    if target_id == callback.from_user.id:
        if isinstance(callback.message, Message):
            await callback.message.edit_text("Вы удалили себя из списка владельцев.")
        await callback.answer("Доступ владельца снят.", show_alert=True)
        return
    await callback.answer("Владелец удалён.")
    await panel_owners(callback, session)


@router.callback_query(F.data == "panel:search")
async def panel_search(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await require_owner(session, callback.from_user.id) or callback.message is None:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(OwnerInput.search)
    await callback.message.answer("Отправьте Telegram ID или известный @username пользователя.")
    await callback.answer()


async def profile_text(
    session: AsyncSession,
    profile: UserProfile,
    settings: Settings,
) -> str:
    last = (
        f"{format_local_datetime(profile.last_violation_at, settings.zoneinfo)} "
        f"({html.escape(settings.timezone)})"
        if profile.last_violation_at
        else "нет"
    )
    aliases = list(
        (
            await session.scalars(
                select(UsernameAlias)
                .where(UsernameAlias.user_id == profile.user_id)
                .order_by(UsernameAlias.last_seen_at.desc())
            )
        ).all()
    )
    cases = list(
        (
            await session.scalars(
                select(ModerationCase)
                .where(ModerationCase.target_user_id == profile.user_id)
                .order_by(ModerationCase.created_at.desc())
                .limit(10)
            )
        ).all()
    )
    alias_line = ", ".join(f"@{item.username}" for item in aliases) or "нет"
    case_lines = [
        f"• {format_local_datetime(case.created_at, settings.zoneinfo, '%d.%m.%Y')} — "
        f"{html.escape(reason_label(case.reason))} / "
        f"{html.escape(resolution_label(case.resolution or case.status))}"
        for case in cases
    ]
    details = (
        f"<b>Пользователь</b>\n{user_reference(profile.user_id, profile)}\n"
        f"Известные usernames: {alias_line}\n"
        f"Нарушений: {profile.violation_count}\n"
        f"Удалений: {profile.deletion_count}\n"
        f"Мутов: {profile.mute_count}\n"
        f"Последнее нарушение: {last}"
    )
    if case_lines:
        details += "\n\n<b>Последние случаи</b>\n" + "\n".join(case_lines)
    return details


@router.message(OwnerInput.search)
async def search_user(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if message.from_user is None or not await require_owner(session, message.from_user.id):
        await state.clear()
        return
    profile = await find_user(session, message.text or "")
    if profile is None:
        await message.answer("Пользователь не найден в локальной базе.")
    else:
        allowlisted = await session.get(AllowlistEntry, profile.user_id) is not None
        await message.answer(
            await profile_text(session, profile, settings),
            reply_markup=profile_keyboard(
                profile.user_id,
                allowlisted=allowlisted,
                user_label=user_button_label(profile.user_id, profile),
                profile_url=user_profile_url(profile.user_id, profile),
            ),
        )
    await state.clear()


@router.callback_query(F.data.startswith("case:"))
async def case_action(
    callback: CallbackQuery,
    session: AsyncSession,
    actions: CaseActionService,
    notifier: NotificationService,
    settings: Settings,
) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, case_id, action = (callback.data or "").split(":", 2)
    case = await session.get(ModerationCase, case_id)
    if case is None:
        await callback.answer("Случай не найден.", show_alert=True)
        return
    if action == "show":
        await notifier.deliver_case(
            session, case, force_all=True, only_owner_id=callback.from_user.id
        )
        await session.commit()
        await callback.answer("Карточка отправлена ниже.")
        return
    if action == "history":
        profile = await session.get(UserProfile, case.target_user_id)
        if callback.message and profile:
            allowlisted = await session.get(AllowlistEntry, profile.user_id) is not None
            await callback.message.answer(
                await profile_text(session, profile, settings),
                reply_markup=profile_keyboard(
                    profile.user_id,
                    allowlisted=allowlisted,
                    user_label=user_button_label(profile.user_id, profile),
                    profile_url=user_profile_url(profile.user_id, profile),
                ),
            )
        await callback.answer()
        return
    if action == "unmute":
        try:
            await actions.unmute_user(
                session,
                case.target_user_id,
                callback.from_user.id,
                case_id=case.id,
            )
            await callback.answer("Ограничение снято.", show_alert=True)
        except CaseActionError as error:
            await callback.answer(str(error), show_alert=True)
        return
    try:
        await actions.act(session, case_id, callback.from_user.id, action)
        await callback.answer("Решение сохранено.", show_alert=True)
    except CaseActionError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("sanction:unmute:"))
async def sanction_unmute(
    callback: CallbackQuery,
    session: AsyncSession,
    actions: CaseActionService,
    settings: Settings,
) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    sanction_id = (callback.data or "").rsplit(":", 1)[-1]
    sanction = await session.get(Sanction, sanction_id)
    if sanction is None or not sanction.active:
        await callback.answer("Ограничение уже неактивно.", show_alert=True)
        return
    try:
        await actions.unmute_user(session, sanction.user_id, callback.from_user.id)
        await callback.answer("Ограничение снято.", show_alert=True)
        await panel_sanctions(callback, session, settings)
    except CaseActionError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("allow:remove:"))
async def allow_remove(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int((callback.data or "").rsplit(":", 1)[-1])
    entry = await session.get(AllowlistEntry, user_id)
    if entry:
        await session.delete(entry)
        await add_audit(
            session,
            "allowlist_removed",
            actor_user_id=callback.from_user.id,
            target_user_id=user_id,
        )
        await session.commit()
    await callback.answer("Удалено из белого списка.")
    await panel_allowlist(callback, session)


@router.callback_query(F.data.startswith("profile:"))
async def profile_action(
    callback: CallbackQuery,
    session: AsyncSession,
    actions: CaseActionService,
    settings: Settings,
) -> None:
    if not await require_owner(session, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, action, raw_user_id = (callback.data or "").split(":", 2)
    user_id = int(raw_user_id)
    profile = await session.get(UserProfile, user_id)
    if profile is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    if action == "allow":
        if await session.get(AllowlistEntry, user_id) is None:
            session.add(
                AllowlistEntry(
                    user_id=user_id,
                    added_by_user_id=callback.from_user.id,
                    reason="manual_search",
                )
            )
            await add_audit(
                session,
                "allowlist_added",
                actor_user_id=callback.from_user.id,
                target_user_id=user_id,
            )
            await session.commit()
        result = "Добавлен в белый список."
    elif action == "unallow":
        entry = await session.get(AllowlistEntry, user_id)
        if entry is not None:
            await session.delete(entry)
            await add_audit(
                session,
                "allowlist_removed",
                actor_user_id=callback.from_user.id,
                target_user_id=user_id,
            )
            await session.commit()
        result = "Удалён из белого списка."
    elif action == "unmute":
        try:
            await actions.unmute_user(session, user_id, callback.from_user.id)
        except CaseActionError as error:
            await callback.answer(str(error), show_alert=True)
            return
        result = "Ограничение снято."
    else:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        allowlisted = await session.get(AllowlistEntry, user_id) is not None
        await callback.message.answer(
            await profile_text(session, profile, settings),
            reply_markup=profile_keyboard(
                user_id,
                allowlisted=allowlisted,
                user_label=user_button_label(user_id, profile),
                profile_url=user_profile_url(user_id, profile),
            ),
        )
    await callback.answer(result, show_alert=True)
