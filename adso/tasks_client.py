"""Cliente para Google Tasks API v3 — lista ADSO dedicada."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/tasks"]
ADSO_LIST_NAME = "ADSO"
_NOTES_MAX_BYTES = 8000


# ---------------------------------------------------------------------------
# Helpers síncronos (ejecutados en thread pool)
# ---------------------------------------------------------------------------

def _load_service(creds_path: Path, token_path: Path):
    """Carga credenciales desde token.json y construye el servicio.

    Refresca el token si expiró. Devuelve None si no hay token almacenado
    (requiere correr el flujo OAuth una vez con auth_google_tasks.py).
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not token_path.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        else:
            return None

    return build("tasks", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# TasksClient
# ---------------------------------------------------------------------------

class TasksClient:
    """Wrapper async sobre Google Tasks API v3.

    Gestiona la lista 'ADSO' (la crea si no existe) y publica tareas en ella.
    Si las credenciales no están configuradas, opera en modo no-op con log de aviso.
    """

    def __init__(self, creds_path: str) -> None:
        """Args:
            creds_path: Path al JSON de credenciales OAuth (client_secrets).
                El token persistido se guarda en el mismo directorio como token_tasks.json.
        """
        _p = Path(creds_path)
        # Tolerar que se pase el directorio en vez del archivo JSON
        self._creds_path = _p / "google-oauth.json" if _p.is_dir() else _p
        self._token_path = self._creds_path.parent / "token_tasks.json"
        self._service = None
        self._list_id: Optional[str] = None
        # Evitar repetir el mismo warning de auth en cada intento
        self._auth_error_logged: bool = False

    async def _ensure_service(self) -> bool:
        """Inicializa el servicio si no está listo. Devuelve True si disponible."""
        if self._service is not None:
            return True
        try:
            svc = await asyncio.to_thread(_load_service, self._creds_path, self._token_path)
        except Exception as exc:
            if not self._auth_error_logged:
                logger.warning(
                    "Google Tasks: error al cargar credenciales: %s — "
                    "Ejecutar scripts/auth_google_tasks.py para re-autenticar.",
                    exc,
                )
                self._auth_error_logged = True
            return False

        if svc is None:
            if not self._auth_error_logged:
                logger.warning(
                    "Google Tasks deshabilitado: token no encontrado en %s. "
                    "Ejecutar scripts/auth_google_tasks.py para autenticar.",
                    self._token_path,
                )
                self._auth_error_logged = True
            return False

        self._service = svc
        self._auth_error_logged = False  # reset en caso de recuperación
        return True

    @property
    def auth_failed(self) -> bool:
        """True si el último intento de autenticación falló."""
        return self._auth_error_logged

    async def _get_list_id(self) -> Optional[str]:
        """Obtiene o crea la lista ADSO, cacheando el ID."""
        if self._list_id:
            return self._list_id

        def _find_or_create() -> str:
            result = self._service.tasklists().list(maxResults=100).execute()
            for lst in result.get("items", []):
                if lst["title"] == ADSO_LIST_NAME:
                    return lst["id"]
            new_list = self._service.tasklists().insert(
                body={"title": ADSO_LIST_NAME}
            ).execute()
            logger.info("Google Tasks: lista '%s' creada (id=%s)", ADSO_LIST_NAME, new_list["id"])
            return new_list["id"]

        try:
            self._list_id = await asyncio.to_thread(_find_or_create)
            return self._list_id
        except Exception as exc:
            logger.warning("Google Tasks: error obteniendo lista ADSO: %s", exc)
            return None

    async def create_task(
        self,
        title: str,
        notes: str,
        due_date: Optional[str] = None,
    ) -> Optional[str]:
        """Crea una tarea en la lista ADSO.

        Args:
            title: Título de la tarea.
            notes: Texto del campo notes (descripción + links obsidian://).
            due_date: Fecha límite ISO 8601 (YYYY-MM-DD). Opcional.

        Returns:
            ID de la tarea creada, o None si falló.
        """
        if not await self._ensure_service():
            return None

        list_id = await self._get_list_id()
        if not list_id:
            return None

        # Truncar notes si excede límite de la API
        notes_bytes = notes.encode("utf-8")
        if len(notes_bytes) > _NOTES_MAX_BYTES:
            notes = notes_bytes[:_NOTES_MAX_BYTES].decode("utf-8", errors="ignore")

        task_body: dict = {"title": title, "notes": notes}
        if due_date:
            # La API acepta RFC 3339; solo almacena la parte de fecha
            date_part = due_date[:10]  # tomar solo YYYY-MM-DD si viene con hora
            task_body["due"] = f"{date_part}T00:00:00.000Z"

        def _insert():
            return self._service.tasks().insert(
                tasklist=list_id, body=task_body
            ).execute()

        try:
            result = await asyncio.to_thread(_insert)
            logger.info(
                "Tarea creada en Google Tasks: '%s' (id=%s)", title, result.get("id")
            )
            return result.get("id")
        except Exception as exc:
            logger.warning("Google Tasks: error creando tarea '%s': %s", title, exc)
            return None


# ---------------------------------------------------------------------------
# Helper de contenido
# ---------------------------------------------------------------------------

def build_task_notes(fm: dict, note_path: Path, vault_path: Path, description: str = "") -> str:
    """Construye el campo notes para Google Tasks.

    Formato:
        <descripción original del usuario>
        Proyecto: X  (o Área: X si no hay proyecto)
        Prioridad: high/medium/low
        Horario: DD/MM/YYYY HH:MM  (solo si tiene hora no-medianoche)

    No incluye links obsidian:// — no funcionan desde Google Tasks/Calendar.

    Args:
        fm: Frontmatter de la nota guardada.
        note_path: Path absoluto de la nota.
        vault_path: Path raíz del vault.
        description: Texto original del usuario (body limpio, sin callouts).

    Returns:
        String listo para el campo notes de Google Tasks.
    """
    parts: list[str] = []

    if description:
        parts.append(description)
        parts.append("")  # línea en blanco separadora

    if fm.get("project"):
        parts.append(f"Proyecto: {fm['project']}")
    elif fm.get("area"):
        parts.append(f"Área: {fm['area']}")

    if fm.get("priority"):
        parts.append(f"Prioridad: {fm['priority']}")

    # Mostrar hora si `scheduled` o `due_date` traen un componente horario que
    # no sea medianoche (una fecha sola llega como `date` o como datetime 00:00).
    time_source = fm.get("scheduled") or fm.get("due_date")
    if time_source:
        try:
            dt = (
                time_source
                if isinstance(time_source, datetime)
                else datetime.fromisoformat(str(time_source))
            )
            if dt.hour != 0 or dt.minute != 0:
                parts.append(f"Horario: {dt.strftime('%d/%m/%Y %H:%M')}")
        except (TypeError, ValueError):
            pass

    return "\n".join(parts)
