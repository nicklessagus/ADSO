# CLAUDE.md — ADSO

Instrucciones para Claude Code al trabajar en este repositorio.

---

## Proyecto

**ADSO** (*Autonomous Data Structuring Orchestrator*) es un bot de Telegram personal escrito en Python que actúa como escriba, observador y clasificador del conocimiento: captura información no estructurada, la clasifica mediante LLMs, la persiste como notas Markdown en un vault de Obsidian y permite recuperarla mediante consultas en lenguaje natural.

Documentación completa en `docs/`.

---

## Setup de desarrollo

```bash
git clone git@github.com:nicklessagus/ADSO.git
cd ADSO
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
git config core.hooksPath hooks   # guard anti-fuga de secretos en mensajes de commit

# pytest necesita TELEGRAM_ALLOWED_USER_ID: `security.py` lo valida al importarse y
# sin él lanza RuntimeError. Las otras dos no se validan en import time (`config.py`
# las lee con default `""`), pero se exportan igual para que nada intente pegarle a
# la API real con una key vacía.
export TELEGRAM_ALLOWED_USER_ID=12345 TELEGRAM_TOKEN=dummy GEMINI_API_KEY=dummy
pytest
```

Requiere Python ≥ 3.11. No hay dependencias nativas — venv estándar alcanza, no necesita conda.

Para correr el bot (no solo tests), crear `.env` y `config.yaml`:

```bash
# .env
TELEGRAM_TOKEN=...
TELEGRAM_ALLOWED_USER_ID=...
GEMINI_API_KEY=...
VAULT_PATH=/path/al/vault

# Arrancar
python -m adso
```

Para correr con Docker (producción / RPi4):

```bash
docker compose up --build
```

---

## Infraestructura de despliegue

- **Hardware:** Raspberry Pi 4, 4GB RAM, ARM64
- **Entorno:** Docker + docker-compose
- **Lenguaje:** Python ≥ 3.11 (dev y Docker), implementación asíncrona
- **Vault:** Markdown en filesystem local (Syncthing para sync en vivo + Git para backup/DR — ver `docs/architecture.md`)
- **Health check:** `heartbeat_job` toca `/tmp/adso_heartbeat` cada 60s. Docker verifica que el archivo tenga menos de 2 minutos (`CMD-SHELL test -n "$(find /tmp/adso_heartbeat -mmin -2)"` en compose — `find` solo devuelve exit≠0 si el path no existe, así que sin el `test -n` un heartbeat congelado nunca se detectaba; el `HEALTHCHECK` del Dockerfile usa aritmética con `date`, equivalente); 3 fallos consecutivos → `unhealthy`. `start_period: 30s` para absorber el arranque. **`unhealthy` no reinicia nada** (Docker fuera de Swarm lo ignora y `restart: unless-stopped` solo actúa si el proceso muere): de eso se encarga `watchdog.py` — ver la decisión más abajo. El job en sí es un `Path(...).touch()` de 1-4 ms; lo que se silencia (ver `logging_setup.py`) son las dos líneas INFO que **apscheduler** emitía por corrida.

- **Verificación del índice:** `make check-sync` corre `scripts/check_vault_sync.py` **dentro del contenedor** (que es donde están montados `/vault` y el volumen de Chroma) y diffea las notas del disco contra los documentos de ChromaDB, en solo lectura. Responde la pregunta que abre el `VaultWatcher` —*¿de verdad funcionó?*—, porque un watcher mudo y uno sano se ven igual desde afuera. Exit 0 si los dos lados coinciden, 1 si no, así que sirve de gate de un deploy o de un cron.

Toda propuesta de implementación debe evaluarse contra las restricciones de CPU y RAM de la RPi4. Mencionar explícitamente el impacto estimado en recursos.

---

## Stack

| Componente | Tecnología |
|---|---|
| Bot | `python-telegram-bot[job-queue]` v21+ (async) |
| LLM primario | Gemini API — modelo `gemini-3.5-flash-lite` (línea flash-lite estable; free tier jul-2026: ~1.000-1.500 RPD, 15 RPM, 250k TPM — Google ya no publica el cap del free tier en la doc, verificarlo por proyecto en AI Studio). Clasificación y síntesis de reportes |
| LLM de Vision | `gemini-3.6-flash` (`GEMINI_VISION_MODEL`) — solo OCR/descripción de imágenes y PDFs escaneados. Constante separada porque la quota del free tier es **por modelo**: rasterizar un PDF de 20 páginas no debe consumir RPD del bucket de la captura diaria |
| LLM fallback | Groq — `llama-3.1-8b-instant` (sin schema constrained; post-validado). |
| Embeddings | Gemini Embedding API (remoto, no local) |
| Vector DB | ChromaDB embebido |
| Transcripción | `faster-whisper` (modelo `tiny` o `base`) |
| Extracción web | **No implementada** — el bot no descarga ninguna URL. El único link con camino propio es arxiv.org (API, ver Fase 5); cualquier otro link se trata como texto plano (`media_type: text`, teclado `[Tarea]`/`[Nota]`) |
| Extracción PDF | `pymupdf` (texto + metadata) — detección heurística de papers + extracción local de secciones (abstract, keywords, métodos, conclusiones) para armar el contenido que se manda al LLM; el preview solo muestra título + abstract para papers (las keywords y las secciones van al prompt, no a la pantalla) y texto crudo para genéricos |
| Calendar | Google Calendar API v3 *(diferido — Fase 6; diseño: lectura de todos los calendarios, escritura y borrado solo en calendario `ADSO` dedicado)* |
| Tasks | Google Tasks API — solo **alta** de tareas en la lista `ADSO` dedicada (`tasklists().list` para encontrarla o crearla + `tasks().insert`). No hay borrado ni lectura de listas externas: ambas cosas son diseño de Fase 6, no código |
| Vault | Markdown + YAML Frontmatter en filesystem |
| Backup vault | Repo git privado en GitHub — push automático con debounce configurable (`backup.debounce_seconds`) |

---

## Estructura de módulos

```
adso/
├── bot.py                  # Bootstrap de la aplicación PTB y registro de handlers — la lógica vive en handlers/
├── handlers/
│   ├── commands.py         # /start /help /status /reset /clasificar
│   ├── input.py            # Entrada de mensajes: texto, audio, imagen, documento, links
│   ├── capture.py          # Flujo de captura: clasificación, preview, corrección, confirmación
│   ├── callbacks.py        # Callbacks de inline keyboards
│   ├── manage.py           # Gestión: solo crear proyecto, área y sección (el resto de VALID_OPERATIONS se responde "todavía no está disponible")
│   ├── query.py            # /buscar — retrieval semántico (Fase 7.0)
│   ├── reports.py          # /reporte y /reporte_full — flujo interactivo
│   └── jobs.py             # Crons: reclassify_inbox, reindex nocturno, heartbeat (el reporte semanal está configurado pero aún sin job — ver docs/improvements-2026-07.md §2.2)
├── keyboards.py            # Construcción de inline keyboards
├── constants.py            # Raíz del grafo (sin imports locales): taxonomía del vault (NOTE_TYPES, STATUS_BY_TYPE, STATUS_ON_CONFIRM, DEFAULT_EXCLUDE_DIRS) + callback IDs
├── bot_utils.py            # Utilidades (spawn_tracked, Stopwatch, helpers de mensajes)
├── logging_setup.py        # configure_logging(): nivel desde LOG_LEVEL + silenciado de librerías ruidosas
├── watchdog.py             # Thread de liveness: mata el proceso si el event loop deja de avanzar
├── transcriber.py          # Transcripción de audio con faster-whisper
├── llm_client.py           # Cliente Gemini/Groq: llamadas a API, retries, modo degradado, build_system_prompt
├── llm_schema.py           # Schema de salida de Gemini + validación de respuesta + sanitización de frontmatter + patrones de injection (re-exportados desde llm_client por compatibilidad)
├── document_extractor.py   # Extracción de PDFs (pymupdf) y documentos de texto
├── arxiv_client.py         # Metadata de papers via API de arXiv
├── vault_writer.py         # Escritura de .md al filesystem + git backup con debounce
├── vault_watcher.py        # Watcher de filesystem (watchdog): detecta conflictos Syncthing y cambios/borrados externos, y los delega a los callbacks que arma `_watcher_callbacks` en bot.py (re-embed; y al borrar, `vault_writer.remove_broken_wikilinks`)
├── vault_search.py         # Búsqueda estructural: backlinks ([[wikilinks]]), tags, filtros por frontmatter
├── vault_cache.py          # Caché de parsing de notas por (mtime, size) — evita re-parsear notas sin cambios en scans repetidos
├── embeddings.py           # Pipeline de embeddings y ChromaDB
├── knowledge_query.py      # Retrieval semántico — busca notas por similitud vectorial en ChromaDB (no llama al LLM)
├── reporters.py            # Generación de reportes .md (scope, ideas, salud, cola de lectura)
├── tasks_client.py         # Google Tasks API
├── security.py             # Middleware de autenticación
└── config.py               # Variables de entorno, constantes (GEMINI_MODEL) y carga de config.yaml
```

No existe `calendar_client.py` — Google Calendar es Fase 6 diferida (solo Tasks está implementado).

---

## Convenciones de código

- **Idioma — inglés para todo lo nuevo (desde 2026-08-26):** nombres de funciones y variables, docstrings, comentarios, documentos nuevos e issues se escriben en **inglés**. El código, los tests, este archivo y `docs/` existentes están en español y **no se traducen hacia atrás**. Al editar un archivo que ya está en español, la corrección se escribe en español — un archivo bilingüe es peor que uno consistente en el idioma equivocado. Excepción permanente: los mensajes que el bot manda al usuario por Telegram siguen en español con infinitivo impersonal (ver § Tono y estilo de mensajes).
- **Asíncrono siempre:** usar `async/await`. Ninguna operación de I/O debe ser bloqueante.
- **Manejo de excepciones explícito:** capturar errores de red, timeouts de API y errores de filesystem con mensajes claros al usuario.
- **Sin pérdida de datos:** ante error al escribir al vault, loguear y notificar al usuario. No silenciar excepciones.
- **Modular:** cada módulo tiene responsabilidad única. `bot.py` orquesta, no procesa.
- **Documentación de funciones:** docstring en funciones públicas con descripción, args y comportamiento ante error.
- **Type hints** en todas las firmas de función.

---

## Seguridad — reglas no negociables

