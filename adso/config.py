"""Carga y validación de configuración desde config.yaml y variables de entorno."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from adso.constants import DEFAULT_EXCLUDE_DIRS

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Error en la carga o validación de configuración."""


# ---------------------------------------------------------------------------
# Constantes de modelo
# ---------------------------------------------------------------------------

# Modelo de generación de Gemini usado en todo el bot (clasificación, síntesis
# de reportes, Gemini Vision). Fuente única de verdad: cambiar el modelo es una
# sola línea. Línea flash-lite estable; free tier jul-2026 ~1.000-1.500 RPD /
# 15 RPM / 250k TPM (verificar cap real por proyecto en AI Studio — Google ya no
# publica los números del free tier en la doc, solo en el dashboard de AI Studio).
#
# `ADSO_GEMINI_MODEL` overridea el default sin tocar código. Existe para que el
# harness de regresión (`scripts/llm_regression.py`) pueda apuntar a un modelo
# candidato sin editar este archivo; en producción se deja sin setear.
GEMINI_MODEL = os.environ.get("ADSO_GEMINI_MODEL") or "gemini-3.5-flash-lite"

# Modelo separado para Gemini Vision (OCR de imágenes y PDFs escaneados). El
# free tier de Google acota la quota POR MODELO, así que rasterizar un PDF de 20
# páginas ya no consume RPD del mismo bucket que la clasificación de notas — que
# es el flujo de todos los días. La calidad no motiva el split: el resultado de
# Vision se muestra en el preview y lo valida el usuario antes de confirmar.
GEMINI_VISION_MODEL = (
    os.environ.get("ADSO_GEMINI_VISION_MODEL") or "gemini-3.6-flash"
)


# ---------------------------------------------------------------------------
# Sub-dataclasses para secciones de config.yaml
# ---------------------------------------------------------------------------

@dataclass
class RagConfig:
    similarity_threshold: float = 0.75
    max_results: int = 10
    max_expansion_depth: int = 2


@dataclass
class LinksConfig:
    similarity_threshold: float = 0.82
    max_suggestions: int = 5


@dataclass
class VaultSeedItem:
    name: str
    description: str


@dataclass
class VaultSeedConfig:
    projects: list[VaultSeedItem] = field(default_factory=list)
    areas: list[VaultSeedItem] = field(default_factory=list)


@dataclass
class VaultConfig:
    exclude_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_DIRS))


@dataclass
class WhisperConfig:
    model: str = "base"
    model_dir: str = "/app/data/whisper"
    language: str = "es"


@dataclass
class ReindexConfig:
    enabled: bool = True
    time: str = "03:00"


@dataclass
class SyncConfig:
    interval_minutes: int = 30


@dataclass
class BackupConfig:
    enabled: bool = True
    debounce_seconds: int = 30


@dataclass
class DocumentsConfig:
    max_size_mb: int = 20


@dataclass
class LlmConfig:
    degraded_retry_minutes: int = 30
    disambiguation_threshold: float = 0.7


@dataclass
class TasksConfig:
    debug: bool = False


@dataclass
class WatcherConfig:
    debug: bool = False


@dataclass
class WeeklyReportConfig:
    enabled: bool = True
    day: str = "friday"
    time: str = "12:00"
    sections: dict[str, bool] = field(default_factory=lambda: {
        "notes_summary": True,
        "most_active_project": True,
        "papers_queue": True,
        "inbox_suggestion": True,
        "tasks_summary": True,
        "stale_ideas": True,
        "paper_suggestion": True,
    })
    stale_idea_days: int = 60


