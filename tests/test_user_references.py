from __future__ import annotations

from datetime import UTC, datetime, timedelta

from groupguard.keyboards import (
    allowlist_keyboard,
    case_keyboard,
    case_list_keyboard,
    owner_list_keyboard,
    profile_keyboard,
    sanction_list_keyboard,
)
from groupguard.models import ModerationCase, UserProfile
from groupguard.presentation import (
    user_button_label,
    user_profile_url,
    user_reference,
)
from groupguard.services.notifications import render_case


def profile(*, username: str | None = None, display_name: str = "Elyorboy Sultonov") -> UserProfile:
    return UserProfile(
        user_id=2_063_305_202,
        current_username=username,
        display_name=display_name,
    )


def moderation_case() -> ModerationCase:
    return ModerationCase(
        id="case-id",
        chat_id=-1001,
        source_message_id=12,
        target_user_id=2_063_305_202,
        status="open",
        reason="exact_duplicate",
        excerpt="Объявление",
        phone_masks=["998 ***** 3366"],
        delete_available_until=datetime.now(UTC) + timedelta(hours=48),
    )


def test_user_reference_links_public_username() -> None:
    user = profile(username="elyorboy")

    assert user_profile_url(user.user_id, user) == "https://t.me/elyorboy"
    assert 'href="https://t.me/elyorboy"' in user_reference(user.user_id, user)
    assert "@elyorboy" in user_reference(user.user_id, user)


def test_user_reference_links_by_id_and_explains_missing_username() -> None:
    user = profile()
    text = user_reference(user.user_id, user)

    assert user_profile_url(user.user_id, user) == "tg://user?id=2063305202"
    assert 'href="tg://user?id=2063305202"' in text
    assert "username отсутствует" in text
    assert "ID: <code>2063305202</code>" in text
    assert user_button_label(user.user_id, user) == "Elyorboy Sultonov"


def test_sanction_keyboard_has_profile_link_and_separate_unmute_action() -> None:
    keyboard = sanction_list_keyboard(
        [("sanction-id", "Elyorboy Sultonov", "tg://user?id=2063305202")]
    )
    profile_button, unmute_button = keyboard.inline_keyboard[0]

    assert profile_button.url == "tg://user?id=2063305202"
    assert unmute_button.callback_data == "sanction:unmute:sanction-id"


def test_all_user_lists_have_the_same_profile_link_pattern() -> None:
    user_data = (2_063_305_202, "Elyorboy Sultonov", "tg://user?id=2063305202")

    owner_profile = owner_list_keyboard([user_data]).inline_keyboard[0][0]
    allowed_profile = allowlist_keyboard([user_data]).inline_keyboard[0][0]
    search_profile = profile_keyboard(
        user_data[0],
        allowlisted=False,
        user_label=user_data[1],
        profile_url=user_data[2],
    ).inline_keyboard[0][0]

    assert owner_profile.url == allowed_profile.url == search_profile.url == user_data[2]
    assert owner_profile.text == allowed_profile.text == search_profile.text


def test_case_list_and_card_link_profile_and_use_readable_reason() -> None:
    case = moderation_case()
    user = profile()
    url = user_profile_url(user.user_id, user)
    label = user_button_label(user.user_id, user)

    list_keyboard = case_list_keyboard([(case, label, url)])
    card_keyboard = case_keyboard(case, user_label=label, profile_url=url)

    assert "точный повтор объявления" in list_keyboard.inline_keyboard[0][0].text
    assert list_keyboard.inline_keyboard[1][0].url == url
    assert card_keyboard is not None
    assert card_keyboard.inline_keyboard[-1][0].url == url
    assert 'href="tg://user?id=2063305202"' in render_case(case, user)
    assert "username отсутствует" in render_case(case, user)
