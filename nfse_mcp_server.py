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
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

# DATA_DIR: onde ficam config.json, secrets.json, clientes.json e certs/.
# Por padrão = mesmo diretório do script. Para separar código de dados
# (repo git num lugar, dados em outro) defina NFSE_DATA_DIR no ambiente.
# Exemplo no claude_desktop_config.json:
#   "env": { "NFSE_DATA_DIR": "/Users/você/nfse-adsense" }
DATA_DIR = Path(os.environ.get("NFSE_DATA_DIR", SKILL_DIR)).expanduser().resolve()

import emitir_nfse as nfse

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

server = Server("nfse-nacional")


# ═════════════════════════════════════════════════════════════════════════════
# Helpers do setup conversacional
# ═════════════════════════════════════════════════════════════════════════════

CONFIG_PATH   = DATA_DIR / "config.json"
SECRETS_PATH  = DATA_DIR / "secrets.json"
CLIENTES_PATH = DATA_DIR / "clientes.json"

# Campos obrigatórios em config.json para conseguir emitir uma nota.
CONFIG_OBRIGATORIOS = {
    "cnpj":           "CNPJ do prestador (14 dígitos, somente números)",
    "cLocEmi":        "Código IBGE do município emissor (7 dígitos — ex: 4205407 = Florianópolis)",
    "serie":          "Série da NFS-e (ex: '900')",
    "pTotTribFed":    "Alíquota aproximada de tributo federal (%). Ex: '7.68' para Lucro Presumido + exportação (IRPJ 4,80% + CSLL 2,88%)",
    "versao_leiaute": "Versão do leiaute DPS (padrão: '1.01')",
    "cert_path":      "Caminho (relativo ao projeto) do certificado A1 .pfx. Ex: 'certs/meu_cert.pfx'",
    "api_base_url":   "Base da API de emissão (padrão: 'https://sefin.nfse.gov.br/SefinNacional')",
    "adn_base_url":   "Base da ADN para consulta/DANFSE (padrão: 'https://adn.nfse.gov.br/contribuintes')",
    "output_dir":     "Diretório absoluto onde salvar XML e PDF das notas emitidas",
}

# Campos opcionais (afetam funcionalidades extras).
CONFIG_OPCIONAIS = {
    "razao_social":        "Razão social (exibida em assuntos de e-mails)",
    "assinatura_email":    "Bloco de assinatura nos e-mails para a contabilidade",
    "mailgun_domain":      "Domínio Mailgun (necessário para envio de e-mails)",
    "mailgun_from":        "Remetente Mailgun. Ex: 'Financeiro <financeiro@exemplo.com.br>'",
    "email_remetente_cc":  "E-mail em CC nos envios",
    "email_contabilidade": "E-mail do destino (contabilidade)",
    "gmail_user":          "Conta Gmail p/ leitura IMAP de pagamentos e e-mails da contabilidade",
    "contab_remetentes":   "Dict { 'email@contab': 'categoria' } para classificar e-mails recebidos",
    "contab_categorias":   "Dict { 'categoria': 'Rótulo 📑' } — labels das categorias",
    "email_parsers":       "Lista [{sender, parser}] de remetentes de pagamentos a parsear. Default: Remessa Online. Parsers built-in: 'remessa_online', 'rendimento'.",
}

CONFIG_DEFAULTS = {
    "versao_leiaute": "1.01",
    "api_base_url":   "https://sefin.nfse.gov.br/SefinNacional",
    "adn_base_url":   "https://adn.nfse.gov.br/contribuintes",
    "serie":          "900",
}

SECRETS_OBRIGATORIOS = {
    "cert_password": "Senha do certificado digital A1",
}

SECRETS_OPCIONAIS = {
    "mailgun_api_key":     "API key da conta Mailgun (para envio de e-mails)",
    "gmail_client_id":     "OAuth2 Gmail client_id (para leitura IMAP)",
    "gmail_client_secret": "OAuth2 Gmail client_secret",
    "gmail_refresh_token": "OAuth2 Gmail refresh_token (gerado por setup_gmail_oauth.py)",
}


