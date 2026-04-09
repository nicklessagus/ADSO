"""Carga y validación de configuración desde config.yaml y variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Error en la carga o validación de configuración."""


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
    tasks: TasksConfig = field(default_factory=TasksConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_section(cls: type, data: dict[str, Any] | None) -> Any:
    """Construye una sub-dataclass desde un dict, ignorando claves desconocidas."""
    if data is None:
        return cls()
    known = {f.name for f in cls.__dataclass_fields__.values()}
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


def _build_weekly_report(data: dict[str, Any] | None) -> WeeklyReportConfig:
    """Construye WeeklyReportConfig con soporte para ambos formatos de sections."""
    if data is None:
        return WeeklyReportConfig()

    sections = data.get("sections", None)
    # config.yaml.example usa formato lista 'include:', configuration.md usa dict
    if isinstance(sections, list):
        # Convertir lista a dict booleano
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
        rag=_build_section(RagConfig, raw.get("rag")),
        links=_build_section(LinksConfig, raw.get("links")),
        vault_seed=_build_vault_seed(raw.get("vault_seed")),
        vault=_build_section(VaultConfig, raw.get("vault")),
        whisper=_build_section(WhisperConfig, raw.get("whisper")),
        content_extraction=_build_section(
            ContentExtractionConfig, raw.get("content_extraction")
        ),
        reindex=_build_section(ReindexConfig, raw.get("reindex")),
        sync=_build_section(SyncConfig, raw.get("sync")),
        backup=_build_section(BackupConfig, raw.get("backup")),
        documents=_build_section(DocumentsConfig, raw.get("documents")),
        llm=_build_section(LlmConfig, raw.get("llm")),
        weekly_report=_build_weekly_report(raw.get("weekly_report")),
        tasks=_build_section(TasksConfig, raw.get("tasks")),
        watcher=_build_section(WatcherConfig, raw.get("watcher")),
    )

    _validate_types(settings)

    return settings
