"""Carga y validación de configuración desde config.yaml y variables de entorno."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

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
    exclude_dirs: list[str] = field(
        default_factory=lambda: ["05-Archive", ".obsidian", ".trash"]
    )


@dataclass
class WhisperConfig:
    model: str = "base"
    model_dir: str = "/app/data/whisper"
    language: str = "es"


@dataclass
class ContentExtractionConfig:
    engine: str = "gemini"


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
    max_web_tokens: int = 8000
    max_paper_tokens: int = 128000
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
    anthropic_api_key: str = ""
    google_calendar_creds: str = "/credentials/google-oauth.json"
    vault_path: Path = Path("/vault")
    chroma_data_dir: Path = Path("/app/data/chroma")

    # Secciones de config.yaml
    rag: RagConfig = field(default_factory=RagConfig)
    links: LinksConfig = field(default_factory=LinksConfig)
    vault_seed: VaultSeedConfig = field(default_factory=VaultSeedConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    content_extraction: ContentExtractionConfig = field(
        default_factory=ContentExtractionConfig
    )
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


def _build_vault_seed(data: dict[str, Any] | None) -> VaultSeedConfig:
    """Construye VaultSeedConfig validando que cada ítem tenga description."""
    if data is None:
        return VaultSeedConfig()

    projects: list[VaultSeedItem] = []
    for item in data.get("projects", []):
        if not isinstance(item, dict) or "name" not in item:
            raise ConfigError("vault_seed.projects: cada ítem requiere 'name'")
        if "description" not in item or not item["description"]:
            raise ConfigError(
                f"vault_seed.projects: '{item['name']}' requiere 'description'"
            )
        projects.append(VaultSeedItem(name=item["name"], description=item["description"]))

    areas: list[VaultSeedItem] = []
    for item in data.get("areas", []):
        if not isinstance(item, dict) or "name" not in item:
            raise ConfigError("vault_seed.areas: cada ítem requiere 'name'")
        if "description" not in item or not item["description"]:
            raise ConfigError(
                f"vault_seed.areas: '{item['name']}' requiere 'description'"
            )
        areas.append(VaultSeedItem(name=item["name"], description=item["description"]))

    return VaultSeedConfig(projects=projects, areas=areas)


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
    if isinstance(sections, list):
        sections = {s: True for s in sections}

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

    # Validar rangos
    if not 0.0 <= settings.rag.similarity_threshold <= 1.0:
        raise ConfigError("rag.similarity_threshold debe estar entre 0.0 y 1.0")
    if not 0.0 <= settings.links.similarity_threshold <= 1.0:
        raise ConfigError("links.similarity_threshold debe estar entre 0.0 y 1.0")
    if not 0.0 <= settings.llm.disambiguation_threshold <= 1.0:
        raise ConfigError("llm.disambiguation_threshold debe estar entre 0.0 y 1.0")

    valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    if settings.weekly_report.day not in valid_days:
        raise ConfigError(f"weekly_report.day: '{settings.weekly_report.day}' no es válido")

    valid_whisper = {"tiny", "base"}
    if settings.whisper.model not in valid_whisper:
        raise ConfigError(f"whisper.model: '{settings.whisper.model}' no es válido (tiny | base)")

    valid_engines = {"gemini", "trafilatura"}
    if settings.content_extraction.engine not in valid_engines:
        raise ConfigError(
            f"content_extraction.engine: '{settings.content_extraction.engine}' "
            f"no es válido (gemini | trafilatura)"
        )


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
    if config_path is None:
        config_path = Path("config.yaml")
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        raise ConfigError(f"config.yaml no encontrado en {config_path.resolve()}")

    with open(config_path, "r", encoding="utf-8") as f:
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
        telegram_allowed_user_id=int(os.environ.get("TELEGRAM_ALLOWED_USER_ID", "0")),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        google_calendar_creds=os.environ.get(
            "GOOGLE_CALENDAR_CREDS", "/credentials/google-oauth.json"
        ),
        vault_path=Path(os.environ.get("VAULT_PATH", "/vault")),
        chroma_data_dir=Path(os.environ.get("CHROMA_DATA_DIR", "/app/data/chroma")),
        # Secciones de config.yaml
        rag=_build_section(RagConfig, raw.get("rag"), "rag", unknown),
        links=_build_section(LinksConfig, raw.get("links"), "links", unknown),
        vault_seed=_build_vault_seed(raw.get("vault_seed")),
        vault=_build_section(VaultConfig, raw.get("vault"), "vault", unknown),
        whisper=_build_section(WhisperConfig, raw.get("whisper"), "whisper", unknown),
        content_extraction=_build_section(
            ContentExtractionConfig,
            raw.get("content_extraction"),
            "content_extraction",
            unknown,
        ),
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