def _deep_merge(dst: dict, src: dict) -> dict:
    """Merge recursivo de `src` em `dst`, in-place. Retorna `dst`."""
    for k, v in src.items():
        if (
            k in dst
            and isinstance(dst[k], dict)
            and isinstance(v, dict)
        ):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def _load_json_safe(path: Path) -> dict:
    """Lê um JSON se existir, senão retorna {}."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_json_pretty(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _mascarar_valor(chave: str, valor) -> str:
    """Não retorna secrets em claro — só indica 'configurado' ou mostra primeiros chars."""
    if valor is None:
        return "(não configurado)"
    if isinstance(valor, (list, dict)):
        return f"({len(valor)} item(s))"
    s = str(valor)
    if not s:
        return "(vazio)"
    if len(s) <= 4:
        return "••••"
    return s[:3] + "•" * max(3, len(s) - 6) + s[-3:]


def _testar_pfx(pfx_path: Path, senha: bytes) -> dict:
    """Valida o .pfx abrindo-o com a senha. Retorna info pública do cert."""
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509 import NameOID

    if not pfx_path.exists():
        return {"ok": False, "erro": f"Arquivo não encontrado: {pfx_path}"}
    try:
        data = pfx_path.read_bytes()
        _, cert, _ = pkcs12.load_key_and_certificates(data, senha or None)
    except Exception as e:
        # Não vaza a senha — só a classe do erro
        return {"ok": False, "erro": f"Falha ao abrir PFX: {type(e).__name__}"}

    if cert is None:
        return {"ok": False, "erro": "Nenhum certificado no PFX"}

    def _attr(name, oid):
        try:
            return next(a.value for a in name if a.oid == oid)
        except StopIteration:
            return None

    return {
        "ok":         True,
        "titular":    _attr(cert.subject, NameOID.COMMON_NAME),
        "emissor":    _attr(cert.issuer,  NameOID.COMMON_NAME),
        "valido_de":  cert.not_valid_before_utc.isoformat(),
        "valido_ate": cert.not_valid_after_utc.isoformat(),
    }


def _diagnostico_setup() -> dict:
    """Monta o estado atual de configuração, campo a campo."""
    config   = _load_json_safe(CONFIG_PATH)
    secrets  = _load_json_safe(SECRETS_PATH)
    clientes = _load_json_safe(CLIENTES_PATH)

    # Config — separa presentes/faltantes
    cfg_presentes  = {k: _mascarar_valor(k, config.get(k)) for k in CONFIG_OBRIGATORIOS if k in config}
    cfg_faltantes  = [
        {"campo": k, "descricao": CONFIG_OBRIGATORIOS[k], "default_sugerido": CONFIG_DEFAULTS.get(k)}
        for k in CONFIG_OBRIGATORIOS if k not in config
    ]
    opt_presentes  = {k: _mascarar_valor(k, config.get(k)) for k in CONFIG_OPCIONAIS if k in config}
    opt_faltantes  = [{"campo": k, "descricao": CONFIG_OPCIONAIS[k]} for k in CONFIG_OPCIONAIS if k not in config]

    # Secrets
    sec_presentes = {k: _mascarar_valor(k, secrets.get(k)) for k in {**SECRETS_OBRIGATORIOS, **SECRETS_OPCIONAIS} if k in secrets}
    sec_obr_falt  = [{"campo": k, "descricao": SECRETS_OBRIGATORIOS[k]} for k in SECRETS_OBRIGATORIOS if k not in secrets]
    sec_opt_falt  = [{"campo": k, "descricao": SECRETS_OPCIONAIS[k]}   for k in SECRETS_OPCIONAIS   if k not in secrets]

    # Clientes
    lista_clientes = [k for k in clientes if not k.startswith("_")]

    # Certificado
    cert_path_rel = config.get("cert_path")
    cert_status: dict = {"caminho_configurado": cert_path_rel, "arquivo_existe": False, "validado": False}
    if cert_path_rel:
        cert_abs = DATA_DIR / cert_path_rel
        cert_status["arquivo_existe"] = cert_abs.exists()

    # Pronto para emitir?
    pronto = (
        not cfg_faltantes
        and not sec_obr_falt
        and lista_clientes
        and cert_status["arquivo_existe"]
    )

    # Próximos passos sugeridos
    proximos: list[str] = []
    if cfg_faltantes:
        # Dica: oferecer atalho via XML se o usuário tiver uma nota antiga
        if len(cfg_faltantes) >= 3:
            proximos.append(
                "Atalho: se você já tem uma NFS-e emitida antes (mesmo que pela "
                "interface web do EmissorNacional), passe o caminho do .xml e "
                "chame `inferir_de_xml` para pré-popular CNPJ/município/série."
            )
        proximos.append(f"Preencher {len(cfg_faltantes)} campo(s) obrigatório(s) em config.json — use `escrever_config`")
    if sec_obr_falt:
        proximos.append("Configurar senha do certificado — use `escrever_secrets`")
    if not lista_clientes:
        proximos.append("Adicionar pelo menos um tomador — use `adicionar_cliente`")
    if cert_path_rel and not cert_status["arquivo_existe"]:
        proximos.append(f"Copiar o arquivo .pfx para `{cert_path_rel}` (caminho relativo ao projeto)")
    if pronto:
        proximos.append("Validar certificado com `testar_certificado`")
        proximos.append("Tudo pronto! Pode pedir `emitir_nota` ou `emitir_notas_mes`.")

    return {
        "pronto_para_emitir": pronto,
        "arquivos": {
            "config.json":   CONFIG_PATH.exists(),
            "secrets.json":  SECRETS_PATH.exists(),
            "clientes.json": CLIENTES_PATH.exists(),
        },
        "config": {
            "obrigatorios_presentes": cfg_presentes,
            "obrigatorios_faltantes": cfg_faltantes,
            "opcionais_presentes":    opt_presentes,
            "opcionais_faltantes":    opt_faltantes,
        },
        "secrets": {
            "presentes":             sec_presentes,
            "obrigatorios_faltantes": sec_obr_falt,
            "opcionais_faltantes":    sec_opt_falt,
        },
        "clientes": {
            "total":  len(lista_clientes),
            "lista":  lista_clientes,
        },
        "certificado":   cert_status,
        "proximos_passos": proximos,
    }


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # ═══════════════════════════════════════════════════════════════════
        # Setup tools — rodam mesmo sem config.json / clientes.json existirem
        # ═══════════════════════════════════════════════════════════════════
        types.Tool(
            name="status_setup",
            description=(
                "Verifica o estado atual de configuração do emissor NFS-e Nacional. "
                "Retorna JSON estruturado listando quais campos obrigatórios/opcionais "
                "de config.json e secrets.json estão presentes ou faltando, quais "
                "clientes estão cadastrados, se o certificado digital existe no disco "
                "e sugere próximos passos. USE ESTA TOOL PRIMEIRO quando o usuário "
                "pedir 'configure o NFS-e', 'setup', 'configuração', 'o que falta pra "
                "emitir nota' — ela te diz exatamente o que perguntar ao usuário."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="escrever_config",
            description=(
                "Escreve (merge) campos em config.json. Aceita um dict `campos` com "
                "chave/valor dos campos a gravar. Faz merge recursivo — preserva "
                "valores existentes, só sobrescreve as chaves passadas. Cria o arquivo "
                "se não existir. Use depois de PERGUNTAR ao usuário os valores faltantes "
                "(obtidos via `status_setup`). Nunca invente valores — pergunte antes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "campos": {
                        "type": "object",
                        "description": (
                            "Dict de campos a mesclar em config.json. Ex: "
                            "{'cnpj': '12345678000190', 'cLocEmi': '4205407', "
                            "'serie': '900', 'mailgun_domain': 'empresa.com.br'}. "
                            "Para defaults comuns (versao_leiaute, api_base_url, "
                            "adn_base_url), você pode passar os valores retornados "
                            "como `default_sugerido` por `status_setup`."
                        ),
                    },
                },
                "required": ["campos"],
            },
        ),
        types.Tool(
            name="escrever_secrets",
            description=(
                "Escreve (merge) campos em secrets.json. NUNCA ecoe os valores de "
                "volta ao usuário — o arquivo é sensível. Use para gravar senha do "
                "certificado, API keys do Mailgun e tokens OAuth2 do Gmail. Pergunte "
                "os valores ao usuário e passe diretamente, sem repetir."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "campos": {
                        "type": "object",
                        "description": (
                            "Dict de campos a mesclar em secrets.json. Ex: "
                            "{'cert_password': 'senha_do_pfx', 'mailgun_api_key': '...'}"
                        ),
                    },
                },
                "required": ["campos"],
            },
        ),
        types.Tool(
            name="adicionar_cliente",
            description=(
                "Adiciona ou atualiza um tomador de serviço em clientes.json. "
                "`alias` é a chave usada em outras tools (ex: 'google', 'venatus'). "
                "`dados` é o dict com xNome, end, aliases, etc — mesma estrutura de "
                "clientes.example.json. Pergunte ao usuário país/endereço antes de "
                "chamar. Valida se tem pelo menos xNome."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "Chave identificadora curta (ex: 'google', 'acme_us')",
                    },
                    "dados": {
                        "type": "object",
                        "description": (
                            "Dict completo do cliente. Formato: "
                            "{'xNome': 'Razão Social', "
                            "'cNaoNIF': '2' (se exterior) OU 'cnpj': '...' (se BR), "
                            "'end': {'cPais': 'US', 'cEndPost': '...', 'xCidade': '...', "
                            "'xEstProvReg': '...', 'xLgr': '...', 'nro': '...', 'xBairro': '-'}, "
                            "'aliases': ['variante1', 'variante2']}"
                        ),
                    },
                },
                "required": ["alias", "dados"],
            },
        ),
        types.Tool(
            name="inferir_de_xml",
            description=(
                "Lê uma NFS-e (ou DPS) já emitida anteriormente e extrai CNPJ, "
                "cLocEmi, série, versão do leiaute, pTotTribFed e os dados do "
                "tomador (cliente). Retorna um preview SEM gravar nada. Use no "
                "setup quando o usuário disser 'já tenho uma nota emitida' — ele "
                "te dá o caminho do XML, você chama esta tool, mostra o preview, "
                "e depois pede confirmação antes de gravar via `escrever_config` "
                "e `adicionar_cliente`. Evita ficar digitando CNPJ/município/série "
                "à mão."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "xml_path": {
                        "type": "string",
                        "description": (
                            "Caminho absoluto do arquivo XML (.xml) de uma NFS-e "
                            "ou DPS já emitida. Pode ser uma nota gerada pela "
                            "interface web do EmissorNacional ou qualquer DPS "
                            "assinado no leiaute v1.01."
                        ),
                    },
                },
                "required": ["xml_path"],
            },
        ),
        types.Tool(
            name="testar_certificado",
            description=(
                "Abre o certificado A1 (.pfx) configurado em config.cert_path com a "
                "senha em secrets.cert_password e retorna informações públicas: "
                "titular (CN), emissor, validade. Use DEPOIS de configurar cert_path "
                "e cert_password para confirmar que está tudo certo. Não vaza senha "
                "em mensagens de erro."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        # ═══════════════════════════════════════════════════════════════════
        # Tools de produção
        # ═══════════════════════════════════════════════════════════════════
        types.Tool(
            name="listar_pagamentos",
            description=(
                "Lista pagamentos recebidos no Gmail para um dado mês, parseando "
                "e-mails de corretoras de câmbio (Remessa Online, Banco Rendimento). "
                "Retorna lista de transferências com USD, BRL, data e nome do "
                "ordenante — prontas para virar NFS-e. Use antes de `emitir_notas_mes` "
                "no fluxo 'emite as notas do AdSense desse mês'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mes": {
                        "type": "string",
                        "description": "Mês de referência no formato AAAA-MM. Ex: '2026-03'",
                    },
                },
                "required": ["mes"],
            },
        ),
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


def _json_reply(obj) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(obj, ensure_ascii=False, indent=2))]


def _text_reply(s: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=s)]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    # ═══════════════════════════════════════════════════════════════════════
    # Setup tools — rodam ANTES de load_config, pois config pode nem existir
    # ═══════════════════════════════════════════════════════════════════════
    if name == "status_setup":
        return _json_reply(_diagnostico_setup())

    if name == "escrever_config":
        campos = arguments.get("campos") or {}
        if not isinstance(campos, dict) or not campos:
            return _text_reply("Erro: parâmetro `campos` deve ser um dict não-vazio.")
        atual = _load_json_safe(CONFIG_PATH)
        _deep_merge(atual, campos)
        _save_json_pretty(CONFIG_PATH, atual)
        return _json_reply({
            "status":             "config.json atualizado",
            "campos_gravados":    list(campos.keys()),
            "total_campos_agora": len([k for k in atual if not k.startswith("_")]),
            "diagnostico":        _diagnostico_setup(),
        })

    if name == "escrever_secrets":
        campos = arguments.get("campos") or {}
        if not isinstance(campos, dict) or not campos:
            return _text_reply("Erro: parâmetro `campos` deve ser um dict não-vazio.")
        atual = _load_json_safe(SECRETS_PATH)
        _deep_merge(atual, campos)
        _save_json_pretty(SECRETS_PATH, atual)
        try:
            SECRETS_PATH.chmod(0o600)  # -rw------- : só o dono lê/escreve
        except Exception:
            pass
        return _json_reply({
            "status":          "secrets.json atualizado",
            "campos_gravados": list(campos.keys()),  # só nomes, nunca valores
            "permissao":       "0600",
            "diagnostico":     _diagnostico_setup(),
        })

    if name == "adicionar_cliente":
        alias = (arguments.get("alias") or "").strip()
        dados = arguments.get("dados") or {}
        if not alias:
            return _text_reply("Erro: parâmetro `alias` é obrigatório.")
        if not isinstance(dados, dict) or not dados.get("xNome"):
            return _text_reply("Erro: `dados` deve conter pelo menos `xNome` (razão social).")
        if not dados.get("end") or not isinstance(dados["end"], dict):
            return _text_reply("Erro: `dados.end` (endereço) é obrigatório.")
        dados.setdefault("aliases", [alias])
        atual = _load_json_safe(CLIENTES_PATH)
        ja_existia = alias in atual
        atual[alias] = dados
        _save_json_pretty(CLIENTES_PATH, atual)
        return _json_reply({
            "status":    "atualizado" if ja_existia else "criado",
            "alias":     alias,
            "xNome":     dados["xNome"],
            "total_clientes": len([k for k in atual if not k.startswith("_")]),
            "diagnostico":    _diagnostico_setup(),
        })

    if name == "inferir_de_xml":
        xml_path = (arguments.get("xml_path") or "").strip()
        if not xml_path:
            return _text_reply("Erro: parâmetro `xml_path` é obrigatório.")
        p = Path(xml_path).expanduser()
        if not p.exists():
            return _text_reply(f"Erro: arquivo não encontrado: {p}")
        try:
            inferido = nfse.inferir_config_de_xml(str(p))
        except Exception as e:
            return _text_reply(
                f"Erro ao parsear XML: {type(e).__name__}: {e}. "
                "O arquivo é uma NFS-e/DPS v1.01 válida?"
            )
        cliente = inferido.get("cliente") or {}
        alias_sugerido = (cliente.get("aliases") or [None])[0]
        return _json_reply({
            "status": "preview",
            "mensagem": (
                "Campos extraídos. NADA foi gravado ainda. Exiba ao usuário e "
                "confirme antes de chamar `escrever_config` e `adicionar_cliente`."
            ),
            "config_sugerida":  inferido.get("config") or {},
            "cliente_sugerido": cliente,
            "alias_sugerido":   alias_sugerido,
            "proximos_passos": [
                "Exiba o preview ao usuário",
                "Pergunte se quer gravar (e se o alias sugerido está ok)",
                "Se sim, chame `escrever_config` com `config_sugerida`",
                "E chame `adicionar_cliente` com `alias_sugerido` + `cliente_sugerido`",
                "Depois peça cert_path, cert_password, output_dir e demais campos opcionais",
            ],
        })

    if name == "testar_certificado":
        config = _load_json_safe(CONFIG_PATH)
        secrets = _load_json_safe(SECRETS_PATH)
        cert_rel = config.get("cert_path")
        if not cert_rel:
            return _text_reply("Erro: `cert_path` ainda não está em config.json. Use `escrever_config` primeiro.")
        senha = (secrets.get("cert_password") or "").encode()
        if not senha:
            return _text_reply("Erro: `cert_password` ainda não está em secrets.json. Use `escrever_secrets` primeiro.")
        pfx_abs = DATA_DIR / cert_rel
        return _json_reply(_testar_pfx(pfx_abs, senha))

    # ═══════════════════════════════════════════════════════════════════════
    # Tools de produção — requerem configuração completa
    # ═══════════════════════════════════════════════════════════════════════
    config   = nfse.load_config()
    secrets  = nfse.load_secrets()
    clientes = nfse.load_clientes()

    if not config:
        return _text_reply(
            "❌ config.json ainda não foi criado. "
            "Chame `status_setup` para ver o que falta, depois `escrever_config` "
            "com os valores perguntados ao usuário."
        )

    # Garante que cert_path é absoluto (config.json guarda caminho relativo a DATA_DIR)
    if config.get("cert_path"):
        config["cert_path"] = str(DATA_DIR / config["cert_path"])

    # ── listar_pagamentos ────────────────────────────────────────────────────
    if name == "listar_pagamentos":
        mes = arguments.get("mes")
        if not mes:
            return _text_reply("Erro: parâmetro `mes` é obrigatório (formato AAAA-MM).")
        try:
            pagamentos = nfse.buscar_pagamentos_mes(config, secrets, mes)
        except Exception as e:
            return _text_reply(f"Erro ao buscar pagamentos via Gmail: {e}")
        return _json_reply({
            "mes":   mes,
            "total": len(pagamentos),
            "pagamentos": [
                {
                    "data":          p.get("dCompet"),
                    "cliente_nome":  p.get("cliente_nome"),
                    "usd":           p.get("vUSD"),
                    "brl":           p.get("vBRL"),
                    "remetente":     p.get("_remetente"),
                    "assunto":       p.get("_subject"),
                }
                for p in pagamentos
            ],
        })

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