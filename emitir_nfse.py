#!/usr/bin/env python3
"""
Emissor automático de NFS-e Nacional (padrão Sistema Nacional NFS-e)
API REST do Sistema Nacional NFS-e com mTLS + XMLDSig.

Exemplos de uso:
  # Emitir uma nota específica
  python3 emitir_nfse.py --cliente google --usd 3206.19 --brl 16265.32 --competencia 2026-03-23

  # Emitir todas as notas de um mês (lê Gmail automaticamente)
  python3 emitir_nfse.py --mes 2026-03

  # Verificar e-mails e solicitações da contabilidade
  python3 emitir_nfse.py --verificar

  # Dry-run (gera e assina o XML, não envia à API)
  python3 emitir_nfse.py --dry-run --cliente venatus --usd 1697.29 --brl 8898.73 --competencia 2026-03-03
"""

import argparse
import base64
import calendar
import gzip
import email as email_lib
import hashlib
import imaplib
import json
import os
import re
import smtplib
import urllib.parse
import urllib.request
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
from lxml import etree

# ─── Constantes XMLDSig ──────────────────────────────────────────────────────

XMLDSIG    = "http://www.w3.org/2000/09/xmldsig#"
C14N       = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
SHA256     = "http://www.w3.org/2001/04/xmlenc#sha256"
RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ENV_SIG    = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
NS_NFSE    = "http://www.sped.fazenda.gov.br/nfse"

SKILL_DIR = Path(__file__).parent

