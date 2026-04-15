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
- Leitura de e-mails da contabilidade via **Gmail IMAP + XOAUTH2** com categorização (fiscal, contábil, pessoal, financeiro)
- **Servidor MCP** expondo 4 tools ao Claude Desktop:
  - `emitir_nota` — emite uma nota individual
  - `emitir_notas_mes` — emite um lote e opcionalmente envia tudo em um único e-mail
  - `verificar_emails_contabilidade` — lê caixa de entrada e categoriza pendências
  - `encaminhar_nota_contabilidade` — encaminha XML+PDF já salvos em disco

---

## 📁 Estrutura do projeto

```
nfse-nacional-mcp/
├── emitir_nfse.py           # script principal (CLI + lib)
├── nfse_mcp_server.py       # servidor MCP para Claude Desktop
├── setup_gmail_oauth.py     # gera refresh_token OAuth2 do Gmail
├── config.example.json      # → copie para config.json
├── secrets.example.json     # → copie para secrets.json
├── clientes.example.json    # → copie para clientes.json
├── requirements.txt
├── .gitignore
├── README.md
└── certs/                   # coloque seu .pfx aqui (não versionado)
```

---

## 🚀 Setup

### 1. Clone e instale dependências

```bash
git clone git@github.com:SEU_USUARIO/nfse-nacional-mcp.git
cd nfse-nacional-mcp

# Opção A — uv (recomendado, isola dependências)
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Opção B — pip tradicional
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure sua empresa

```bash
cp config.example.json   config.json
cp secrets.example.json  secrets.json
cp clientes.example.json clientes.json
```

Edite cada um dos arquivos:

- **`config.json`** — CNPJ, razão social, cLocEmi, série, regime tributário, domínio Mailgun, e-mail da contabilidade, `output_dir`, etc.
- **`secrets.json`** — senha do certificado A1, API key do Mailgun, credenciais OAuth2 do Gmail (opcional, só se quiser usar leitura de e-mails)
- **`clientes.json`** — dados dos tomadores (remova os exemplos e adicione os seus)

### 3. Coloque o certificado digital

```bash
mkdir -p certs
cp /caminho/do/seu_certificado.pfx certs/
# Atualize config.json → "cert_path": "certs/seu_certificado.pfx"
```

### 4. (Opcional) Configure OAuth2 do Gmail para leitura

Só necessário se quiser usar `verificar_emails_contabilidade`.

```bash
# 1. Crie credenciais OAuth "Desktop app" em https://console.cloud.google.com/
# 2. Habilite Gmail API no projeto
# 3. Coloque client_id / client_secret em secrets.json
# 4. Rode o setup e siga o fluxo no navegador
python3 setup_gmail_oauth.py
```

O refresh_token é salvo automaticamente em `secrets.json`.

### 5. (Opcional) Mailgun

Só necessário se quiser envio automático para a contabilidade.

1. Crie uma conta em https://mailgun.com e registre/verifique seu domínio.
2. Copie a **Private API key** em Account → API Keys.
3. Coloque em `secrets.json → mailgun_api_key` e configure `mailgun_domain` / `mailgun_from` em `config.json`.

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

### 1. Configure o `claude_desktop_config.json`

No macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`

No Windows:
`%APPDATA%\Claude\claude_desktop_config.json`

Adicione:

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
        "--with", "google-auth-oauthlib",
        "python3",
        "/CAMINHO/ABSOLUTO/nfse-nacional-mcp/nfse_mcp_server.py"
      ]
    }
  }
}
```

Substitua `/CAMINHO/ABSOLUTO/nfse-nacional-mcp` pelo caminho real onde você clonou o repositório.

### 2. Reinicie o Claude Desktop

### 3. Peça ao Claude

```
Emita uma NFS-e para o cliente X no valor de USD 1000 / BRL 5500,
competência 15/03/2026, e encaminhe para a contabilidade.
```

Claude vai:
1. Exibir um resumo da nota.
2. Pedir confirmação.
3. Chamar `emitir_nota` (ou `emitir_notas_mes` se for um lote).
4. Opcionalmente enviar XML+PDF para a contabilidade via Mailgun.

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
