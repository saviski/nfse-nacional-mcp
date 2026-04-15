#!/usr/bin/env python3
"""
Bootstrap one-shot: registra este projeto como servidor MCP em todos os
agentes MCP-compatíveis detectados no sistema.

Uso:
    python3 setup_mcp.py                       # interativo — detecta e pergunta
    python3 setup_mcp.py --all                 # registra em tudo que detectar
    python3 setup_mcp.py --list                # só lista, não grava
    python3 setup_mcp.py --only cursor,cline   # registra só nesses
    python3 setup_mcp.py --project-scoped      # cria .mcp.json na raiz do repo
                                               # (Claude Code + Cursor abrem
                                               #  direto do projeto)

Agentes suportados:
  - Claude Desktop         (macOS/Windows/Linux)
  - Claude Code (CLI)      via subprocess `claude mcp add`
  - Cursor                 (~/.cursor/mcp.json)
  - Windsurf               (~/.codeium/windsurf/mcp_config.json)
  - Cline (VS Code ext.)   globalStorage do saoudrizwan.claude-dev

Zed e Continue usam schema diferente — ver AGENTS.md para instruções manuais.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR    = Path(__file__).parent.resolve()
SERVER_NAME  = "nfse-nacional"
SERVER_PATH  = SKILL_DIR / "nfse_mcp_server.py"

# Dependências que o `uv run` baixa automaticamente na primeira chamada.
UV_DEPS = [
    "mcp[cli]",
    "requests",
    "cryptography",
    "lxml",
    "signxml",
    "google-auth",
    "google-auth-oauthlib",
]


def _entry(server_path: str) -> dict:
    """Entrada MCP — o schema `mcpServers` é compartilhado por quase todos os agentes."""
    args = ["run"]
    for dep in UV_DEPS:
        args += ["--with", dep]
    args += ["python3", server_path]
    return {"command": "uv", "args": args}


SERVER_ENTRY = _entry(str(SERVER_PATH))


# ─── Resolução de paths por SO ───────────────────────────────────────────────

def _plat() -> str:
    return platform.system()  # "Darwin", "Windows", "Linux"


def _resolve(path_str: str) -> Path:
    """Expande $VAR e ~; retorna Path absoluto."""
    return Path(os.path.expandvars(path_str)).expanduser()


# ─── Specs de cada agente ────────────────────────────────────────────────────

AGENT_SPECS: list[dict] = [
    {
        "key":  "claude_desktop",
        "name": "Claude Desktop",
        "paths": {
            "Darwin":  "~/Library/Application Support/Claude/claude_desktop_config.json",
            "Windows": "$APPDATA/Claude/claude_desktop_config.json",
            "Linux":   "~/.config/Claude/claude_desktop_config.json",
        },
        "mode": "json",
    },
    {
        "key":  "cursor",
        "name": "Cursor (user scope)",
        "paths": {"*": "~/.cursor/mcp.json"},
        "mode": "json",
    },
    {
        "key":  "windsurf",
        "name": "Windsurf",
        "paths": {"*": "~/.codeium/windsurf/mcp_config.json"},
        "mode": "json",
    },
    {
        "key":  "cline",
        "name": "Cline (VS Code)",
        "paths": {
            "Darwin":  "~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
            "Windows": "$APPDATA/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
            "Linux":   "~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
        },
        "mode": "json",
    },
]


def _agent_path(spec: dict) -> Path | None:
    """Path do arquivo de config do agente no SO atual."""
    paths = spec["paths"]
    key = _plat() if _plat() in paths else "*"
    if key not in paths:
        return None
    return _resolve(paths[key])


# ─── Detecção ────────────────────────────────────────────────────────────────

def detect_agents() -> list[tuple[dict, Path | None]]:
    """
    Detecta agentes instalados. Um JSON-based agent é 'detectado' se:
      - o arquivo de config já existe, OU
      - o diretório pai existe (app foi instalado + aberto pelo menos 1×)

    Claude Code CLI é detectado pela presença do binário `claude` no PATH.
    """
    detected: list[tuple[dict, Path | None]] = []

    for spec in AGENT_SPECS:
        path = _agent_path(spec)
        if not path:
            continue
        if path.exists() or path.parent.exists():
            detected.append((spec, path))

    if shutil.which("claude"):
        detected.append((
            {
                "key":  "claude_code",
                "name": "Claude Code (CLI, user scope)",
                "mode": "cli",
            },
            None,
        ))

    return detected


# ─── Registro ────────────────────────────────────────────────────────────────

def register_json(spec: dict, path: Path, entry: dict) -> str:
    """Merge `entry` em `path` → mcpServers[SERVER_NAME]. Retorna status humano."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return f"erro: JSON inválido em {path.name} ({e})"
    else:
        data = {}
        path.parent.mkdir(parents=True, exist_ok=True)

    data.setdefault("mcpServers", {})
    atual = data["mcpServers"].get(SERVER_NAME)

    if atual == entry:
        return "já registrado (idêntico) ✓"

    status = "atualizado" if SERVER_NAME in data["mcpServers"] else "registrado"
    data["mcpServers"][SERVER_NAME] = entry
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return status