# ─── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Lê config.json. Retorna {} se o arquivo ainda não existe
    (permite que o setup conversacional rode antes da configuração)."""
    path = SKILL_DIR / "config.json"
    return json.load(open(path)) if path.exists() else {}

def load_secrets() -> dict:
    path = SKILL_DIR / "secrets.json"
    return json.load(open(path)) if path.exists() else {}

def load_clientes() -> dict:
    """Lê clientes.json. Retorna {} se o arquivo ainda não existe."""
    path = SKILL_DIR / "clientes.json"
    return json.load(open(path)) if path.exists() else {}

def inferir_config_de_xml(xml_path: str) -> dict:
    """
    Lê uma NFS-e (ou DPS) já emitida e extrai os campos de config
    e um cliente (tomador) sugerido. Não grava nada — apenas devolve
    um dict com duas chaves: `config` e `cliente`, prontos para revisão.

    Útil no setup: se você tem uma nota válida emitida pela interface
    web, pode passar o XML para pré-popular config.json sem ficar
    digitando CNPJ/série/município à mão.

    O XML pode estar em qualquer nível: um DPS isolado, um envelope
    NFSe contendo o DPS, ou uma nota completa com assinatura.
    """
    tree = etree.parse(xml_path)
    root = tree.getroot()

    # Mapa de namespace: aceita o XML com ou sem prefixo
    ns = {"n": NS_NFSE}

    def first_text(xpath_local: str) -> str | None:
        # Tenta com namespace explícito (prefixando cada segmento) e sem
        # namespace (fallback tolerante para XMLs sem xmlns)
        with_ns = ".//" + "/".join(f"n:{seg}" for seg in xpath_local.split("/"))
        without_ns = f".//{xpath_local}"
        for xp in (with_ns, without_ns):
            try:
                el = root.xpath(xp, namespaces=ns)
            except etree.XPathEvalError:
                continue
            if el:
                txt = (el[0].text or "").strip()
                if txt:
                    return txt
        return None

    config_inferida: dict = {}

    cnpj = first_text("prest/CNPJ") or first_text("CNPJEmit")
    if cnpj:
        config_inferida["cnpj"] = cnpj

    cloc = first_text("cLocEmi")
    if cloc:
        config_inferida["cLocEmi"] = cloc

    serie = first_text("serie")
    if serie:
        # Leiaute guarda `00900`, config.json usa `900`
        config_inferida["serie"] = serie.lstrip("0") or "0"

    versao = root.get("versao") or first_text("versao")
    if versao:
        config_inferida["versao_leiaute"] = versao

    ptotfed = first_text("pTotTribFed")
    if ptotfed:
        config_inferida["pTotTribFed"] = ptotfed

    # Tomador — vira um cliente sugerido
    cliente_inferido: dict = {}
    xnome = first_text("toma/xNome") or first_text("xNomeToma")
    if xnome:
        cliente_inferido["xNome"] = xnome

        # Tipo: se tem cNaoNIF é exterior; se tem CNPJ ou CPF é BR
        cnao = first_text("toma/cNaoNIF")
        if cnao:
            cliente_inferido["cNaoNIF"] = cnao
        tom_cnpj = first_text("toma/CNPJ")
        if tom_cnpj:
            cliente_inferido["cnpj"] = tom_cnpj
        tom_cpf = first_text("toma/CPF")
        if tom_cpf:
            cliente_inferido["cpf"] = tom_cpf

        end: dict = {}
        for campo in ("cPais", "cEndPost", "xCidade", "xEstProvReg",
                      "xLgr", "nro", "xCpl", "xBairro"):
            val = first_text(f"toma/end/endExt/{campo}") or first_text(f"toma/end/{campo}")
            if val:
                end[campo] = val
        if end:
            cliente_inferido["end"] = end

        # Sugere alias a partir do nome (primeira palavra, minúscula)
        cliente_inferido["aliases"] = [xnome.split()[0].lower()] if xnome else []

    return {
        "config":  config_inferida,
        "cliente": cliente_inferido,
    }


def resolver_cliente(nome: str, clientes: dict) -> tuple:
    """Resolve o nome do cliente para a chave do clientes.json.
    Retorna (chave, dados_cliente) ou lança ValueError."""
    nome_lower = nome.lower().strip()
    for chave, dados in clientes.items():
        if chave.startswith("_"):
            continue
        aliases = dados.get("aliases", [chave])
        if nome_lower in [a.lower() for a in aliases] or nome_lower == chave:
            return chave, dados
    raise ValueError(
        f"Cliente '{nome}' não encontrado em clientes.json.\n"
        f"Clientes disponíveis: {[k for k in clientes if not k.startswith('_')]}"
    )

# ─── Parsers de e-mail ───────────────────────────────────────────────────────

def _get_body(msg) -> str:
    """Extrai o texto de um e-mail (multipart ou não)."""
    if msg.is_multipart():
        plain = html = ""
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                plain = part.get_payload(decode=True).decode("utf-8", errors="ignore")
            elif ct == "text/html" and not plain:
                html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
        return plain or html
    return msg.get_payload(decode=True).decode("utf-8", errors="ignore")

def parse_rendimento(msg) -> dict:
    """Parser para e-mails do Banco Rendimento (cambioonline@mail-rendimento.com.br)."""
    body = _get_body(msg)

    def find(pattern):
        m = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else None

    def parse_br(s):
        return float(s.replace(".", "").replace(",", ".")) if s else None

    raw_usd  = find(r"Valor Recebido[:\s]+US\$\s*([\d\.,]+)")
    raw_brl  = find(r"Total[:\s]+R\$\s*([\d\.,]+)")
    raw_date = find(r"Remessa recebida do exterior\s+(\d{2}/\d{2}/\d{4})")
    ordenante= find(r"Ordenante[:\s]+([^\n\r]+)")

    if not all([raw_usd, raw_brl, raw_date]):
        raise RuntimeError(f"Rendimento: campos não encontrados no e-mail (usd={raw_usd}, brl={raw_brl}, data={raw_date})")

    d, m_d, y = raw_date.split("/")
    cliente_nome = (ordenante or "").strip()

    return {
        "vUSD":        parse_br(raw_usd),
        "vBRL":        parse_br(raw_brl),
        "dCompet":     f"{y}-{m_d}-{d}",
        "cliente_nome": cliente_nome,
    }

def parse_remessa_online(msg) -> dict:
    """Parser para e-mails da Remessa Online (nao-responder@remessaonline.com.br)."""
    body    = _get_body(msg)
    subject = msg.get("Subject", "")

    def find(pattern, text):
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else None

    def parse_br(s):
        return float(s.replace(".", "").replace(",", ".")) if s else None

    # Valores no corpo
    raw_brl = find(r"Valor em reais\s+BRL\s*([\d\.,]+)", body)
    raw_usd = find(r"Valor em moeda estrangeira[:\s]+USD\s*([\d\.,]+)", body)

    # Data: usa o cabeçalho Date do e-mail
    date_header = msg.get("Date", "")
    try:
        dt = parsedate_to_datetime(date_header)
        dCompet = dt.strftime("%Y-%m-%d")
    except Exception:
        dCompet = datetime.now().strftime("%Y-%m-%d")

    # Nome do cliente: extrai do assunto "Comprovante de Transferência 1/NOME - #ID"
    cliente_nome = find(r"Comprovante de Transfer[eê]ncia\s+\d+/([^-#\n]+)", subject)
    if not cliente_nome:
        cliente_nome = find(r"Transfer[eê]ncia\s+\d+/([^-#\n]+)", body)
    cliente_nome = (cliente_nome or "").strip()

    if not all([raw_usd, raw_brl]):
        raise RuntimeError(f"Remessa Online: campos não encontrados (usd={raw_usd}, brl={raw_brl})")

    return {
        "vUSD":         parse_br(raw_usd),
        "vBRL":         parse_br(raw_brl),
        "dCompet":      dCompet,
        "cliente_nome": cliente_nome,
    }

# Registry built-in de parsers por nome — referenciados em config.json
# via `email_parsers: [{sender, parser}]`. Adicionar um novo parser =
# escrever a função aqui e adicionar ao BUILTIN_PARSERS.
BUILTIN_PARSERS = {
    "remessa_online": parse_remessa_online,
    "rendimento":     parse_rendimento,
}

# Default: só Remessa Online — é a corretora mais comum em conjunto com AdSense.
# Usuários que usam Banco Rendimento ou outra fonte devem configurar
# `email_parsers` em config.json explicitamente.
DEFAULT_EMAIL_PARSERS = [
    {"sender": "nao-responder@remessaonline.com.br", "parser": "remessa_online"},
    {"sender": "noreply@remessaonline.com.br",       "parser": "remessa_online"},
]


def _resolver_parsers(config: dict) -> list[tuple]:
    """
    Resolve a lista de (sender, callable) a partir de `config.email_parsers`.

    Cada entrada é um dict {sender, parser} onde `parser` é o nome de um
    parser em BUILTIN_PARSERS. Se a config não tiver `email_parsers`, usa
    DEFAULT_EMAIL_PARSERS (Remessa Online).
    """
    entries = config.get("email_parsers") or DEFAULT_EMAIL_PARSERS
    resolved = []
    for entry in entries:
        sender = entry.get("sender")
        pname  = entry.get("parser")
        if not sender or not pname:
            continue
        fn = BUILTIN_PARSERS.get(pname)
        if not fn:
            print(f"   ⚠️  Parser '{pname}' desconhecido (sender={sender}). "
                  f"Built-ins: {list(BUILTIN_PARSERS.keys())}")
            continue
        resolved.append((sender, fn))
    return resolved

# ─── Gmail OAuth2 ────────────────────────────────────────────────────────────

def _gmail_access_token(secrets: dict) -> str:
    """Troca o refresh_token por um access_token fresco via OAuth2."""
    data = urllib.parse.urlencode({
        "client_id":     secrets["gmail_client_id"],
        "client_secret": secrets["gmail_client_secret"],
        "refresh_token": secrets["gmail_refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def _xoauth2_string(user: str, access_token: str) -> str:
    """Monta a string XOAUTH2 codificada em base64 para o SMTP."""
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode()).decode()


# ─── Gmail IMAP ──────────────────────────────────────────────────────────────

def imap_connect(config: dict, secrets: dict):
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    access_token = _gmail_access_token(secrets)
    auth_string  = _xoauth2_string(config["gmail_user"], access_token)
    imap.authenticate("XOAUTH2", lambda x: auth_string)
    return imap

def buscar_pagamentos_mes(config: dict, secrets: dict, mes: str) -> list:
    """
    Busca todos os e-mails de pagamento de um mês (formato AAAA-MM).
    Retorna lista de dicts com os dados de cada transferência.
    """
    ano, m = mes.split("-")
    _, ultimo_dia = calendar.monthrange(int(ano), int(m))

    meses_imap = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    nome_mes = meses_imap[int(m) - 1]
    imap_ini = f"01-{nome_mes}-{ano}"
    imap_fim = f"{ultimo_dia}-{nome_mes}-{ano}"

    imap = imap_connect(config, secrets)
    imap.select("INBOX")

    transferencias = []
    parsers = _resolver_parsers(config)

    for remetente, parser in parsers:
        criteria = (
            f'(FROM "{remetente}" '
            f'SINCE "{imap_ini}" '
            f'BEFORE "{imap_fim}")'
        )
        _, msgs = imap.search(None, criteria)
        ids = msgs[0].split()

        for msg_id in ids:
            _, data = imap.fetch(msg_id, "(RFC822)")
            msg = email_lib.message_from_bytes(data[0][1])
            try:
                dados = parser(msg)
                dados["_remetente"] = remetente
                dados["_subject"]   = msg.get("Subject", "")
                transferencias.append(dados)
            except Exception as e:
                print(f"   ⚠️  Skipping e-mail '{msg.get('Subject','')}': {e}")

    imap.logout()

    # Ordena por data
    transferencias.sort(key=lambda x: x["dCompet"])
    return transferencias

def buscar_ultimo_pagamento(config: dict, secrets: dict) -> dict:
    """Busca o e-mail de pagamento mais recente de qualquer corretora."""
    imap = imap_connect(config, secrets)
    imap.select("INBOX")

    candidatos = []
    for remetente, parser in _resolver_parsers(config):
        _, msgs = imap.search(None, "FROM", f'"{remetente}"')
        ids = msgs[0].split()
        if not ids:
            continue
        _, data = imap.fetch(ids[-1], "(RFC822)")
        msg = email_lib.message_from_bytes(data[0][1])
        try:
            dados = parser(msg)
            dados["_remetente"] = remetente
            dados["_subject"]   = msg.get("Subject", "")
            candidatos.append(dados)
        except Exception:
            pass

    imap.logout()
    if not candidatos:
        raise RuntimeError("Nenhum e-mail de pagamento encontrado.")
    return max(candidatos, key=lambda x: x["dCompet"])

# ─── Verificação de e-mails da contabilidade ─────────────────────────────────
#
# Categorias e remetentes são lidos de config.json:
#   "contab_remetentes": { "fiscal@sua.contabilidade": "fiscal", ... }
#   "contab_categorias": { "fiscal": "📑 Fiscal", ... }
# Se ausentes, usa defaults genéricos abaixo.

CONTAB_CATEGORIAS_DEFAULT = {
    "fiscal":     "📑 Fiscal (documentos NFS-e)",
    "contabil":   "📊 Contábil (extratos e movimentações)",
    "pessoal":    "👥 Pessoal (folha, recibos, DARF)",
    "financeiro": "💰 Financeiro (cobranças e faturas)",
}

def _decode_header(h: str) -> str:
    """Decodifica cabeçalho de e-mail (suporte a =?UTF-8?Q?...?=)."""
    from email.header import decode_header
    parts = decode_header(h or "")
    result = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            result.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(chunk)
    return " ".join(result)

def verificar_emails_contabilidade(config: dict, secrets: dict, dias: int = 60) -> dict:
    """
    Busca e categoriza e-mails recentes dos remetentes da contabilidade
    configurados em config.json (`contab_remetentes`). Retorna dict com
    listas de e-mails por categoria.
    """
    contab_remetentes = config.get("contab_remetentes") or {}
    contab_categorias = config.get("contab_categorias") or CONTAB_CATEGORIAS_DEFAULT
    if not contab_remetentes:
        return {cat: [] for cat in contab_categorias}

    imap = imap_connect(config, secrets)
    imap.select("INBOX")

    # Data de corte no formato IMAP
    desde = (datetime.now() - timedelta(days=dias))
    meses_imap = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    imap_desde = f"{desde.day:02d}-{meses_imap[desde.month-1]}-{desde.year}"

    emails_por_categoria = {cat: [] for cat in contab_categorias}

    remetentes_busca = list(contab_remetentes.keys())

    vistos = set()  # evitar duplicatas por msg-id

    for remetente in remetentes_busca:
        criteria = f'(FROM "{remetente}" SINCE "{imap_desde}")'
        try:
            _, msgs = imap.search(None, criteria)
        except Exception:
            continue
        ids = msgs[0].split()
        for msg_id in ids:
            try:
                _, data = imap.fetch(msg_id, "(RFC822)")
                msg = email_lib.message_from_bytes(data[0][1])
                msg_key = msg.get("Message-ID", str(msg_id))
                if msg_key in vistos:
                    continue
                vistos.add(msg_key)

                subject = _decode_header(msg.get("Subject", "(sem assunto)"))
                date_h  = msg.get("Date", "")
                from_h  = _decode_header(msg.get("From", remetente))

                try:
                    dt = parsedate_to_datetime(date_h)
                    date_fmt = dt.strftime("%d/%m/%Y")
                except Exception:
                    date_fmt = date_h[:16] if date_h else "?"

                # Determina categoria pelo remetente configurado
                cat = contab_remetentes.get(remetente, "contabil")
                if cat not in emails_por_categoria:
                    cat = next(iter(emails_por_categoria))

                # Extrai primeiro parágrafo do corpo como prévia
                body = _get_body(msg)
                linhas = [l.strip() for l in body.splitlines() if l.strip()]
                preview = next((l for l in linhas if len(l) > 20), "")[:120]

                emails_por_categoria[cat].append({
                    "data":    date_fmt,
                    "from":    from_h,
                    "subject": subject,
                    "preview": preview,
                })
            except Exception:
                continue

    imap.logout()

    # Ordena cada categoria por data (mais recente primeiro)
    for cat in emails_por_categoria:
        emails_por_categoria[cat].sort(key=lambda x: x["data"], reverse=True)

    return emails_por_categoria


def imprimir_resumo_contabilidade(emails: dict, config: dict | None = None):
    """Imprime resumo formatado dos e-mails da contabilidade."""
    total = sum(len(v) for v in emails.values())
    if total == 0:
        print("✅ Nenhum e-mail recente da contabilidade encontrado.")
        return

    print(f"\n{'═'*60}")
    print(f"📬  E-MAILS DA CONTABILIDADE  ({total} mensagens recentes)")
    print(f"{'═'*60}\n")

    categorias = (config or {}).get("contab_categorias") or CONTAB_CATEGORIAS_DEFAULT
    for cat, label in categorias.items():
        items = emails.get(cat, [])
        if not items:
            continue
        print(f"  {label}  ({len(items)} e-mail{'s' if len(items)>1 else ''})")
        print(f"  {'─'*54}")
        for item in items:
            print(f"  📅 {item['data']}  |  {item['subject']}")
            if item["preview"]:
                print(f"     ↳ {item['preview']}")
        print()

    # Destaca solicitações pendentes (heurística por palavras-chave no assunto)
    pendentes = []
    kw_solicitacao = ["solicita", "enviar", "favor", "importante", "documentos",
                      "gestão fiscal", "contabilidade", "guia", "darf", "folha"]
    for cat, items in emails.items():
        for item in items:
            if any(kw in item["subject"].lower() for kw in kw_solicitacao):
                pendentes.append(f"  • [{item['data']}] {item['subject']}")

    if pendentes:
        print(f"  ⚠️  AÇÕES PENDENTES IDENTIFICADAS:")
        for p in pendentes:
            print(p)
        print()

# ─── Certificado / mTLS ──────────────────────────────────────────────────────

def carregar_pfx(cert_path: str, senha: bytes):
    with open(cert_path, "rb") as f:
        pfx = f.read()
    return pkcs12.load_key_and_certificates(pfx, senha)

def session_mtls(cert_path: str, senha: bytes) -> requests.Session:
    pk, cert, _ = carregar_pfx(cert_path, senha)
    pem_key  = pk.private_bytes(Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pem_cert = cert.public_bytes(Encoding.PEM)
    tmp_key  = tempfile.NamedTemporaryFile(delete=False, suffix=".pem", mode="wb")
    tmp_cert = tempfile.NamedTemporaryFile(delete=False, suffix=".pem", mode="wb")
    tmp_key.write(pem_key);  tmp_key.close()
    tmp_cert.write(pem_cert); tmp_cert.close()
    os.chmod(tmp_key.name, 0o600)
    os.chmod(tmp_cert.name, 0o600)
    session = requests.Session()
    session.cert = (tmp_cert.name, tmp_key.name)
    session.verify = True
    session._tmp_files = [tmp_key.name, tmp_cert.name]
    return session

def cleanup_session(session: requests.Session):
    for f in getattr(session, "_tmp_files", []):
        try:
            os.unlink(f)
        except OSError:
            pass

# ─── API NFS-e ───────────────────────────────────────────────────────────────

def _extrair_nnfse_da_chave(chave: str) -> int | None:
    """Extrai o nNFSe (int) de uma chave de acesso NFS-e Nacional (50 dígitos).

    Estrutura: [0:7]=cLocEmi, [7]=tpEmit, [8]=tpInsc, [9:23]=CNPJ,
               [23:36]=nNFSe (13), [36:40]=aamm, [40:50]=DV/hash.
    """
    if not chave or len(chave) < 36:
        return None
    try:
        return int(chave[23:36])
    except (ValueError, TypeError):
        return None


def consultar_ultimo_nnfse_via_api(session: requests.Session, config: dict) -> int:
    """Consulta /contribuintes/DFe/0 e retorna o maior nNFSe emitido pelo nosso CNPJ.

    Faz UMA única requisição (diferente do antigo HEAD loop) — a API devolve o
    lote completo de NSU=0 em diante com todas as DF-e relacionadas ao contribuinte.
    Retorna 0 se nenhuma nota nossa for encontrada.
    """
    adn_base = config.get("adn_base_url", "https://adn.nfse.gov.br/contribuintes").rstrip("/")
    url = f"{adn_base}/DFe/0"
    cnpj = config["cnpj"]
    r = session.get(url, headers={"Accept": "application/json"}, timeout=30)
    if not r.ok:
        return 0
    try:
        data = r.json()
    except ValueError:
        return 0
    maior = 0
    for e in data.get("LoteDFe", []) or []:
        if e.get("TipoDocumento") != "NFSE":
            continue
        if e.get("TipoEvento"):  # ignora eventos (cancelamento etc.)
            continue
        ch = e.get("ChaveAcesso", "") or ""
        # Filtra: tpEmit=2 (prestador) e CNPJ é o nosso
        if len(ch) < 36 or ch[7] != "2" or ch[9:23] != cnpj:
            continue
        n = _extrair_nnfse_da_chave(ch)
        if n is not None and n > maior:
            maior = n
    return maior


def consultar_ultimo_nnfse_local(config: dict) -> int:
    """Varre output_dir por XMLs de NFS-e e retorna o maior nNFSe encontrado.

    Fallback quando a API não responde. Lê o atributo Id de <infNFSe> ou a
    primeira ChaveAcesso presente no arquivo.
    """
    output_dir = Path(config.get("output_dir", str(SKILL_DIR / "notas")))
    if not output_dir.exists():
        return 0
    cnpj = config["cnpj"]
    maior = 0
    for xml_path in output_dir.glob("*.xml"):
        try:
            data = xml_path.read_bytes()
            root = etree.fromstring(data)
        except (OSError, etree.XMLSyntaxError):
            continue
        # Procura <infNFSe Id="NFS...">
        inf = root.find(f".//{{{NS_NFSE}}}infNFSe")
        chave = None
        if inf is not None:
            idv = inf.get("Id", "")
            if idv.startswith("NFS"):
                chave = idv[3:]
            elif idv:
                chave = idv
        if not chave:
            # fallback: <chNFSe> ou <ChaveAcesso>
            for tag in ("chNFSe", "ChaveAcesso"):
                el = root.find(f".//{{{NS_NFSE}}}{tag}") or root.find(f".//{tag}")
                if el is not None and el.text:
                    chave = el.text.strip()
                    break
        if not chave or len(chave) < 36:
            continue
        if chave[9:23] != cnpj:
            continue
        n = _extrair_nnfse_da_chave(chave)
        if n is not None and n > maior:
            maior = n
    return maior


def _cache_ndps_path() -> Path:
    return SKILL_DIR / "ultimo_ndps.json"


def ler_cache_ndps() -> int:
    try:
        with open(_cache_ndps_path(), "r", encoding="utf-8") as f:
            return int(json.load(f).get("ultimo_nnfse", 0))
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return 0


def salvar_cache_ndps(n: int, chave: str | None = None) -> None:
    try:
        payload = {
            "ultimo_nnfse": int(n),
            "chave":        chave,
            "atualizado":   datetime.now().isoformat(timespec="seconds"),
        }
        with open(_cache_ndps_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def descobrir_proximo_ndps(session: requests.Session, config: dict) -> int:
    """Descobre o próximo nDPS seguro combinando 3 fontes, sem rate-limit loop.

    Ordem:
      1. API /DFe/0                  (fonte oficial — 1 única chamada)
      2. Varredura dos XMLs locais   (output_dir)
      3. Cache local ultimo_ndps.json
    Retorna max(n) + 1 entre todas as fontes disponíveis.
    """
    candidatos = []
    try:
        n_api = consultar_ultimo_nnfse_via_api(session, config)
        if n_api:
            candidatos.append(("api",   n_api))
    except requests.RequestException:
        pass
    try:
        n_loc = consultar_ultimo_nnfse_local(config)
        if n_loc:
            candidatos.append(("local", n_loc))
    except Exception:
        pass
    n_cache = ler_cache_ndps()
    if n_cache:
        candidatos.append(("cache", n_cache))
    if not candidatos:
        return 1
    maior = max(n for _, n in candidatos)
    return maior + 1

# ─── Construção do XML DPS ───────────────────────────────────────────────────

def _toma_xml(cliente: dict) -> str:
    """Gera o bloco XML <toma> a partir dos dados do cliente."""
    end = cliente.get("end", {})
    xNome   = cliente["xNome"]
    cNaoNIF = cliente.get("cNaoNIF", "2")

    end_xml = ""
    if end:
        cPais        = end.get("cPais", "")
        xCidade      = end.get("xCidade", "")
        xEstProvReg  = end.get("xEstProvReg", "")
        cEndPost     = end.get("cEndPost", "")
        xLgr         = end.get("xLgr", "")
        nro          = end.get("nro", "")
        xCpl         = end.get("xCpl", "")
        xBairro      = end.get("xBairro", "-")

        # TCEnderExt: cPais, cEndPost, xCidade, xEstProvReg — todos obrigatórios.
        end_ext = (
            f"<endExt>"
            f"<cPais>{cPais}</cPais>"
            f"<cEndPost>{cEndPost}</cEndPost>"
            f"<xCidade>{xCidade}</xCidade>"
            f"<xEstProvReg>{xEstProvReg}</xEstProvReg>"
            f"</endExt>"
        )
        end_xml = (
            f"<end>"
            + end_ext
            + f"<xLgr>{xLgr}</xLgr>"
            + (f"<nro>{nro}</nro>" if nro else "")
            + (f"<xCpl>{xCpl}</xCpl>" if xCpl else "")
            + f"<xBairro>{xBairro}</xBairro>"
            + f"</end>"
        )

    return (
        f"<toma>"
        f"<cNaoNIF>{cNaoNIF}</cNaoNIF>"
        f"<xNome>{xNome}</xNome>"
        + end_xml +
        f"</toma>"
    )

def build_dps_xml(config: dict, dados: dict, ndps: int, cliente: dict) -> tuple:
    """Retorna (xml_string, dps_id)."""
    cnpj          = config["cnpj"]
    serie         = config["serie"].zfill(5)
    cloc          = config["cLocEmi"]
    versao        = config.get("versao_leiaute", "1.01")
    p_trib_fed    = config.get("pTotTribFed", "7.68")  # alíquota approx. Simples Nacional exportação

    ndps_str  = str(ndps).zfill(15)        # Id XML: 45 chars fixos
    ndps_elem = str(ndps)                   # <nDPS>: sem zeros à esquerda (pattern [1-9][0-9]{0,14})
    dps_id    = f"DPS{cloc}2{cnpj}{serie}{ndps_str}"
    dhEmi    = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S") + "-03:00"
    vServ    = f"{dados['vBRL']:.2f}"
    vMoeda   = f"{dados['vUSD']:.2f}"
    toma_xml = _toma_xml(cliente)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<DPS xmlns="{NS_NFSE}" versao="{versao}">
  <infDPS Id="{dps_id}">
    <tpAmb>1</tpAmb>
    <dhEmi>{dhEmi}</dhEmi>
    <verAplic>python-nfse-1.0</verAplic>
    <serie>{serie}</serie>
    <nDPS>{ndps_elem}</nDPS>
    <dCompet>{dados['dCompet']}</dCompet>
    <tpEmit>1</tpEmit>
    <cLocEmi>{cloc}</cLocEmi>
    <prest>
      <CNPJ>{cnpj}</CNPJ>
      <regTrib>
        <opSimpNac>1</opSimpNac>
        <regEspTrib>0</regEspTrib>
      </regTrib>
    </prest>
    {toma_xml}
    <serv>
      <locPrest>
        <cPaisPrestacao>{cliente['end']['cPais'] if cliente.get('end') else 'US'}</cPaisPrestacao>
      </locPrest>
      <cServ>
        <cTribNac>010902</cTribNac>
        <xDescServ>Vinculação de publicidade</xDescServ>
        <cNBS>117039200</cNBS>
      </cServ>
      <comExt>
        <mdPrestacao>1</mdPrestacao>
        <vincPrest>0</vincPrest>
        <tpMoeda>220</tpMoeda>
        <vServMoeda>{vMoeda}</vServMoeda>
        <mecAFComexP>01</mecAFComexP>
        <mecAFComexT>01</mecAFComexT>
        <movTempBens>1</movTempBens>
        <mdic>0</mdic>
      </comExt>
    </serv>
    <valores>
      <vServPrest>
        <vServ>{vServ}</vServ>
      </vServPrest>
      <trib>
        <tribMun>
          <tribISSQN>3</tribISSQN>
          <tpRetISSQN>1</tpRetISSQN>
        </tribMun>
        <tribFed>
          <piscofins>
            <CST>08</CST>
          </piscofins>
        </tribFed>
        <totTrib>
          <pTotTrib>
            <pTotTribFed>{p_trib_fed}</pTotTribFed>
            <pTotTribEst>0.00</pTotTribEst>
            <pTotTribMun>0.00</pTotTribMun>
          </pTotTrib>
        </totTrib>
      </trib>
    </valores>
  </infDPS>
</DPS>"""

    return xml, dps_id

