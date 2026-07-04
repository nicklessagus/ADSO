# Fase 7 — Consultas RAG en lenguaje natural: Diseño

Estado: **propuesta** (pre-implementación). Este documento define el diseño; el
código se implementa por etapas (ver *Plan de implementación*).

---

## Objetivo

Permitir recuperar conocimiento del vault mediante consultas en lenguaje natural
("qué tengo sobre difusión de calor", "papers pendientes de tesis", "mostrá lo
relacionado con esta idea"). El bot recupera las notas relevantes, las presenta
con links `obsidian://`, y **opcionalmente** sintetiza una respuesta en lenguaje
natural fundamentada exclusivamente en esas notas.

### Regla de oro (no negociable)

**ADSO es un sistema de retrieval, no de razonamiento.** En modo consulta:

- Recupera y presenta notas relevantes del vault.
- **No agrega conocimiento propio ni opina** sobre el contenido.
- La síntesis (cuando se pide) se fundamenta *solo* en las notas recuperadas,
  cita las fuentes por `[[wikilink]]`, y si las notas no cubren la pregunta lo
  dice explícitamente ("no encontré nada relevante en el vault").

Esto no es solo una restricción de producto: es la línea que separa un asistente
de conocimiento verificable de un chatbot que alucina sobre notas personales.

---

## El driver central: presupuesto de generación (20 RPD)

El free tier de Gemini da **~20 requests/día de generación** (`gemini-2.5-flash-lite`).
Este es el condicionante que define toda la arquitectura. La clave es una
asimetría de costos:

| Operación | Costo | Quota |
|---|---|---|
| Retrieval (embedding de la consulta) | 1 llamada de *embedding* | Embedding API — mucho más alta que generación |
| Búsqueda en ChromaDB | local, gratis | — |
| Expansión estructural (backlinks/outgoing) | local, gratis (filesystem) | — |
| **Síntesis** en lenguaje natural | **1 llamada de *generación*** | **~20 RPD** ← escaso |

**Decisión de diseño: retrieval-first, síntesis opt-in.**

- Una consulta normal (`/buscar X`) devuelve las notas relevantes gastando solo
  1 embedding (quota abundante), **sin tocar** el presupuesto de generación.
- La síntesis en lenguaje natural es un botón `[Sintetizar]` explícito: gasta 1
  de los ~20 requests diarios **solo si el usuario lo pide**.
- La síntesis automática (sintetizar en cada consulta) queda como **opción
  configurable a futuro** (`rag.auto_synthesize`, default `false`) — decidido con
  el usuario: probar on-demand primero, dejar la puerta abierta a la automática.

---

## Capacidades ya disponibles (no hay que construirlas)

La infraestructura de retrieval ya existe; Fase 7 es sobre todo orquestación.

- **`embeddings.query_similar(query_text, n_results, threshold, where)`**
  (`embeddings.py:253`) — embebe la consulta y busca en ChromaDB. Ya soporta:
  - `threshold` → filtro por similitud mínima.
  - `where` → filtro de metadata de ChromaDB (**esto habilita el scope por
    `project`/`area` sin código extra**).
  - Devuelve `list[SimilarNote]` con `note_id`, `distance`, `metadata`, `snippet`.
- **`vault_search.get_backlinks(note_name, vault_path)`** (`vault_search.py:130`)
  y **`get_wikilinks(note_path)`** (`vault_search.py:460`) — para la expansión
  estructural desde un nodo (backlinks entrantes + wikilinks salientes). Sin LLM.
- **`reporters.py`** — generador de informes `.md` con header ASCII + versión +
  fecha, `_note_block()` para renderizar notas, y `_llm_synthesis()` como patrón
  de llamada a Gemini de texto libre (a adaptar con prompt grounded).
- **Config `rag.*`** (`config.py:22`): `similarity_threshold: 0.75`,
  `max_results: 10`, `max_expansion_depth: 2`.
- **Botones de desambiguación** (`constants.py:15`): `CB_DISAMBIG_QUERY` ya está
  cableado — hoy responde "disponible en próxima versión" (`callbacks.py:174`).
  Es el punto de entrada natural a habilitar.

> Nota: `knowledge_query.py` figura en la doc como módulo existente pero **aún no
> está creado**. Este diseño lo define.

---

## Arquitectura

```
consulta (comando o botón)
    │
    ├─ [1] intención: ¿es una consulta? (/buscar explícito · botón · heurística)
    │
    ├─ [2] scope: ¿todo el vault o project/area? (botones si falta)
    │
    ▼
knowledge_query.retrieve(query, scope, vault_path, embeddings)   ← sin LLM
    │   embed(query) → ChromaDB query_similar(threshold, where=scope)
    │   → top-K SimilarNote
    │
    ├─ [3] (opcional) expansión estructural desde los top hits
    │       get_backlinks + get_wikilinks  → notas relacionadas
    │
    ▼
QueryResult { notas: [ScoredNote], expandido: bool, scope }
    │
    ├─ [4a] presentación retrieval-only (DEFAULT, 0 gen calls)
    │        cortos (2-3): inline + [Sintetizar] [Informe .md]
    │        largos: informe .md como documento
    │
    └─ [4b] síntesis on-demand (botón [Sintetizar], 1 gen call)
             knowledge_query.synthesize(query, notas)   ← grounded, cita fuentes
             → texto inline + notas fuente debajo + fallback Groq
```

---

## Nuevo módulo: `knowledge_query.py`

Responsabilidad única: orquestar retrieval semántico + estructural y (opcional)
síntesis grounded. **No** maneja Telegram (eso queda en un handler nuevo).

```python
@dataclass
class ScoredNote:
    note_id: str
    path: Path
    title: str
    snippet: Optional[str]
    similarity: float          # 1 - distance, normalizado 0-1
    via: str                   # "semantic" | "backlink" | "outgoing"

@dataclass
class QueryResult:
    query: str
    notes: list[ScoredNote]    # ordenadas por relevancia, deduplicadas
    scope: Optional[dict]      # {"project": ...} | {"area": ...} | None
    expanded: bool

async def retrieve(
    query: str,
    vault_path: Path,
    embeddings: EmbeddingsClient,
    scope: Optional[dict] = None,        # → where de ChromaDB
    threshold: Optional[float] = None,   # default rag.similarity_threshold
    max_results: int = 10,
    expand: bool = False,                # expansión estructural
) -> QueryResult: ...

async def synthesize(
    query: str,
    notes: list[ScoredNote],
    vault_path: Path,
) -> Optional[str]:
    """Síntesis grounded. 1 llamada de generación. None si falla (fallback Groq)."""
```

- `retrieve` es puro retrieval (embedding + ChromaDB + filesystem). Testeable con
  ChromaDB mockeado.
- La deduplicación al fusionar semántico + estructural es por `note_id`; gana la
  mayor `similarity`, y se registra el `via` de origen.

---

## Detección de intención (etapas)

Decidido con el usuario: **`/buscar` explícito + botón primero; heurística
después.** Nunca re-habilitar `mode=query` en el clasificador general (gastaría
la quota de generación en cada mensaje, incluso capturas).

1. **`/buscar <consulta>`** — comando explícito, cero ambigüedad, cero LLM. Es el
   MVP. Si no hay texto, el bot pide la consulta.
2. **Botón `[Buscar en vault]`** — el `CB_DISAMBIG_QUERY` que ya existe. Aparece
   cuando el usuario manda algo que *podría* ser consulta y el bot ofrece
   `[Guardar como nota]` `[Buscar en vault]`. Deja de responder "próxima versión"
   y llama al pipeline con el texto pendiente.
3. **Heurística (etapa posterior)** — detectar consultas por patrón sin comando:
   arranca con "qué tengo/hay sobre", "mostrá/mostrame", "buscá", "dame todo…",
   termina en "?". Solo dispara la *desambiguación* (no asume): muestra
   `[Guardar como nota]` `[Buscar en vault]`. Falsos positivos cuestan un botón,
   no una acción.

---

## Síntesis grounded (on-demand)

Botón `[Sintetizar]` en el resultado de una consulta. Gasta 1 llamada de
generación. Prompt (patrón de `reporters._llm_synthesis`, endurecido):

- Sistema: "Sos un asistente de retrieval de un vault personal. Respondé la
  pregunta **usando SOLO** el contenido de las notas provistas. Citá las notas
  que uses por su `[[wikilink]]`. **No agregues información que no esté en las
  notas.** Si las notas no responden la pregunta, decí exactamente que no
  encontraste información relevante en el vault. Español, conciso."
- Las notas van envueltas en `<input>` (mismo blindaje que la captura:
  neutralización de tags de control vía el `classify`/helper de `llm_client`).
- **Siempre** se muestran las notas fuente con su link debajo de la síntesis —
  el usuario verifica.
- **Fallback a Groq** si Gemini está caído/sin quota (mismo patrón que `classify`).
- Config `rag.auto_synthesize: false` (nuevo) reservado para, a futuro,
  sintetizar automáticamente sin botón.

---

## Scope y expansión

- **Scope**: si la consulta ya trae scope ("papers pendientes de **tesis**"), la
  heurística/handler arma `where={"project": "tesis"}`. Si no, y el resultado es
  ambiguo o amplio, se ofrecen botones `[Todo]` `[Proyecto…]` `[Área…]` (mismo
  patrón que `/reporte`). El `where` va directo a `query_similar`.
- **Expansión desde nodo**: cuando el usuario pide "mostrá lo relacionado con X"
  o desde un resultado, el bot pregunta `[Solo relaciones directas]`
  `[Expandir un grado más]` (hasta `rag.max_expansion_depth`). Usa
  `get_backlinks` + `get_wikilinks` en paralelo. Todo local, sin generación.

---

## Presentación (reusa el formato ya definido)

Formato de cada ítem (idéntico inline y en informe): **título · estado/área ·
snippet · link `obsidian://`** (helper `reporters._note_block`).

- **Resultados cortos (2-3)**: inline + botones `[Sintetizar]`
  `[Ver referencias completas]` `[Generar informe .md]`.
- **Resultados largos / expansión**: informe `.md` como documento (header ASCII +
  versión + fecha vía `reporters._report_header`).
- **Con síntesis**: texto de síntesis primero, notas fuente con links debajo,
  botones para profundizar.

---

## Config nueva

```yaml
rag:
  similarity_threshold: 0.75     # ya existe
  max_results: 10                # ya existe
  max_expansion_depth: 2         # ya existe
  auto_synthesize: false         # NUEVO — sintetizar sin botón (futuro)
  snippet_chars: 200             # NUEVO — largo del snippet en resultados
```

---

## Constantes / keyboards nuevas

- `constants.py`: `CB_RAG_SYNTH` (sintetizar), `CB_RAG_EXPAND_DIRECT`,
  `CB_RAG_EXPAND_MORE`, `CB_RAG_REPORT`, prefijo `CB_RAG_SCOPE_PREFIX`.
- `keyboards.py`: `build_query_result_keyboard(has_synth, scope_needed)`,
  `build_expansion_keyboard()`. Reusa el patrón de scope de los reportes.
- Comando `/buscar` registrado en `bot.py` (con `@authorized` + gate global).
- Nuevo handler `handlers/query.py` (orquesta Telegram; llama a `knowledge_query`).

---

## Plan de implementación

### Etapa 7.0 — Retrieval puro (0 gen calls) · MVP
- `knowledge_query.retrieve()` + `ScoredNote`/`QueryResult`.
- Comando `/buscar <q>` → resultados inline/`.md`.
- Cablear `CB_DISAMBIG_QUERY` al pipeline (reemplaza "próxima versión").
- Tests: retrieve con ChromaDB mockeado, umbral, vault vacío, formato de salida.

### Etapa 7.1 — Scope + expansión estructural (0 gen calls)
- `where` por project/area + botones de scope.
- `retrieve(expand=True)` con backlinks/outgoing + teclado de expansión.
- Tests: filtro de scope, dedup semántico+estructural, profundidad.

### Etapa 7.2 — Síntesis grounded on-demand (1 gen call, opt-in)
- `knowledge_query.synthesize()` + botón `[Sintetizar]` + fallback Groq.
- `rag.auto_synthesize` (flag, default false).
- Tests: prompt grounded (mock LLM), "no encontré nada", fuentes siempre visibles,
  fallback Groq.

### Etapa 7.3 — Detección de intención natural (0 gen calls)
- Heurística de patrones → desambiguación (no asume).
- Tests: patrones que disparan / que no.

---

## Impacto en RPi4

- **Retrieval**: 1 embedding remoto + query a ChromaDB (ya en uso para links
  automáticos, footprint conocido). Sin costo de CPU/RAM nuevo relevante.
- **Expansión estructural**: escaneo del vault vía `vault_search` — ya cacheado
  por `vault_cache` (mtime/size). El costo dominante (read+parse en SD lenta) se
  amortiza con el caché existente.
- **Síntesis**: opt-in, 1 request; el trabajo pesado es remoto (Gemini/Groq).
- No se agrega ningún modelo local. El presupuesto de generación (no la RPi) es
  el límite real.

---

## Preguntas abiertas

1. **Umbral de "resultado vacío"**: si nada supera `similarity_threshold`, ¿se
   muestra "no encontré nada" o se bajan los mejores N por debajo del umbral con
   aviso? (Propuesta: mostrar top-3 bajo umbral con nota "baja confianza".)
2. **Longitud de contexto para síntesis**: ¿cuántas notas/snippets se le pasan al
   LLM? Acotar para no inflar tokens (propuesta: top-5 snippets, no bodies
   completos, salvo papers donde el abstract).
3. **`last_retrieved`**: ¿registrar en el frontmatter cuándo una nota apareció en
   resultados? Habilita la "detección de conocimiento obsoleto" (idea post-Fase 8)
   pero implica escritura al vault en cada consulta — evaluar costo/beneficio.
4. **Cache de embeddings de consulta**: consultas repetidas podrían cachear el
   embedding. Probablemente innecesario dado el volumen personal — diferir.