def register_claude_code_cli() -> str:
    """Registra via `claude mcp add` no escopo user (global)."""
    base = ["claude", "mcp", "add", SERVER_NAME, "--scope", "user", "--"]
    cmd  = base + [SERVER_ENTRY["command"]] + SERVER_ENTRY["args"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "erro: `claude` não encontrado no PATH"
    except Exception as e:
        return f"erro: {type(e).__name__}: {e}"

    if r.returncode == 0:
        return "registrado (escopo user)"

    out = (r.stderr or r.stdout or "").strip()
    if "already exists" in out.lower() or "already configured" in out.lower():
        # Remove e re-adiciona para atualizar
        subprocess.run(
            ["claude", "mcp", "remove", SERVER_NAME, "--scope", "user"],
            capture_output=True,
        )
        r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r2.returncode == 0:
            return "atualizado (escopo user)"
        return f"erro: {(r2.stderr or r2.stdout).strip()}"

    return f"erro: {out}"


def write_project_mcp_json() -> Path:
    """
    Cria .mcp.json na raiz do projeto. Claude Code e Cursor (via .cursor/mcp.json)
    lerão automaticamente quando abrirem este diretório. Torna o repo
    self-contained — quem clonar só precisa abrir no editor.

    Usa caminho RELATIVO no `python3` arg para portabilidade.
    """
    # Entry com path relativo (comparado ao absoluto do user scope)
    entry = _entry("nfse_mcp_server.py")
    data = {"mcpServers": {SERVER_NAME: entry}}

    path = SKILL_DIR / ".mcp.json"
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _parse_choice(raw: str, n: int) -> list[int]:
    """Lê '1,3' / 'all' / '2-4' e retorna lista de índices 1-based."""
    raw = raw.strip().lower()
    if raw in ("all", "a", "todos", "t", "*"):
        return list(range(1, n + 1))
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [i for i in out if 1 <= i <= n]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Registra o MCP nfse-nacional em todos os agentes MCP detectados.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--all",            action="store_true", help="Registra em todos os detectados, sem perguntar")
    ap.add_argument("--list",           action="store_true", help="Só lista agentes detectados, não grava nada")
    ap.add_argument("--only",           metavar="KEYS",      help="CSV das chaves a registrar (ex: claude_desktop,cursor)")
    ap.add_argument("--project-scoped", action="store_true", help="Cria .mcp.json na raiz do repo (agnóstico, portátil)")
    args = ap.parse_args()

    # ── Modo project-scoped: cria .mcp.json no repo e sai ────────────────────
    if args.project_scoped:
        p = write_project_mcp_json()
        print(f"✅ .mcp.json criado: {p}")
        print()
        print("Claude Code e Cursor lerão este arquivo automaticamente quando")
        print("abertos neste diretório. Nada mais precisa ser feito —")
        print("o repo agora é self-contained.")
        print()
        print("Se for versionar o repo, o .mcp.json pode entrar no git.")
        return 0

    # ── Detecção ─────────────────────────────────────────────────────────────
    detected = detect_agents()
    if not detected:
        print("❌ Nenhum agente MCP detectado.")
        print()
        print("Agentes suportados: Claude Desktop, Claude Code CLI, Cursor, Windsurf, Cline.")
        print("Se o seu agente não está nessa lista, veja AGENTS.md para instruções manuais.")
        return 1

    print("📍 Agentes MCP detectados:\n")
    for i, (spec, path) in enumerate(detected, 1):
        location = str(path) if path else "via `claude` CLI"
        print(f"  [{i}] {spec['name']}")
        print(f"      └─ {location}")
    print()

    if args.list:
        return 0

    # ── Seleção ──────────────────────────────────────────────────────────────
    if args.all:
        escolhidos = list(range(1, len(detected) + 1))
    elif args.only:
        keys = {k.strip() for k in args.only.split(",")}
        escolhidos = [i for i, (spec, _) in enumerate(detected, 1) if spec["key"] in keys]
        if not escolhidos:
            print(f"❌ Nenhum dos keys --only ({args.only}) corresponde aos detectados.")
            return 1
    else:
        ans = input("Registrar em quais? [ex: 1,3 ou 'all', Enter=cancelar]: ")
        if not ans.strip():
            print("Cancelado.")
            return 0
        try:
            escolhidos = _parse_choice(ans, len(detected))
        except ValueError:
            print("❌ Entrada inválida.")
            return 1
        if not escolhidos:
            print("❌ Nenhuma seleção válida.")
            return 1

    # ── Registro ─────────────────────────────────────────────────────────────
    print()
    print("Registrando…\n")
    falhas = 0
    for i in escolhidos:
        spec, path = detected[i - 1]
        if spec["mode"] == "cli":
            status = register_claude_code_cli()
        else:
            status = register_json(spec, path, SERVER_ENTRY)
        if status.startswith("erro"):
            falhas += 1
        print(f"  • {spec['name']:35s} → {status}")

    print()
    print("Próximos passos:")
    print("  1. Reinicie os agentes GUI afetados (⌘Q e reabra)")
    print("  2. Em uma conversa nova, diga: 'configure o NFS-e Nacional pra mim'")
    print()
    print("Pré-requisito: `uv` no PATH  (curl -LsSf https://astral.sh/uv/install.sh | sh)")

    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
