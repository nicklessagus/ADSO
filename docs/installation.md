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
git clone https://github.com/nicklessagus/ADSO.git
cd ADSO
cp .env.example .env
cp config.yaml.example config.yaml
```

Completar el `.env` con las credenciales reales:

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
VAULT_PATH=/ruta/al/vault

# ─── Permisos de archivos (Docker) ────────────────────────────────────────────
# Obtener con: id -u && id -g
ADSO_UID=1000
ADSO_GID=1000
```

El `config.yaml` copiado del ejemplo es válido para empezar. Ajustá a gusto (ver `docs/configuration.md`).

### Variante: directorio de deploy separado (opcional)

Para separar el código fuente del despliegue (útil si desarrollás en la misma máquina donde corre el bot), se puede usar un directorio de deploy propio:

```bash
mkdir -p ~/docker/ADSO/credentials
cp .env config.yaml ~/docker/ADSO/
```

**No copiar el `docker-compose.yml`.** El del repo queda como base y única fuente
de verdad de todo lo compartido (healthcheck, hardening, logging, volúmenes); lo
propio de la máquina va en un archivo aparte, `~/docker/ADSO/local.yml`:

```yaml
services:
  adso-bot:
    build:
      context: /ruta/al/repo/ADSO     # el código vive en el repo
    # + acá los volúmenes/variables propios de esta máquina (ver §4.3)
```

El `Makefile` los combina:

```
docker compose -p adso --project-directory ~/docker/ADSO \
  -f docker-compose.yml -f ~/docker/ADSO/local.yml up --build -d
```

- `--project-directory` hace que `.env`, `./config.yaml` y `./credentials`
  resuelvan contra el directorio de deploy. Importa si además tenés un `.env` en
  el repo: sin esto se cargaría el equivocado.
- `-p adso` fija el nombre de proyecto. **Sin esto, mover el compose de
  directorio cambia el nombre del volumen** y Docker crea uno nuevo vacío —
  perdés el índice de ChromaDB y el modelo de whisper descargado.
- El archivo **no** se llama `docker-compose.override.yml` a propósito: ese
  nombre lo carga compose automáticamente y se mezclaría por accidente en
  cualquier comando suelto lanzado desde ese directorio.

> **Por qué así y no copiando el compose.** La variante anterior (copiar y editar
> a mano) deja dos archivos que nada mantiene sincronizados: los cambios al
> compose del repo no llegan nunca a producción, y no hay nada que lo detecte. El
> fix del healthcheck de la auditoría 2026-07-31 estuvo semanas commiteado y
> documentado como implementado mientras producción corría el roto.

Si usás el flujo simple de un solo directorio, ignorá el Makefile y usá
`docker compose` directo.

---

## 3. Crear el vault

Crear el directorio del vault (el mismo que apunta `VAULT_PATH` en `.env`) si no existe:

```bash
mkdir -p /ruta/al/vault
```

El bot crea la estructura de carpetas (`00-Inbox`, `01-Projects`, etc.) automáticamente al arrancar.

Si usás Syncthing para sincronizar el vault entre dispositivos, agregar ese directorio como carpeta compartida en Syncthing.

El vault ya incluye un `.gitignore` que excluye archivos de estado local de Obsidian (workspace, cache), conflictos de Syncthing y archivos de sistema.

---

## 4. Backup automático del vault (opcional)

ADSO puede hacer commit+push automático a un repo git privado cada vez que se escribe o modifica una nota (con debounce de 30 segundos). Syncthing hace la sincronización en vivo entre dispositivos — git es el backup histórico y el DR.

### 4.1 Crear el repo en GitHub

Crear un repo privado (ej: `nicklessagus/ADSO_Vault`) en GitHub. No inicializar con README ni .gitignore — el vault ya tiene su propio `.gitignore`.

### 4.2 Inicializar git en el vault

```bash
git -C /ruta/al/vault init -b main
git -C /ruta/al/vault add .
git -C /ruta/al/vault commit -m "Initial vault"
git -C /ruta/al/vault remote add origin git@github.com:<usuario>/ADSO_Vault.git
git -C /ruta/al/vault push -u origin main
```

