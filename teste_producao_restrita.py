#!/usr/bin/env python3
"""
Envia uma DPS de teste à PRODUÇÃO RESTRITA (homologação) do Sistema Nacional
NFS-e, montada com a configuração atual (incluindo o grupo IBS/CBS), e mostra
se foi autorizada ou rejeitada.

Nunca fala com a produção: o endereço é fixo e a DPS sai com tpAmb=2. Não
grava cache de nDPS nem arquivos na pasta de notas — a numeração de teste é
própria (derivada do horário) e não interfere na sequência real.

Uso (com NFSE_DATA_DIR apontando para a pasta com config/secrets/clientes):
  python3 teste_producao_restrita.py --cliente google --usd 7061.65 --brl 35012.76
  python3 teste_producao_restrita.py --cliente venatus --usd 100 --brl 500 --competencia 2026-10-15

Saída: uma linha começando com AUTORIZADA, REJEITADA <códigos> ou ERRO.
"""

import argparse
import json
import sys
import time
from datetime import date

import emitir_nfse as nfse

RESTRITA = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cliente", required=True)
    ap.add_argument("--usd", type=float, required=True)
    ap.add_argument("--brl", type=float, required=True)
    ap.add_argument("--competencia", default=date.today().isoformat(),
                    help="AAAA-MM-DD; não pode ser futura (padrão: hoje)")
    args = ap.parse_args()

    config, secrets, clientes = nfse.load_config(), nfse.load_secrets(), nfse.load_clientes()
    config["cert_path"] = str(nfse.DATA_DIR / config["cert_path"])
    _, cliente = nfse.resolver_cliente(args.cliente, clientes)

    ndps = 900_000_000 + int(time.time()) % 100_000_000   # numeração de teste
    dados = {"vUSD": args.usd, "vBRL": args.brl, "dCompet": args.competencia}
    xml, dps_id = nfse.build_dps_xml(config, dados, ndps, cliente)
    xml = xml.replace("<tpAmb>1</tpAmb>", "<tpAmb>2</tpAmb>")
    if "<tpAmb>2</tpAmb>" not in xml:
        print("ERRO: não foi possível marcar a DPS como homologação (tpAmb=2)")
        return 2
    senha = (secrets.get("cert_password") or "").encode()
    assinado = nfse.assinar_dps(xml, config["cert_path"], senha, dps_id)

    com_ibscbs = b"<IBSCBS>" in assinado
    session = nfse.session_mtls(config["cert_path"], senha)
    try:
        nfse_xml = nfse.emitir_via_api(session, {**config, "api_base_url": RESTRITA}, assinado)
    except RuntimeError as e:
        texto = str(e)
        try:
            erros = json.loads(texto[texto.index("{"):]).get("erros", [])
            codigos = " ".join(f"{x.get('Codigo')}: {x.get('Descricao')}" for x in erros)
        except (ValueError, AttributeError):
            codigos = texto[:500]
        print(f"REJEITADA (IBS/CBS {'presente' if com_ibscbs else 'ausente'}) {codigos}")
        return 1
    except Exception as e:
        print(f"ERRO {type(e).__name__}: {e}")
        return 2
    finally:
        nfse.cleanup_session(session)

    print(f"AUTORIZADA (IBS/CBS {'presente' if com_ibscbs else 'ausente'}) "
          f"chave {nfse.extrair_chave_acesso(nfse_xml)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
