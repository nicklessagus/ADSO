# Instalación y puesta en marcha de ADSO

Guía paso a paso para levantar ADSO en cualquier máquina con Docker (desarrollo local, RPi4, servidor).

---

## Requisitos previos

- Docker y docker-compose-v2 instalados
- Token de bot de Telegram
- API key de Gemini (Google AI Studio)
- Directorio vacío para el vault de Obsidian

### Instalar docker-compose-v2 (Ubuntu/Debian)

```bash
sudo apt install docker-compose-v2
```

### Agregar tu usuario al grupo docker (evita usar sudo)

```bash
sudo usermod -aG docker $USER
newgrp docker
```

---

## 1. Obtener credenciales

### Token de Telegram

1. Abrí Telegram y buscá `@BotFather`
2. Mandá `/newbot` → nombre → username (debe terminar en `bot`)
3. BotFather te entrega el token: `123456789:ABCdef...`

Para obtener tu `TELEGRAM_ALLOWED_USER_ID`:
- Buscá `@userinfobot` en Telegram → mandá cualquier mensaje → te dice tu ID numérico

### API key de Gemini

1. Entrá a [aistudio.google.com](https://aistudio.google.com)
2. "Get API key" → "Create API key"
3. Copiá la key

### API key de Groq (fallback LLM)

Groq se usa como LLM de respaldo cuando Gemini no responde. Sin esta key el bot igual funciona, pero no tiene fallback ante fallos de la API primaria.

1. Registrate en [console.groq.com](https://console.groq.com)
2. API Keys → "Create API Key"
3. Copiá la key

### Google Tasks (opcional)

Sin esto el bot funciona normalmente — solo no sincroniza tareas con Google Tasks.

1. Ir a [console.cloud.google.com](https://console.cloud.google.com) → crear un proyecto (ej: `ADSO`)
2. APIs & Services → Library → buscar **Google Tasks API** → habilitar
3. APIs & Services → Credentials → **Create Credentials** → en el wizard:
   - API: `Google Tasks API`
   - Tipo de datos: `Datos de los usuarios` → Siguiente
   - Completar pantalla de consentimiento (nombre de app, email) → Guardar y continuar
   - Permisos: dejar vacío → Guardar y continuar
   - Tipo de aplicación: **Aplicación de escritorio** → Crear
4. Descargar el JSON de credenciales → guardarlo como `google-oauth.json` en un directorio local (ej: `./credentials/`)
5. En la pantalla de consentimiento OAuth → sección **Test users** → agregar tu cuenta de Google. Sin este paso el script de auth falla con `access_denied`.
6. Correr el script de autenticación una vez:

```bash
python scripts/auth_google_tasks.py --creds /ruta/al/directorio/credentials/google-oauth.json

# Si la RPi4 no tiene browser: el script imprime una URL, abrirla en otra máquina,
# autorizar, pegar el código de vuelta en la terminal.
```

7. El script genera `token_tasks.json` en el mismo directorio que el JSON de credenciales.
8. Configurar en `.env` — apuntar al **directorio** (no al archivo):

```bash
GOOGLE_CALENDAR_CREDS=/ruta/al/directorio/credentials
```

El docker-compose monta ese directorio como `/credentials/` dentro del contenedor.
Si no usás Docker, la variable también funciona con la ruta local del directorio.

---

## 2. Configurar el proyecto

```bash
git clone git@github.com:nicklessagus/ADSO.git
cd ADSO

# Variables de entorno
cp .env.example .env
```

Editá `.env`:

```bash
# ─── Requeridas ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN=<token del BotFather>
TELEGRAM_ALLOWED_USER_ID=<tu ID numérico>
GEMINI_API_KEY=<tu API key de Gemini>
GROQ_API_KEY=<tu API key de Groq>       # fallback LLM cuando Gemini no responde

# ─── Opcionales ───────────────────────────────────────────────────────────────
# ANTHROPIC_API_KEY=                    # LLM secundario alternativo
# LOG_LEVEL=INFO                        # DEBUG | INFO | WARNING | ERROR

# ─── Paths (defaults para Docker) ─────────────────────────────────────────────
# VAULT_PATH=/vault                     # directorio local de las notas
# CHROMA_DATA_DIR=/app/data/chroma      # persistencia de ChromaDB
# GOOGLE_CALENDAR_CREDS=/ruta/al/directorio/credentials  # directorio con google-oauth.json + token_tasks.json

# ─── Permisos de archivos (Docker) ────────────────────────────────────────────
# El contenedor corre con este UID/GID para que los archivos del vault
# sean del usuario del host (no root). Obtener con: id -u && id -g
# ADSO_UID=1000
# ADSO_GID=1000
```

```bash
# Configuración del bot
cp config.yaml.example config.yaml
```

El `config.yaml` por defecto es válido para empezar. Ajustá a gusto (ver `docs/configuration.md`).

---

## 3. Crear el vault

```bash
mkdir -p /ruta/a/tu/vault
```

El bot crea la estructura de carpetas (`00-Inbox`, `01-Projects`, etc.) automáticamente al arrancar.

---

## 4. Arrancar

```bash
docker compose up --build
```

Deberías ver:

```
adso-bot | [adso.vault_writer] INFO: Estructura del vault verificada: /vault
adso-bot | [adso.bot] INFO: ADSO iniciando — vault en /vault
adso-bot | [apscheduler.scheduler] INFO: Scheduler started
adso-bot | [telegram.ext.Application] INFO: Application started
```

Para correr en background:

```bash
docker compose up -d --build
docker compose logs -f   # ver logs
```

---

## 5. Verificar

Abrí Telegram, buscá tu bot y mandá cualquier mensaje de texto. El bot debería responder con un preview de clasificación y botones de confirmación.

---

## Actualizar

```bash
git pull
docker compose up --build
```

---

## Detener

```bash
docker compose down
```

---

## Notas de compatibilidad

- **Gemini API:** ADSO usa `gemini-2.5-flash-lite` (free tier: 1000 requests/día, sin tarjeta de crédito). `gemini-2.0-flash` está deprecado desde febrero 2026 y retirado en marzo 2026 — no usar.
- **python-telegram-bot v21+:** requiere el extra `[job-queue]` para el scheduler de trabajos periódicos. Ya incluido en `requirements.txt`.
- **docker compose v2:** usar `docker compose` (sin guion). Instalar con `sudo apt install docker-compose-v2`.

## Notas para RPi4

- La primera vez que se usa `faster-whisper`, descarga el modelo (tiny: ~70MB, base: ~140MB). Requiere conexión a internet.
- ChromaDB persiste en el volumen Docker `adso-data`. No se pierde al recrear el contenedor.
- El vault persiste en el directorio local configurado en `VAULT_PATH`.

---

## Estructura de volúmenes

| Volumen | Contenido |
|---|---|
| `VAULT_PATH` (host) | Notas Markdown — el vault de Obsidian |
| `adso-data` (Docker) | ChromaDB + modelos Whisper descargados |
| `./config.yaml` (host) | Configuración del bot (montado read-only) |
| directorio de credenciales Google (host) | `google-oauth.json` + `token_tasks.json` — montado en `/credentials/` |
