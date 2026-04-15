#!/usr/bin/env python3
"""
Servidor MCP — Emissor NFS-e Nacional
Expõe tools para emissão de NFS-e via API do Sistema Nacional e gestão
de e-mails da contabilidade.

Configuração no claude_desktop_config.json:
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
        "--with", "google-auth-oauthlib",
        "python3",
        "/CAMINHO/COMPLETO/nfse_mcp_server.py"
      ]
    }
  }
}

Substitua /CAMINHO/COMPLETO pelo caminho absoluto da pasta do projeto.
"""

import asyncio
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

import emitir_nfse as nfse

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

server = Server("nfse-nacional")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="emitir_nota",
            description=(
                "Emite uma NFS-e via API do Sistema Nacional NFS-e. "
                "Use SOMENTE após exibir o resumo ao usuário e receber confirmação explícita. "
                "Retorna a chave de acesso da nota emitida."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cliente": {
                        "type": "string",
                        "description": "Alias do cliente conforme clientes.json",
                    },
                    "usd": {
                        "type": "number",
                        "description": "Valor recebido em USD",
                    },
                    "brl": {
                        "type": "number",
                        "description": "Valor creditado em BRL após conversão",
                    },
                    "competencia": {
                        "type": "string",
                        "description": "Data de competência no formato AAAA-MM-DD",
                    },
                    "encaminhar_contabilidade": {
                        "type": "boolean",
                        "description": "Se true, envia XML+PDF para o endereço configurado em config.email_contabilidade após emissão",
                        "default": False,
                    },
                    "ndps": {
                        "type": "integer",
                        "description": "Número do DPS (evita loop de descoberta via API). Se omitido, descobre automaticamente.",
                    },
                },
                "required": ["cliente", "usd", "brl", "competencia"],
            },
        ),
        types.Tool(
            name="verificar_emails_contabilidade",
            description=(
                "Verifica e-mails recentes dos remetentes configurados em config.contab_remetentes "
                "e categoriza solicitações pendentes conforme config.contab_categorias."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dias": {
                        "type": "integer",
                        "description": "Janela de busca em dias (padrão: 60)",
                        "default": 60,
                    },
                },
            },
        ),
        types.Tool(
            name="emitir_notas_mes",
            description=(
                "Emite várias NFS-e em uma única execução e, se solicitado, envia TODAS no mesmo "
                "e-mail para a contabilidade (um único e-mail com N anexos XML + N anexos PDF). "
                "Descobre o nDPS inicial automaticamente via API /DFe/0 e incrementa "
                "sequencialmente para cada nota do lote. "
                "Use SOMENTE após exibir o resumo do lote ao usuário e receber confirmação explícita."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "notas": {
                        "type": "array",
                        "description": "Lista de notas a emitir, na ordem em que receberão nDPS.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "cliente":     {"type": "string",  "description": "Alias conforme clientes.json"},
                                "usd":         {"type": "number",  "description": "Valor em USD"},
                                "brl":         {"type": "number",  "description": "Valor em BRL"},
                                "competencia": {"type": "string",  "description": "Data de competência AAAA-MM-DD"},
                            },
                            "required": ["cliente", "usd", "brl", "competencia"],
                        },
                        "minItems": 1,
                    },
                    "encaminhar_contabilidade": {
                        "type": "boolean",
                        "description": "Se true, envia TODAS as notas em um único e-mail após a emissão do lote.",
                        "default": False,
                    },
                    "ndps_inicial": {
                        "type": "integer",
                        "description": "Força o nDPS da primeira nota (evita consulta à API). Se omitido, descobre automaticamente.",
                    },
                },
                "required": ["notas"],
            },
        ),
        types.Tool(
            name="encaminhar_nota_contabilidade",
            description=(
                "Encaminha uma NFS-e já emitida (XML+PDF salvos em disco) "
                "para o endereço configurado em config.email_contabilidade, com CC para config.email_remetente_cc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "xml_path": {
                        "type": "string",
                        "description": "Caminho absoluto do arquivo XML da NFS-e",
                    },
                    "pdf_path": {
                        "type": "string",
                        "description": "Caminho absoluto do arquivo PDF (opcional)",
                        "default": "",
                    },
                    "cliente": {
                        "type": "string",
                        "description": "Alias do cliente conforme clientes.json",
                    },
                    "competencia": {
                        "type": "string",
                        "description": "Data de competência AAAA-MM-DD",
                    },
                    "usd": {"type": "number"},
                    "brl": {"type": "number"},
                },
                "required": ["xml_path", "cliente", "competencia", "usd", "brl"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    config   = nfse.load_config()
    secrets  = nfse.load_secrets()
    clientes = nfse.load_clientes()

    # Garante que cert_path é absoluto (config.json guarda caminho relativo)
    config["cert_path"] = str(SKILL_DIR / config["cert_path"])

    # ── emitir_nota ─────────────────────────────────────────────────────────
    if name == "emitir_nota":
        cliente_chave = arguments["cliente"]
        usd           = float(arguments["usd"])
        brl           = float(arguments["brl"])
        competencia   = arguments["competencia"]
        encaminhar    = arguments.get("encaminhar_contabilidade", False)

        dados = {"vUSD": usd, "vBRL": brl, "dCompet": competencia}

        senha   = (secrets.get("cert_password") or "").encode()
        session = nfse.session_mtls(config["cert_path"], senha)

        ndps = arguments.get("ndps")
        if not ndps:
            # descobrir_proximo_ndps combina API /DFe/0 + varredura local + cache.
            ndps = nfse.descobrir_proximo_ndps(session, config)

        try:
            resultado = nfse.emitir_uma_nota(
                config, secrets, clientes, session,
                dados, cliente_chave, ndps, dry_run=False,
            )
        finally:
            nfse.cleanup_session(session)

        resposta = {
            "status":       "emitida",
            "chave_acesso": resultado.get("chave"),
            "cliente":      resultado["cliente"]["xNome"],
            "competencia":  competencia,
            "usd":          usd,
            "brl":          brl,
        }

        if encaminhar and not resultado.get("dry_run"):
            try:
                nfse.enviar_nfse_contabilidade(
                    config, secrets,
                    dados, resultado["cliente"],
                    resultado["xml_bytes"],
                    resultado.get("pdf_bytes"),
                )
                resposta["encaminhado_para"] = config["email_contabilidade"]
            except Exception as e:
                resposta["aviso_encaminhamento"] = str(e)

        return [types.TextContent(type="text",
                                  text=json.dumps(resposta, ensure_ascii=False, indent=2))]

    # ── emitir_notas_mes ─────────────────────────────────────────────────────
    elif name == "emitir_notas_mes":
        notas_in   = arguments["notas"]
        encaminhar = bool(arguments.get("encaminhar_contabilidade", False))

        senha   = (secrets.get("cert_password") or "").encode()
        session = nfse.session_mtls(config["cert_path"], senha)

        ndps0 = arguments.get("ndps_inicial")
        if not ndps0:
            ndps0 = nfse.descobrir_proximo_ndps(session, config)

        resultados = []
        erros      = []
        try:
            for i, n in enumerate(notas_in):
                dados = {
                    "vUSD":    float(n["usd"]),
                    "vBRL":    float(n["brl"]),
                    "dCompet": n["competencia"],
                }
                try:
                    res = nfse.emitir_uma_nota(
                        config, secrets, clientes, session,
                        dados, n["cliente"], ndps0 + i, dry_run=False,
                    )
                    resultados.append(res)
                except Exception as e:
                    erros.append({"indice": i, "cliente": n.get("cliente"),
                                  "erro": str(e)})
        finally:
            nfse.cleanup_session(session)

        resposta = {
            "total_solicitadas": len(notas_in),
            "total_emitidas":    len(resultados),
            "notas": [
                {
                    "cliente":      r["cliente"]["xNome"],
                    "competencia":  r["dados"]["dCompet"],
                    "usd":          r["dados"]["vUSD"],
                    "brl":          r["dados"]["vBRL"],
                    "chave_acesso": r.get("chave"),
                }
                for r in resultados
            ],
        }
        if erros:
            resposta["erros"] = erros

        if encaminhar and resultados:
            try:
                nfse.enviar_notas_contabilidade(config, secrets, [{
                    "dados":     r["dados"],
                    "cliente":   r["cliente"],
                    "xml_bytes": r["xml_bytes"],
                    "pdf_bytes": r.get("pdf_bytes"),
                } for r in resultados])
                resposta["encaminhado_para"] = config["email_contabilidade"]
                resposta["envio_agrupado"]   = True
            except Exception as e:
                resposta["aviso_encaminhamento"] = str(e)

        return [types.TextContent(type="text",
                                  text=json.dumps(resposta, ensure_ascii=False, indent=2))]

    # ── verificar_emails_contabilidade ───────────────────────────────────────
    elif name == "verificar_emails_contabilidade":
        dias   = int(arguments.get("dias", 60))
        emails = nfse.verificar_emails_contabilidade(config, secrets, dias=dias)

        categorias = config.get("contab_categorias") or nfse.CONTAB_CATEGORIAS_DEFAULT
        total = sum(len(v) for v in emails.values())
        linhas = []
        if total == 0:
            linhas.append(f"Nenhum e-mail encontrado nos últimos {dias} dias.")
        else:
            linhas.append(f"{total} e-mail(s) da contabilidade nos últimos {dias} dias:\n")
            for cat, label in categorias.items():
                msgs = emails.get(cat, [])
                if msgs:
                    linhas.append(f"{label}: {len(msgs)} e-mail(s)")
                    for m in msgs[:5]:
                        linhas.append(f"  • {m.get('data','')} — {m.get('assunto','(sem assunto)')}")

        return [types.TextContent(type="text", text="\n".join(linhas))]

    # ── encaminhar_nota_contabilidade ────────────────────────────────────────
    elif name == "encaminhar_nota_contabilidade":
        xml_path    = Path(arguments["xml_path"])
        pdf_path    = Path(arguments["pdf_path"]) if arguments.get("pdf_path") else None
        cliente_chave = arguments["cliente"]
        competencia = arguments["competencia"]
        usd         = float(arguments["usd"])
        brl         = float(arguments["brl"])

        _, cliente = nfse.resolver_cliente(cliente_chave, clientes)
        dados      = {"vUSD": usd, "vBRL": brl, "dCompet": competencia}

        xml_bytes = xml_path.read_bytes() if xml_path.exists() else None
        pdf_bytes = pdf_path.read_bytes() if pdf_path and pdf_path.exists() else None

        if not xml_bytes:
            return [types.TextContent(type="text",
                                      text=f"Erro: XML não encontrado em {xml_path}")]

        nfse.enviar_nfse_contabilidade(config, secrets, dados, cliente, xml_bytes, pdf_bytes)

        return [types.TextContent(type="text",
                                  text=f"✅ NFS-e encaminhada para {config['email_contabilidade']}")]

    return [types.TextContent(type="text", text=f"Tool '{name}' não reconhecida.")]


async def main_async():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main_async())