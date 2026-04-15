# NFS-e Nacional — Emissor + MCP Server

Emissão automatizada de **NFS-e pelo Sistema Nacional** (leiaute DPS v1.01) via API
REST, com certificado digital A1 (mTLS). Inclui um **servidor MCP** pronto para usar
dentro do Claude Desktop — basta pedir ao Claude para emitir a nota que ele chama as
tools deste repositório.

> Regime fiscal alvo: **Lucro Presumido**, exportação de serviços
> (mas tudo é parametrizável em `config.json` para outros cenários).

---

## ✨ Features

- Geração do XML DPS v1.01 com assinatura XMLDSig enveloped (RSA-SHA256, C14N)
- Conexão mTLS com ICP-Brasil A1 (PFX)
- Emissão via `POST https://sefin.nfse.gov.br/SefinNacional/nfse`
- Download da DANFSE (PDF) via `GET https://adn.nfse.gov.br/danfse/{chave}`
- Descoberta automática do próximo `nDPS` (combinação API `/DFe/0` + scan local + cache)
- Envio de XML+PDF para a contabilidade via **Mailgun** (um e-mail por nota **ou** um único e-mail com todas as notas do lote)
- Leitura de e-mails via **Gmail IMAP + XOAUTH2**:
  - Parsers **configuráveis** via `config.json → email_parsers`. Built-ins: `remessa_online` (default — combinação mais comum com AdSense) e `rendimento` (Banco Rendimento). Adicionar uma nova corretora = escrever uma função e registrá-la em `BUILTIN_PARSERS` — ver `emitir_nfse.py`.
  - Categorização de e-mails da contabilidade (fiscal / contábil / pessoal / financeiro)
- **Watcher automatizado** via GitHub Actions: verifica semanalmente se a API oficial mudou e abre PR via Claude Code (ver `.github/workflows/check-nfse-changes.yml`)
- **Servidor MCP** expondo 11 tools ao Claude Desktop:
  - **Setup conversacional** — deixe o Claude te configurar passo a passo:
    - `status_setup` — diagnóstico do que falta em config/secrets/clientes
    - `inferir_de_xml` — lê uma NFS-e antiga e pré-popula CNPJ/município/série/cliente (atalho ⭐)
    - `escrever_config` — merge de campos em config.json
    - `escrever_secrets` — merge de campos em secrets.json (permissão 0600)
    - `adicionar_cliente` — cadastra tomador de serviço em clientes.json
    - `testar_certificado` — valida .pfx + senha e retorna titular/validade
  - **Produção**:
    - `listar_pagamentos` — lê Gmail e lista transferências de um mês
    - `emitir_nota` — emite uma nota individual
    - `emitir_notas_mes` — emite um lote e opcionalmente envia tudo em um único e-mail
    - `verificar_emails_contabilidade` — lê caixa de entrada e categoriza pendências
    - `encaminhar_nota_contabilidade` — encaminha XML+PDF já salvos em disco

---

## 📁 Estrutura do projeto

```
nfse-nacional-mcp/
├── emitir_nfse.py            # script principal (CLI + lib)
├── nfse_mcp_server.py        # servidor MCP para Claude Desktop
├── setup_claude_desktop.py   # bootstrap: registra o MCP no Claude Desktop
├── setup_gmail_oauth.py      # gera refresh_token OAuth2 do Gmail
├── config.example.json       # → copie para config.json
├── secrets.example.json      # → copie para secrets.json
├── clientes.example.json     # → copie para clientes.json
├── requirements.txt
├── .gitignore
├── README.md
├── certs/                    # coloque seu .pfx aqui (não versionado)
└── .github/
    ├── workflows/
    │   └── check-nfse-changes.yml   # watcher semanal da API oficial
    └── scripts/
        └── check_nfse_changes.py    # snapshot + diff das URLs oficiais
```

---

## 🚀 Setup

Existem **dois caminhos**. O conversacional é o recomendado — você não precisa editar JSON à mão.

