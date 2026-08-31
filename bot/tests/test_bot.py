"""Bot access-control regressions."""
from __future__ import annotations

from types import SimpleNamespace

from src.bot import main


def _message(user_id: int):
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id))


def test_empty_allowlist_fails_closed_by_default(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "telegram_allowed_user_ids", "")
    monkeypatch.setattr(main.settings, "bot_public", False)
    assert main._is_allowed(_message(100)) is False


def test_explicit_allowlist_allows_only_listed_user(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "telegram_allowed_user_ids", "100,200")
    monkeypatch.setattr(main.settings, "bot_public", False)
    assert main._is_allowed(_message(100)) is True
    assert main._is_allowed(_message(300)) is False


def test_public_mode_requires_explicit_flag(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "telegram_allowed_user_ids", "")
    monkeypatch.setattr(main.settings, "bot_public", True)
    assert main._is_allowed(_message(300)) is True
