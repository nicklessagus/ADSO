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

La instalación usa dos directorios separados:

| Directorio | Propósito |
|---|---|
| `~/Repos/ADSO` | Código fuente — desarrollo y builds |
| `~/docker/ADSO` | Despliegue — compose, config y credenciales |

```bash
git clone git@github.com:nicklessagus/ADSO.git ~/Repos/ADSO
```

El directorio de deploy (`~/docker/ADSO/`) ya contiene `.env`, `config.yaml` y la carpeta `credentials/` pre-creados. Solo hace falta completar el `.env` con las credenciales reales:

```bash
# ─── Requeridas ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN=<token del BotFather>
TELEGRAM_ALLOWED_USER_ID=<tu ID numérico>
GEMINI_API_KEY=<tu API key de Gemini>
GROQ_API_KEY=<tu API key de Groq>

# ─── Opcionales ───────────────────────────────────────────────────────────────
# ANTHROPIC_API_KEY=
# LOG_LEVEL=INFO

# ─── Paths ────────────────────────────────────────────────────────────────────
VAULT_PATH=/home/pi/NAS/Sync/ADSO

# ─── Permisos de archivos (Docker) ────────────────────────────────────────────
# Obtener con: id -u && id -g
ADSO_UID=1000
ADSO_GID=1000
```

El `config.yaml` por defecto es válido para empezar. Ajustá a gusto (ver `docs/configuration.md`).

---

## 3. Crear el vault

El vault vive en `~/NAS/Sync/ADSO/` para que Syncthing lo sincronice junto con el resto de los dispositivos. Crear la carpeta si no existe:

```bash
mkdir -p ~/NAS/Sync/ADSO
```

El bot crea la estructura de carpetas (`00-Inbox`, `01-Projects`, etc.) automáticamente al arrancar.

Agregar `~/NAS/Sync/ADSO` como nueva carpeta compartida en Syncthing para sincronizarla con los clientes.

El vault ya incluye un `.gitignore` que excluye archivos de estado local de Obsidian (workspace, cache), conflictos de Syncthing y archivos de sistema.

---

## 4. Backup automático del vault (opcional)

ADSO puede hacer commit+push automático a un repo git privado cada vez que se escribe o modifica una nota (con debounce de 30 segundos). Syncthing hace la sincronización en vivo entre dispositivos — git es el backup histórico y el DR.

### 4.1 Crear el repo en GitHub

Crear un repo privado (ej: `nicklessagus/ADSO_Vault`) en GitHub. No inicializar con README ni .gitignore — el vault ya tiene su propio `.gitignore`.

### 4.2 Inicializar git en el vault

```bash
git -C ~/NAS/Sync/ADSO init -b main
git -C ~/NAS/Sync/ADSO add .
git -C ~/NAS/Sync/ADSO commit -m "Initial vault"
git -C ~/NAS/Sync/ADSO remote add origin git@github.com:<usuario>/ADSO_Vault.git
git -C ~/NAS/Sync/ADSO push -u origin main
```

Si el vault ya estaba inicializado (solo falta el remote):

```bash
git -C ~/NAS/Sync/ADSO remote add origin git@github.com:<usuario>/ADSO_Vault.git
git -C ~/NAS/Sync/ADSO push -u origin main
```

### 4.3 SSH key para el container Docker

El container necesita acceso SSH a GitHub. ADSO monta la key SSH del host en `/ssh-keys/` dentro del container. El `docker-compose.yml` del directorio de deploy (`~/docker/ADSO/`) tiene el volumen y la variable `GIT_SSH_COMMAND` configurados — no hace falta ningún paso extra si la key del host tiene acceso al repo.

> **Nota:** el `docker-compose.yml` del repositorio de código (`~/Repos/ADSO/`) es la plantilla de referencia y no incluye el volumen SSH ni la variable `GIT_SSH_COMMAND`. El directorio de deploy (`~/docker/ADSO/`) tiene su propio compose con ambas configuraciones, separado del repo de código (ver `Makefile` — `make deploy` copia al directorio de deploy).

Si querés usar una key dedicada para ADSO:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/adso_vault -C "adso-vault-backup" -N ""
# Agregar ~/.ssh/adso_vault.pub como deploy key en el repo de GitHub (Settings → Deploy keys → write access)
```

Luego cambiar en `docker-compose.yml`:
```yaml
- GIT_SSH_COMMAND=ssh -i /ssh-keys/adso_vault -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
```

### 4.4 Activar en config.yaml

```yaml
backup:
  enabled: true
  debounce_seconds: 30   # segundos de inactividad antes de hacer commit+push
```

Hacer `make deploy` para aplicar.

### Comportamiento

- Cada confirmación de nota desde Telegram → commit+push (debounce 30s)
- Cada cambio externo detectado por el watcher (edición o creación desde Obsidian via Syncthing) → commit+push
- Cada borrado externo → commit+push (incluye la eliminación del archivo)
- Error en push → el bot notifica por Telegram inmediatamente
- Push exitoso → notificación solo si `watcher.debug: true`

---

## 5. Arrancar

Desde el repositorio de código:

```bash
cd ~/Repos/ADSO
make deploy     # build + arranque en background
make logs       # ver logs en vivo
```

Deberías ver:

```
adso-bot | [adso.vault_writer] INFO: Estructura del vault verificada: /vault
adso-bot | [adso.bot] INFO: ADSO iniciando — vault en /vault
adso-bot | [apscheduler.scheduler] INFO: Scheduler started
adso-bot | [telegram.ext.Application] INFO: Application started
```

### Comandos disponibles (Makefile)

| comando | acción |
|---|---|
| `make deploy` | build + reinicia el contenedor |
| `make stop` | detiene sin borrar |
| `make restart` | reinicia sin rebuild |
| `make logs` | tail de logs en vivo |
| `make status` | estado del contenedor |
| `make shell` | bash dentro del contenedor |
| `make prune` | limpia imágenes huérfanas post-rebuild |

---

## 6. Verificar

Abrí Telegram, buscá tu bot y mandá cualquier mensaje de texto. El bot debería responder con un preview de clasificación y botones de confirmación.

---

## Actualizar

```bash
cd ~/Repos/ADSO
git pull
make deploy
```

---

## Detener

```bash
make stop
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
| `~/.ssh` (host) | SSH keys para git push del backup — montado en `/ssh-keys/` (read-only) |