### Caminho A — Setup conversacional (recomendado) ⭐

#### A1. Clone e registre o MCP no Claude Desktop

```bash
git clone git@github.com:SEU_USUARIO/nfse-nacional-mcp.git
cd nfse-nacional-mcp
python3 setup_claude_desktop.py
```

O script detecta seu SO e escreve a entrada `nfse-nacional` no
`claude_desktop_config.json`. Depois reinicie o Claude Desktop (⌘Q no macOS).

> Pré-requisito: ter [`uv`](https://docs.astral.sh/uv/) instalado
> (`curl -LsSf https://astral.sh/uv/install.sh | sh`). O `uv` baixa as
> dependências Python (mcp, cryptography, signxml…) automaticamente na
> primeira chamada — você não precisa rodar `pip install`.

#### A2. Peça ao Claude para configurar

Em uma conversa nova no Claude Desktop:

> **Você:** configure o NFS-e Nacional pra mim

O Claude vai chamar `status_setup`, ver que falta tudo, e começar a te
perguntar campo a campo. Você pode **acelerar muito** o processo se já tem
uma nota emitida (mesmo que pela interface web do EmissorNacional):

> **Você:** já emiti uma nota antes, o XML está em `/Users/me/notas/nota5.xml`

> **Claude:** \[chama `inferir_de_xml`\] Extraí:
>  - CNPJ: **22.705.078/0001-44**
>  - Município: **4205407** (Florianópolis)
>  - Série: **900**
>  - pTotTribFed: **7.68%**
>  - Cliente sugerido: **Venatus Media Ltd** (GB)
>
>  Posso gravar isso em config.json e cadastrar Venatus como cliente?

Você confirma, o Claude chama `escrever_config` + `adicionar_cliente`,
e pergunta os campos restantes (certificado, Mailgun, Gmail…) um de cada vez.

#### A3. Coloque o certificado .pfx no disco

Essa parte o Claude não faz — você precisa copiar o arquivo manualmente:

```bash
mkdir -p certs
cp /caminho/do/seu_certificado.pfx certs/
```

Aí diga ao Claude: *"certificado é `certs/meu_cert.pfx`, senha é `XXX`"*. Ele
chama `escrever_config` + `escrever_secrets` (senha nunca volta no chat) e
depois `testar_certificado` para validar — retorna titular, emissor e validade.

#### A4. (Opcional) Gmail e Mailgun

Se quiser `listar_pagamentos` e envio automático para a contabilidade:

```bash
# Gmail OAuth2 — one-time
python3 setup_gmail_oauth.py       # abre browser, grava refresh_token em secrets.json
```

Depois peça ao Claude para gravar `gmail_user`, `mailgun_domain`,
`mailgun_from` e `email_contabilidade` — ele usa `escrever_config` /
`escrever_secrets`.

---

### Caminho B — Setup manual

Se preferir editar JSON à mão:

```bash
git clone git@github.com:SEU_USUARIO/nfse-nacional-mcp.git
cd nfse-nacional-mcp

# Dependências (uv OU pip)
uv venv && source .venv/bin/activate && uv pip install -r requirements.txt
# ─ ou ─
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Arquivos de config
cp config.example.json   config.json
cp secrets.example.json  secrets.json
cp clientes.example.json clientes.json
```

Edite cada um:

- **`config.json`** — CNPJ, razão social, cLocEmi, série, regime tributário, domínio Mailgun, e-mail da contabilidade, `output_dir`, etc.
- **`secrets.json`** — senha do certificado A1, API key do Mailgun, credenciais OAuth2 do Gmail (opcional)
- **`clientes.json`** — dados dos tomadores

Coloque o certificado:

```bash
mkdir -p certs
cp /caminho/do/seu_certificado.pfx certs/
# Atualize config.json → "cert_path": "certs/seu_certificado.pfx"
```

Gmail OAuth2 (opcional, só se quiser `listar_pagamentos` ou `verificar_emails_contabilidade`):

```bash
# 1. Crie credenciais OAuth "Desktop app" em https://console.cloud.google.com/
# 2. Habilite Gmail API no projeto
# 3. Coloque client_id / client_secret em secrets.json
python3 setup_gmail_oauth.py     # grava refresh_token em secrets.json
```

Mailgun (opcional, só se quiser envio automático):

1. Crie uma conta em https://mailgun.com e registre/verifique seu domínio.
2. Copie a **Private API key** em Account → API Keys.
3. Coloque em `secrets.json → mailgun_api_key` e configure `mailgun_domain` / `mailgun_from` em `config.json`.

Finalmente, registre o MCP no Claude Desktop com `python3 setup_claude_desktop.py`
(ou edite `claude_desktop_config.json` manualmente — ver seção *Usando via MCP* abaixo).

---

## 🧪 Testando sem emitir (dry-run)

```bash
python3 emitir_nfse.py --dry-run \
  --cliente alias_do_cliente \
  --usd 1000.00 \
  --brl 5500.00 \
  --competencia 2026-03-15
```

Gera um `dryrun_DPS*.xml` no diretório atual, assinado, pronto para inspeção.
Nada é enviado à Receita.

---

## 📝 Emitindo uma nota (CLI)

```bash
python3 emitir_nfse.py \
  --cliente alias_do_cliente \
  --usd 1000.00 \
  --brl 5500.00 \
  --competencia 2026-03-15
```

- O `nDPS` é descoberto automaticamente (API `/DFe/0` + scan local + cache).
- Force manualmente com `--ndps 42` se quiser pular a descoberta.
- Use `--mes` para emitir várias de uma vez (lê um lote interativo).

---

## 🧰 Usando via MCP (Claude Desktop)

### Registro manual (se você não usou `setup_claude_desktop.py`)

No macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
No Windows: `%APPDATA%\Claude\claude_desktop_config.json`

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

Substitua `/CAMINHO/ABSOLUTO/nfse-nacional-mcp` pelo caminho real e reinicie o Claude Desktop.

---

## 💡 Exemplo: fluxo mensal AdSense

Cenário: todo mês você recebe depósitos via **Remessa Online** (a corretora
mais comum em conjunto com AdSense) e precisa emitir uma NFS-e para cada um.

### Primeira vez — fluxo completo

```
Você:    emite as notas da minha conta AdSense do mês de março/2026

Claude:  [chama listar_pagamentos com mes="2026-03"]
         Encontrei 2 pagamentos no Gmail (Remessa Online, março/2026):

           1. 03/03 — Google Inc — USD 1697.29 → BRL  8898.73
           2. 23/03 — Google Inc — USD 3206.19 → BRL 16265.32

         Vou emitir 2 NFS-e a partir de nDPS=6 (sequencial).
         Confirma emissão e envio para fiscal@suacontabilidade.com.br?

Você:    confirmo

Claude:  [chama emitir_notas_mes com encaminhar_contabilidade=true]
         ✅ 2 notas emitidas e enviadas em um único e-mail:
           - nNFSe=6, chave 42054072227...0006
           - nNFSe=7, chave 42054072227...0007
```

### O que o Claude faz por trás

1. `listar_pagamentos` — conecta no Gmail via OAuth2, busca e-mails de
   `nao-responder@remessaonline.com.br` no mês pedido, parseia USD/BRL/data
   de cada um.
2. Você confere a lista e confirma.
3. `emitir_notas_mes`:
   - Descobre o próximo `nDPS` via API `/DFe/0`
   - Para cada pagamento: constrói DPS v1.01 → assina (XMLDSig) → POST mTLS → baixa DANFSE
   - No final, junta todas as notas em UM e-mail para a contabilidade via Mailgun
4. XML + PDF de cada nota salvos em `config.output_dir`.

### Outras corretoras / AdSense via PIX Internacional

Se você recebe por outra corretora (p.ex. Banco Rendimento), adicione em
`config.json`:

```json
"email_parsers": [
  { "sender": "nao-responder@remessaonline.com.br", "parser": "remessa_online" },
  { "sender": "cambioonline@mail-rendimento.com.br", "parser": "rendimento" }
]
```

Parsers built-in disponíveis: **`remessa_online`** e **`rendimento`**. Para
uma corretora nova, escreva uma função em `emitir_nfse.py` (veja
`parse_remessa_online` e `parse_rendimento` como referência — recebem um
`email.message.Message` e devolvem `{vUSD, vBRL, dCompet, cliente_nome}`)
e registre em `BUILTIN_PARSERS`.

### Nota avulsa (sem Gmail)

Se você prefere dar os valores direto:

```
Você:    emita uma NFS-e para google, USD 1000, BRL 5500,
         competência 15/03/2026, encaminha pra contabilidade
```

O Claude mostra o resumo, pede confirmação e chama `emitir_nota`.

---

## ⚙️ Campos tributários (ajuste para o seu caso)

O projeto vem pré-configurado para **Lucro Presumido — exportação de serviços**:

| Campo         | Valor padrão  | Significado                                              |
|---------------|---------------|----------------------------------------------------------|
| `opSimpNac`   | `2`           | 2 = Não-Simples Nacional                                  |
| `tribISSQN`   | `3`           | 3 = Exportação de Serviços (ISS não incide)              |
| PIS/COFINS CST| `08`          | Sem incidência / alíquota zero                           |
| `pTotTribFed` | `7.68`        | IRPJ 4,80% + CSLL 2,88% (PIS/COFINS = 0% p/ exportação)  |
| `pTotTribEst` | `0.00`        |                                                          |
| `pTotTribMun` | `0.00`        |                                                          |

Se a sua empresa opera em outro regime ou presta serviços no mercado interno,
ajuste `emitir_nfse.py → build_dps_xml()` e/ou `config.json → pTotTribFed`.

---

## 🔒 Segurança

- **Nunca commite** `secrets.json`, `config.json`, `clientes.json`, nem arquivos em `certs/` — todos já estão no `.gitignore`.
- Use repositório **privado** no GitHub.
- A senha do certificado A1 fica apenas em `secrets.json` local.
- Considere usar `git-crypt` ou `sops` se precisar versionar segredos.

---

## 🐛 Debug

### 404 ao chamar a API

O WAF do Sefin Nacional pode bloquear temporariamente por rate limit (HEAD loops, retries). Espere alguns minutos e prefira passar `--ndps` explicitamente.

### Testar o endpoint manualmente com curl (mTLS)

```bash
# Extrai cert e key do PFX
openssl pkcs12 -in certs/seu.pfx -clcerts -nokeys -out /tmp/cert.crt -passin pass:SUA_SENHA
openssl pkcs12 -in certs/seu.pfx -nocerts -nodes   -out /tmp/cert.key -passin pass:SUA_SENHA

# POST do DPS
curl -v \
  --cert /tmp/cert.crt \
  --key  /tmp/cert.key \
  -H "Content-Type: application/xml" \
  -H "Accept: application/xml" \
  --data-binary @dryrun_DPS.xml \
  https://sefin.nfse.gov.br/SefinNacional/nfse
```

### Gerar e inspecionar o XML antes de enviar

```bash
python3 emitir_nfse.py --dry-run --cliente alias --usd 100 --brl 550 --competencia 2026-03-01
xmllint --format dryrun_DPS*.xml
```

---

## 📚 Referências

- [Portal NFS-e Nacional](https://www.nfse.gov.br/)
- [Swagger — Contribuinte ISSQN](https://www.nfse.gov.br/swagger/contribuintesissqn/)
- [Leiaute NFS-e — Notas Técnicas](https://www.gov.br/nfse/pt-br)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Mailgun API](https://documentation.mailgun.com/docs/mailgun/api-reference/)

---

## 📄 Licença

MIT — use por sua conta e risco. Não há garantia de que os endpoints, leiautes ou
regras fiscais continuarão válidos; valide sempre contra a documentação oficial antes
de usar em produção.
