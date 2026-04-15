#!/usr/bin/env python3
"""
Bootstrap one-shot: registra este projeto como servidor MCP no
`claude_desktop_config.json` do Claude Desktop.

Uso:
    python3 setup_claude_desktop.py

Depois de rodar, reinicie o Claude Desktop e diga:
    "configure o NFS-e Nacional pra mim"

O Claude vai chamar as setup tools do MCP e te guiar passo a passo
(CNPJ, certificado, Mailgun, clientes, etc).
"""

import json
import os
import platform
import sys
from pathlib import Path

SKILL_DIR   = Path(__file__).parent.resolve()
SERVER_NAME = "nfse-nacional"


def config_path() -> Path:
    """Retorna o caminho do claude_desktop_config.json no SO atual."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA não definido — você está mesmo no Windows?")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    # Linux (não oficial, mas funciona com builds community)
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def build_entry() -> dict:
    """Entrada MCP que será inserida no claude_desktop_config.json."""
    return {
        "command": "uv",
        "args": [
            "run",
            "--with", "mcp[cli]",
            "--with", "requests",
            "--with", "cryptography",
            "--with", "lxml",
            "--with", "signxml",
            "--with", "google-auth",
            "--with", "google-auth-oauthlib",
            "python3",
            str(SKILL_DIR / "nfse_mcp_server.py"),
        ],
    }


def main() -> int:
    cfg_path = config_path()
    print(f"📍 claude_desktop_config.json → {cfg_path}")

    # Lê config existente (ou começa do zero)
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"❌ Arquivo existe mas está inválido: {e}")
            print("   Corrija manualmente ou remova o arquivo antes de rodar este script.")
            return 1
    else:
        data = {}
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        print("   (arquivo novo)")

    data.setdefault("mcpServers", {})

    # Confirma antes de sobrescrever entrada existente
    if SERVER_NAME in data["mcpServers"]:
        existing = data["mcpServers"][SERVER_NAME]
        if existing == build_entry():
            print(f"✅ '{SERVER_NAME}' já registrado e idêntico — nada a fazer.")
            return 0
        print(f"⚠️  '{SERVER_NAME}' já existe no claude_desktop_config.json.")
        print(f"   Entrada atual: {json.dumps(existing, indent=2)}")
        ans = input("   Sobrescrever? [y/N] ").strip().lower()
        if ans not in ("y", "yes", "s", "sim"):
            print("Cancelado.")
            return 0

    data["mcpServers"][SERVER_NAME] = build_entry()

    # Escreve
    cfg_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print()
    print(f"✅ Registrado '{SERVER_NAME}' no Claude Desktop.")
    print()
    print("Próximos passos:")
    print("  1. Feche e reabra o Claude Desktop (ou ⌘Q no macOS)")
    print("  2. Em uma conversa nova, diga:")
    print("     > configure o NFS-e Nacional pra mim")
    print("  3. Claude vai chamar `status_setup` e guiar você pelos próximos passos.")
    print()
    print("Pré-requisito: você precisa ter `uv` instalado.")
    print("  macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh")
    print("  Windows:      powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
