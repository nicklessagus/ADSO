FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY adso/ adso/

# Pre-crear directorios de datos con permisos correctos ANTES de declarar VOLUME.
# Docker copia estos directorios al volumen nombrado en el primer arranque.
# Para bind-mounts, crear el directorio equivalente en el host.
RUN mkdir -p /app/data/whisper /app/data/chroma && chmod -R 777 /app/data

# Volúmenes
VOLUME ["/vault", "/credentials", "/app/data"]

# Health check: verifica que el bot actualizó el heartbeat en los últimos 120 segundos
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD test -f /tmp/adso_heartbeat && \
        test $(( $(date +%s) - $(date +%s -r /tmp/adso_heartbeat) )) -lt 120

# Entry point
CMD ["python", "-m", "adso"]