# ---------------------------------------------------------------------------
# Settings principal
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    """Configuración completa de ADSO."""

    # Variables de entorno (secretos)
    telegram_token: str = ""
    telegram_allowed_user_id: int = 0
    gemini_api_key: str = ""
    groq_api_key: str = ""
    google_calendar_creds: str = "/credentials/google-oauth.json"
    vault_path: Path = Path("/vault")
    chroma_data_dir: Path = Path("/app/data/chroma")

    # Secciones de config.yaml
    rag: RagConfig = field(default_factory=RagConfig)
    links: LinksConfig = field(default_factory=LinksConfig)
    vault_seed: VaultSeedConfig = field(default_factory=VaultSeedConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    reindex: ReindexConfig = field(default_factory=ReindexConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    documents: DocumentsConfig = field(default_factory=DocumentsConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    weekly_report: WeeklyReportConfig = field(default_factory=WeeklyReportConfig)

    # Claves del YAML que el loader no reconoce (`sección.clave`, o `sección`
    # suelta si la sección entera es desconocida). No aborta el arranque —
    # se loguea a WARNING. Ver I2 en docs/audit-2026-07-31.md.
    unknown_keys: list[str] = field(default_factory=list)
    tasks: TasksConfig = field(default_factory=TasksConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _primary_user_id(raw: str) -> int:
    """ID principal de `TELEGRAM_ALLOWED_USER_ID` (el primero de la lista).

    `security.py` acepta varios IDs separados por comas, pero acá se hacía
    `int(...)` directo sobre el valor crudo: con `"123,456"` el bot moría al
    arrancar con un `ValueError` sin mensaje de configuración. Este campo se usa
    para *mandar* notificaciones (destinatario único), así que se toma el
    primero; la autorización sigue usando el set completo de `security.py`.
    G7 de docs/audit-2026-07-31.md.

    Args:
        raw: Valor crudo de la variable de entorno.

    Returns:
        Primer ID numérico, o 0 si no hay ninguno.
    """
    for parte in raw.split(","):
        parte = parte.strip()
        if parte.isdigit():
            return int(parte)
    return 0


def _build_section(
    cls: type,
    data: dict[str, Any] | None,
    path: str | None = None,
    unknown: list[str] | None = None,
) -> Any:
    """Construye una sub-dataclass desde un dict, registrando claves desconocidas.

    Args:
        cls: Dataclass de la sección.
        data: Dict crudo del YAML, o None si la sección no está.
        path: Nombre de la sección, para reportar claves desconocidas.
        unknown: Lista donde acumular las claves ignoradas (`sección.clave`).

    Returns:
        Instancia de `cls` con los valores conocidos; el resto, defaults.

    Raises:
        ConfigError: Si la sección no es un mapa de claves (ej: una lista).
    """
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path or cls.__name__}: se esperaba un mapa de claves, "
            f"se obtuvo {type(data).__name__}"
        )
    known = {f.name for f in cls.__dataclass_fields__.values()}
    if unknown is not None and path:
        unknown.extend(f"{path}.{k}" for k in data if k not in known)
    filtered = {k: v for k, v in data.items() if k in known}
    return cls(**filtered)


def _build_vault_seed(
    data: dict[str, Any] | None, unknown: list[str] | None = None
) -> VaultSeedConfig:
    """Construye VaultSeedConfig validando que cada ítem tenga description.

    Args:
        data: Dict crudo de la sección `vault_seed`, o None.
        unknown: Lista donde acumular las claves ignoradas (`vault_seed.clave`).
            A diferencia de `_build_section`/`_build_weekly_report`, esta
            sección no delega en `cls(**filtered)` —arma `VaultSeedConfig` a
            mano porque valida `description` por ítem—, así que el reporte de
            claves desconocidas tenía que agregarse acá aparte (#45C: un typo
            como `proyectos` en vez de `projects` sembraba un vault vacío en
            silencio).

    Raises:
        ConfigError: Si la sección no es un mapa de claves, o si algún ítem no
            tiene `name`/`description`.
    """
    if data is None:
        return VaultSeedConfig()
    # Misma guarda que `_build_section`: `vault_seed` escrito como lista —la
    # confusión natural, porque sus hijos SÍ son listas— llegaba al `data.get()`
    # y mataba el arranque con `AttributeError` crudo, sin decir qué clave tocar.
    if not isinstance(data, dict):
        raise ConfigError(
            f"vault_seed: se esperaba un mapa de claves (projects / areas), "
            f"se obtuvo {type(data).__name__}"
        )

    if unknown is not None:
        known = {f.name for f in VaultSeedConfig.__dataclass_fields__.values()}
        unknown.extend(f"vault_seed.{k}" for k in data if k not in known)

    return VaultSeedConfig(
        projects=_seed_items(data, "projects"),
        areas=_seed_items(data, "areas"),
    )


def _seed_items(data: dict[str, Any], key: str) -> list[VaultSeedItem]:
    """Ítems de `vault_seed.projects` o `vault_seed.areas`, validando name y description.

    Raises:
        ConfigError: Si algún ítem no es un mapa con `name` y `description` no vacía.
    """
    items: list[VaultSeedItem] = []
    for item in data.get(key, []):
        if not isinstance(item, dict) or "name" not in item:
            raise ConfigError(f"vault_seed.{key}: cada ítem requiere 'name'")
        if not item.get("description"):
            raise ConfigError(f"vault_seed.{key}: '{item['name']}' requiere 'description'")
        items.append(VaultSeedItem(name=item["name"], description=item["description"]))
    return items


def _build_weekly_report(
    data: dict[str, Any] | None, unknown: list[str] | None = None
) -> WeeklyReportConfig:
    """Construye WeeklyReportConfig aceptando `sections` como dict o como lista.

    Args:
        data: Dict crudo de la sección `weekly_report`, o None.
        unknown: Lista donde acumular las claves ignoradas.

    Returns:
        WeeklyReportConfig con los valores del YAML y defaults para el resto.

    Raises:
        ConfigError: Si la sección no es un mapa de claves.
    """
    if data is None:
        return WeeklyReportConfig()
    if not isinstance(data, dict):
        raise ConfigError(
            f"weekly_report: se esperaba un mapa de claves, "
            f"se obtuvo {type(data).__name__}"
        )

    if unknown is not None:
        known = {f.name for f in WeeklyReportConfig.__dataclass_fields__.values()}
        unknown.extend(f"weekly_report.{k}" for k in data if k not in known)

    sections = data.get("sections", None)
    # `sections` acepta dict {nombre: bool} o lista [nombre, ...]. La lista se
    # normaliza a dict. OJO: la clave es `sections`, no `include` — el
    # config.yaml desplegado usaba `include` y se descartaba en silencio (I2 de
    # docs/audit-2026-07-31.md). Por eso ahora las claves ajenas se reportan.
    #
    # Validación de tipo (#45B): un tipo externo (string, número) se asignaba
    # verbatim y llegaba tal cual a los reporters. El caso caro es el mapa con
    # valor no-bool: `papers_queue: "false"` es un string, que es truthy, así
    # que la sección queda encendida justo cuando el usuario la apagó.
    if sections is not None:
        if isinstance(sections, list):
            if not all(isinstance(s, str) for s in sections):
                raise ConfigError(
                    "weekly_report.sections: la lista debe contener solo "
                    f"nombres de sección (strings), se obtuvo {sections!r}"
                )
            sections = {s: True for s in sections}
        elif isinstance(sections, dict):
            invalid = {
                k: v for k, v in sections.items()
                if not isinstance(k, str) or not isinstance(v, bool)
            }
            if invalid:
                raise ConfigError(
                    "weekly_report.sections: el mapa debe ser {nombre: bool}, "
                    f"valor(es) inválido(s): {invalid!r}"
                )
        else:
            raise ConfigError(
                "weekly_report.sections: se esperaba un mapa {nombre: bool} o "
                f"una lista de nombres, se obtuvo {type(sections).__name__}"
            )

    result = WeeklyReportConfig(
        enabled=data.get("enabled", True),
        day=data.get("day", "friday"),
        time=data.get("time", "12:00"),
        stale_idea_days=data.get("stale_idea_days", 60),
    )
    if sections is not None:
        result.sections = sections
    return result


def _validate_types(settings: Settings) -> None:
    """Valida tipos básicos de la configuración."""
    checks: list[tuple[str, Any, type]] = [
        ("rag.similarity_threshold", settings.rag.similarity_threshold, (int, float)),
        ("rag.max_results", settings.rag.max_results, int),
        ("links.similarity_threshold", settings.links.similarity_threshold, (int, float)),
        ("links.max_suggestions", settings.links.max_suggestions, int),
        ("backup.debounce_seconds", settings.backup.debounce_seconds, int),
        ("sync.interval_minutes", settings.sync.interval_minutes, int),
        ("llm.disambiguation_threshold", settings.llm.disambiguation_threshold, (int, float)),
        ("llm.degraded_retry_minutes", settings.llm.degraded_retry_minutes, int),
        ("documents.max_size_mb", settings.documents.max_size_mb, int),
    ]
    for name, value, expected in checks:
        if not isinstance(value, expected):
            raise ConfigError(
                f"{name}: se esperaba {expected.__name__ if isinstance(expected, type) else expected}, "
                f"se obtuvo {type(value).__name__} ({value!r})"
            )

    # `vault.exclude_dirs` debe ser lista de strings (#45A): un string suelto
    # carga sin error pero cambia la semántica del chequeo de exclusión a un
    # test de substring silencioso — deja de excluir lo que debería y empieza
    # a excluir cualquier cosa cuyo nombre sea substring de ese string.
    exclude_dirs = settings.vault.exclude_dirs
    if not isinstance(exclude_dirs, list) or not all(
        isinstance(x, str) for x in exclude_dirs
    ):
        raise ConfigError(
            f"vault.exclude_dirs: se esperaba una lista de strings, "
            f"se obtuvo {exclude_dirs!r}"
        )

    # Validar rangos
    if not 0.0 <= settings.rag.similarity_threshold <= 1.0:
        raise ConfigError("rag.similarity_threshold debe estar entre 0.0 y 1.0")
    if not 0.0 <= settings.links.similarity_threshold <= 1.0:
        raise ConfigError("links.similarity_threshold debe estar entre 0.0 y 1.0")
    if not 0.0 <= settings.llm.disambiguation_threshold <= 1.0:
        raise ConfigError("llm.disambiguation_threshold debe estar entre 0.0 y 1.0")

    # Las horas se validan acá y no al programar el job: `bot.py` hacía
    # `datetime.strptime(settings.reindex.time, "%H:%M")` y un "3am" en el YAML
    # mataba el arranque con traceback crudo, mientras el resto de la config da
    # ConfigError con mensaje claro. G9 de docs/audit-2026-07-31.md.
    for campo, valor in (
        ("reindex.time", settings.reindex.time),
        ("weekly_report.time", settings.weekly_report.time),
    ):
        try:
            datetime.strptime(str(valor), "%H:%M")
        except (ValueError, TypeError):
            # PyYAML resuelve un escalar sin comillas con dos puntos como
            # sexagesimal (YAML 1.1): `12:00` llega acá como el int 720. El
            # mensaje genérico reportaba entonces un valor que NO aparece en el
            # archivo del usuario. Con `03:00` no pasa (el cero inicial rompe el
            # resolver), así que el ejemplo del propio error funcionaba y el del
            # usuario no. Se sigue rechazando —adivinar la intención de un
            # número es peor— pero nombrando la causa real.
            if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0:
                horas, minutos = divmod(valor, 60)
                if horas < 24:
                    raise ConfigError(
                        f"{campo}: el YAML leyó la hora como el número {valor}. "
                        f"Falta escribirla entre comillas: "
                        f'time: "{horas:02d}:{minutos:02d}"'
                    ) from None
            raise ConfigError(
                f"{campo}: '{valor}' no es una hora válida (formato HH:MM, ej: 03:00)"
            ) from None

    valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    if settings.weekly_report.day not in valid_days:
        raise ConfigError(f"weekly_report.day: '{settings.weekly_report.day}' no es válido")

    valid_whisper = {"tiny", "base"}
    if settings.whisper.model not in valid_whisper:
        raise ConfigError(f"whisper.model: '{settings.whisper.model}' no es válido (tiny | base)")



# ---------------------------------------------------------------------------
# Carga principal
# ---------------------------------------------------------------------------

def load_settings(config_path: Path | str | None = None) -> Settings:
    """Carga configuración desde config.yaml y variables de entorno.

    Args:
        config_path: Path al config.yaml. Si None, usa ./config.yaml.

    Returns:
        Settings con todos los valores cargados y validados.

    Raises:
        ConfigError: Si config.yaml no existe o tiene valores inválidos.
    """
    config_path = Path("config.yaml" if config_path is None else config_path)

    if not config_path.exists():
        raise ConfigError(f"config.yaml no encontrado en {config_path.resolve()}")

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ConfigError("config.yaml debe ser un documento YAML con claves")

    # Las secciones válidas son exactamente los campos de Settings que son
    # dataclasses. Derivarlo del propio Settings evita que esta lista se
    # desincronice al agregar una sección nueva.
    unknown: list[str] = []
    _probe = Settings()
    known_sections = {
        f.name for f in fields(_probe) if is_dataclass(getattr(_probe, f.name))
    }
    unknown.extend(k for k in raw if k not in known_sections)

    settings = Settings(
        # Variables de entorno (con defaults para desarrollo)
        telegram_token=os.environ.get("TELEGRAM_TOKEN", ""),
        telegram_allowed_user_id=_primary_user_id(
            os.environ.get("TELEGRAM_ALLOWED_USER_ID", "0")
        ),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        google_calendar_creds=os.environ.get(
            "GOOGLE_CALENDAR_CREDS", "/credentials/google-oauth.json"
        ),
        vault_path=Path(os.environ.get("VAULT_PATH", "/vault")),
        chroma_data_dir=Path(os.environ.get("CHROMA_DATA_DIR", "/app/data/chroma")),
        # Secciones de config.yaml
        rag=_build_section(RagConfig, raw.get("rag"), "rag", unknown),
        links=_build_section(LinksConfig, raw.get("links"), "links", unknown),
        vault_seed=_build_vault_seed(raw.get("vault_seed"), unknown),
        vault=_build_section(VaultConfig, raw.get("vault"), "vault", unknown),
        whisper=_build_section(WhisperConfig, raw.get("whisper"), "whisper", unknown),
        reindex=_build_section(ReindexConfig, raw.get("reindex"), "reindex", unknown),
        sync=_build_section(SyncConfig, raw.get("sync"), "sync", unknown),
        backup=_build_section(BackupConfig, raw.get("backup"), "backup", unknown),
        documents=_build_section(
            DocumentsConfig, raw.get("documents"), "documents", unknown
        ),
        llm=_build_section(LlmConfig, raw.get("llm"), "llm", unknown),
        weekly_report=_build_weekly_report(raw.get("weekly_report"), unknown),
        tasks=_build_section(TasksConfig, raw.get("tasks"), "tasks", unknown),
        watcher=_build_section(WatcherConfig, raw.get("watcher"), "watcher", unknown),
        unknown_keys=sorted(unknown),
    )

    _validate_types(settings)

    if settings.unknown_keys:
        logger.warning(
            "config.yaml: %d clave(s) ignorada(s) por el loader (revisar tipeo o "
            "docs/configuration.md): %s",
            len(settings.unknown_keys),
            ", ".join(settings.unknown_keys),
        )

    return settings
