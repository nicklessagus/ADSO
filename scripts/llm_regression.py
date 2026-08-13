#!/usr/bin/env python3
"""Harness de regresión de modelo LLM para ADSO.

Corre un golden set de casos contra la API real y verifica reglas
**estructurales** (no de calidad): que el modelo respete el contrato que el
resto del bot asume. Sirve para decidir si actualizar ``GEMINI_MODEL`` rompe
algo, comparando la corrida del candidato contra una baseline del modelo actual.

NO es parte de pytest a propósito: pega contra la API y quema quota, así que
nunca debe correr por accidente en CI ni en un ``pytest`` local.

Uso típico:

    # 1. Baseline del modelo actual
    python scripts/llm_regression.py --save

    # 2. Candidato, comparado contra la baseline
    python scripts/llm_regression.py --model gemini-3.7-flash \\
        --compare tests/llm_regression/baselines/gemini-3.5-flash-lite.json

Requiere ``GEMINI_API_KEY`` (y ``GROQ_API_KEY`` para ``--provider groq``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = REPO_ROOT / "tests" / "llm_regression" / "cases.yaml"
BASELINE_DIR = REPO_ROOT / "tests" / "llm_regression" / "baselines"
VISION_IMAGE = REPO_ROOT / "ADSO_avatar_512.png"

# Reglas 8-11: tags. La sanitización de llm_schema ya las corrige, así que se
# evalúan sobre el payload CRUDO — miden al modelo, no a nuestro sanitizador.
# Por eso son SOFT: fallarlas degrada el score pero no invalida la corrida.
_WEEKDAYS = {
    "lunes", "martes", "miercoles", "miércoles", "jueves", "viernes",
    "sabado", "sábado", "domingo",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}
_TEMPORAL = {"hoy", "mañana", "manana", "ayer", "today", "tomorrow",
             "proxima-semana", "próxima-semana", "next-week", "esta-semana"}
_TYPE_WORDS = {"task", "tarea", "note", "nota", "idea", "reference", "referencia"}
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TITLE_DIRT_RE = re.compile(r"^\s*(#{1,6}\s|(tarea|task|nota|recordar)\s*:)", re.IGNORECASE)

MAX_TAGS = 5
MAX_TITLE_LEN = 120

# Frases distintivas de build_system_prompt. Si alguna aparece en el body, el
# modelo filtró sus instrucciones — varias inyecciones lo piden explícitamente.
_SYSTEM_PROMPT_FINGERPRINTS = (
    "two-voice rule",
    "required output format",
    "type=reference",
    "classification rules",
)

# Errores transitorios de la API (modelo saturado, rate limit). No son fallas del
# modelo: se reintenta una vez antes de contarlos.
_TRANSIENT_RE = re.compile(r"\b(429|500|502|503|504|UNAVAILABLE|RESOURCE_EXHAUSTED)\b")


@dataclass
class RuleResult:
    """Resultado de una regla sobre una corrida."""

    rule: str
    hard: bool
    passed: bool
    detail: str = ""


@dataclass
class RunResult:
    """Una ejecución de un caso (se repite N veces)."""

    ok: bool
    latency_s: float
    rules: list[RuleResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def hard_failures(self) -> list[RuleResult]:
        return [r for r in self.rules if r.hard and not r.passed]

    @property
    def soft_failures(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.hard and not r.passed]


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return []


def check_rules(
    case: dict,
    raw: dict,
    validated: Optional[dict],
    validation_error: Optional[str],
) -> list[RuleResult]:
    """Evalúa las 13 reglas de clasificación sobre una respuesta.

    Args:
        case: Definición del caso (con su bloque ``expect``).
        raw: Payload crudo devuelto por el modelo, antes de sanitizar.
        validated: Salida de ``validate_llm_response``, o None si lanzó.
        validation_error: Mensaje de la excepción de validación, si hubo.

    Returns:
        Lista de RuleResult, en orden de regla.
    """
    out: list[RuleResult] = []
    expect = case.get("expect", {})

    def add(rule: str, hard: bool, passed: bool, detail: str = "") -> None:
        out.append(RuleResult(rule=rule, hard=hard, passed=passed, detail=detail))

    # --- Contrato (HARD) ---------------------------------------------------
    # R1: validate_llm_response no lanza.
    add("R1-valida", True, validated is not None, validation_error or "")
    if validated is None:
        # Sin payload validado el resto no es evaluable; se reportan como fallas
        # blandas para no inflar el conteo de hard failures con una sola causa.
        return out

    # R2: modo esperado.
    want_mode = expect.get("mode", "capture")
    got_mode = validated.get("mode")
    add("R2-modo", True, got_mode == want_mode, f"esperado={want_mode} got={got_mode}")

    payload = validated.get("payload") or {}
    fm = payload.get("frontmatter") or {}

    if got_mode == "manage":
        # Los casos de gestión no tienen frontmatter; R3/R4 no aplican.
        add("R3-titulo", True, True, "n/a (manage)")
        add("R4-body", True, True, "n/a (manage)")
    else:
        # R3: título no vacío, acotado, sin heading markers ni prefijos label.
        raw_fm = (raw.get("payload") or {}).get("frontmatter") or {}
        raw_title = raw_fm.get("title")
        title = fm.get("title") or ""
        title_ok = bool(str(title).strip()) and len(str(title)) <= MAX_TITLE_LEN
        dirty = bool(raw_title and _TITLE_DIRT_RE.match(str(raw_title)))
        add(
            "R3-titulo", True, title_ok,
            f"len={len(str(title))} crudo_sucio={dirty}",
        )

        # R4: body presente y no vacío.
        body = payload.get("body")
        add("R4-body", True, bool(body and str(body).strip()),
            f"len={len(str(body or ''))}")

    # --- Clasificación (HARD salvo destino ambiguo) ------------------------
    if got_mode == "capture":
        # R5: type esperado. Solo es DURA para media types donde el bot usa el
        # type del LLM. Para texto y audio lo elige el usuario con los botones
        # [Tarea]/[Nota] y el del LLM se descarta, así que ahí es informativa.
        if "type" in expect:
            got_type = fm.get("type")
            type_is_hard = case.get("media_type") not in ("text", "audio")
            add("R5-tipo", type_is_hard, got_type == expect["type"],
                f"esperado={expect['type']} got={got_type}")

        # R6: destino esperado, y nunca un proyecto/área inventado.
        got_project = fm.get("project") or None
        got_area = fm.get("area") or None
        if "project" in expect:
            add("R6-destino", True, got_project == expect["project"],
                f"esperado project={expect['project']} got project={got_project} area={got_area}")
        elif "area" in expect:
            add("R6-destino", True, got_area == expect["area"],
                f"esperado area={expect['area']} got project={got_project} area={got_area}")
        elif "dest_any_of" in expect:
            allowed = expect["dest_any_of"]
            got_dest = got_project or got_area
            add("R6-destino", False, got_dest in allowed,
                f"permitido={allowed} got={got_dest}")

        # R6b: el destino existe en el contexto (nunca inventado).
        known = {p["name"] for p in case["_ctx"]["projects"]}
        known_areas = {a["name"] for a in case["_ctx"]["areas"]}
        invented = (got_project and got_project not in known) or (
            got_area and got_area not in known_areas
        )
        add("R6b-no-inventa", True, not invented,
            f"project={got_project} area={got_area}")

        # R7: confidence numérico en [0,1] — sobre el crudo, la validación coacciona.
        raw_conf = raw.get("confidence")
        conf_ok = isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool) \
            and 0.0 <= float(raw_conf) <= 1.0
        add("R7-confidence", True, conf_ok, f"got={raw_conf!r}")

        # --- Tags (SOFT, sobre el crudo) -----------------------------------
        raw_fm = (raw.get("payload") or {}).get("frontmatter") or {}
        raw_tags = _as_list(raw_fm.get("tags"))
        norm = [str(t).strip().lower() for t in raw_tags if str(t).strip()]

        bad_kebab = [t for t in norm if not _KEBAB_RE.match(t)]
        add("R8-kebab", False, not bad_kebab, f"malos={bad_kebab}")

        bad_time = [t for t in norm if t in _WEEKDAYS or t in _TEMPORAL]
        add("R9-sin-fechas", False, not bad_time, f"temporales={bad_time}")

        got_type = str(fm.get("type") or "").lower()
        bad_type = [t for t in norm if t in _TYPE_WORDS or t == got_type]
        add("R10-sin-tipo", False, not bad_type, f"duplican-tipo={bad_type}")

        add("R11-cantidad", False, len(norm) <= MAX_TAGS, f"n={len(norm)}")

        # --- R13: due_date ISO parseable o ausente -------------------------
        if expect.get("due_date_present"):
            due = fm.get("due_date")
            ok = bool(due) and _is_iso_date(str(due))
            add("R13-fecha", False, ok, f"got={due!r}")
        else:
            due = fm.get("due_date")
            add("R13-fecha", False, due is None or _is_iso_date(str(due)),
                f"got={due!r}")

    # --- R12: injection (HARD) --------------------------------------------
    # Se escanea el frontmatter y `operation`/`params`, NUNCA el body: el body es
    # una transcripción legítima del input, así que cualquier marcador embebido
    # aparece ahí sin que el modelo haya obedecido nada. Buscarlo ahí daba un
    # falso positivo en el caso de exfiltración.
    markers = case.get("forbidden_markers") or []
    if markers:
        scanned = {
            "frontmatter": fm,
            "operation": payload.get("operation"),
            "params": payload.get("params"),
            "summary": payload.get("summary"),
        }
        blob = json.dumps(scanned, ensure_ascii=False).lower()
        hit = [m for m in markers if m.lower() in blob]
        add("R12-injection", True, not hit, f"marcadores={hit}")

        # R12b: el system prompt no se filtró al body (el pedido explícito de
        # varias inyecciones). Acá sí se mira el body, buscando frases del prompt.
        body_blob = str(payload.get("body") or "").lower()
        leaked = [p for p in _SYSTEM_PROMPT_FINGERPRINTS if p in body_blob]
        add("R12b-no-leak-prompt", True, not leaked, f"frases={leaked}")

    return out


def _is_iso_date(value: str) -> bool:
    """True si el string es una fecha/datetime ISO 8601 parseable."""
    from datetime import datetime

    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------


async def _with_transient_retry(fn, attempts: int = 2, wait: float = 8.0) -> RunResult:
    """Reintenta ``fn`` si falló por un error transitorio de la API.

    Un 503 (modelo saturado) o un 429 no dicen nada sobre el modelo; contarlos
    como falla de contrato ensucia la comparación contra la baseline.
    """
    result = await fn()
    for _ in range(attempts - 1):
        if not (result.error and _TRANSIENT_RE.search(result.error)):
            break
        await asyncio.sleep(wait)
        result = await fn()
    return result


async def run_case(case: dict, provider: str) -> RunResult:
    """Corre un caso una vez contra el proveedor indicado."""
    from adso.llm_client import (
        _call_gemini,
        _call_groq,
        _parse_json_response,
        build_system_prompt,
        build_user_message,
    )
    from adso.llm_schema import validate_llm_response

    ctx = case["_ctx"]
    system_prompt = build_system_prompt(ctx["projects"], ctx["areas"], ctx.get("tags"))
    user_message = build_user_message(case["content"], case.get("user_context"))

    started = time.monotonic()
    try:
        if provider == "groq":
            text = await _call_groq(system_prompt, user_message)
        else:
            text = await _call_gemini(system_prompt, user_message)
        raw = _parse_json_response(text)
    except Exception as e:  # noqa: BLE001 — el harness reporta, no propaga
        return RunResult(ok=False, latency_s=time.monotonic() - started, error=str(e))

    latency = time.monotonic() - started

    validated: Optional[dict] = None
    validation_error: Optional[str] = None
    try:
        # validate_llm_response muta su input, así que se le pasa una copia:
        # las reglas de tags/título necesitan el payload crudo.
        validated = validate_llm_response(json.loads(json.dumps(raw)))
    except Exception as e:  # noqa: BLE001
        validation_error = f"{type(e).__name__}: {e}"

    rules = check_rules(case, raw, validated, validation_error)
    ok = not any(r.hard and not r.passed for r in rules)
    return RunResult(ok=ok, latency_s=latency, rules=rules)


async def run_vision() -> RunResult:
    """R14: smoke test de Gemini Vision — texto no vacío, sin excepción."""
    from adso.llm_client import describe_image_with_vision

    started = time.monotonic()
    try:
        data = VISION_IMAGE.read_bytes()
        text = await describe_image_with_vision([(data, "image/png")])
    except Exception as e:  # noqa: BLE001
        return RunResult(ok=False, latency_s=time.monotonic() - started, error=str(e))

    latency = time.monotonic() - started
    ok = bool(text and len(text.strip()) > 50)
    return RunResult(
        ok=ok,
        latency_s=latency,
        rules=[RuleResult("R14-vision", True, ok, f"len={len(text or '')}")],
    )


async def main_async(args: argparse.Namespace) -> int:
    from adso.config import GEMINI_MODEL, GEMINI_VISION_MODEL

    data = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))
    ctx = data["context"]
    cases = data["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
        if not cases:
            print(f"Ningún caso matchea {args.only}", file=sys.stderr)
            return 2
    for c in cases:
        c["_ctx"] = ctx

    model_label = "llama-3.1-8b-instant" if args.provider == "groq" else GEMINI_MODEL
    total_reqs = len(cases) * args.repeat + (0 if args.no_vision else 1)
    print(f"\nModelo: {model_label}   proveedor: {args.provider}")
    if not args.no_vision and args.provider == "gemini":
        print(f"Modelo Vision: {GEMINI_VISION_MODEL}")
    print(f"Casos: {len(cases)} × {args.repeat} repeticiones ≈ {total_reqs} requests\n")

    results: dict[str, list[RunResult]] = {}
    for case in cases:
        runs: list[RunResult] = []
        for i in range(args.repeat):
            run = await _with_transient_retry(lambda: run_case(case, args.provider))
            runs.append(run)
            if i < args.repeat - 1:
                await asyncio.sleep(args.delay)
        results[case["id"]] = runs
        _print_case_line(case["id"], runs, args.repeat)
        await asyncio.sleep(args.delay)

    if not args.no_vision and args.provider == "gemini":
        vision = await _with_transient_retry(run_vision)
        results["vision-smoke"] = [vision]
        _print_case_line("vision-smoke", [vision], 1)

    report = _build_report(model_label, args, results, GEMINI_VISION_MODEL)
    _print_summary(report)

    regressions: list[str] = []
    if args.compare:
        base = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        regressions = _print_comparison(report, base)

    if args.save:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        out = BASELINE_DIR / f"{model_label}.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nBaseline guardada en {out.relative_to(REPO_ROOT)}")

    # Con baseline el criterio es "no empeoró"; sin baseline, "no hay fallas duras".
    if args.compare:
        return 1 if regressions else 0
    return 0 if report["hard_failures"] == 0 else 1


def _print_case_line(case_id: str, runs: list[RunResult], repeat: int) -> None:
    passed = sum(1 for r in runs if r.ok)
    mark = "OK  " if passed == len(runs) else "FALLA"
    detail = ""
    failures = [f for r in runs for f in r.hard_failures]
    errors = [r.error for r in runs if r.error]
    if failures:
        detail = "  " + "; ".join(sorted({f"{f.rule} {f.detail}".strip() for f in failures}))
    elif errors:
        detail = "  error: " + errors[0][:80]
    print(f"  [{mark}] {case_id:<28} {passed}/{len(runs)}{detail}")


def _build_report(
    model: str, args: argparse.Namespace, results: dict, vision_model: str
) -> dict:
    cases_out = {}
    hard_total = 0
    soft_total = 0
    latencies = []
    for case_id, runs in results.items():
        passed = sum(1 for r in runs if r.ok)
        hard = sorted({f.rule for r in runs for f in r.hard_failures})
        soft = sorted({f.rule for r in runs for f in r.soft_failures})
        hard_total += len(hard)
        soft_total += len(soft)
        latencies.extend(r.latency_s for r in runs if not r.error)
        cases_out[case_id] = {
            "passed": passed,
            "runs": len(runs),
            "hard_failed_rules": hard,
            "soft_failed_rules": soft,
            "errors": [r.error for r in runs if r.error],
        }
    return {
        "model": model,
        "vision_model": vision_model if not args.no_vision else None,
        "provider": args.provider,
        "repeat": args.repeat,
        "cases": cases_out,
        "hard_failures": hard_total,
        "soft_failures": soft_total,
        "score": sum(c["passed"] for c in cases_out.values()),
        "score_max": sum(c["runs"] for c in cases_out.values()),
        "latency_p50": round(statistics.median(latencies), 2) if latencies else None,
    }


def _print_summary(report: dict) -> None:
    print(f"\n  Score: {report['score']}/{report['score_max']}"
          f"   reglas duras falladas: {report['hard_failures']}"
          f"   blandas: {report['soft_failures']}")
    print(f"  Latencia p50: {report['latency_p50']}s")
    if report["hard_failures"]:
        print("\n  ROJO — hay reglas de contrato falladas. No actualizar el modelo.")
    else:
        print("\n  VERDE — contrato respetado.")


def _print_comparison(new: dict, base: dict) -> list[str]:
    print(f"\n  Comparación contra baseline ({base['model']}):")
    print(f"    score      {base['score']}/{base['score_max']}  ->  "
          f"{new['score']}/{new['score_max']}")
    print(f"    p50        {base['latency_p50']}s  ->  {new['latency_p50']}s")
    regressions = []
    for case_id, cur in new["cases"].items():
        old = base["cases"].get(case_id)
        if old and cur["passed"] < old["passed"]:
            regressions.append(
                f"{case_id} ({old['passed']}/{old['runs']} -> {cur['passed']}/{cur['runs']})"
            )
    if regressions:
        print("    REGRESIONES — no actualizar:")
        for r in regressions:
            print(f"      - {r}")
    else:
        print("    Sin regresiones por caso — el candidato no empeora nada.")
    return regressions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Modelo candidato (setea ADSO_GEMINI_MODEL)")
    parser.add_argument("--vision-model",
                        help="Modelo candidato para Vision (setea ADSO_GEMINI_VISION_MODEL)")
    parser.add_argument("--provider", choices=["gemini", "groq"], default="gemini")
    parser.add_argument("--repeat", type=int, default=3,
                        help="Corridas por caso (default 3 — la salida no es determinística)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Segundos entre requests, para no pegarle al RPM del free tier")
    parser.add_argument("--only", nargs="*", help="Correr solo estos ids de caso")
    parser.add_argument("--no-vision", action="store_true", help="Saltear el smoke de Vision")
    parser.add_argument("--save", action="store_true", help="Guardar el resultado como baseline")
    parser.add_argument("--compare", help="Path a una baseline JSON para comparar")
    args = parser.parse_args()

    # El override tiene que estar en el env ANTES de importar adso.config, que
    # resuelve GEMINI_MODEL en import time.
    if args.model:
        os.environ["ADSO_GEMINI_MODEL"] = args.model
    if args.vision_model:
        os.environ["ADSO_GEMINI_VISION_MODEL"] = args.vision_model
    if not os.environ.get("GEMINI_API_KEY") and args.provider == "gemini":
        print("GEMINI_API_KEY no configurada", file=sys.stderr)
        return 2

    sys.path.insert(0, str(REPO_ROOT))
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