# ─── Assinatura XML ───────────────────────────────────────────────────────────

def assinar_dps(xml_str: str, cert_path: str, senha: bytes, ref_id: str) -> bytes:
    """Assina a DPS com XMLDSig enveloped, RSA-SHA256, C14N 1.0 inclusive.

    Usa signxml (que faz canonicalização corretamente incluindo namespaces ancestrais).
    O prefixo ds: é omitido — o sistema nacional espera default namespace.
    """
    from signxml import XMLSigner, methods, algorithms
    pk, cert, _ = carregar_pfx(cert_path, senha)
    key_pem  = pk.private_bytes(Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    cert_pem = cert.public_bytes(Encoding.PEM)

    root = etree.fromstring(xml_str.encode("utf-8"))
    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm=algorithms.SignatureMethod.RSA_SHA256,
        digest_algorithm=algorithms.DigestAlgorithm.SHA256,
        c14n_algorithm=algorithms.CanonicalizationMethod.CANONICAL_XML_1_0,
    )
    signer.namespaces = {None: XMLDSIG}
    signed = signer.sign(root, key=key_pem, cert=cert_pem, reference_uri=ref_id)
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8")

# ─── Emissão via API ─────────────────────────────────────────────────────────

def emitir_via_api(session, config, xml_assinado):
    """POST DPS XML (gzip+base64) ao Emissor Público Nacional. Retorna NFS-e XML bytes.

    Endpoint: POST https://sefin.nfse.gov.br/SefinNacional/nfse
    Body: {"DpsXmlGZipB64": "<base64(gzip(xml_assinado))>"}
    Resposta 200: JSON com o XML da NFS-e gerada; 400: JSON com erros[]."""
    url = config["api_base_url"].rstrip("/") + "/nfse"
    gz = gzip.compress(xml_assinado)
    payload = {"DpsXmlGZipB64": base64.b64encode(gz).decode()}
    r = session.post(url, json=payload,
                     headers={"Accept": "application/json"},
                     timeout=60)
    try:
        data = r.json()
    except ValueError:
        data = None
    if not r.ok or (data and data.get("erros")):
        raise RuntimeError(f"API {r.status_code} [{url}]:\n{r.text[:2000]}")
    # Sucesso: resposta contém NfseXmlGZipB64 (ou similar) — detectar.
    for key in ("NfseXmlGZipB64", "nfseXmlGZipB64", "NFSeXmlGZipB64"):
        if data and key in data and data[key]:
            return gzip.decompress(base64.b64decode(data[key]))
    # Fallback: alguns retornos trazem XML direto em campo string
    for key in ("NfseXml", "xmlNFSe", "XmlNFSe"):
        if data and key in data and data[key]:
            return data[key].encode() if isinstance(data[key], str) else data[key]
    # Último recurso: se conteúdo é XML bruto
    if r.headers.get("content-type","").startswith("application/xml"):
        return r.content
    raise RuntimeError(f"Resposta sem XML da NFS-e reconhecível:\n{r.text[:2000]}")

