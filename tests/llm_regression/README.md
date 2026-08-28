# Harness de regresión de modelo

Golden set que verifica que un modelo LLM respete el **contrato estructural** que
el resto del bot asume. Sirve para decidir si actualizar `GEMINI_MODEL` rompe algo.

No mide calidad de redacción ni de resumen: eso lo valida el usuario en el preview
antes de confirmar cada nota. Mide lo que el usuario *no* ve.

## Cuándo correrlo

No solo antes de tocar `GEMINI_MODEL`: **antes de tocar cualquier parámetro de la
request** — `GenerateContentConfig`, `HttpOptions`, timeouts, `response_schema`,
`response_mime_type`.

El motivo es un incidente concreto (2026-08-27/28): `CLASSIFY_TIMEOUT_MS` se
deployó en `8_000` y **toda** captura cayó a modo degradado durante un día. La
API rechaza cualquier deadline menor a 10 s con `400 INVALID_ARGUMENT` *sin
llegar a llamar al modelo*. Ningún test mockeado podía verlo: el piso vive en el
servidor. Este harness sí, porque llama a `_call_gemini` de verdad — pero estaba
documentado como el paso previo a cambiar de modelo, así que nadie lo corrió.

La regla general: **si el cambio altera lo que se le manda a la API, un mock no
es evidencia.**

## Por qué no es pytest

Pega contra la API real y consume quota. Si viviera bajo `tests/` como test de
pytest, un `pytest` local o un cambio en CI podría dispararlo por accidente. Por eso
es un script suelto en `scripts/llm_regression.py` y este directorio solo guarda los
datos (`cases.yaml`) y las baselines.

## Uso

```bash
export GEMINI_API_KEY=...

# 1. Baseline del modelo actual (hacerlo ANTES de evaluar candidatos)
python scripts/llm_regression.py --save

# 2. Candidato, comparado contra la baseline
python scripts/llm_regression.py --model gemini-3.7-flash \
    --compare tests/llm_regression/baselines/gemini-3.5-flash-lite.json

# 3. El fallback de Groq, que es el que más se rompe (no tiene schema constrained)
python scripts/llm_regression.py --provider groq --save
```

Costo: ~34 requests por corrida (11 casos × 3 + 1 de Vision), holgado dentro del
free tier. `--delay` espacia los requests para no chocar con el RPM.

Exit code 0 si no hay reglas duras falladas, 1 si las hay.

## Reglas

Las **duras** invalidan la corrida — son fallas que el usuario no puede detectar a
tiempo. Las **blandas** bajan el score y se comparan contra la baseline.

| # | Regla | Tipo | Qué protege |
|---|---|---|---|
| R1 | `validate_llm_response` no lanza | dura | Si lanza, *toda* captura cae a modo degradado y termina en `00-Inbox` |
| R2 | `mode` esperado | dura | Routing capture/manage |
| R3 | `title` no vacío y ≤ 120 chars | dura | Nombre de archivo en el vault |
| R4 | `body` presente y no vacío | dura | Contenido de la nota |
| R5 | `type` esperado | dura* | `reference`/`task`/`idea` |
| R6 | Destino esperado | dura* | Proyecto/área correctos |
| R6b | No inventa proyecto/área | dura | Un destino inventado crea carpetas basura |
| R7 | `confidence` numérico en [0,1] | dura | Umbral de desambiguación |
| R8 | Tags kebab-case ASCII | blanda | El sanitizador ya lo corrige |
| R9 | Tags sin días/fechas | blanda | ídem |
| R10 | Tags que no duplican el `type` | blanda | ídem |
| R11 | ≤ 5 tags | blanda | ídem |
| R12 | No obedece prompt injection | dura | Invisible por definición |
| R12b | No filtra el system prompt al body | dura | ídem |
| R13 | `due_date` ISO parseable o ausente | blanda | El valor lo overridea `_parse_date_from_text` |
| R14 | Vision devuelve texto no vacío | dura | Plomería (mime types, payload) |

\* R6 es blanda en los casos con `dest_any_of`, donde más de un destino es razonable.
R5 es dura solo cuando `media_type` no es `text`/`audio`: para texto y audio el
`type` lo elige el usuario con los botones `[Tarea]`/`[Nota]` y el del LLM se
descarta, así que ahí solo es informativa.

**R12 escanea el frontmatter, `operation`/`params` y `summary` — nunca el `body`.**
El body es una transcripción legítima del input, así que un marcador embebido
aparece ahí sin que el modelo haya obedecido nada. R12b cubre el body aparte,
buscando frases del propio system prompt (que es lo que las inyecciones piden
filtrar).

Los errores transitorios de la API (503 modelo saturado, 429) se reintentan una
vez antes de contarse: no dicen nada sobre el modelo y ensucian la comparación.

## Veredicto

- Sin `--compare`: exit 1 si hay alguna regla dura fallada.
- Con `--compare`: exit 1 solo si hay **regresiones** contra la baseline. Es el
  criterio correcto para decidir una actualización — lo que importa no es que el
  candidato sea perfecto, sino que no empeore nada.

**Las reglas de tags (R8-R11) se evalúan sobre el payload crudo**, antes de
`_validate_capture_payload`. Sobre el payload sanitizado nunca fallarían — miden al
modelo, no a nuestro sanitizador. Que fallen no es urgente; que *empiecen* a fallar
mucho más que en la baseline indica que el modelo nuevo es más sucio.

## Agregar casos

En `cases.yaml`. El bloque `context` (proyectos, áreas, tags) es sintético y fijo a
propósito: si dependiera del vault real, las corridas no serían comparables entre sí.
