"""Fixtures globales para todos los tests de ADSO."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from telegram import CallbackQuery, Chat, Message, Update, User


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    """Crea estructura de vault temporal con carpetas PARA."""
    for d in ["00-Inbox", "01-Projects", "02-Areas", "03-Resources", "05-Archive"]:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def sample_config(tmp_path: Path) -> Path:
    """config.yaml de ejemplo para tests."""
    config = tmp_path / "config.yaml"
    config.write_text("""
rag:
  similarity_threshold: 0.75
  max_results: 10
links:
  similarity_threshold: 0.82
  max_suggestions: 5
vault:
  exclude_dirs:
    - "05-Archive"
    - ".obsidian"
    - ".trash"
backup:
  debounce_seconds: 1
llm:
  degraded_retry_minutes: 30
  disambiguation_threshold: 0.7
""", encoding="utf-8")
    return config


@pytest.fixture
def llm_fixture() -> dict:
    """Carga una fixture de respuesta LLM."""
    def _load(name: str) -> dict:
        return json.loads(
            (FIXTURES_DIR / "llm_responses" / name).read_text(encoding="utf-8")
        )
    return _load


# ---------------------------------------------------------------------------
# Factories de objetos Telegram
# ---------------------------------------------------------------------------

ALLOWED_USER_ID = 42


def make_user(user_id: int = ALLOWED_USER_ID) -> User:
    """Crea un User de Telegram."""
    return User(id=user_id, is_bot=False, first_name="Test")


def make_chat(chat_id: int = 1) -> Chat:
    """Crea un Chat de Telegram."""
    return Chat(id=chat_id, type="private")


def make_message(
    text: str = "",
    user_id: int = ALLOWED_USER_ID,
    message_id: int = 1,
) -> MagicMock:
    """Crea un Message de Telegram mockeado (PTB v21 congela objetos reales)."""
    msg = MagicMock(spec=Message)
    msg.message_id = message_id
    msg.chat = make_chat()
    msg.from_user = make_user(user_id)
    msg.text = text
    msg.reply_text = AsyncMock()
    return msg


@pytest.fixture
def make_update():
    """Factory de objetos Update de Telegram."""
    _counter = [0]

    def _make(
        text: str = "",
        user_id: int = ALLOWED_USER_ID,
    ) -> MagicMock:
        _counter[0] += 1
        msg = make_message(text=text, user_id=user_id, message_id=_counter[0])
        update = MagicMock(spec=Update)
        update.update_id = _counter[0]
        update.message = msg
        update.effective_user = msg.from_user
        update.callback_query = None
        return update

    return _make


@pytest.fixture
def make_callback_query():
    """Factory de CallbackQuery para simular respuestas a inline keyboards."""
    _counter = [0]

    def _make(
        data: str,
        user_id: int = ALLOWED_USER_ID,
        message: Message = None,
    ) -> Update:
        _counter[0] += 1
        if message is None:
            message = make_message(message_id=_counter[0])
        user = make_user(user_id)

        callback_query = MagicMock(spec=CallbackQuery)
        callback_query.data = data
        callback_query.from_user = user
        callback_query.message = message
        callback_query.answer = AsyncMock()
        callback_query.edit_message_text = AsyncMock()
        callback_query.edit_message_reply_markup = AsyncMock()

        update = MagicMock(spec=Update)
        update.update_id = _counter[0]
        update.callback_query = callback_query
        update.effective_user = user
        update.message = None

        return update

    return _make


@pytest.fixture
def mock_context(vault_path: Path, sample_config: Path):
    """Context de python-telegram-bot mockeado con settings."""
    from adso.config import load_settings

    settings = load_settings(sample_config)
    settings.vault_path = vault_path

    context = MagicMock()
    context.bot_data = {
        "settings": settings,
        "git_backup": None,
        "embeddings": None,
    }
    context.user_data = {}
    return context