def extrair_chave_acesso(nfse_xml):
    """Chave de acesso da NFS-e: 50 dígitos do atributo Id do <infNFSe> (formato 'NFS' + 50 dígitos)."""
    root = etree.fromstring(nfse_xml)
    inf = root.find(f".//{{{NS_NFSE}}}infNFSe")
    if inf is not None:
        idv = inf.get("Id","")
        if idv.startswith("NFS"):
            return idv[3:]
        return idv or None
    return None

def baixar_pdf(session, config, chave):
    """Baixa o DANFSE em PDF. Endpoint oficial: https://adn.nfse.gov.br/danfse/{chave}

    O serviço /danfse retorna 502 intermitente (upstream gateway). Faz retries curtos.
    """
    adn_base = config.get("adn_base_url","https://adn.nfse.gov.br").split("/contribuintes")[0]
    url = f"{adn_base}/danfse/{chave}"
    for _ in range(6):
        try:
            r = session.get(url, headers={"Accept":"application/pdf"}, timeout=30)
            if r.ok and r.headers.get("content-type","").startswith("application/pdf"):
                return r.content
            if r.status_code == 502:
                time.sleep(2)
                continue
            return None
        except requests.RequestException:
            time.sleep(2)
            continue
    return None

# ─── E-mail para contabilidade ───────────────────────────────────────────────