- Todo contenido externo (PDFs, imágenes, documentos, metadata de arXiv) se pasa al LLM dentro de etiquetas `<input>` con instrucción explícita de no seguir instrucciones internas. Además, cuando el contenido a clasificar (texto de PDF/OCR/Vision/documento o metadata de arXiv) dispara `check_injection_risk`, `_classify_and_preview`/`_classify_and_preview_arxiv` anteponen un aviso al preview (`_INJECTION_PREVIEW_WARNING`) para que el usuario escrute antes de confirmar. No bloquea — la nota igual requiere confirmación explícita.
- El LLM siempre responde en JSON estructurado con schema fijo.
- Autenticación por `TELEGRAM_ALLOWED_USER_ID` en dos capas: (1) gate global en `bot.py` (`_global_auth_gate` registrado como `TypeHandler(Update, ...)` en `group=-1`) que descarta con `ApplicationHandlerStop` cualquier update de usuario no autorizado antes de llegar a los handlers; (2) el decorador `@authorized` por handler como segunda barrera. Ambos usan `is_authorized()` de `security.py`. Un handler nuevo sin decorar ya no es un bypass.
- Credenciales solo en variables de entorno. Nunca hardcodeadas.
- **Nunca pegar salida que resuelva secretos en algo que se commitea o pushea** (mensaje de commit, PR, issue, doc versionado). En particular `docker compose config` **imprime `.env` resuelto en texto plano** (`GEMINI_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`). Para verificar que dos configs son equivalentes, comparar hashes (`docker compose config | sha256sum`) o `git diff`, nunca pegar el volcado. Post-mortem: el commit `889c5ca` (2026-08-13) filtró las tres keys en su mensaje; GitHub lo publicó en el feed público en segundos (Groq lo detectó 1s después del push), un `--amend` posterior no lo borra de GitHub ni del reflog, y 4 días después el `TELEGRAM_TOKEN` cosechado se usó para spam. Un force-push correctivo **no alcanza**: toda credencial que tocó un push público se considera comprometida y se rota. Push protection de GitHub **no cubre este canal** (escanea contenido de archivos, no mensajes de commit); el guard que sí lo cubre son los hooks del repo — `hooks/commit-msg` y `hooks/pre-push` rechazan mensajes con patrones de credencial (activados con `git config core.hooksPath hooks`, incluido en el setup). Ver [[decisions-log]] y la memoria del incidente.

---

## Vault de Obsidian

### Taxonomía
```
01-Projects/{proyecto}/{seccion}/nota.md       # tiene inicio y fin
02-Areas/{area}/nota.md                        # sin fin, continuo — áreas reales (docencia, investigacion, etc.)
03-Resources/                                  # archivos adjuntos (PDFs, imágenes, etc.) y material de referencia permanente
00-Inbox/nota.md                               # sin clasificar
05-Archive/                                    # proyectos inactivos o completados
```
Las ideas (`type: idea`) viven en su proyecto o área correspondiente, igual que `reference`. No hay carpeta `04-Ideas/`.

### Ciclo de vida
Nota con `type: idea` en área → Proyecto activo → Archivo → (borrado con doble confirmación)
Las áreas no tienen ciclo de vida.

El ciclo de arriba es el **modelo**, no lo que hace el bot: de todas esas transiciones solo está implementada la creación del proyecto/área. Archivar, convertir una idea en proyecto y borrar se hacen a mano en el filesystem o en Obsidian.

Una idea tiene tres estados: `raw` (capturada, sin procesar), `implemented` (se hizo algo con ella — se convirtió en proyecto, tarea, o se aplicó), `discarded` (descartada conscientemente). No hay presión de "desarrollarla": puede quedarse en `raw` indefinidamente hasta que el usuario tome una decisión.

### Frontmatter mínimo requerido
```yaml
---
title: ""
date_created: ""   # ISO 8601
date_modified: ""  # ISO 8601
type: ""           # reference | task | idea | project-index | area-index
tags: []           # siempre en inglés, kebab-case; el LLM reutiliza tags existentes del vault (excluyendo 00-Inbox) antes de crear nuevos
source: telegram   # "telegram" para notas de usuario, "system" para auto-generadas
media_type: ""     # text | audio | image | link | document — automático
status: active     # valores dependen del type — ver docs/frontmatter-schema.md
---
```

Los tipos `project-index` y `area-index` se generan automáticamente al crear proyecto/área (no por clasificación del LLM). Ambos requieren `description` — el bot la pide obligatoriamente en la creación. Schema completo en `docs/frontmatter-schema.md`.

### Regla de confirmación
Ninguna nota se escribe al vault sin confirmación explícita del usuario. El bot muestra un preview del frontmatter y los links sugeridos, y el usuario confirma con inline keyboard.

**Única excepción, deliberada: el cron `reclassify_inbox` (Caso A).** Una nota degradada que ya tiene destino asignado se reclasifica y se reescribe sin volver a pasar por el usuario: la captura original sí se confirmó, pero el título, el tipo, los tags y las fechas de la segunda pasada del LLM —y, para `document`/`image`/`link`, también el body— se persisten sin revisión, y `check_injection_risk` no corre en ese camino. El Caso B (sin destino) sí espera `/clasificar`.

- **Notas y tareas** (`reference`, `idea`, `task`): primera fila `[Cancelar]` `[Corregir]` `[Reubicar]`, segunda fila `[Confirmar]`. El texto libre está bloqueado hasta que el usuario apriete `[Corregir]` (activa modo corrección con lock). Durante el lock solo se acepta texto plano — audio, archivos y `/comandos` quedan bloqueados (todos salvo `/reset`, via el decorador `command_guard`). La corrección puede ajustar título, prioridad, tags y tipo; para tareas también fecha límite en lenguaje natural.

**Prefijos válidos en modo corrección** (`_handle_text_correction` en `capture.py`). Los acepta tanto la rama de notas (`_apply_note_corrections`, prefijos excluyentes: gana el primero que matchea) como la de tareas (`_apply_task_corrections`, que busca todos los campos en el mismo texto y en cualquier orden):
- `titulo <texto>` / `título <texto>` — reemplaza el título
- `prioridad alta|media|baja` — también `high|medium|low`
- `tag <nombre>` / `agregar tag <nombre>` — añade un tag
- `tipo <valor>` / `type <valor>` — cambia el tipo (`_apply_type_correction`, agnóstico del tipo vigente, es el que convierte tarea ↔ nota). Acepta `reference`/`referencia`/`note`/`nota`, `task`/`tarea` e `idea`; con el prefijo puesto y un valor no reconocido la corrección se da por atendida igual (no cae al fallback de título) y `_resync_status_with_type` realinea `status` y descarta `due_date`/`scheduled` si el tipo nuevo no es tarea
- **Solo tareas:** `fecha <expresión>` — y también una expresión temporal suelta en el texto, sin prefijo, porque `_apply_task_corrections` corre `_parse_date_from_text` sobre todo el mensaje
- Sin prefijo y texto ≤ 200 chars sin saltos de línea → se usa como nuevo título (fallback)
- Sin prefijo y texto largo o multi-línea → se rechaza con mensaje de ayuda (`error_msg_id` guardado en `pending`); el lock se mantiene activo para que el usuario reintente. Cuando la siguiente corrección es válida, ese mensaje de error se borra junto con el mensaje del usuario, quedando solo el preview actualizado.

**Failsafe:** `/reset` cancela cualquier operación pendiente y limpia todo el estado (`pending_note`, `pending_capture_ctx`, `block_msg_ids`, etc.). Funciona en cualquier momento, sin confirmación. Implementado en `handle_reset` (`commands.py`).

