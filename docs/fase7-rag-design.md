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

## Presupuesto de LLM (verificado 2026-07, free tier)

> **Corrección importante:** el CLAUDE.md decía "~20 RPD observado". Eso quedó
> obsoleto — Google subió fuerte el free tier. Números verificados (jul 2026):

| Modelo | Uso | Free tier |
|---|---|---|
| `gemini-3.1-flash-lite` | generación (clasificación, síntesis) | **~1.000 RPD**, 15 RPM, 250k TPM (verificar en AI Studio) |
| `gemini-embedding-001` | embeddings (indexado + consultas) | **1.000 RPD**, 100 RPM |
| Groq (`llama-3.1-8b-instant`) | fallback de generación | ya integrado |

Implicancias para el diseño:

- **Generación ya no es el cuello de botella.** A ~1.000 RPD y 250k TPM, sintetizar
  en cada consulta es perfectamente viable. La síntesis on-demand se mantiene por
  **decisión de producto** (retrieval honesto primero, no gastar en respuestas que
  no se piden), no por escasez de quota.
- **El presupuesto más ajustado ahora es embeddings (1.000 RPD)**, y se comparte
  con el indexado (cada confirmación de nota + reindex). Mitigante confirmado:
  `reindex_vault` es **incremental** (saltea notas sin cambios por `content_hash`,
  `embeddings.py`), así que el steady-state es bajo. Para un vault personal, 1.000
  embeddings/día cubren indexado + consultas con margen amplio. Google mismo
  describe este tier como "great for small-scale RAG".
- **Sin necesidad de otro proveedor.** Gemini alcanza para generación y
  embeddings; Groq ya cubre el fallback de generación. No hay que sumar deps.

**Decisión de diseño: retrieval-first, síntesis on-demand.**

- Una consulta normal (`/buscar X`) devuelve las notas relevantes con solo 1
  embedding (quota holgada).
- La síntesis es un botón `[Sintetizar]` explícito (1 generación, cuando se pide).
- La síntesis automática queda como **opción configurable** (`rag.auto_synthesize`,
  default `false`) — ahora barata de habilitar; se deja apagada por elección, no
  por límite.

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
- **Semánticos y estructurales NO se mezclan en una sola lista ordenada.** Los
  backlinks/outgoing no tienen score de similitud, así que ordenarlos junto a los
  semánticos sería arbitrario. `QueryResult` los mantiene separados por `via`; la
  presentación los muestra en dos secciones ("Resultados" / "Relacionadas", ver
  abajo). La deduplicación es por `note_id`: si una nota aparece por semántica y
  por backlink, gana la semántica (tiene score) y no se repite en "Relacionadas".

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
- **Contexto = body completo de las top-3 notas, leído desde disco** (vía
  `vault_cache`, gratis, sin gastar embedding ni generación extra). Decidido así
  porque los snippets de ~200 chars son demasiado finos para una síntesis útil, y
  con 1M TPM de contexto los tokens no son restricción. Para papers, el body ya
  contiene abstract + secciones, que es exactamente lo que se quiere. Se acota a
  top-3 para no diluir la respuesta (ver pregunta abierta sobre el N exacto).
- Las notas van envueltas en `<input>` (mismo blindaje que la captura:
  neutralización de tags de control vía el `classify`/helper de `llm_client`).
- **Siempre** se muestran las notas fuente con su link debajo de la síntesis —
  el usuario verifica.
- **Fallback a Groq** (`llama-3.1-8b-instant`, ya integrado) si Gemini está
  caído/sin quota (mismo patrón que `classify`).
- Config `rag.auto_synthesize: false` (nuevo) reservado para, a futuro,
  sintetizar automáticamente sin botón (ahora barato de habilitar — ver
  *Presupuesto de LLM*).

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

**Dos secciones separadas** (decisión de diseño — honesto con la procedencia):

- **Resultados** — hits semánticos, ordenados por similitud.
- **Relacionadas** — notas estructurales (backlinks/outgoing), solo si hubo
  expansión. Agrupadas por `via`, sin score (no se mezclan con las de arriba).

Modos de entrega:

- **Resultados cortos (2-3)**: inline + botones `[Sintetizar]`
  `[Ver referencias completas]` `[Generar informe .md]`.
- **Resultados largos / expansión**: informe `.md` como documento (header ASCII +
  versión + fecha vía `reporters._report_header`), con las dos secciones.
- **Con síntesis**: texto de síntesis primero, luego las notas fuente con links,
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

### Etapa 7.0 — Retrieval puro (0 gen calls) · MVP ✅ IMPLEMENTADO
- `knowledge_query.retrieve()` + `ScoredNote`/`QueryResult` (`adso/knowledge_query.py`).
- Comando `/buscar <q>` → resultados inline (≤3) o informe `.md` (`adso/handlers/query.py`).
- `CB_DISAMBIG_QUERY` cableado al pipeline (reemplaza "próxima versión");
  `CB_QUERY_REPORT` para el botón `[Generar informe .md]`.
- Fallback de baja confianza: si nada supera el umbral, relaja y muestra top-3
  con aviso (`below_threshold`).
- Tests: `tests/unit/test_knowledge_query.py`, `tests/e2e/test_query_handler.py`.
- Pendiente del checkpoint (pregunta abierta #3): eval manual de calidad de
  retrieval contra el vault real.

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

## Decisiones tomadas (revisión 2026-07)

- **Contexto de síntesis**: body completo de las top-3 notas leído desde disco
  (no snippets). Ver *Síntesis grounded*.
- **Presentación**: dos secciones separadas (Resultados / Relacionadas). Ver
  *Presentación*.
- **`last_retrieved`: descartado para Fase 7.** Es la idea de registrar cuándo una
  nota apareció por última vez en resultados de búsqueda (para detectar "notas que
  nunca se recuperan" → candidatas a revisión, idea post-Fase 8). Se descarta
  porque escribir ese campo en el frontmatter en cada consulta **dispararía el
  VaultWatcher → re-embed** de la nota, gastando quota de embeddings en cada
  búsqueda. Si alguna vez se quiere, hacerlo out-of-band (store aparte, sin tocar
  el `.md`).
- **Proveedor**: seguir con Gemini (generación + embeddings) + Groq como fallback.
  El free tier alcanza sobrado (ver *Presupuesto de LLM*); no se suma otro proveedor.

## Preguntas abiertas

1. **Umbral de "resultado vacío"**: si nada supera `similarity_threshold`, ¿se
   muestra "no encontré nada" o se bajan los mejores N por debajo del umbral con
   aviso? (Propuesta: mostrar top-3 bajo umbral con nota "baja confianza".)
2. **N exacto de notas a la síntesis**: se decidió "body completo top-3"; queda
   confirmar si 3 es el número o se ajusta (¿top-2 para respuestas más enfocadas,
   top-5 para consultas amplias?). Definir al implementar 7.2 con casos reales.
3. **Eval de calidad de retrieval**: los tests unitarios cubren el plumbing, no la
   *calidad* (¿aparecen las notas correctas para consultas reales?). Falta un set
   chico de consultas de referencia contra el vault real como checkpoint manual
   antes de dar 7.0 por cerrado.
