from __future__ import annotations

import html

from groupguard.models import UserProfile

REASON_LABELS = {
    "similar_text": "похожий текст с тем же номером",
    "phone_other_user": "тот же номер у другого аккаунта",
    "repeated_media": "повтор той же фотографии",
    "similar_media": "визуально похожая фотография",
    "edited_suspicious": "подозрительное редактирование",
    "exact_duplicate": "точный повтор объявления",
}

RESOLUTION_LABELS = {
    "kept": "✅ оставлено",
    "deleted": "🗑 удалено",
    "deleted_missing": "🗑 удалено (сообщение уже отсутствовало)",
    "muted": "🔇 удалено, мут на 7 дней",
    "allowlisted": "🛡 добавлено в белый список",
    "auto_muted": "🤖 автоматически удалено, мут на 7 дней",
    "auto_partial": "⚠️ автоматическая санкция выполнена частично",
    "mute_partial": "⚠️ мут назначен, сообщение удалить не удалось",
    "mute_missing": "🔇 мут на 7 дней (сообщение уже отсутствовало)",
    "expired": "⌛ срок проверки истёк",
}


def user_profile_url(_user_id: int, profile: UserProfile | None) -> str | None:
    if profile and profile.current_username:
        return f"https://t.me/{profile.current_username}"
    return None


def user_button_label(user_id: int, profile: UserProfile | None) -> str:
    if profile and profile.current_username:
        return f"@{profile.current_username}"
    display_name = " ".join(profile.display_name.split()) if profile else ""
    if not display_name:
        return str(user_id)
    return f"{display_name[:27]}…" if len(display_name) > 28 else display_name


def user_reference(user_id: int, profile: UserProfile | None) -> str:
    display_name = " ".join(profile.display_name.split()) if profile else ""
    display_name = display_name or "Неизвестный пользователь"
    profile_url = user_profile_url(user_id, profile)
    escaped_name = html.escape(display_name)
    linked_name = (
        f'<a href="{html.escape(profile_url, quote=True)}">{escaped_name}</a>'
        if profile_url
        else escaped_name
    )
    username = (
        f"@{html.escape(profile.current_username)}"
        if profile and profile.current_username
        else "<i>username отсутствует</i>"
    )
    return f"{linked_name} · {username} · ID: <code>{user_id}</code>"


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


def resolution_label(resolution: str) -> str:
    return RESOLUTION_LABELS.get(resolution, resolution)