Si el vault ya estaba inicializado (solo falta el remote):

```bash
git -C /ruta/al/vault remote add origin git@github.com:<usuario>/ADSO_Vault.git
git -C /ruta/al/vault push -u origin main
```

### 4.3 SSH key para el container Docker

El container necesita acceso SSH a GitHub para hacer push. ADSO espera la key montada en `/ssh-keys/` dentro del container. El `docker-compose.yml` del repo no incluye ese volumen por defecto.

Crear una key **dedicada** para ADSO (no reutilizar la personal ni montar `~/.ssh` completo al container) y precargar el host key de GitHub para que SSH pueda verificar el servidor:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/adso_vault -C "adso-vault-backup" -N ""
# Agregar ~/.ssh/adso_vault.pub como deploy key en el repo del vault en GitHub
# (Settings → Deploy keys → marcar "Allow write access")

# Host key de GitHub, para verificación estricta del servidor
ssh-keyscan github.com > ~/.ssh/adso_known_hosts
```

Agregar el volumen y la variable — en `~/docker/ADSO/local.yml` si usás la
variante de deploy separado, o en `docker-compose.yml` si tenés un solo
directorio (en ese caso, ojo con no commitear paths locales):

```yaml
    volumes:
      # ... volúmenes existentes ...
      - ${HOME}/.ssh/adso_vault:/ssh-keys/adso_vault:ro
      - ${HOME}/.ssh/adso_known_hosts:/ssh-keys/known_hosts:ro
    environment:
      # ... variables existentes ...
      - GIT_SSH_COMMAND=ssh -i /ssh-keys/adso_vault -o UserKnownHostsFile=/ssh-keys/known_hosts -o StrictHostKeyChecking=yes
```

> **No usar `StrictHostKeyChecking=no` / `UserKnownHostsFile=/dev/null`:** deshabilita la verificación de identidad del servidor, y un atacante en la red podría hacerse pasar por GitHub y recibir el contenido completo del vault en el próximo push. Montar solo la key dedicada, nunca `~/.ssh` entero.

### 4.4 Activar en config.yaml

```yaml
backup:
  enabled: true
  debounce_seconds: 30   # segundos de inactividad antes de hacer commit+push
```

Reiniciar el contenedor para aplicar (`docker compose up --build -d`, o `make deploy` si se usa el directorio de deploy separado).

### Comportamiento

- Cada confirmación de nota desde Telegram → commit+push (debounce 30s)
- Cada cambio externo detectado por el watcher (edición o creación desde Obsidian via Syncthing) → commit+push
- Cada borrado externo → commit+push (incluye la eliminación del archivo)
- Error en push → el bot notifica por Telegram inmediatamente
- Push exitoso → notificación solo si `watcher.debug: true`

---

## 5. Arrancar

Desde el directorio del repo:

```bash
docker compose up --build -d    # build + arranque en background
docker compose logs -f          # ver logs en vivo
```

Deberías ver:

```
adso-bot | [adso.vault_writer] INFO: Estructura del vault verificada: /vault
adso-bot | [adso.bot] INFO: ADSO iniciando — vault en /vault
adso-bot | [apscheduler.scheduler] INFO: Scheduler started
adso-bot | [telegram.ext.Application] INFO: Application started
```

### Atajos del Makefile (variante con directorio de deploy separado)

Si se usa la variante de deploy separado (sección 2), el `Makefile` envuelve los comandos de compose:

| comando | acción |
|---|---|
| `make deploy` | copia `config.yaml` al deploy dir + build + reinicia |
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
git pull
docker compose up --build -d    # o make deploy con deploy dir separado
```

---

## Detener

```bash
docker compose stop             # o make stop
```

---

## Notas de compatibilidad

- **Gemini API:** ADSO usa `gemini-3.1-flash-lite` (estable desde mayo 2026; free tier: ~1000 requests/día, sin tarjeta de crédito). Los `gemini-2.0-flash*` fueron retirados el 1-jun-2026 — no usar.
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
