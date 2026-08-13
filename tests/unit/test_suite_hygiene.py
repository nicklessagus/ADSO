"""Guards sobre la salud de la propia suite de tests.

Existe por el hallazgo G15 de `docs/audit-2026-07-31.md`: los markers
`integration` y `e2e` estaban declarados en `pyproject.toml` y documentados en
`docs/testing.md`, pero ningún test los tenía aplicados. El filtro
`-m "not integration and not e2e"` de CI no excluía nada — corría los 618
tests— y el día que alguien aplicara los markers a mano, 193 tests iban a
desaparecer de CI en silencio, sin que nada fallara.

La solución es asignar el marker por directorio en un hook de `conftest.py`.
Estos tests verifican que esa asignación siga siendo correcta y exhaustiva.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import TESTS_ROOT, marker_for_path


# Directorios de tests y el marker que les corresponde. `unit` no lleva marker:
# es el default y lo que CI usaría si alguna vez se quiere un gate rápido.
_EXPECTED_DIRS = {"unit": None, "integration": "integration", "e2e": "e2e"}


class TestMarkerForPath:

    def test_e2e_dir(self) -> None:
        assert marker_for_path(TESTS_ROOT / "e2e" / "test_bot_extra.py") == "e2e"

    def test_integration_dir(self) -> None:
        assert marker_for_path(
            TESTS_ROOT / "integration" / "test_capture_flow.py"
        ) == "integration"

    def test_unit_dir_sin_marker(self) -> None:
        assert marker_for_path(TESTS_ROOT / "unit" / "test_config.py") is None

    def test_conftest_raiz_sin_marker(self) -> None:
        assert marker_for_path(TESTS_ROOT / "conftest.py") is None

    def test_path_fuera_de_tests(self) -> None:
        """Un path ajeno a tests/ no debe romper el hook ni marcarse."""
        assert marker_for_path(Path("/etc/passwd")) is None

    def test_subdirectorio_anidado_hereda_el_marker(self) -> None:
        """Si algún día e2e/ crece en subcarpetas, siguen siendo e2e."""
        assert marker_for_path(TESTS_ROOT / "e2e" / "media" / "test_x.py") == "e2e"


class TestSuiteLayout:

    def test_todos_los_archivos_de_test_resuelven_su_marker(self) -> None:
        """Recorre la suite real: cada test file cae en un directorio mapeado.

        Guarda contra el modo de falla original — un archivo de test que no
        recibe el marker que su ubicación implica.
        """
        for path in TESTS_ROOT.rglob("test_*.py"):
            top = path.relative_to(TESTS_ROOT).parts[0]
            assert top in _EXPECTED_DIRS, (
                f"{path} vive en tests/{top}/, que no tiene marker asignado. "
                f"Agregarlo a _DIR_MARKERS en tests/conftest.py y a "
                f"_EXPECTED_DIRS acá, o mover el test."
            )
            assert marker_for_path(path) == _EXPECTED_DIRS[top]

    def test_no_hay_directorios_de_test_sin_decidir(self) -> None:
        """Un directorio nuevo con tests obliga a decidir su marker.

        Sin esto, `tests/smoke/` aparecería sin marker y quedaría fuera de
        cualquier filtro `-m` sin que nadie se entere.
        """
        con_tests = {
            p.relative_to(TESTS_ROOT).parts[0]
            for p in TESTS_ROOT.rglob("test_*.py")
        }
        assert con_tests <= set(_EXPECTED_DIRS), (
            f"Directorios de test sin marker decidido: "
            f"{sorted(con_tests - set(_EXPECTED_DIRS))}"
        )