def enviar_notas_contabilidade(config, secrets, notas):
    """Envia uma ou mais NFS-e em um único e-mail para a contabilidade via Mailgun.

    `notas` é uma lista de dicts com chaves: `dados`, `cliente`, `xml_bytes`, `pdf_bytes`.
    Quando há mais de uma nota, todas são anexadas na mesma mensagem.
    Sempre adiciona CC para `email_remetente_cc` (o próprio remetente).
    """
    if not notas:
        return
    # Usa a competência da primeira nota (assume-se que é um lote do mesmo período).
    mes_fmt = notas[0]["dados"]["dCompet"][:7].replace("-", "/")
    nomes   = [n["cliente"]["xNome"] for n in notas]

    razao_social = config.get("razao_social", "")
    assinatura   = config.get("assinatura_email", "")
    marca = f" — {razao_social}" if razao_social else ""
    assinatura_bloco = f"\nAtenciosamente,\n{assinatura}\n\n" if assinatura else "\n"
    rodape = (
        f"---\nE-mail gerado automaticamente pelo emissor de NFS-e Nacional"
        + (f" — {razao_social}." if razao_social else ".")
    )

    if len(notas) == 1:
        assunto = f"NFS-e {mes_fmt} — {nomes[0]}{marca}"
        d = notas[0]["dados"]
        corpo = (
            f"Olá,\n\nSegue a NFS-e referente ao pagamento de {nomes[0]},\n"
            f"competência {mes_fmt}, no valor de R$ {d['vBRL']:,.2f} (US$ {d['vUSD']:,.2f}).\n"
            + assinatura_bloco + rodape
        )
    else:
        nomes_unicos = sorted(set(nomes))
        lista_nomes  = ", ".join(nomes_unicos)
        assunto = f"NFS-e {mes_fmt} — {len(notas)} notas ({lista_nomes}){marca}"
        linhas  = []
        tot_brl = 0.0
        tot_usd = 0.0
        for n in notas:
            d = n["dados"]
            tot_brl += d["vBRL"]
            tot_usd += d["vUSD"]
            linhas.append(
                f"  • {n['cliente']['xNome']} — {d['dCompet']} — "
                f"R$ {d['vBRL']:,.2f} (US$ {d['vUSD']:,.2f})"
            )
        corpo = (
            f"Olá,\n\nSeguem {len(notas)} NFS-e referentes aos pagamentos "
            f"recebidos na competência {mes_fmt}:\n\n"
            + "\n".join(linhas)
            + f"\n\nTotal: R$ {tot_brl:,.2f} (US$ {tot_usd:,.2f}).\n"
            + assinatura_bloco + rodape
        )

    # Monta os anexos com nomes únicos por (cliente, ordem).
    tag      = mes_fmt.replace("/", "_")
    contagem = {}
    ocorr    = {}
    for n in notas:
        k = n["cliente"]["xNome"].lower().replace(" ", "_")
        ocorr[k] = ocorr.get(k, 0) + 1

    anexos = []  # lista de tuplas ("attachment", (filename, bytes, mime))
    for n in notas:
        nome_cli = n["cliente"]["xNome"].lower().replace(" ", "_")
        contagem[nome_cli] = contagem.get(nome_cli, 0) + 1
        suf  = f"_{contagem[nome_cli]}" if ocorr[nome_cli] > 1 else ""
        base = f"nfse_{tag}_{nome_cli}{suf}"
        anexos.append(("attachment", (f"{base}.xml", n["xml_bytes"], "application/xml")))
        if n.get("pdf_bytes"):
            anexos.append(("attachment", (f"{base}.pdf", n["pdf_bytes"], "application/pdf")))

    # ── Envio via Mailgun API ──────────────────────────────────────────────
    api_key = secrets.get("mailgun_api_key") or os.environ.get("MAILGUN_API_KEY")
    if not api_key:
        raise RuntimeError("mailgun_api_key ausente em secrets.json")
    domain = config.get("mailgun_domain")
    remet  = config.get("mailgun_from")
    if not domain or not remet:
        raise RuntimeError("mailgun_domain e mailgun_from devem estar configurados em config.json")
    cc_self = config.get("email_remetente_cc")
    destino = config["email_contabilidade"]

    url  = f"https://api.mailgun.net/v3/{domain}/messages"
    data = {
        "from":    remet,
        "to":      destino,
        "subject": assunto,
        "text":    corpo,
    }
    if cc_self:
        data["cc"] = cc_self

    r = requests.post(url, auth=("api", api_key), data=data, files=anexos, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Mailgun {r.status_code}: {r.text[:500]}")


def enviar_nfse_contabilidade(config, secrets, dados, cliente, xml_bytes, pdf_bytes=None):
    """Compat: envia UMA única NFS-e. Delegates para enviar_notas_contabilidade."""
    enviar_notas_contabilidade(config, secrets, [{
        "dados": dados, "cliente": cliente,
        "xml_bytes": xml_bytes, "pdf_bytes": pdf_bytes,
    }])

# ─── Fluxo de emissão de uma nota ────────────────────────────────────────────

def emitir_uma_nota(config, secrets, clientes, session, dados, cliente_chave,
                    ndps, dry_run=False):
    """Emite uma NFS-e. Retorna dict com resultado incluindo xml e pdf bytes."""
    _, cliente = resolver_cliente(cliente_chave, clientes)

    print(f"\n  📋 {cliente['xNome']} | {dados['dCompet']} | USD {dados['vUSD']:,.2f} | BRL {dados['vBRL']:,.2f}")
    print(f"  📄 Construindo DPS (nDPS={ndps})...")

    xml_str, dps_id = build_dps_xml(config, dados, ndps, cliente)

    print(f"  ✍️  Assinando XML...")
    xml_assinado = assinar_dps(xml_str, config["cert_path"],
                               (secrets.get("cert_password") or
                                os.environ.get("NFSE_CERT_PASSWORD", "")).encode(),
                               dps_id)

    if dry_run:
        out = SKILL_DIR / f"dryrun_{dps_id[:40]}.xml"
        out.write_bytes(xml_assinado)
        print(f"  ⚠️  Dry-run: XML salvo em {out.name}")
        return {"dps_id": dps_id, "dry_run": True, "xml_bytes": xml_assinado,
                "pdf_bytes": None, "cliente": cliente, "dados": dados}

    print(f"  📤 Emitindo via API...")
    nfse_xml = emitir_via_api(session, config, xml_assinado)
    chave    = extrair_chave_acesso(nfse_xml)
    print(f"  ✅ Emitida! Chave: {chave}")

    # Atualiza cache local do último nNFSe — útil caso a API /DFe/0 falhe depois.
    n_emitido = _extrair_nnfse_da_chave(chave) if chave else None
    if n_emitido:
        salvar_cache_ndps(n_emitido, chave)

    pdf_bytes = baixar_pdf(session, config, chave) if chave else None

    # Salva os arquivos localmente
    output_dir = Path(config.get("output_dir", str(SKILL_DIR / "notas")))
    output_dir.mkdir(parents=True, exist_ok=True)
    tag  = dados["dCompet"][:7].replace("-", "_")
    nome = cliente["xNome"].lower().replace(" ", "_")
    (output_dir / f"nfse_{tag}_{nome}.xml").write_bytes(nfse_xml)
    if pdf_bytes:
        (output_dir / f"nfse_{tag}_{nome}.pdf").write_bytes(pdf_bytes)
        print(f"  💾 Arquivos salvos em {output_dir}/nfse_{tag}_{nome}.[xml|pdf]")
    else:
        print(f"  💾 XML salvo em {output_dir}/nfse_{tag}_{nome}.xml")

    return {
        "dps_id":    dps_id,
        "chave":     chave,
        "dry_run":   False,
        "xml_bytes": nfse_xml,
        "pdf_bytes": pdf_bytes,
        "cliente":   cliente,
        "dados":     dados,
    }

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mes",         help="Emite todas as notas do mês AAAA-MM (lê Gmail)")
    p.add_argument("--cliente",     help="Nome/alias do cliente (google, venatus, ...)")
    p.add_argument("--competencia", help="Data de competência AAAA-MM-DD")
    p.add_argument("--usd",   type=float)
    p.add_argument("--brl",   type=float)
    p.add_argument("--ndps",  type=int,  help="Força nDPS (sem consultar API)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verificar", action="store_true",
                   help="Verifica e-mails da contabilidade (sem emitir notas)")
    p.add_argument("--dias", type=int, default=60,
                   help="Janela de busca de e-mails da contabilidade em dias (padrão: 60)")
    p.add_argument("--encaminhar", action="store_true",
                   help="Encaminha automaticamente para contabilidade sem perguntar")
    args = p.parse_args()

    config   = load_config()
    secrets  = load_secrets()

    # ── Modo verificar: não precisa de certificado ───────────────────────────
    if args.verificar:
        print(f"🔍 Buscando e-mails da contabilidade (últimos {args.dias} dias)...")
        emails = verificar_emails_contabilidade(config, secrets, dias=args.dias)
        imprimir_resumo_contabilidade(emails)
        return

    # ── Para emissão, carrega clientes e certificado ─────────────────────────
    clientes = load_clientes()
    senha_str = (os.environ.get("NFSE_CERT_PASSWORD")
                 or secrets.get("cert_password")
                 or input("🔑 Senha do certificado A-1: "))
    secrets["cert_password"] = senha_str

    print("🔌 Sessão mTLS...")
    session = session_mtls(config["cert_path"], senha_str.encode())

    def perguntar_encaminhar_lote(resultados):
        """Pergunta se deve encaminhar lote de notas (1+) em um único e-mail."""
        if args.dry_run or not resultados:
            return
        emitidas = [r for r in resultados if not r.get("dry_run")]
        if not emitidas:
            return
        n = len(emitidas)
        label = "a NFS-e" if n == 1 else f"as {n} NFS-e em um único e-mail"
        if args.encaminhar:
            confirmar = "s"
        else:
            confirmar = input(
                f"\n  📨 Encaminhar {label} para {config['email_contabilidade']}? (s/n): "
            ).strip().lower()
        if confirmar == "s":
            enviar_notas_contabilidade(config, secrets, [{
                "dados":     r["dados"],
                "cliente":   r["cliente"],
                "xml_bytes": r["xml_bytes"],
                "pdf_bytes": r.get("pdf_bytes"),
            } for r in emitidas])
            print(f"  ✅ {n} nota(s) enviada(s) para {config['email_contabilidade']}")
        else:
            print("  ↩️  Envio cancelado.")

    try:
        # ── Modo batch: --mes AAAA-MM ────────────────────────────────────────
        if args.mes:
            print(f"📧 Buscando pagamentos de {args.mes} no Gmail...")
            transferencias = buscar_pagamentos_mes(config, secrets, args.mes)

            if not transferencias:
                print("Nenhuma transferência encontrada para este mês.")
                return

            print(f"\n📋 Encontradas {len(transferencias)} transferência(s):\n")
            for i, t in enumerate(transferencias, 1):
                print(f"  {i}. {t['cliente_nome']} | {t['dCompet']} | USD {t['vUSD']:,.2f} | BRL {t['vBRL']:,.2f}")

            if not args.dry_run:
                resp = input("\nEmitir todas? (s/n): ").strip().lower()
                if resp != "s":
                    print("Cancelado.")
                    return

            print("\n🔢 Consultando próximo nDPS...")
            ndps_atual = args.ndps or descobrir_proximo_ndps(session, config)

            resultados = []
            for i, t in enumerate(transferencias):
                ndps = ndps_atual + i
                resultado = emitir_uma_nota(
                    config, secrets, clientes, session,
                    t, t["cliente_nome"], ndps, args.dry_run
                )
                resultados.append(resultado)

            perguntar_encaminhar_lote(resultados)
            print(f"\n✅ {len(transferencias)} nota(s) emitida(s).")

        # ── Modo avulso ──────────────────────────────────────────────────────
        else:
            if args.cliente and args.usd and args.brl and args.competencia:
                dados = {"vUSD": args.usd, "vBRL": args.brl, "dCompet": args.competencia,
                         "cliente_nome": args.cliente}
            else:
                print("📧 Buscando último pagamento no Gmail...")
                dados = buscar_ultimo_pagamento(config, secrets)
                print(f"   ✓ {dados['cliente_nome']} | {dados['dCompet']} | USD {dados['vUSD']:,.2f}")

            ndps = args.ndps or descobrir_proximo_ndps(session, config)
            resultado = emitir_uma_nota(
                config, secrets, clientes, session,
                dados, dados.get("cliente_nome", args.cliente or "google"),
                ndps, args.dry_run
            )
            perguntar_encaminhar_lote([resultado])

            print("\n✅ Concluído.")

    finally:
        cleanup_session(session)


if __name__ == "__main__":
    main()
