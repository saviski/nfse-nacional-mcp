# Registrando o MCP em outros agentes

Este servidor MCP é **agnóstico de cliente**: o mesmo binário (`nfse_mcp_server.py`)
funciona em qualquer agente que fale o protocolo MCP. A única coisa que muda
entre agentes é **onde** você cola a entrada de configuração.

> 🚀 **Caminho rápido:** rode `python3 setup_mcp.py` no diretório do projeto.
> Ele detecta os agentes instalados, pergunta em quais registrar e faz o
> merge nos arquivos corretos. As seções abaixo são para registro manual
> ou agentes não cobertos pelo script.

A entrada padrão (schema `mcpServers`) é a seguinte — substitua `/CAMINHO/ABSOLUTO`
pelo path real onde você clonou este repo:

```json
{
  "mcpServers": {
    "nfse-nacional": {
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
        "/CAMINHO/ABSOLUTO/nfse-nacional-mcp/nfse_mcp_server.py"
      ]
    }
  }
}
```

A maioria dos agentes (Claude Desktop, Claude Code, Cursor, Windsurf, Cline)
aceita literalmente esse bloco. Zed e Continue usam schemas diferentes — veja
as seções específicas abaixo.

---

## Tabela de referência rápida

| Agente                | Arquivo                                                                 | Schema           | Escopo         |
|-----------------------|-------------------------------------------------------------------------|------------------|----------------|
| Claude Desktop        | `~/Library/Application Support/Claude/claude_desktop_config.json` †     | `mcpServers`     | global         |
| Claude Code (CLI)     | `~/.claude.json` (ou via `claude mcp add`)                              | `mcpServers`     | user / project |
| Claude Code (project) | `.mcp.json` na raiz do repo                                             | `mcpServers`     | project        |
| Cursor                | `~/.cursor/mcp.json` (ou `.cursor/mcp.json` no repo)                    | `mcpServers`     | user / project |
| Windsurf              | `~/.codeium/windsurf/mcp_config.json`                                   | `mcpServers`     | global         |
| Cline (VS Code)       | `cline_mcp_settings.json` (via palette: *Cline: Open MCP Settings*)     | `mcpServers`     | global         |
| Zed                   | `~/.config/zed/settings.json`                                           | `context_servers` | global/project |
| Continue              | `~/.continue/config.json`                                                | `experimental.modelContextProtocolServers` | global/project |
| Claude Agent SDK      | definição programática (código Python/TS)                               | —                | runtime        |

† No Windows: `%APPDATA%\Claude\claude_desktop_config.json`  ·  No Linux: `~/.config/Claude/claude_desktop_config.json`

---

## Claude Desktop

Abra `~/Library/Application Support/Claude/claude_desktop_config.json` (crie
se não existir) e cole a entrada padrão dentro de `mcpServers`. Reinicie o
Claude Desktop (**⌘Q** no macOS — fechar a janela não basta).

Depois, em uma conversa nova:

> **Você:** configure o NFS-e Nacional pra mim

---

## Claude Code (CLI)

**Opção 1 — via comando (recomendado):**

```bash
claude mcp add nfse-nacional \
  --scope user \
  -- uv run \
       --with "mcp[cli]" \
       --with requests \
       --with cryptography \
       --with lxml \
       --with signxml \
       --with google-auth \
       --with google-auth-oauthlib \
       python3 /CAMINHO/ABSOLUTO/nfse-nacional-mcp/nfse_mcp_server.py
```

Use `--scope project` se preferir registrar só para o diretório atual.

**Opção 2 — `.mcp.json` no repo (portátil):**

```bash
cd nfse-nacional-mcp
python3 setup_mcp.py --project-scoped
```

Isso cria `.mcp.json` na raiz do repo com caminho **relativo**. Quem clonar
e abrir a pasta no Claude Code já tem o MCP disponível — ideal para times.

**Verificar se registrou:**

```bash
claude mcp list
```

---

## Cursor

Abra (ou crie) `~/.cursor/mcp.json` e cole a entrada padrão. Reinicie o Cursor
(**Cmd+Shift+P → Developer: Reload Window**).

Para registro por projeto, crie `.cursor/mcp.json` dentro do repo com o mesmo
conteúdo — assim o MCP só ativa quando você abre aquele workspace.

---

## Windsurf

Abra `~/.codeium/windsurf/mcp_config.json`, crie o diretório pai se necessário,
e cole a entrada padrão. Reinicie o Windsurf.

