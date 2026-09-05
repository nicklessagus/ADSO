"""Tests para adso.config — carga y validación de configuración."""

from __future__ import annotations

import pytest
from pathlib import Path

from adso.config import ConfigError, load_settings


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Directorio temporal para config.yaml de test."""
    return tmp_path


def _write_config(config_dir: Path, content: str) -> Path:
    """Escribe un config.yaml temporal y retorna su path."""
    p = config_dir / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Config válido
# ---------------------------------------------------------------------------

class TestValidConfig:

    def test_load_full_config(self, config_dir: Path) -> None:
        path = _write_config(config_dir, """
rag:
  similarity_threshold: 0.8
  max_results: 5
  max_expansion_depth: 3
links:
  similarity_threshold: 0.9
  max_suggestions: 3
vault:
  exclude_dirs:
    - "05-Archive"
    - ".obsidian"
whisper:
  model: tiny
reindex:
  enabled: false
  time: "04:00"
sync:
  interval_minutes: 15
backup:
  enabled: true
  debounce_seconds: 60
documents:
  max_size_mb: 10
llm:
  degraded_retry_minutes: 15
  disambiguation_threshold: 0.8
weekly_report:
  enabled: false
  day: monday
  time: "09:00"
  stale_idea_days: 30
""")
        s = load_settings(path)
        assert s.rag.similarity_threshold == 0.8
        assert s.rag.max_results == 5
        assert s.links.similarity_threshold == 0.9
        assert s.links.max_suggestions == 3
        assert s.whisper.model == "tiny"
        assert s.reindex.enabled is False
        assert s.sync.interval_minutes == 15
        assert s.backup.debounce_seconds == 60
        assert s.backup.enabled is True
        assert s.documents.max_size_mb == 10
        assert s.llm.disambiguation_threshold == 0.8
        assert s.llm.degraded_retry_minutes == 15
        assert s.weekly_report.enabled is False
        assert s.weekly_report.day == "monday"

    def test_load_minimal_config(self, config_dir: Path) -> None:
        """Config vacío → todos los defaults aplicados."""
        path = _write_config(config_dir, "---\n")
        s = load_settings(path)
        assert s.rag.similarity_threshold == 0.75
        assert s.rag.max_results == 10
        assert s.links.similarity_threshold == 0.82
        assert s.backup.debounce_seconds == 30
        assert s.backup.enabled is True  # default
        assert s.llm.disambiguation_threshold == 0.7

    def test_backup_disabled(self, config_dir: Path) -> None:
        """backup.enabled: false deshabilita el git backup."""
        path = _write_config(config_dir, """
backup:
  enabled: false
  debounce_seconds: 30
""")
        s = load_settings(path)
        assert s.backup.enabled is False
        assert s.backup.debounce_seconds == 30

    def test_partial_config_merges_defaults(self, config_dir: Path) -> None:
        """Config parcial → se completa con defaults."""
        path = _write_config(config_dir, """
rag:
  max_results: 20
backup:
  debounce_seconds: 10
""")
        s = load_settings(path)
        assert s.rag.max_results == 20
        assert s.rag.similarity_threshold == 0.75  # default
        assert s.backup.debounce_seconds == 10
        assert s.llm.disambiguation_threshold == 0.7  # default

    def test_env_vars_loaded(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Variables de entorno se cargan correctamente."""
        monkeypatch.setenv("TELEGRAM_TOKEN", "test-token-123")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "42")
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("VAULT_PATH", "/tmp/test-vault")
        path = _write_config(config_dir, "---\n")
        s = load_settings(path)
        assert s.telegram_token == "test-token-123"
        assert s.telegram_allowed_user_id == 42
        assert s.gemini_api_key == "gemini-key"
        assert s.vault_path == Path("/tmp/test-vault")

    def test_unknown_keys_ignored(self, config_dir: Path) -> None:
        """Claves desconocidas en config.yaml se ignoran sin error."""
        path = _write_config(config_dir, """
rag:
  similarity_threshold: 0.8
  unknown_key: 42
some_future_section:
  foo: bar
""")
        s = load_settings(path)
        assert s.rag.similarity_threshold == 0.8