**Guards de comando uniformes (`command_guard` en `bot_utils.py`, #47):** cada comando repetía —o se olvidaba— sus propios chequeos de estado pendiente. Ahora los aplica un solo decorador, por dentro de `@authorized` (que queda como el más externo, así que un usuario no autorizado se sigue descartando en silencio antes de cualquier respuesta). La regla: el **lock de corrección** bloquea todos los comandos salvo `/reset`; el **teclado pendiente** bloquea además los que arrancan flujo propio — `command_guard(starts_flow=True)` en `/clasificar`, `/buscar`, `/reporte` y `/reporte_full`, `starts_flow=False` en `/status`, `/help` y `/start`. `/reset` no se decora nunca. El aviso de teclado pendiente pasa por `reply_blocked` (deja los dos ids en `block_msg_ids` para borrarlos al resolver el teclado), y si el comando llega como callback (`/clasificar` desde el botón `[Clasificar inbox]`, donde `update.message` es None) el decorador contesta primero el `callback_query.answer()` y responde sobre el mensaje del botón.

`[Reubicar]` cambia únicamente el destino (`[Inbox]` / `[Elegir área]` `[Elegir proyecto]` / `[Cancelar]`) en ambos tipos.

### Prioridad y fecha inferidas
El LLM infiere `priority` y `due_date` del lenguaje del mensaje para tareas. `priority` se usa tal como la devuelve el LLM; si no hay señal, usa `medium`.

`due_date` se resuelve en dos pasos: el LLM propone una fecha (`build_system_prompt` le mete la fecha de hoy y el día de la semana en inglés y español), pero luego `_classify_and_preview` corre `_parse_date_from_text()` sobre el texto original y overridea el resultado si encuentra una expresión válida. El parser local es determinístico y más fiable que el LLM para expresiones relativas en español ("el viernes", "mañana", "el próximo lunes"). El LLM tiene problemas con la aritmética de días de la semana. Ojo: la fecha del prompt es un `datetime.now()` **naive** — la zona horaria del proceso (`TZ`, que compose setea), no UTC — mientras que `_parse_date_from_text` resuelve la suya con `_user_tz()`; si las dos difieren, el override local es el que manda.

`_parse_date_from_text()` computa "ahora" en la zona horaria del usuario para evitar off-by-one en días de semana cerca de medianoche local. `_user_tz()` resuelve la zona en orden `ADSO_TIMEZONE` → `TZ` (docker-compose ya la setea a `America/Argentina/Buenos_Aires`) → UTC. Requiere el paquete `tzdata` (en `requirements.txt`/`pyproject.toml`) para que `zoneinfo` resuelva nombres IANA en la imagen `python:3.11-slim` (que no trae la base de datos de zonas del sistema). Acepta un parámetro `now` inyectable para tests. Valida el rango de hora/minuto (`0≤h≤23`, `0≤m≤59`) y descarta la hora si está fuera de rango en vez de lanzar `ValueError`. Los matches relativos ("mañana", "hoy", "pasado mañana") usan límites de palabra (`\b`) para no matchear dentro de otras palabras.

Ambos campos aparecen en el preview y el usuario puede corregirlos con `[Corregir]`.

**Sanitización del frontmatter LLM** (`_validate_capture_payload` en `llm_schema.py`):
- **Whitelist de claves (`ALLOWED_FRONTMATTER_KEYS`):** solo sobreviven las claves documentadas en `docs/frontmatter-schema.md` (base + destino + tareas + académicas + `description`/`sections` de los índices). Cualquier otra se descarta con log a `warning`. Cierra el vector por el que el fallback de Groq (sin schema constrained) o una prompt injection en un PDF/OCR podían meter claves arbitrarias — en particular `handler` y `content`, que corrompían el archivo al escribirlo (ver `_build_post` abajo). Se aplica antes de que `extra_fm`/`user_context` se inyecten en `capture.py`, así que esos campos no se ven afectados.
- **Título (`_clean_title`):** se coacciona a string (`title: null` o un número del fallback de Groq ya no lanzan `TypeError`) y se le stripean heading markers de markdown (`#`, `##`) y prefijos label (`Tarea:`, `Task:`, `Nota:`, `Recordar:`). El regex se aplica **en bucle** hasta que el título no cambia: ambas alternativas están ancladas en `^`, así que `"## Tarea: X"` necesita dos pasadas.
- **Tags:** un string suelto (`"python, ml"` — típico de Groq) se parte por comas antes de normalizar; cualquier otro tipo inesperado cae a `[]`. Se filtran días de la semana (lunes…domingo, monday…sunday) y expresiones temporales (hoy, mañana, proxima-semana) que no son etiquetas semánticas útiles. También se filtran tags que duplican el `type` (task, note, idea, etc.).
- **Tipos coaccionados (defensa contra respuestas del LLM, sobre todo el fallback de Groq sin schema):** `confidence` se fuerza a float en `[0,1]` (default 0.5 si no es numérico) en `validate_llm_response` — evita `TypeError` en la comparación con el umbral de desambiguación. `year` se coacciona a `int` o se descarta. `authors`/`keywords` se fuerzan a lista de strings (un string suelto se parte por comas; otro tipo → `None`). `read_status` se valida contra `{read, unread}` (`VALID_READ_STATUS`) o se descarta. `type`/`status`/`priority` se normalizan con `_norm_enum()` (str + strip + lower) antes de validarse: `"Task"`/`"Pending"`/`"High"` (capitalización habitual de llama-3.1-8b) ya no tiran toda la respuesta a modo degradado, y un valor no hasheable (dict/list) en `status` produce `LLMResponseError` en vez de `TypeError`. `body` ausente **o `null`** (el schema de Gemini lo permite) se normaliza a `""` — si no, el preview reventaba con `AttributeError`.

---

## Tono y estilo de mensajes

Los mensajes que el bot envía al usuario por Telegram usan **infinitivo impersonal**.

- ✅ `Confirmar, corregir o cancelar.`
- ✅ `Usar /clasificar para continuar.`
- ✅ `¿Guardar como tarea o como nota?`
- ❌ voseo: `Confirmá`, `Mandá`, `Podés`
- ❌ primera persona del bot: `Puedo`, `No pude`
- ❌ ustedeo: `Confirme`, `Cancele`

---

## Modelo de interacción

El bot funciona en un único chat de Telegram. No hay estado de contexto persistente. Toda la interacción se basa en **lenguaje natural + inline keyboards**.

### Estado default: captura
El usuario manda contenido (texto, audio, link, imagen, documento). Para texto y audio el bot pregunta primero `[Tarea]` o `[Nota]`. Los dos botones no son simétricos: `[Tarea]` **fija** `type: task` (`forced_type`), mientras que `[Nota]` solo **impide** `task` (`prevent_task`, que convierte `task` → `reference`) y deja en pie la elección del LLM entre `reference` e `idea`. Para PDFs e imágenes el type se infiere del contenido. Los links **no** tienen camino propio salvo arxiv.org: cualquier otra URL es texto y pasa por los mismos `[Tarea]`/`[Nota]`. El bot propone clasificación y el usuario confirma, edita o cancela con inline keyboards.

### Estado transiente: consulta
El usuario pregunta algo sobre el vault. El bot resuelve la consulta, devuelve el resultado (inline o como archivo `.md` con links `obsidian://`) y vuelve al estado default.

### Inline keyboards
Los botones son el mecanismo principal de interacción después del lenguaje natural:

| Momento | Botones |
|---|---|
| **Texto / audio recibido** | fila 1: `[Cancelar]` `[Tarea]` `[Nota]` — el usuario elige el tipo; el LLM infiere el resto. fila 2: `[🔎 Buscar en el vault]` — busca ese texto (retrieval semántico) en vez de guardarlo (`CB_DISAMBIG_QUERY`) |
| **PDF recibido** | `[Cancelar]` `[Ya lo leí]` `[Lo quiero leer]` — setea `read_status` en frontmatter |
| **Imagen recibida** | fila 1: `[OCR]` `[Gemini Vision]` — métodos de extracción. fila 2: `[Cancelar]` `[Describir]` (`build_fallback_pdf_keyboard`, compartido con el PDF escaneado sin texto) |
| **Resultado OCR** | `[Cancelar]` `[Corregir]` / `[Gemini Vision]` `[Confirmar]` — dos filas; Gemini Vision descarta el OCR y reprocesa |
| **Resultado Gemini Vision** | `[Cancelar]` `[Corregir]` `[Confirmar]` |
| **Audio transcripto** | `[Cancelar]` `[Corregir]` `[Confirmar]` → al confirmar, el mismo `build_save_keyboard` del texto: fila 1 `[Cancelar]` `[Tarea]` `[Nota]`, fila 2 `[🔎 Buscar en el vault]` |
| **Captura nota o tarea** | `[Cancelar]` `[Corregir]` `[Reubicar]` / `[Confirmar]` — dos filas; igual para notas y tareas, con o sin destino |
| **Reubicar destino** | `[Inbox]` / `[Elegir área]` `[Elegir proyecto]` / `[Cancelar]` — tres filas |
| **Consulta** (si falta scope) | `[Todo]` `[Proyecto1]` `[Proyecto2]` ... *(diseño de Fase 7 — sin código: `run_query` nunca pide scope, aunque `retrieve()` acepta el parámetro)* |
| **Resultado de consulta** | `[Generar informe .md]` — botón único. `[Ver referencias completas]` es diseño de Fase 7, sin código ni callback |
| **Expansión desde nodo** | `[Solo relaciones directas]` `[Expandir un grado más]` *(diseño de Fase 7 — sin código)* |
| **Desambiguación** (modo incierto) | `[Guardar como nota]` `[Buscar en vault]` *(diseño de Fase 7 — sin código: el teclado y su callback se borraron en 2026-09 por no tener productor; `needs_disambiguation` se sigue calculando en `classify`)* |
| **Fallback OCR sin texto** | `[Gemini Vision]` / `[Cancelar]` `[Describir]` — OCR no encontró texto, sin botón OCR |
| **`/reporte` — tipo** | `[Proyecto/Área/Inbox]` `[Ideas]` / `[Salud del vault]` `[Cola de lectura]` / `[Cancelar]` — tres filas |
| **`/reporte` — categoría** | `[Proyectos]` `[Áreas]` / `[Cancelar]` `[Inbox\|Todas\|Toda la cola]` — dos filas |
| **`/reporte` — lista de items** | botones de items en pares / `[Cancelar]` `[← Volver]` — última fila fija |

**Failsafe global:** `/reset` cancela cualquier estado pendiente (teclados, correcciones, capturas) y vuelve al estado inicial. Funciona siempre, sin confirmación.

### Desambiguación de intención
El diseño es que, si el LLM no tiene confianza alta en el modo, el bot pregunte con botones en vez de asumir. **No está implementado:** `classify` sigue calculando `needs_disambiguation` (y `llm.disambiguation_threshold` existe solo para eso), pero nadie lo lee y el teclado de dos botones se borró en 2026-09. Lo que sí funciona es la fila `[🔎 Buscar en el vault]` de `build_save_keyboard`, único productor de `CB_DISAMBIG_QUERY`: ejecuta el retrieval semántico real (Fase 7.0, el mismo pipeline que `/buscar`). Al buscar desde un teclado, `run_query` recibe el mensaje de los botones como `keyboard_msg` y lo edita como mensaje de estado — el teclado se retira, como en cualquier otro callback (si la edición falla por mensaje viejo, cae a un mensaje nuevo).

El LLM no usa `mode=query` ni `mode=edit` (removidos del prompt hasta Fase 7). Todo input que no sea `manage` se clasifica como `capture`. Si el LLM devuelve `query` o `edit` de todas formas, `_redirect_unimplemented_mode()` (`capture.py`) los redirige a `capture` **re-validando el payload** con `_validate_capture_payload` — `validate_llm_response` no sanitiza los payloads de esos modos, así que sin esa pasada el frontmatter llegaba crudo al vault. Si el payload no se puede sanear (tipo inválido, `frontmatter: null` — legal en el schema de Gemini), cae a modo degradado (`make_degraded_result()` en `llm_client.py`): Inbox + `status: pending-classification`.

### Consultas con refinamiento de scope *(diseño de Fase 7.1 — sin código)*
El patrón previsto es: el LLM interpreta lo que pueda del lenguaje natural y los botones cubren lo que falta. Si el usuario ya especificó el scope ("papers pendientes de tesis"), el bot responde directo. Si no ("dame todo lo que tengo que hacer"), el bot ofrece botones para elegir scope (toda la bóveda, uno o más proyectos).

Hoy no existe: `run_query` busca siempre sobre todo el vault y nunca pregunta. `retrieve()` ya acepta el parámetro `scope`, pero ningún caller se lo pasa — es plomería adelantada, no una función.

### Output de consultas
El corte lo fija `_INLINE_MAX = 3` en `query.py`.

- **Resultados cortos** (1-3 ítems): inline + botón `[Generar informe .md]`. Cada ítem lleva título, proyecto/área, estado y % de similitud, más un snippet recortado a 160 chars — **sin link `obsidian://`**: el link solo aparece en el informe `.md`.
- **Resultados largos** (más de 3): informe `.md` enviado directo como documento, sin pasar por el inline. Header estándar (logo ASCII + versión + fecha), y por nota: similitud, ubicación, estado, snippet en blockquote y `[Abrir en Obsidian](obsidian://…)`.
- **Síntesis LLM, expansión desde nodo y relaciones** son diseño de Fase 7 (7.2+): hoy el informe de consulta no los incluye. `/buscar` es retrieval puro — no llama al LLM.

Todos los informes `.md` tienen header estándar con logo ASCII, versión y fecha. Se asume Obsidian instalado y sincronizado.

---

## Modos de operación

El LLM clasifica cada mensaje en uno de estos modos antes de procesarlo:

| Modo | Ejemplos |
|---|---|
| **Captura** | Texto, audio, link, imagen, PDF con contenido a guardar |
| **Consulta** | "qué tengo sobre X", "mostrá relaciones", "todo pendiente" |
| **Edición** | "actualizá la nota X" (solo `reference` e `idea`) |
| **Gestión** | Crear proyecto, crear área, crear sección. Archivar, renombrar, borrar y convertir idea → proyecto están en `VALID_OPERATIONS` (el LLM puede proponerlos y el bot pide confirmación), pero `_cb_manage_confirm` los responde con `"Operación 'X' todavía no está disponible."` — no hay código que los ejecute. `build_intent_keyboard` solo ofrece `[Crear proyecto]` y `[Crear área]` |

No hay modo Agenda — el agendamiento se maneja via tasks con `due_date`, que se pushea al campo de fecha límite de Google Tasks. `scheduled` **no crea ningún evento**: no existe `calendar_client.py`, y su único uso es el texto de horario que va al campo `notes` de la task. El evento en un calendario `ADSO` es diseño de Fase 6. Las tasks no se editan via ADSO.

**El bot es un sistema de retrieval, no de razonamiento.** En modo consulta, recupera y presenta notas relevantes del vault. No agrega conocimiento propio ni opina sobre el contenido.

Las operaciones de gestión piden una confirmación con `[Cancelar]` `[Confirmar]` antes de ejecutarse. La **doble** confirmación para borrar es diseño: no hay ninguna implementada, entre otras cosas porque tampoco hay borrado.

---

## Embeddings

- Se calculan via **Gemini Embedding API** (nunca localmente).
- Se almacenan en **ChromaDB** en `/app/data/chroma/`.
- Se generan de forma asíncrona inmediatamente después de confirmar una nota.
- Umbral de similitud para sugerir links: `links.similarity_threshold` en `config.yaml` (default: `0.82`).
- Umbral de similitud para consultas RAG: `rag.similarity_threshold` en `config.yaml` (default: `0.75`).
- Carpetas excluidas del índice: `vault.exclude_dirs` en `config.yaml`.

`config.yaml` debe existir siempre; si falta, el bot falla con error claro al arrancar.

**Rate limit de updates (`rate_limit` en `config.yaml`, #1):** el `_global_auth_gate` de `bot.py` pasa cada update autorizado por un `TokenBucket` (`security.py`) antes de despacharlo. `enabled: true`, `burst: 10`, `refill_seconds: 2.0`. No protege contra terceros —hay un solo usuario autorizado— sino contra una ráfaga accidental: reenviar 40 mensajes de una dispara 40 clasificaciones contra un free tier de 15 RPM, y las que sobreviven llegan minutos tarde. El bucket avisa una vez por ráfaga (flag `notified`), no una vez por update descartado. Va **después** de la autenticación, así que un no autorizado no puede gastar tokens. El otro control de concurrencia es `_embed_semaphore` (4) en el pipeline de embeddings.

Las áreas y proyectos pueden sembrarse opcionalmente desde `config.yaml` en el primer arranque, y luego se gestionan via el bot.

---

## Fases de desarrollo

| Fase | Funcionalidad | Estado |
|---|---|---|
| 1 | Captura de texto, clasificación, confirmación, escritura al vault + búsqueda estructural (backlinks, tags, frontmatter) | ✅ |
| 2 | Indexado del vault + links automáticos (embeddings + ChromaDB) | ✅ |
| 3 | Audio (faster-whisper) + PDFs (pymupdf) + documentos de texto | ✅ |
| 4 | Imágenes y capturas (OCR + Gemini Vision) | ✅ |
| 5 | Integraciones externas (arXiv) | ✅ |
| 6 | Google Calendar + Google Tasks | 🔄 parcial — de Tasks solo el alta (push unidireccional); Calendar diferido, sin una línea de código |
| 7 | Consultas RAG en lenguaje natural | 🔄 parcial — 7.0 retrieval puro (`/buscar`) implementado; scope/expansión/síntesis pendientes. Diseño en `docs/fase7-rag-design.md` |
| 8 | Análisis del vault: reportes a pedido (scope, ideas, salud, cola de lectura), scoring de papers, detección de gaps | 🔄 parcial — reportes implementados |

### Fase 5 — arXiv

Cuando el usuario manda un link de arxiv.org, el bot lo detecta por dominio y usa la **API de arXiv** (no scraping) para extraer metadata literal: título, autores, año, abstract, DOI, keywords. La nota resultante tiene el mismo formato que un paper subido como PDF:

- **Frontmatter:** campos académicos (`authors`, `year`, `doi`, `keywords`) literales de la API — el LLM no los inventa; las `keywords` son las categorías de arXiv (`cs.LG`, etc.), no keywords de autor. `read_status` **no** viene de la API: es un `fm.setdefault("read_status", "unread")` en `_classify_and_preview_arxiv`. El LLM solo aporta proyecto, área, tags y summary. Este es además el único camino que agrega el tag `paper` (ver la decisión sobre `type: paper` más abajo).
- **Body:** `> [!summary] AI Summary` (del campo `summary` del LLM, resumen breve en español) + `## Abstract` (texto literal de la API) + `## Personal Notes`. Se usa el campo `summary` y **no** `body` del LLM porque `body` contiene el documento completo con callout + secciones — usarlo causaría duplicación del abstract.
- **`source_url`:** apunta a arxiv.org sin versión (ej: `https://arxiv.org/abs/2301.12345`). No se descarga el PDF.
- **`media_type`:** `link`.

El flujo de confirmación es idéntico al de cualquier captura: preview → `build_capture_keyboard` (`[Cancelar]` `[Corregir]` `[Reubicar]` / `[Confirmar]`).

La detección de arXiv ocurre en `handle_text()`, antes del flujo genérico. Soporta URLs `abs/`, `pdf/`, con o sin versión (`v2`), y formato antiguo (`hep-ph/XXXXXXX`). Si la API de arXiv falla, el bot ofrece guardar el link como nota genérica.

**Detección de duplicados:** antes de mostrar el preview, se busca en todo el vault (excluido Archive) si ya existe una nota con el mismo `source_url` o el mismo `doi`. Si se encuentra, se muestra la ruta del archivo existente y un teclado `[Cancelar]` `[Crear igual]`. `[Crear igual]` retoma el flujo normal sin restricciones de destino. La búsqueda por `doi` permite detectar papers subidos previamente como PDF. Diseño: un paper = una nota (los demás proyectos que lo necesiten lo referencian via wikilink).

---

## Ideas futuras (post Fase 8)

Capacidades exploratorias que dependen de tener un vault maduro con suficientes notas y embeddings.

- **Clustering de temas emergentes:** UMAP + HDBSCAN sobre embeddings de ChromaDB, etiquetado por LLM. Viable en RPi4.
- **Transferencia de métodos entre proyectos:** cruzar `methods` de papers entre proyectos para detectar técnicas aplicables que no se están usando.
- **Red de citas interna:** campo `cites` en papers, análisis tipo PageRank para identificar papers fundacionales y gaps de lectura.
- **Análisis temporal:** evolución de temas y métodos en el vault a lo largo del tiempo. Detección de frentes de investigación activos.
- **Detección de conocimiento obsoleto:** trackear `last_retrieved` por nota — las que nunca aparecen en resultados RAG ni tienen links son candidatas a revisión.
- **Generación de Canvas:** crear archivos `.canvas` (JSON) automáticamente desde clusters de embeddings, posicionando notas similares cerca.
- **Bibliografía anotada on-demand:** generar un documento consolidado con papers de un proyecto, agrupados por método o tema, con `relevance`, `contribution` y `conclusions`.
- **NASA ADS:** integración de cuenta para importar colecciones/listas de papers en bloque o por sync periódico. No es flujo de captura individual — requiere OAuth o API key de ADS y un mecanismo de reconciliación con el vault (evitar duplicados por DOI/arXiv ID).
- **Reportes en PDF:** exportar `/reporte` y `/reporte_full` como PDF además de `.md`. Candidato: `fpdf2` (puro Python, sin deps del sistema, ARM64 nativo). Implementación: un helper `_build_pdf(md_content)` que parsea el MD generado y lo renderiza con fpdf2; los reporters reciben `fmt: str = "md" | "pdf"`. Alternativa más simple pero output básico: `markdown` lib → HTML → `xhtml2pdf`. Pendiente de prueba en RPi4.
- **Reintento de git push en heartbeat:** si un push falla (error de red, etc.), los commits quedan locales hasta la próxima actividad. Para garantía extra: el `heartbeat_job` podría revisar si `repo.head.commit` tiene commits sin pushear (`repo.iter_commits('origin/main..HEAD')`) y hacer push silencioso. Solo notificar si también falla ese reintento.

---

## Validación de código

- Todo el código generado es validado con **OpenAI Codex** antes de incorporarse al repositorio.
- Estrategia de testing completa en `docs/testing.md`: unit, integration y e2e con cobertura ≥ 70% (gate de CI sobre todo `adso/` menos el bootstrap `bot.py`/`__main__.py`; actual 91%).
- **Los markers `integration`/`e2e` se asignan solos** por directorio, en un hook de `tests/conftest.py`. No escribirlos a mano en los tests. CI corre la suite completa (1334 tests) — ningún test toca la red, y desde 2026-09-18 eso lo **hace cumplir** una fixture autouse de `conftest.py` que bloquea todo socket saliente que no sea loopback (#67): antes era una convención, y 14 tests de reportes la violaban en silencio porque `_llm_synthesis` se traga cualquier excepción.
- `adso/handlers/*` **está en la medición de cobertura**. No volver a ponerlo en el `omit` de `pyproject.toml`: los e2e sí lo ejercitan, y omitirlo hacía que un test nuevo sobre un handler no moviera el gate (I3 en `docs/audit-2026-07-31.md`).

### Test-first — obligatorio

Ninguna funcionalidad nueva ni fix entra sin test que lo cubra, y **el test se escribe antes que el código**. El orden no es negociable:

1. **Planificar** la modificación: qué cambia, en qué módulo, qué invariante preserva.
2. **Escribir el test** de unidad que la especifique (y el de cobertura del path nuevo). Correrlo para verlo fallar.
3. **Implementar** el cambio.
4. **Correr el test** y verificar que pasa, junto con la suite completa.

Escribir el test después produce tests que confirman lo implementado en vez de especificar el comportamiento buscado. Un cambio que llega sin test se devuelve al paso 2.

**Bug encontrado → reproductor primero, siempre.** Antes de tocar `adso/`, escribir el test que reproduce el bug y verlo fallar. No es solo higiene de proceso: **es lo que valida que el bug existe**. En la auditoría 2026-08-26, de 41 mecanismos auditados uno resultó falso — `find_tasks` "duplicaba" tareas según una lectura del código, pero el fix rompió un test existente cuyo fixture documenta que la duplicación es deliberada. Sin el ciclo test-primero, ese "fix" se habría commiteado como mejora.

Vale para bugs propios y, sobre todo, para bugs que reporta otro (una revisión, un agente, un issue): un mecanismo descrito en prosa que nadie ejecutó es una hipótesis, no un hallazgo.

**Bugs conocidos sin arreglar todavía: reproductor con `xfail(strict=True)`.** Un bug que se detecta pero no se arregla en el momento se documenta con un test que **especifica el comportamiento correcto** y lleva `@pytest.mark.xfail(strict=True, reason="BUG <id>: <causa>")`. La suite queda verde, y el día que alguien arregla el bug el test pasa a XPASS — que con `strict` es un **fallo**, así que obliga a sacar la marca en el mismo commit del fix. Es lo que hace cumplir el ciclo test-first para bugs: el test existe y falla *antes* de que exista el fix.

Dos reglas que hacen que el mecanismo sirva: (1) verificar con `--runxfail` que el test falla por el mecanismo descrito y no por un mock mal armado — un reproductor que "falla" por una fixture rota documenta un bug inexistente; (2) acompañarlo de **contra-casos sin marca** que fijen el comportamiento vecino correcto, porque casi todos estos fixes son guards de una línea fáciles de aplicar de más (no borrar *ningún* wikilink en vez de solo los válidos, degradar *todo* type inválido en vez de solo los que el usuario ya eligió).

Los reproductores de la auditoría 2026-08-26 viven en `tests/unit/test_audit_2026_08_*.py`; el índice de qué cubre cada uno está en `docs/audit-2026-08-26.md`.

### Spec → tests → implementación, con agentes separados

Para trabajo de varios ítems (un lote de mejoras, una tanda de fixes), el ciclo
test-first se ejecuta con **roles separados**. El motivo es concreto: cuando la
misma cabeza escribe el test y el código, el test tiende a confirmar lo que el
código hace en vez de especificar lo que debería hacer, y nadie lo nota porque
está verde.

**1. Spec (el árbitro).** Un documento de comportamiento: qué DEBE pasar, los
**contra-casos** de lo que no puede romperse, y la **superficie de API que los
tests pueden tocar** (nombres concretos de símbolos y firmas). Esto último no es
burocracia: sin eso, el que escribe los tests y el que implementa eligen nombres
distintos para lo mismo y el trabajo se tira.

La escribe quien tiene el contexto completo —los issues, el código, la evidencia
de producción—, porque un error acá se propaga a los dos agentes siguientes. Se
puede delegar, pero vuelve para revisión antes de soltarla.

**2. Tests (agente A).** Recibe **solo la spec**. Puede leer `adso/` para saber
*cómo* montar un test (qué handler invocar, cómo mockear), pero **el
comportamiento esperado sale de la spec, no del código**. Prohibido tocar
`adso/`. Lo que la spec no cubra se anota como **pregunta abierta en el informe
final** — no se inventa.

Cada test que especifica comportamiento nuevo nace con
`@pytest.mark.xfail(strict=True)`; los contra-casos nacen sin marca y deben
pasar desde ya.

**Verificar por qué falla cada test — son dos casos distintos y confundirlos es
el error más fácil de cometer:**

- *El símbolo todavía no existe* (una función, un atributo nuevo): un
  `ImportError`/`AttributeError` sobre ese símbolo **es la razón correcta**. No
  hay nada más que verificar.
- *El flujo ya existe y hoy hace lo incorrecto*: el test tiene que fallar **en la
  aserción**, no al armar el escenario. Si falla con
  `AttributeError: Mock object has no attribute 'document'`, ese test no
  especifica nada — va a seguir fallando después de implementado y le hace perder
  horas al que implementa persiguiendo un fantasma que era el mock.

En una frase: **el test falla por la ausencia del comportamiento, no por un
defecto del andamiaje del test.** Es el espejo de la regla para reproducir bugs
(donde un mock roto documenta un bug inexistente); acá un andamiaje roto hace
parecer que se especificó algo cuando no se especificó nada.

**2b. El agente de tests audita su propia cobertura contra la spec** y reporta
**qué requisitos quedaron sin test que los verifique**. No es opcional: un
requisito que ningún test ejecuta es un deseo, no un contrato — el implementador
puede dejar la suite en verde sin cumplirlo, que es justo la fuga que el método
existe para tapar. En el lote 2 aparecieron tres así (uno de ellos, "la
reconciliación corre sin cliente de embeddings", era el escenario donde el fix
más falta hacía). En el lote 1 nadie lo auditó y el hueco pasó desapercibido.

**3. El árbitro contesta las preguntas abiertas** antes de lanzar la
implementación, en un addendum a la spec. **Si una respuesta crea un requisito
nuevo, vuelve al agente de tests** — no lo cubre el implementador, que estaría
escribiendo el test de su propio código. Pasó en el lote 2: definir qué es un
"wikilink roto" obligó a agregar contra-casos que ningún test tenía. Este paso no es opcional: sin él los
dos agentes divergen. Los agentes **nunca se comunican entre sí** — todo pasa por
el árbitro, asincrónico. Si se hablan, se contaminan.

**4. Implementación (agente B).** Recibe la spec + los tests. **No puede
modificar ningún test.** La única edición permitida es sacar una marca `xfail`
de un test que su fix hizo pasar. Si un test le parece mal —contradice la spec,
mock roto, imposible de satisfacer— **escala y sigue con el resto**.

**5. Verificación (el árbitro):** que los tests no se hayan aflojado (contar
tests y asserts, buscar `assert True` / `skip` / marcas nuevas), **y recorrer la
spec requisito por requisito confirmando que cada uno tiene un test que lo
ejecuta** — sobre todo los del addendum, que son los que nacieron después de
escritos los tests. Después: suite completa, `ruff`, commit, deploy.

**Es cobertura de la SPEC, no del código.** No es "toda función tiene test" —eso
es lo que mide la herramienta de coverage, y responde *¿alguna prueba tocó esta
línea?*. Acá la pregunta es otra: *¿existe un test que falle si este requisito se
rompe?*. Los dos huecos del lote 1 estaban en código **cubierto al 100%**: las
líneas corrían, la suite verde, y el requisito sin asertar. Por eso el recorrido
se hace leyendo la spec y buscando qué test la ejecuta, nunca al revés —
arrancando desde las funciones jamás se encuentra un requisito que nadie escribió.

Ese recorrido no es redundante con el paso 2b: son dos redes distintas. En el
lote 1 nadie auditó cobertura —ni el agente ni yo— y quedaron **dos requisitos
del addendum sin verificar**: que `authors` fuera lista (el test decía
explícitamente "la spec no fija la forma: se acepta cualquiera", escrito antes
de que la fijara) y que el whitespace quedara colapsado. La implementación
resultó correcta en los dos casos, pero eso fue suerte: nada lo exigía. Contar
tests y ver la suite verde **no** detecta un requisito que ningún test ejecuta.

**Quién decide sobre un test que parece mal.** El implementador nunca: tiene
conflicto de interés directo, el test es justo lo que le bloquea. El autor del
test tiene el sesgo opuesto —lo defiende— y eso sirve de contrapeso, pero
también puede estar equivocado. **Decide el árbitro.** El criterio de fondo:
quien decide sobre un test no puede ser quien se beneficia de que sea más débil.

El árbitro sí puede editar un test, con un límite: solo para **aumentar su
fidelidad** (arreglar un mock que hacía pasar el test por accidente), nunca para
debilitar una aserción.

**Qué destapó en su primera corrida** (lote 1, 2026-08-26), que es la razón de
mantenerlo: 10 contra-casos pasaban **sin ejecutar nada** (a sus updates les
faltaba `effective_user` y `@authorized` los descartaba en silencio), y un test
e2e preexistente pasaba por accidente porque nunca seteaba `doc.mime_type` y el
`MagicMock` era truthy. Además, tener que *decidir* un contrato ambiguo en vez de
copiar lo que hacía el código destapó un bug que nadie buscaba (#63).

**Qué modelo va en cada rol.** El criterio no es la jerarquía del rol sino
**dónde hay oráculo**: el modelo fuerte va donde nada verifica automáticamente
el resultado.

| Rol | Modelo | Por qué |
|---|---|---|
| Árbitro (spec, addendum, paso 5) | Opus | Un error suyo se propaga a los dos agentes y nadie lo revisa |
| Agente de tests (A) | Opus | **Único rol sin oráculo** — un test débil se ve igual que uno bueno: verde |
| Implementador (B) | Sonnet | Los tests son un oráculo ejecutable; si la caga, la suite se pone roja |

El que sorprende es el agente de tests, porque su trabajo *parece* mecánico. No
lo es: sus tres tareas críticas son de juicio — resistir la gravedad de mirar el
código y asertar lo que ya hace, distinguir "falla en la aserción" de "falla
porque el andamiaje está roto", y el paso 2b, que es pedirle que argumente en
contra de su propio trabajo. Las dos primeras son exactamente lo que falló en el
lote 1 (10 contra-casos pasando sin ejecutar nada).

El implementador es además donde está casi todo el ahorro: es el rol que más
tokens quema (lee spec + todos los tests + los módulos, e itera hasta verde), y
el más acorralado — no toca tests, el criterio de éxito es binario, y escala en
vez de decidir. Su única fuga real (aflojar un test para pasarlo) la agarra el
paso 5 con un `git diff tests/`.

Excepción: subir el implementador a Opus cuando el diff toca **semántica de
control de flujo** — reintentos, concurrencia, orden de escritura. Se sube ese
ítem, no el lote entero.

**Cuándo NO usarlo:** un fix puntual de un solo ítem. Ahí el ciclo test-first de
arriba alcanza y montar tres roles es puro overhead.

### Harness de regresión de modelo

Antes de tocar `GEMINI_MODEL` hay que correr `scripts/llm_regression.py`, que verifica contra la API real que el modelo respete el **contrato estructural** que el resto del bot asume. No mide calidad de redacción ni de resumen: eso lo valida el usuario en el preview antes de confirmar cada nota. Mide lo que el usuario no ve — sobre todo que `validate_llm_response` no lance (si lanza, *toda* captura cae a modo degradado) y que el modelo no obedezca prompt injection.

**No es un test de pytest a propósito:** pega contra la API y quema quota, así que vive en `scripts/` para que un `pytest` local o un cambio en CI no lo dispare por accidente. Los datos (`cases.yaml`) y las baselines viven en `tests/llm_regression/`. Reglas completas en `tests/llm_regression/README.md`.

```bash
make llm-baseline                                        # baseline del modelo actual (--save)
make llm-check MODEL=gemini-3.7-flash BASE=gemini-3.5-flash-lite
```

Flags de `scripts/llm_regression.py` que los targets del Makefile no exponen:

| Flag | Para qué |
|---|---|
| `--vision-model` | evalúa un candidato de Vision por separado del modelo de clasificación |
| `--only <ids>` | corre solo esos casos de `cases.yaml` — el ciclo corto al perseguir una regla que falla |
| `--repeat N` | corridas por caso (default **3**), porque la salida del modelo no es determinística: con `1` un fallo intermitente se lee como determinístico |
| `--delay S` | segundos entre llamadas (default 1.0), para no chocar contra los 15 RPM |
| `--no-vision` | saltea el smoke de Vision — no quema RPD del bucket de Vision cuando solo se toca clasificación |
| `--provider groq` | corre el harness contra el fallback en vez de Gemini |

`ADSO_GEMINI_MODEL` overridea `GEMINI_MODEL` sin tocar código — existe para que el harness apunte a un candidato; en producción se deja sin setear. Con `--compare` el exit code refleja **regresiones contra la baseline**, no fallas absolutas: lo que decide una actualización no es que el candidato sea perfecto, sino que no empeore nada. Baseline de `gemini-3.5-flash-lite` (ago-2026): 33/34 (1 soft failure, `R5-tipo` sobre un `media_type: text`, que es informativo por diseño), p50 1.39s.

Dos detalles de diseño que costaron falsos positivos: R12 (injection) escanea frontmatter/`operation`/`params`/`summary` pero **nunca el `body`**, porque el body es transcripción legítima del input y cualquier marcador embebido aparece ahí sin que el modelo obedezca nada; y R5 (`type`) solo es regla dura cuando `media_type` no es `text`/`audio`, porque en texto y audio el type lo eligen los botones `[Tarea]`/`[Nota]` y el del LLM se descarta.

---

## Variables de entorno

```bash
# Requeridas
TELEGRAM_TOKEN
TELEGRAM_ALLOWED_USER_ID   # acepta uno o varios IDs separados por comas. La autenticación
                           # (is_authorized) usa TODOS; las notificaciones que arranca el bot
                           # (arranque, watcher, errores de job) van solo al PRIMERO de la lista
                           # (settings.telegram_allowed_user_id). Los valores no numéricos se
                           # ignoran con warning; si no queda ninguno, security.py aborta el
                           # arranque en vez de dejar el bot inaccesible en silencio.
GEMINI_API_KEY
GROQ_API_KEY               # fallback LLM cuando Gemini no responde; sin esta key el bot funciona pero sin fallback

# Opcionales
LOG_LEVEL                  # DEBUG | INFO | WARNING | ERROR — default: INFO
ADSO_GEMINI_MODEL          # overridea GEMINI_MODEL sin tocar código. Para el harness de
                           # regresión (scripts/llm_regression.py); en producción sin setear.
ADSO_GEMINI_VISION_MODEL   # ídem para GEMINI_VISION_MODEL.
ADSO_TIMEZONE              # zona horaria IANA para parsear fechas relativas ("el viernes",
                           # "mañana"). Ej: America/Argentina/Buenos_Aires. Override explícito;
                           # si falta, se usa TZ (que docker-compose ya define) y luego UTC.
                           # Sin zona correcta, los días de semana cerca de medianoche pueden
                           # resolverse con off-by-one respecto a la hora local.

# Paths (defaults para Docker)
CHROMA_DATA_DIR            # default: /app/data/chroma

# Dos nombres, dos significados. En el `.env` del host son el ORIGEN del bind mount;
# adentro del contenedor, compose los pisa con el valor que ve el proceso. Poner el
# valor del contenedor en el `.env` monta el path equivocado del host.
VAULT_PATH                 # .env: directorio del host con el vault.
                           # Contenedor: /vault (compose lo fija en `environment:`).
GOOGLE_CALENDAR_CREDS      # .env: DIRECTORIO del host con google-oauth.json y token_tasks.json
                           #       (se monta en /credentials; default ./credentials).
                           # Contenedor: el ARCHIVO /credentials/google-oauth.json, que es lo que
                           #       lee config.py. Fuera de Docker (python -m adso) solo vale esta
                           #       segunda lectura: es un path a archivo.

# Seteadas por docker-compose, no por el `.env`
TZ                         # America/Argentina/Buenos_Aires — la lee _user_tz() como fallback de
                           # ADSO_TIMEZONE, y es la zona del datetime.now() del prompt
ANONYMIZED_TELEMETRY       # false — apaga la telemetría de ChromaDB
HF_HOME                    # /app/data/hf_cache — manda el caché de Hugging Face al volumen
                           # persistente `adso-data`. El modelo de whisper NO va acá:
                           # faster-whisper lo baja con download_root a `whisper.model_dir`
                           # (/app/data/whisper)
GIT_SSH_COMMAND            # solo en el override de despliegue (docs/installation.md §4.3): key
                           # dedicada + known_hosts precargado + StrictHostKeyChecking=yes para
                           # el push del backup del vault

# Permisos Docker
ADSO_UID                   # UID del usuario del host — default: 1000
ADSO_GID                   # GID del usuario del host — default: 1000
                           # Se usan en el `user:` de compose, pero la imagen solo crea el
                           # uid/gid 1000 (`Dockerfile:26`) y le chownea /app/data. Un valor
                           # distinto corre sin entrada en /etc/passwd y sin dueño del volumen
                           # de datos: no probado, asumir que rompe el push del backup, el
                           # caché de whisper y ChromaDB.
```

---

## Decisiones clave

Políticas e invariantes que restringen cómo se escribe código nuevo. Los post-mortems de fixes puntuales viven en `docs/decisions-log.md` — leerlo antes de tocar `vault_writer`, `vault_watcher`, `GitBackup`, el flujo de confirmación o el manejo de errores de PTB.

- **La taxonomía vive en `constants.py`:** `NOTE_TYPES`, `LLM_NOTE_TYPES`, `STATUS_BY_TYPE`, `VALID_PRIORITY`, `STATUS_ON_CONFIRM`, `DEFAULT_STATUS_BY_TYPE`, `DEFAULT_EXCLUDE_DIRS` y `ALWAYS_EXCLUDE_DIRS` se definen una sola vez ahí (`DEFAULT_STATUS_BY_TYPE` se **deriva** de `STATUS_ON_CONFIRM` en vez de repetir los tres valores); `llm_schema.VALID_TYPES/VALID_STATUS` (lo que el LLM puede proponer) y `vault_writer.VALID_TYPES/VALID_STATUS` (lo persistible) se derivan de esas constantes. Antes cada módulo tenía su copia y el default por tipo estaba duplicado en `capture.py` y `jobs.py` con dos nombres. Lo mismo para los helpers compartidos que salieron de la pasada de simplificación de 2026-09: `build_note_metadata` (embeddings), `build_index_note` y `_reserve_name_sync` (vault_writer), `count_unclassified_inbox`/`reply_blocked`/`has_destination` (bot_utils — `count_unclassified_inbox` tiene hoy un solo caller, `_cb_confirm`: `handle_clasificar` y `_gather_vault_counts` reimplementan la regla cada uno por su lado, así que ahí sí falta la pasada de unificación), `inherit_inbox_frontmatter` (capture), `_walk_ver_tambien` (el único recorrido del bloque "## Ver también" en vault_writer), `_watcher_callbacks` (bot.py) y las tablas `_query_callbacks`/`_update_callbacks` de `handle_callback` (funciones, no constantes: los tests parchean `callbacks._cb_ocr` y una tabla construida al importar guardaría la referencia original). Al agregar un caller nuevo, usar el helper — no volver a copiar la regla. Contratos en `tests/unit/test_simplification_2026_09.py`.

- **Taxonomía de `type`:** `type` refleja propósito, no formato de origen. Los tipos son: `reference`, `task`, `idea`, `project-index`, `area-index`. No existe `type: draft` — cuando el LLM no puede clasificar con confianza usa `type: idea` con `status: pending-classification`. `project-index` y `area-index` son auto-generados por el bot (no por el LLM) y requieren `description` obligatoria al crear — el bot la pide y no permite omitirla. No existe `type: paper` — un paper es un `reference` con campos académicos opcionales. Los que **efectivamente se escriben** son `authors`, `year`, `journal`, `doi`, `keywords` y `read_status`: son los únicos declarados en `_GEMINI_RESPONSE_SCHEMA`, y el constrained decoding no puede emitir otros. `methods`, `dataset`, `contribution` y `conclusions` figuran en `ALLOWED_FRONTMATTER_KEYS` pero **nada los popula**: `extract_paper_sections` sí saca métodos y conclusiones del PDF, y los manda al prompt como texto, no al frontmatter. El lifecycle de lectura de papers se maneja con tasks (`"leer paper X"`).

  Para identificarlos, el único criterio confiable hoy es `read_status` (que es el que usan los reportes). El tag `paper` lo agrega **solo el camino de arXiv**; en el de PDF pasa lo contrario: `paper` está en `_TYPE_TAGS` y el sanitizador lo borra si el LLM lo propone, así que un paper subido como PDF nunca queda con `#paper`. Asimetría conocida, no intencional.

- **Destino en preview (`build_preview`):** project → `01-Projects/...`; area → `02-Areas/...`; sin destino (cualquier tipo) → `00-Inbox`. Modo degradado: `type: idea` + `status: pending-classification` → inbox.

- **Routing de destino (`_resolve_dest_dir`):** todos los tipos (`reference`, `task`, `idea`) siguen el mismo orden: project > area > Inbox/None. `task` con project va a `01-Projects/{project}/` aunque tenga area seteada.

- **Creación de proyecto/área desde bot:** `_extract_name_from_command()` parsea el nombre directamente con regex para patrones simples (`crear proyecto "X"`, `nuevo proyecto X`). Solo llama al LLM cuando el patrón no es reconocible (ej: "quiero un proyecto para mi tesis"). El intent ya viene confirmado por el botón, solo hace falta el nombre.

- **`params` del modo manage con `properties` declaradas (`_GEMINI_RESPONSE_SCHEMA`):** el constrained decoding de Gemini solo puede emitir claves presentes en el schema, así que el `params: {"type": "OBJECT"}` sin `properties` devolvía siempre `{}` — incluso con el nombre del proyecto visible en el input. `_validate_manage_payload` entonces lanzaba `LLMResponseError` y **todo el modo manage por texto libre caía a modo degradado**: el fallback de `_cb_intent_create` (`manage.py`, cuando `_extract_name_from_command` no reconoce el patrón) proponía el texto crudo del usuario como nombre del proyecto tras gastar 3 reintentos. Detectado por `scripts/llm_regression.py` contra el modelo en producción. Guard de regresión en `test_manage_params_declares_properties`.

- **Presupuesto de reintentos de `classify` por tipo de error (lote 3, #43):** el tipo del error se decide por **`isinstance`**, nunca por el texto del mensaje. "Es rate limit" ≡ `APIError` con `code == 429` (`_is_rate_limit_error`); una excepción que no es `APIError` va por el camino genérico aunque su mensaje diga `429`/`PerDay`. Una vez confirmado el 429 tipado, sí se parsea el payload para separar cuota diaria de RPM y leer `retryDelay` (de `APIError.details` vía `_find_retry_delay`, con el regex sobre `str(e)` como respaldo). Presupuestos: error de red/API → 3 intentos con `RETRY_DELAYS`; **respuesta inválida (`LLMResponseError`, de parseo o de validación) → 2 intentos y un único tiro a Groq**, porque reintentar el mismo prompt contra el mismo modelo casi nunca arregla un JSON malformado, pero otro modelo puede. Groq no se reintenta. `RETRY_DELAYS = [1, 2]`: hay una espera por reintento y el último intento no duerme, así que una lista de tres declaraba un delay inalcanzable.

- **Modo degradado:** si Gemini no responde, el input se guarda en `00-Inbox/` con `status: pending-classification`. Un cron reclasifica cuando la API vuelve, con la misma regla de body que la captura (`VERBATIM_BODY_MEDIA` en `constants.py`): para `text`/`audio` el body es el que mandó el usuario, nunca la reescritura del LLM (#64); para document/image/link el LLM genera el body. El cron (`reclassify_inbox`) se salta la pasada si hay cualquier flujo interactivo en curso — `_PENDING_FLOW_KEYS` cubre todas las keys de flujo (nota/operación/audio/PDF extraído/PDF escaneado/read_status/arXiv/reporte), alineado con `_has_pending_keyboard`, para no notificar en medio de una interacción.

- **Dedup de recursos (`save_resource`):** al copiar a `03-Resources/`, la reutilización de un archivo existente se decide por hash SHA-256 del contenido (con short-circuit por tamaño), no solo por tamaño. Dos archivos distintos del mismo tamaño ya no se confunden — el nuevo se guarda con sufijo numérico en vez de descartarse. Escritura vía `shutil.copy2`; hashing por chunks (memory-safe en RPi4).

- **Dedup de documentos subidos (issue #53):** al recibir un documento por Telegram, `handle_document` calcula el SHA-256 del temporal y busca en `03-Resources/` un archivo con ese contenido (`find_resource_by_hash` en `vault_writer.py`, mismo criterio que `save_resource`: short-circuit por tamaño, hash solo si hay candidato). Si alguna nota lo referencia — por `source_file: "[[archivo]]"` en el frontmatter o por el embed `![[archivo]]` en el body, 05-Archive excluido — se muestra el teclado `[Cancelar]` `[Crear igual]` (`build_duplicate_keyboard`, compartido con el duplicado de arXiv) listando las notas dueñas, y `[Crear igual]` (`pending_duplicate_doc` → `_cb_doc_create_anyway`) retoma el flujo normal via `_dispatch_document`. La clave es el **hash, no el nombre**: dos archivos distintos pueden llamarse igual y el mismo archivo puede llegar con nombres distintos. Un recurso huérfano (sin nota que lo referencie) no bloquea nada. El chequeo va antes de la extracción y del LLM, así que un duplicado no gasta quota.

- **Google Tasks (estado real):** hoy solo hay **push unidireccional** — al confirmar una task se crea en la lista `ADSO` de Google Tasks (`create_task`); el `task_id` devuelto no se persiste todavía. El sync bidireccional descrito abajo es **diseño, no implementado** (ver `docs/improvements-2026-07.md` §5). Diseño previsto: sync cada 30 min (`sync.interval_minutes`), Calendar y Tasks reconciliados en el mismo cron; fuentes de verdad → contenido/estructura de la nota al vault; `scheduled`, `due_date`, `status: done` y título → bidireccional (gana el último cambio); borrar una task en Google Tasks movería la nota a `00-Inbox/` con `status: pending-classification`. Requiere primero persistir `gtask_id` en el frontmatter (§5.1) y el job de reconciliación (§5.2).

- **Google Tasks:** lista `ADSO` dedicada, y hoy solo se le dan de alta tareas (el borrado y la lectura de listas externas son diseño de Fase 6, ver el bullet "estado real" arriba). `due_date` va al campo de fecha límite de Google Tasks — Google Calendar lo muestra automáticamente como chip, sin crear evento separado. Modelo semanal: planificación + revisión via reporte. Las tasks son intenciones de trabajo (scope = proyecto/área), no punteros a notas individuales. El campo `notes` de Google Tasks recibe: descripción original del usuario + proyecto/área + prioridad + horario si tiene hora no-medianoche. **No incluye links `obsidian://`** — no funcionan desde Google Tasks/Calendar. Las tasks no se editan via ADSO — cambios se hacen en Google Tasks/Calendar directamente. Si el push falla, el bot notifica al usuario por Telegram con el motivo; `tasks.debug: true` en `config.yaml` activa notificación también en push exitoso. Token OAuth en `/credentials/token_tasks.json`; si expira, re-autenticar con `scripts/auth_google_tasks.py` (ver procedimiento headless en el script).

- **Syncthing bidireccional:** ADSO es el escritor principal (toda creación de notas pasa por Telegram). Los clientes Obsidian pueden editar notas existentes. `VaultWatcher` detecta los cambios externos via `inotify` y re-embeds automáticamente. Al borrar una nota externamente, además de eliminar su embedding, se limpian los wikilinks rotos en bloques `## Ver también` de otras notas (`remove_broken_wikilinks` en `vault_writer.py`) — el bot notifica por Telegram si hubo notas modificadas. Mover una nota no rompe links porque los wikilinks usan solo el stem del archivo, no el path. Las escrituras del **propio bot** no vuelven por este camino: `mark_bot_written` las registra y `was_bot_written` las reconoce durante `BOT_WRITE_GRACE_SECONDS` (10 s) sin consumir la marca. Tiene que ser una ventana y no un consumo único porque `create_note` produce **dos** eventos inotify por nota —el placeholder `O_EXCL` que reserva el nombre y el `os.replace` de la escritura atómica— y el set anterior absorbía solo el primero: el segundo se procesaba como edición externa y gastaba un embedding de más y una entrada duplicada en el commit de backup (#66). Una edición externa posterior a la ventana se procesa normalmente. El watcher colapsa los eventos por path con una ventana de 2 segundos, pero **no descarta el segundo**: `_schedule_trailing_change` lo re-agenda para el final de la ventana (trailing edge), así que una ráfaga —`on_created` + `on_modified` de una escritura nueva, o los dos autosaves seguidos de Obsidian— produce dos pasadas, una inmediata y una al cierre, en vez de una sola con el contenido intermedio. Descartar el evento perdía el último save y el estado final no se indexaba hasta el reindex nocturno (F2 de `docs/audit-2026-07-31.md`). Además ignora archivos ocultos (`_is_hidden`): los temporales `.adso-tmp-*` de la escritura atómica y cualquier dotfile — sin ese filtro los temporales se indexaban como notas fantasma en ChromaDB y contaminaban el mensaje del commit de backup.

- **Conflictos Syncthing:** ADSO no resuelve, solo notifica. El usuario resuelve manualmente.

- **Caché de parsing del vault (`vault_cache.py`):** todas las funciones de scan de `vault_search.py` leen con `parse_cached`, que cachea el resultado del parse keyed por `(mtime_ns, size)`. Correctness-preserving: cualquier modificación de una nota cambia el mtime y la entrada se invalida sola en el siguiente `stat()` — no hay acoplamiento con `VaultWatcher` ni ventana de staleness. El costo dominante de un scan en la RPi4 (SD lenta) es el `read()+parse`, no el `rglob`. Una captura corre `get_all_tags` dos veces (escanea todo el vault); con el caché el segundo scan baja ~69% (medido: 427→132 ms con 500 notas en RPi4). LRU acotado a 2000 entradas. El frontmatter devuelto es siempre una copia fresca para que mutaciones del caller no corrompan el caché. Métricas (`entries`, `hit_ratio`) expuestas en `/status`.

- **Escrituras atómicas al vault (`_atomic_write_sync` en `vault_writer.py`):** toda escritura de `.md` (`create_note`, `append_to_note`, `set_property`, limpieza de wikilinks) usa temp en el mismo directorio + `fsync` + `os.replace`. Un crash a mitad de escritura (OOM en RPi4, `docker stop`) nunca deja la nota truncada. Regla de oro: sin pérdida de datos. El temporal usa sufijo `.tmp` (no `.md`): además de ser hidden (`.adso-tmp-*`), el sufijo distinto de `.md` hace que el filtro del `VaultWatcher` lo saltee aunque el `_is_hidden` fallara, y evita que un `git add -A` concurrente lo commitee.

- **Sanitización de path (`_safe_component` en `vault_writer.py`):** `project`/`area`/`section` del frontmatter (LLM) y `name`/`project` de operaciones de gestión (`manage.py`) se sanitizan contra path traversal (`..`, separadores, dots iniciales) antes de concatenarse al path del vault. Valor inválido → se descarta (cae a Inbox / se rechaza la operación). Además `create_note` verifica `dest_dir.resolve().is_relative_to(vault_path)` como defensa en profundidad. Complementa el `Path(...).name` que ya protegía `save_resource`.

- **Neutralización de tags en el prompt (`build_user_message` en `llm_client.py`, que es por donde pasa `classify`):** el contenido externo se inserta en `<input>` tras neutralizar cualquier `<input>`/`</input>`/`<system>`/`<user_context>` literal que traiga (se le inserta un espacio tras el `<`, preservando el `<` legítimo de código/matemática). Cierra el vector de escape del wrapper para PDFs/OCR/abstracts.

- **Proteger y detectar son dos pasos distintos, en ese orden (`build_user_message`, #44C):** el `user_context` (el caption que acompaña una imagen, por ejemplo) se pasa por `check_injection_risk` **tal como lo mandó el usuario**, y recién después se le sacan los `<>` que protegen el wrapper `<user_context>`. Al revés —que es como estaba— la limpieza desarmaba el intento justo antes de mirarlo: un texto con tags embebidos dejaba de matchear los patrones pero llegaba perfectamente legible al modelo. Si el detector dispara, el `user_context` se descarta entero (no se sanea: sanear un intento de inyección es un juego perdido, y el campo es una comodidad).

- **Los patrones de inyección cubren voseo y tildes (#44B):** `ignor[aá]`, `olvid[aá]`, `actuá como` — las formas que este usuario efectivamente escribe, que hasta el lote 3 eran justo las que no se detectaban. Se ampliaron con clases de carácter sobre los patrones existentes, **no** con alternativas nuevas, para no tocar el contexto de frase (`… instrucciones`, `como + artículo`) que es lo que evita marcar `actualizar`, `olvidadizo` e `ignorante`. Contra-casos en `tests/unit/test_lote3_llm_config.py::TestInjectionPatternsVoseo`: un detector que marca todo es tan inútil como uno que no marca nada — el aviso del preview deja de significar algo.

- **Tareas de fondo con referencia fuerte (`spawn_tracked` en `bot_utils.py`):** reemplaza `asyncio.create_task` para trabajo fire-and-forget (push a Tasks, indexado, re-embed del watcher). Guarda referencia fuerte (evita GC prematuro que cancele la tarea) y loguea excepciones. El `VaultWatcher` usa su propio set y lo drena en `stop()`.

- **Orden "crear antes de descartar" (regla de oro, sin pérdida de datos):** `_cb_confirm` lee `pending_note`/`clasificar_inbox_path` con `get` y los popea **recién después** de que `create_note` retorne — si la escritura falla (disco lleno, I/O de la SD), el estado sigue en `user_data` y un segundo `[Confirmar]` reintenta. Sin esto se perdía definitivamente el texto de audio/OCR/Vision, que no existe en ningún otro lado. El temporal del recurso adjunto también se borra recién tras la escritura (el reintento lo necesita; `save_resource` dedup por hash, así que no duplica). Por el mismo motivo `reclassify_inbox` crea la nota nueva **antes** de borrar la del Inbox (antes hacía `delete_note` → `create_note`: un fallo de creación evaporaba la nota).

- **`03-Resources/` no entra a ningún scan ni al índice semántico (`ALWAYS_EXCLUDE_DIRS` en `constants.py`):** la taxonomía la define como carpeta de adjuntos, así que un `.md` ahí no es una nota del vault. La exclusión **no** pasa por `exclude_dirs` sino que se concatena siempre dentro de `_scan_vault`, y el motivo es que ampliar `_DEFAULT_EXCLUDE` no habría arreglado nada: los callers reales pasan su propia lista (`_get_existing_tags` en `bot_utils.py` arma una literal para sacar `00-Inbox`), que es justo el camino que alimenta el prompt de clasificación. Misma forma que `_index.md` en `embeddings.should_index`, que desde 2026-09-18 aplica la misma constante (#72): hasta entonces la analogía era falsa —`should_index` no miraba la carpeta— y un `.md` tirado ahí se embebía igual, así que aparecía en `/buscar` mientras `/reporte`, el descubrimiento de tags y `find_by_property` no lo veían nunca. No es configurable porque no es una preferencia sino la taxonomía. Si una nota tiene que ser buscable, no va en `03-Resources`. Issues #58 y #72.

- **El LLM no puede crear carpetas (`canonicalize_destination` en `llm_client.py`, #71):** el `project`/`area` que devuelve el modelo se compara contra los existentes (strip + casefold) y se reemplaza por el **nombre canónico guardado**; si no matchea ninguno se descarta, junto con `section`, que sin proyecto no significa nada. Antes `_resolve_dest_dir` concatenaba el valor crudo y `create_note` hacía `mkdir(parents=True)`: un nombre alucinado —o simplemente `"Tesis "` al lado de un `tesis/` que ya existía— creaba un segundo proyecto sin `_index.md` y partía las notas en dos carpetas. Se aplica dentro de `classify`, que es el único lugar que tiene a la vez el payload y las listas de existentes, y **no** al modo `manage`: crear un proyecto nuevo es exactamente lo que ese modo hace. Descartar el destino no descarta la nota — cae a Inbox con todo su frontmatter, y el usuario la reubica con `[Reubicar]`.

- **`ensure_vault_structure` aborta si el vault no existe (#70):** era un `mkdir(parents=True, exist_ok=True)`, así que un typo en `VAULT_PATH` o un bind mount que no montó arrancaban el bot contra un vault vacío recién creado — con el backup reportando `not_repo` y el watcher mirando el directorio equivocado. Ahora lanza `RuntimeError` nombrando el path y la variable. Las cinco carpetas se siguen creando **dentro** de un vault que ya existe: lo que se valida es la raíz, no la estructura.

- **Los jobs diarios corren en la zona del usuario (`user_tz` en `bot_utils.py`, #68):** PTB fija el scheduler en UTC salvo que se le pase tzinfo, así que `reindex.time: "03:00"` disparaba a las 00:00 locales. El `time` que recibe `run_daily` ahora lleva `tzinfo=user_tz()`. `user_tz` es la misma resolución `ADSO_TIMEZONE` → `TZ` → UTC que ya usaba `_parse_date_from_text`, movida a `bot_utils` para que no la importe `bot.py` desde un handler; `capture._user_tz` quedó como alias.

- **`create_note` siempre escribe un `status` (`DEFAULT_STATUS_BY_TYPE`, #69):** un `status` que llega `None`, vacío o en blanco se trata como **ausente** (se normaliza antes de la coacción de tipo/status, si no la coacción lo convertía en `pending-classification` y el default no se aplicaba nunca), y después se completa con el default del tipo. `area-index` no lleva `status`: su set en `STATUS_BY_TYPE` está vacío a propósito. Un valor inválido pero no vacío —`"banana"`— se sigue coaccionando como antes.

- **El tag de un índice se normaliza; el nombre no (`build_index_note` en `vault_writer.py`, #58):** al crear proyecto o área, el nombre va crudo a `project`/`area` —direccionan la carpeta en disco, y kebab-casearlos apuntaría a un directorio inexistente— pero al tag se le aplica `_to_kebab`. Sin eso, crear `ROCKY` producía el tag `ROCKY` conviviendo con los `rocky` que emite el sanitizador en cualquier otra nota: los únicos 5 tags no-kebab del vault real eran exactamente los 5 nombres de proyecto/área, y partían en dos el vocabulario que el prompt reutiliza. Desde 2026-09 el `_index.md` lo construye un solo helper para el flujo de gestión (`manage.py`) y la siembra (`seed_vault`): antes la siembra escribía el nombre crudo como único tag, sin el marcador `system`.

- **Descubrimiento de proyectos y áreas (`_get_existing_items`):** lee los subdirectorios de `01-Projects/` y `02-Areas/` directamente (no busca por `type: area-index`). Si el subdirectorio tiene `_index.md` con campos `project:`/`area:` y `description:`, los usa; si no, usa el nombre del directorio. Esto garantiza que cualquier área o proyecto con al menos una nota en su carpeta aparece en los reportes y teclados, aunque no tenga índice. El escaneo (iterdir + parse de cada `_index.md`) corre en `asyncio.to_thread` — se ejecuta en todo flujo de clasificación antes de cada `classify()` y no debe congelar el event loop en la RPi4. `/status` también threadiza su conteo del vault (`_gather_vault_counts`: `rglob` + `parse_cached` en vez de `read_note`).

- **Embedding único por texto:** `EmbeddingsClient.compute_embedding()` expone el cálculo para reutilizar el vector. En captura, el body se embebe una vez en el preview (`_body_embedding` viaja en el payload de `pending_note`) y se reutiliza al indexar en `_cb_confirm` **solo si el body no cambió** (sin "Ver también" ni recurso adjunto — si cambió, se recomputa). **Toda la sugerencia de links pasa por `_suggest_links()` (`capture.py`)** — no llamar a `query_similar`/`compute_embedding` directamente desde un flujo de captura: la regla de qué texto se embebe y qué vector puede reutilizarse vive ahí y en ningún otro lado. El helper devuelve `(links, vector_usado)`, y ese vector **solo puede guardarse como `_body_embedding` si el texto consultado era el body**: el flujo de arXiv busca por el abstract y descarta el vector a propósito, porque guardarlo indexaría la nota con un embedding ajeno a su texto. Caracterizado en `tests/unit/test_capture_links.py`. En `knowledge_query.retrieve`, el reintento con umbral relajado reutiliza el embedding de la primera pasada. `query_similar` e `index_note` aceptan el vector precomputado como parámetro opcional. El cliente `genai.Client` se instancia una vez y se reutiliza (lazy) en `embeddings.py`, `llm_client._get_genai_client()` y `reporters.py`.

- **Lock compartido de jobs pesados (`_vault_heavy_lock` en `jobs.py`):** `reclassify_inbox` y `reindex_job` comparten un `asyncio.Lock` — el reindex nocturno espera el lock, la reclasificación saltea la pasada si está tomado. Evita CPU/red concurrente de ambos crons en la RPi4. El reindex además usa `vault_cache.parse_cached` (no relee notas sin cambios desde la SD).

- **Observabilidad de latencia de captura (`Stopwatch` en `bot_utils.py`):** `_classify_and_preview` cronometra `scan` (los dos scans del vault), `classify` y `links`, y emite **una** línea INFO al salir — por *todos* los caminos, incluido el modo degradado y el de excepción (va en un `finally`, porque el camino que más importa medir es justo el que falla), que es justamente el lento (quema el presupuesto de reintentos: 3 intentos para un error de red, 2 más un tiro a Groq para una respuesta inválida). Formato: `Captura (text): scan 0.13s | classify 6.11s | links 1.24s | total 7.48s`. `total` mide desde la construcción del cronómetro, así que `total` >> suma de etapas señala un tramo sin instrumentar. Antes no había ninguna marca de tiempo entre el inicio de la llamada al LLM y el preview: las únicas anclas del log eran la línea que emite el SDK de Gemini al abrir la request y el `Nota creada` de `vault_writer`, que llega *después* de que el usuario confirma y no mide nada del bot. El reloj es inyectable (`clock=`) para tests, misma convención que el `now` de `_parse_date_from_text`. El flujo de arXiv (`_classify_and_preview_arxiv`) todavía **no** está instrumentado.

- **Ruido de log (`logging_setup.py`):** la config de logging vive en su propio módulo, no en `__main__.py`, porque importar `__main__` arranca el bot y la config no se podía testear. Se silencia `apscheduler.executors.default` a WARNING, **no `apscheduler` entero**: el logger del scheduler avisa arranque y `Run time of job was missed`, que es la señal de que el event loop se está bloqueando. Motivo: 2880 de las 3001 líneas de un día (96%) eran las dos INFO por corrida del `heartbeat_job`, y encontrar las ~23 del bot exigía filtrar a mano.

- **Latencia observada (RPi4, ago-2026, vault de 86 notas, 40 llamadas medidas):** `classify` tiene un piso de 1,5 s y p50 ~2,2 s, y **no depende del tamaño**: texto corto (228 tok de salida) p50 4,1 s vs texto largo (341 tok) p50 2,0 s, y el tope real de un PDF (3509 chars — `build_classify_content` en `document_extractor.py` recorta un documento genérico de más de 3500 chars a `[:2500] + [-1000:]`) p50 2,69 s. Ningún input legítimo pasa de ~3 s. Lo que sí pasa es que **~20% de las llamadas hacen un stall del lado del servidor**: mismo input, mismos token counts, **una sola** request HTTP con `200 OK` — medidos 5,7 / 6,7 / 10,1 / 19,6 / 34,4 / 35,0 s. No es reintento interno del SDK (verificado con `httpx` en DEBUG) ni rate limit (medido a ~7 RPM contra un límite de 15). Free tier, sin error: no hay nada que arreglar del lado del bot salvo **no esperarlo** — que es lo que hace `CLASSIFY_TIMEOUT_MS` (ver el bullet siguiente). Complementos: scans del vault 0,02-0,41 s, `compute_embedding` 1,2 s, y el setup de conexión es irrelevante (DNS 0,01 s + TCP/TLS 0,11 s + construcción del cliente 0,14 s = 0,26 s). Corolario para optimizar: recortar tokens de salida **no** compra latencia — en particular, que el LLM genere un `body` que para `text`/`audio` se descarta es desperdicio de quota, no de tiempo.

- **Timeout por llamada en `classify` (`CLASSIFY_TIMEOUT_MS = 12_000`):** va en el `GenerateContentConfig` de `_call_gemini`, **no en el cliente** — `_get_genai_client()` es compartido con Vision, y rasterizar un PDF escaneado tarda legítimamente mucho más. Tres detalles que rompen todo si se equivocan: `HttpOptions.timeout` está en **milisegundos** (poner `8` aborta cada llamada a los 8 ms y manda toda captura a modo degradado); **la API impone un piso de 10 s** y rechaza cualquier valor menor con `400 INVALID_ARGUMENT` (`Manually set deadline 8s is too short`) *sin llamar al modelo*; y el techo tiene que quedar por debajo de los stalls que el timeout existe para cortar. Un timeout entra por el camino genérico de reintentos (no es rate limit ni respuesta inválida), así que conserva los 3 intentos: como el stall es intermitente, el reintento suele resolver más rápido de lo que hubiera tardado esperarlo. Efecto esperado: un stall de 35 s pasa a ~13 s (aborta a los 12, espera 1, reintenta). Costo: ~10 requests extra por día contra un tope de 1000+ RPD. Tests en `tests/unit/test_classify_timeout.py`.

- **Un cambio en la config de la request se valida contra la API real, no solo con mocks (post-mortem 2026-08-27/28):** `CLASSIFY_TIMEOUT_MS` se deployó en `8_000` y **toda** captura cayó a modo degradado durante un día — la API rechazaba el deadline con un 400 antes de llegar al modelo. Ningún test de unidad podía verlo: el piso vive en el servidor y los mocks aceptan cualquier número (el bound del test era `5_000..30_000`, y 8000 lo cumplía). `scripts/llm_regression.py` sí lo habría agarrado, porque llama a `_call_gemini` de verdad — pero el harness estaba documentado como el paso previo a tocar `GEMINI_MODEL` nada más. Regla ampliada: **correrlo también antes de tocar cualquier parámetro del `GenerateContentConfig`/`HttpOptions`** (timeouts, schema, mime type). El guard mockeable que queda es un piso duro sobre la constante, anclado al mensaje de error que lo reportó.

- **El mensaje de reintento no afirma que el servicio esté caído:** decía `"Servicio caído, reintento N/3"`, que es falso para el caso más frecuente — en un stall la API responde `200 OK`, solo que tarde. Ahora dice `"Gemini no responde a tiempo"`, que es cierto tanto si hay stall como si la API está realmente caída.

- **Watchdog de proceso (`watchdog.py`), no sidecar:** el heartbeat *detectaba* un cuelgue del event loop pero nadie actuaba. La solución obvia —que `heartbeat_job` note que corre atrasado y salga— **no puede funcionar**: es un job de apscheduler sobre el mismo loop que vigilaría, así que un loop bloqueado impide que corra y que note nada; solo atraparía los stalls transitorios, que se recuperan solos. El watchdog es un **thread del SO** que mira la antigüedad de `/tmp/adso_heartbeat` y llama a `os._exit(1)` para que `restart: unless-stopped` levante el proceso. Tres cosas que no se pueden cambiar sin romperlo: (1) **`os._exit`, no `sys.exit`** — `sys.exit` en un thread secundario levanta `SystemExit` solo en ese thread y deja el proceso colgado, sin watchdog; (2) el umbral (300 s) es **deliberadamente más lento que el healthcheck** (~120 s), que da visibilidad mientras el watchdog actúa: un bot lento aparece `unhealthy` antes de que nadie lo mate; (3) un heartbeat **ausente** no es lo mismo que uno viejo — el thread arranca antes del primer beat, así que la referencia cae al arranque del watchdog y no a "hace infinito", que sería un restart loop. Se descartó el sidecar `autoheal` porque exige montar `/var/run/docker.sock`, que es root-equivalente en el host, en un despliegue que corre `no-new-privileges` y `cap_drop: ALL`. Límite conocido y aceptado: un cuelgue que retenga el GIL bloquea también al thread — ningún watchdog en proceso cubre eso. Al reiniciar deja un marcador (`consume_trip_marker`) y el arranque avisa por Telegram; si no, un cuelgue seguido de reinicio automático es invisible.

- **Hardening Docker:** `docker-compose.yml` corre con `no-new-privileges` y `cap_drop: ALL` (el bot procesa PDFs/imágenes no confiables con pymupdf/Pillow/tesseract). El backup SSH usa key dedicada + `known_hosts` precargado con `StrictHostKeyChecking=yes` — nunca `~/.ssh` completo ni `StrictHostKeyChecking=no` (ver `docs/installation.md` §4.3).

### Post-mortems movidos a `docs/decisions-log.md`

El *porqué* detrás de código que parece innecesariamente defensivo. Leerlo antes de tocar estos módulos:

- **`vault_writer`:** Construcción del `frontmatter.Post` (`_build_post` / `load_post`); Limpieza de wikilinks acotada al bloque `## Ver también`.
- **`GitBackup`:** Git backup fuera del event loop; Flush del backup al shutdown.
- **`vault_watcher` / embeddings:** `on_moved` + drenado de `bot_written_paths`; Embedding inline al confirmar.
- **Frontmatter editado a mano:** Valores no-string; YAML corrupto.
- **Medios (`callbacks.py` / `input.py`):** Render de PDFs escaneados fuera del event loop; Preview completo y copiable del texto extraído; Caption de imagen reutilizado como descripción; Límite de tamaño post-descarga.
- **Estado y errores:** Limpieza del estado de gestión (`pop_manage_state`); Error handler global de PTB.
