"""Fixtures globales para todos los tests de ADSO."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from telegram import CallbackQuery, Chat, Message, Update, User


TESTS_ROOT = Path(__file__).parent
FIXTURES_DIR = TESTS_ROOT / "fixtures"


# ---------------------------------------------------------------------------
# Markers automáticos por directorio
# ---------------------------------------------------------------------------
#
# Los markers se asignan acá y no archivo por archivo a propósito: la versión
# manual es la que se desincroniza. Antes de esto los markers estaban
# declarados en pyproject.toml y documentados en docs/testing.md pero aplicados
# en cero tests, así que el `-m "not integration and not e2e"` de CI no
# excluía nada (ver G15 en docs/audit-2026-07-31.md). Un archivo nuevo en
# tests/e2e/ queda marcado por existir, sin que nadie tenga que acordarse.

_DIR_MARKERS = {"integration": "integration", "e2e": "e2e"}


def marker_for_path(path: Path) -> str | None:
    """Devuelve el marker que corresponde a un archivo de test por su ubicación.

    Args:
        path: Path del archivo de test.

    Returns:
        Nombre del marker ('integration' | 'e2e'), o None si el directorio no
        lleva marker (tests/unit/) o el path cae fuera de tests/.
    """
    try:
        rel = path.relative_to(TESTS_ROOT)
    except ValueError:
        return None
    if not rel.parts:
        return None
    return _DIR_MARKERS.get(rel.parts[0])


def pytest_collection_modifyitems(items: list) -> None:
    """Aplica el marker de directorio a cada test colectado."""
    for item in items:
        marker = marker_for_path(Path(item.path))
        if marker:
            item.add_marker(getattr(pytest.mark, marker))


# ---------------------------------------------------------------------------
# Guards globales: ningún test sale a la red
# ---------------------------------------------------------------------------
#
# `reporters._llm_synthesis` construye un cliente genai de verdad y se traga
# cualquier excepción, así que 14 tests unitarios abrían conexiones TCP a
# generativelanguage.googleapis.com y pasaban igual — lentos, no determinísticos
# y dependientes de que la API contestara (#67).

_real_socket_connect = socket.socket.connect
_real_create_connection = socket.create_connection

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _es_loopback(address) -> bool:
    """True para destinos que un test sí puede usar: loopback y AF_UNIX."""
    if not isinstance(address, tuple) or not address:
        # AF_UNIX pasa el path como str: no es red.
        return True
    host = address[0]
    return isinstance(host, str) and host.split("%")[0] in _LOOPBACK


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """Bloquea toda conexión saliente que no sea loopback ni AF_UNIX.

    Se parchea ``socket.socket.connect`` —el nivel más bajo, por donde pasan
    ``create_connection``, httpx y el SDK de genai— y también
    ``create_connection`` para que el traceback diga algo legible. ChromaDB y
    cualquier servidor local siguen funcionando.
    """
    def _guard_connect(self, address, *args, **kwargs):
        if not _es_loopback(address):
            raise RuntimeError(
                f"Un test intentó salir a la red hacia {address!r}. "
                "Mockear la llamada en vez de pegarle a la API real."
            )
        return _real_socket_connect(self, address, *args, **kwargs)

    def _guard_create_connection(address, *args, **kwargs):
        if not _es_loopback(address):
            raise RuntimeError(
                f"Un test intentó salir a la red hacia {address!r}. "
                "Mockear la llamada en vez de pegarle a la API real."
            )
        return _real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _guard_connect)
    monkeypatch.setattr(socket, "create_connection", _guard_create_connection)


# Tests cuyo SUJETO es `_llm_synthesis` misma: mockean el cliente genai y miden
# lo que la función hace con él (el deadline HTTP de la request, el timeout de
# una llamada colgada). Stubearla ahí les sacaría justo lo que verifican, y no
# salen a la red igual: el cliente está mockeado y el guard de sockets sigue
# puesto.
_TESTS_QUE_MIDEN_LA_SINTESIS = ("tests/unit/test_lote4.py::TestR5Synthesis",)


@pytest.fixture(autouse=True)
def _sin_sintesis_llm(request, monkeypatch):
    """Neutraliza ``reporters._llm_synthesis`` en toda la suite.

    Devuelve None (la síntesis es un adorno: el reporte se genera igual). Un test
    que parchee `_llm_synthesis` por su cuenta gana, porque su parche se aplica
    después de esta fixture.
    """
    if request.node.nodeid.startswith(_TESTS_QUE_MIDEN_LA_SINTESIS):
        return

    from adso import reporters

    monkeypatch.setattr(reporters, "_llm_synthesis", AsyncMock(return_value=None))


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