# ---------------------------------------------------------------------------
# Config ausente o inválido
# ---------------------------------------------------------------------------

class TestInvalidConfig:

    def test_missing_config_raises(self, config_dir: Path) -> None:
        """Config ausente → ConfigError con mensaje claro."""
        with pytest.raises(ConfigError, match="config.yaml no encontrado"):
            load_settings(config_dir / "nonexistent.yaml")

    def test_invalid_yaml_content(self, config_dir: Path) -> None:
        """YAML que no es un dict → ConfigError."""
        path = _write_config(config_dir, "- item1\n- item2\n")
        with pytest.raises(ConfigError, match="debe ser un documento YAML con claves"):
            load_settings(path)

    def test_invalid_whisper_model(self, config_dir: Path) -> None:
        path = _write_config(config_dir, "whisper:\n  model: large\n")
        with pytest.raises(ConfigError, match="whisper.model"):
            load_settings(path)

    def test_invalid_day(self, config_dir: Path) -> None:
        path = _write_config(config_dir, "weekly_report:\n  day: lunes\n")
        with pytest.raises(ConfigError, match="weekly_report.day"):
            load_settings(path)

    def test_threshold_out_of_range(self, config_dir: Path) -> None:
        path = _write_config(config_dir, "rag:\n  similarity_threshold: 1.5\n")
        with pytest.raises(ConfigError, match="similarity_threshold"):
            load_settings(path)

    def test_threshold_negative(self, config_dir: Path) -> None:
        path = _write_config(config_dir, "links:\n  similarity_threshold: -0.1\n")
        with pytest.raises(ConfigError, match="similarity_threshold"):
            load_settings(path)


# ---------------------------------------------------------------------------
# Vault seed
# ---------------------------------------------------------------------------

class TestVaultSeed:

    def test_vault_seed_loaded(self, config_dir: Path) -> None:
        path = _write_config(config_dir, """
vault_seed:
  projects:
    - name: tesis
      description: "Papers de doctorado"
    - name: adso
      description: "Bot ADSO"
  areas:
    - name: docencia
      description: "Clases y material"
""")
        s = load_settings(path)
        assert len(s.vault_seed.projects) == 2
        assert s.vault_seed.projects[0].name == "tesis"
        assert s.vault_seed.projects[0].description == "Papers de doctorado"
        assert len(s.vault_seed.areas) == 1
        assert s.vault_seed.areas[0].name == "docencia"

    def test_vault_seed_missing_description_raises(self, config_dir: Path) -> None:
        path = _write_config(config_dir, """
vault_seed:
  projects:
    - name: tesis
""")
        with pytest.raises(ConfigError, match="requiere 'description'"):
            load_settings(path)

    def test_vault_seed_empty_description_raises(self, config_dir: Path) -> None:
        path = _write_config(config_dir, """
vault_seed:
  areas:
    - name: docencia
      description: ""
""")
        with pytest.raises(ConfigError, match="requiere 'description'"):
            load_settings(path)

    def test_vault_seed_empty_is_ok(self, config_dir: Path) -> None:
        """vault_seed vacío o ausente → listas vacías, sin error."""
        path = _write_config(config_dir, "---\n")
        s = load_settings(path)
        assert s.vault_seed.projects == []
        assert s.vault_seed.areas == []


