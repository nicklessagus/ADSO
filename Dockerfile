FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
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

# Entry point
CMD ["python", "-m", "adso"]