Windsurf também expõe uma UI em *Settings → MCP Servers → Edit JSON* que abre
exatamente esse arquivo.

---

## Cline (extensão VS Code)

No VS Code (com Cline instalado):

1. Abra a paleta de comandos (**Cmd+Shift+P**)
2. Rode `Cline: Open MCP Settings`
3. Cole a entrada padrão dentro de `mcpServers`
4. Salve (Cline recarrega sozinho)

O arquivo que abre fica em:

- **macOS:** `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
- **Windows:** `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`
- **Linux:** `~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`

---

## Zed

Zed usa um schema próprio dentro de `settings.json`:

```json
{
  "context_servers": {
    "nfse-nacional": {
      "command": {
        "path": "uv",
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
          "/CAMINHO/ABSOLUTO/nfse-nacional-mcp/nfse_mcp_server.py"
        ],
        "env": {}
      }
    }
  }
}
```

Arquivo: `~/.config/zed/settings.json` (ou via `cmd-,` dentro do Zed).

---

## Continue (extensão VS Code / JetBrains)

No `~/.continue/config.json`, dentro do bloco `experimental`:

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "stdio",
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
            "/CAMINHO/ABSOLUTO/nfse-nacional-mcp/nfse_mcp_server.py"
          ]
        }
      }
    ]
  }
}
```

Recarregue a janela do editor depois de salvar.

---

## Claude Agent SDK (Python)

Integração programática — útil para scripts, CI, workflows automatizados:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

options = ClaudeAgentOptions(
    mcp_servers={
        "nfse-nacional": {
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
                "/CAMINHO/ABSOLUTO/nfse-nacional-mcp/nfse_mcp_server.py",
            ],
        }
    },
    allowed_tools=[
        "mcp__nfse-nacional__listar_pagamentos",
        "mcp__nfse-nacional__emitir_notas_mes",
        "mcp__nfse-nacional__encaminhar_nota_contabilidade",
    ],
)

async for msg in query(
    prompt="emita as notas AdSense de março/2026 e encaminhe pra contabilidade",
    options=options,
):
    print(msg)
```

Equivalente TypeScript em `@anthropic-ai/claude-agent-sdk`.

---

## Troubleshooting

### `command not found: uv`

Alguns launchers gráficos (Claude Desktop, Cursor) **não herdam** o `PATH` do
seu shell. Se o agente reclamar que `uv` não existe, troque `"command": "uv"`
pelo caminho absoluto do binário:

```bash
which uv
# /Users/você/.local/bin/uv
```

E substitua na entrada:

```json
"command": "/Users/você/.local/bin/uv"
```

### Caminho com espaços ou acentos

Caminhos com espaços (ex: `Application Support`) funcionam no JSON — não
precisa escapar, só manter entre aspas. Em `.mcp.json` com path relativo,
acentos e espaços funcionam normalmente.

### MCP registrou mas não aparece no agente

1. **Reinicie o agente por completo** — apps GUI muitas vezes só releem o
   config no startup. Fechar a janela ≠ encerrar o processo.
2. Teste o servidor diretamente:
   ```bash
   uv run --with "mcp[cli]" --with requests --with cryptography \
          --with lxml --with signxml --with google-auth \
          --with google-auth-oauthlib \
          python3 nfse_mcp_server.py
   ```
   Deve ficar pendurado esperando input no stdin (é MCP stdio). `Ctrl-C` para
   sair. Se der erro de import, você identifica antes de atribuir ao agente.
3. Veja os logs do agente. Claude Desktop: `~/Library/Logs/Claude/`. Claude
   Code CLI: `claude mcp list` mostra status de cada servidor.

### Múltiplos agentes compartilhando a mesma config

Todos os agentes apontam para o **mesmo** `nfse_mcp_server.py` (caminho absoluto
no registro global). Ele lê `config.json`/`secrets.json`/`clientes.json` do
diretório onde está, então **não duplica configuração**: você configura
uma vez e todos os agentes usam.

Se quiser configurações separadas por agente (ex: duas empresas), clone o repo
em dois diretórios e registre cada um com um nome diferente
(`nfse-empresa-a`, `nfse-empresa-b`).

### Quero versionar o `.mcp.json` no git

Faz sentido se o repo for de uso pessoal ou da sua equipe. Cuidado se o repo
for público: caminhos relativos são seguros, mas não commite chaves ou
paths que revelem estrutura de diretórios internos.