# ---------------------------------------------------------------------------
# Claves desconocidas — I2 de docs/audit-2026-07-31.md
# ---------------------------------------------------------------------------
#
# El config.yaml desplegado declaraba `weekly_report.include:` mientras el
# loader lee `weekly_report.sections:`. La clave se descartaba en silencio:
# `_build_section` filtra lo desconocido sin decir nada. Nadie se enteró porque
# nada consume weekly_report todavía. Estos tests cierran el modo de falla —
# que una clave mal escrita no haga ruido— y anclan los dos YAML del repo.

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestClavesDesconocidas:

    def test_config_yaml_local_esta_alineado_con_el_loader(self) -> None:
        """El config.yaml que `make deploy` copia a producción no debe drift.

        `config.yaml` está gitignoreado desde la publicación del repo (tiene
        configuración del usuario), así que en CI no existe y el test se
        saltea. Sigue valiendo en la máquina de desarrollo, que es donde vive
        el archivo que efectivamente se despliega.
        """
        local = REPO_ROOT / "config.yaml"
        if not local.exists():
            pytest.skip("config.yaml no está en el repo (gitignoreado) — nada que validar")
        settings = load_settings(local)
        assert settings.unknown_keys == [], (
            f"config.yaml tiene claves que el loader ignora: {settings.unknown_keys}"
        )

    def test_config_yaml_example_esta_alineado_con_el_loader(self) -> None:
        """El example es lo que copia un usuario nuevo — no puede mentir."""
        settings = load_settings(REPO_ROOT / "config.yaml.example")
        assert settings.unknown_keys == [], (
            f"config.yaml.example tiene claves que el loader ignora: "
            f"{settings.unknown_keys}"
        )

    def test_clave_desconocida_en_seccion_se_reporta(self, config_dir: Path) -> None:
        path = _write_config(config_dir, """
weekly_report:
  enabled: true
  include:
    - notes_created
""")
        settings = load_settings(path)
        assert "weekly_report.include" in settings.unknown_keys

    def test_seccion_desconocida_se_reporta(self, config_dir: Path) -> None:
        path = _write_config(config_dir, """
telemetria:
  enabled: true
""")
        settings = load_settings(path)
        assert "telemetria" in settings.unknown_keys

    def test_clave_desconocida_loguea_warning(
        self, config_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Sin log, el drift es invisible: el bot corre headless en la RPi."""
        path = _write_config(config_dir, """
llm:
  max_tokens_typo: 99
""")
        with caplog.at_level("WARNING"):
            load_settings(path)
        assert "llm.max_tokens_typo" in caplog.text

    def test_clave_desconocida_no_aborta_el_arranque(self, config_dir: Path) -> None:
        """Warning, no ConfigError.

        El bot es el path de captura del usuario: un typo en config.yaml no
        puede dejarlo sin arrancar y perder capturas. Se avisa y se sigue con
        los defaults.
        """
        path = _write_config(config_dir, """
llm:
  degraded_retry_minutes: 15
  typo_que_no_existe: 1
""")
        settings = load_settings(path)
        assert settings.llm.degraded_retry_minutes == 15

    def test_seccion_con_tipo_invalido_da_config_error(self, config_dir: Path) -> None:
        """Una sección como lista en vez de dict: ConfigError, no AttributeError."""
        path = _write_config(config_dir, """
llm:
  - degraded_retry_minutes
""")
        with pytest.raises(ConfigError, match="llm"):
            load_settings(path)


class TestContentExtractionRemovida:
    """I1 de docs/audit-2026-07-31.md — decisión 2026-08-13.

    `content_extraction` se borró: era la única sección sin fase asociada, y
    su validación (`engine` contra `{gemini, trafilatura}`) podía **abortar el
    arranque** por un campo que ningún módulo lee — con `trafilatura` que ni
    siquiera es dependencia del proyecto. El resto de la config sin consumir se
    mantiene como contrato de fases con diseño escrito.
    """

    def test_engine_invalido_ya_no_aborta_el_arranque(self, config_dir: Path) -> None:
        path = _write_config(config_dir, """
content_extraction:
  engine: loquesea
""")
        # No debe lanzar: la sección ya no existe, así que tampoco se valida.
        load_settings(path)

    def test_se_reporta_como_clave_desconocida(self, config_dir: Path) -> None:
        """Si alguien la deja en su config.yaml, el loader avisa (I2)."""
        path = _write_config(config_dir, "content_extraction:\n  engine: gemini\n")
        assert "content_extraction" in load_settings(path).unknown_keys

    def test_settings_no_expone_el_campo(self, config_dir: Path) -> None:
        settings = load_settings(_write_config(config_dir, "---\n"))
        assert not hasattr(settings, "content_extraction")
