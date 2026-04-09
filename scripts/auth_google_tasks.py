"""Script de autenticación OAuth one-time para Google Tasks.

Ejecutar una vez en un entorno con acceso a navegador (dev machine o RPi4
con display). Guarda token_tasks.json junto al archivo de credenciales.

Uso:
    python scripts/auth_google_tasks.py [--creds PATH]

    --creds: path al client_secrets JSON descargado de Google Cloud Console.
             Default: /credentials/google-oauth.json

El token generado se guarda en el mismo directorio que --creds,
con el nombre token_tasks.json.

Para RPi4 sin display: copiar token_tasks.json generado en dev machine
al directorio de credenciales en la RPi4.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


SCOPES = ["https://www.googleapis.com/auth/tasks"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Auth OAuth para Google Tasks")
    parser.add_argument(
        "--creds",
        default="/credentials/google-oauth.json",
        help="Path al client_secrets.json de Google Cloud Console",
    )
    args = parser.parse_args()

    creds_path = Path(args.creds)
    if not creds_path.exists():
        print(f"Error: no se encontró el archivo de credenciales en {creds_path}", file=sys.stderr)
        print(
            "Descargar desde Google Cloud Console → APIs & Services → Credentials → "
            "OAuth 2.0 Client IDs → Download JSON",
            file=sys.stderr,
        )
        sys.exit(1)

    token_path = creds_path.parent / "token_tasks.json"

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print(
            "Faltan dependencias. Instalar con:\n"
            "  pip install google-auth-oauthlib google-api-python-client",
            file=sys.stderr,
        )
        sys.exit(1)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        print(f"Token ya válido en {token_path}")
        return

    if creds and creds.expired and creds.refresh_token:
        print("Refrescando token existente...")
        try:
            creds.refresh(Request())
        except Exception as exc:
            print(f"No se pudo refrescar: {exc}")
            print("Borrando token inválido y reiniciando flujo OAuth...")
            token_path.unlink(missing_ok=True)
            creds = None

    if creds is None or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        # Modo headless: imprimir URL y leer el código de autorización por stdin
        auth_url, _ = flow.authorization_url(prompt="consent")
        print("\n=== Autorización OAuth ===")
        print("Abrí esta URL en cualquier navegador:\n")
        print(f"  {auth_url}\n")
        print("Después de autorizar, Google redirige a localhost — va a dar error de conexión,")
        print("pero la URL del navegador contiene el código. Copiá el valor del parámetro 'code='.\n")
        code = input("Pegá el código de autorización aquí: ").strip()
        flow.fetch_token(code=code)
        creds = flow.credentials

    token_path.write_text(creds.to_json())
    print(f"\nToken guardado en {token_path}")


if __name__ == "__main__":
    main()
