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
content_extraction:
  engine: trafilatura
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
  max_web_tokens: 4000
  max_paper_tokens: 64000
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
        assert s.content_extraction.engine == "trafilatura"
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

    def test_invalid_engine(self, config_dir: Path) -> None:
        path = _write_config(config_dir, "content_extraction:\n  engine: beautifulsoup\n")
        with pytest.raises(ConfigError, match="content_extraction.engine"):
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
