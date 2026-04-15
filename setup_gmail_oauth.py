#!/usr/bin/env python3
"""
Setup one-time do OAuth2 do Gmail — Emissor NFS-e Nacional

Pré-requisitos:
  1. Baixe o arquivo de credenciais do Google Cloud Console (ver SETUP.md)
     e salve como 'client_secret.json' nesta pasta.
  2. pip install google-auth-oauthlib --break-system-packages

Uso:
  python3 setup_gmail_oauth.py

O script abrirá o browser para você autorizar o acesso ao Gmail.
Após autorizar, o refresh_token é salvo automaticamente em secrets.json.
"""

import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET_FILE = os.path.join(SKILL_DIR, "client_secret.json")
SECRETS_FILE       = os.path.join(SKILL_DIR, "secrets.json")

SCOPES = ["https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.readonly"]


def main():
    # Verifica dependência
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("❌ Dependência ausente. Execute:")
        print("   pip install google-auth-oauthlib --break-system-packages")
        sys.exit(1)

    # Verifica client_secret.json
    if not os.path.exists(CLIENT_SECRET_FILE):
        print("❌ Arquivo 'client_secret.json' não encontrado.")
        print()
        print("Passos para obtê-lo:")
        print("  1. Acesse https://console.cloud.google.com")
        print("  2. Crie um projeto (ou selecione um existente)")
        print("  3. APIs & Services → Enable APIs → procure 'Gmail API' → Enable")
        print("  4. APIs & Services → Credentials → Create Credentials → OAuth client ID")
        print("  5. Application type: Desktop app  |  Name: NFS-e Nacional")
        print("  6. Download JSON → renomeie para 'client_secret.json'")
        print(f"  7. Coloque o arquivo em: {SKILL_DIR}")
        print()
        print("Se for a primeira vez criando credenciais OAuth, você também precisará")
        print("configurar a 'OAuth consent screen' (External, status Testing é suficiente).")
        sys.exit(1)

    print("🔐 Iniciando fluxo OAuth2 do Gmail...")
    print("   O browser vai abrir para você autorizar. Aguarde...")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    # Lê ou cria secrets.json
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, "r") as f:
            secrets = json.load(f)
    else:
        secrets = {}

    # Remove campos legados de app password, se houver
    secrets.pop("gmail_app_password", None)

    secrets["gmail_client_id"]      = creds.client_id
    secrets["gmail_client_secret"]  = creds.client_secret
    secrets["gmail_refresh_token"]  = creds.refresh_token

    with open(SECRETS_FILE, "w") as f:
        json.dump(secrets, f, indent=2, ensure_ascii=False)

    print()
    print("✅ OAuth2 configurado com sucesso!")
    print(f"   Credenciais salvas em: {SECRETS_FILE}")
    print()
    print("Agora você pode emitir notas normalmente:")
    print("  python3 emitir_nfse.py --mes 2026-03")


if __name__ == "__main__":
    main()
