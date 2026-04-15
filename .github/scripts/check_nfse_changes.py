#!/usr/bin/env python3
"""
Verifica se URLs oficiais do NFS-e Nacional mudaram desde o último snapshot.

Usa apenas stdlib para poder rodar no ubuntu-latest sem dependências extras.
Grava o baseline em `.github/nfse-snapshot/baseline/` (commitado pelo workflow)
e, quando detecta mudança, exporta `has_changes=true` e um `report` multilinha
via `GITHUB_OUTPUT` para os passos seguintes abrirem um issue.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

# ──────────────────────────────────────────────────────────────────────────────
# URLs monitoradas. Adicione/remova à vontade — chave é só um apelido interno.
# ──────────────────────────────────────────────────────────────────────────────
URLS: dict[str, str] = {
    "swagger-contribuinte-issqn": "https://www.nfse.gov.br/swagger/contribuintesissqn/swagger.json",
    "portal-nfse-gov":            "https://www.gov.br/nfse/pt-br",
    "emissor-nacional":           "https://www.nfse.gov.br/EmissorNacional/",
    "adn-portal":                 "https://adn.nfse.gov.br/",
}

ROOT     = Path(".github/nfse-snapshot")
BASELINE = ROOT / "baseline"


def fetch(url: str) -> bytes | None:
    req = Request(url, headers={
        "User-Agent": "nfse-nacional-mcp-changelog-watcher/1.0 (+github actions)",
        "Accept":     "*/*",
    })
    try:
        with urlopen(req, timeout=30) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  falha: {e}", file=sys.stderr)
        return None


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    BASELINE.mkdir(parents=True, exist_ok=True)

    changed: list[tuple[str, str, str]] = []   # (key, old_hash, new_hash)
    failed:  list[str] = []
    first:   list[str] = []

    for key, url in URLS.items():
        print(f"🌐 {key} ← {url}")
        content = fetch(url)
        if content is None:
            failed.append(key)
            continue

        new_hash = sha(content)
        hash_file = BASELINE / f"{key}.sha256"
        raw_file  = BASELINE / f"{key}.raw"

        old_hash = hash_file.read_text().strip() if hash_file.exists() else None

        if old_hash is None:
            print(f"   → primeira captura ({new_hash[:16]}…)")
            first.append(key)
        elif old_hash != new_hash:
            print(f"   → MUDOU: {old_hash[:16]}… → {new_hash[:16]}…")
            changed.append((key, old_hash, new_hash))
        else:
            print(f"   → sem mudança ({new_hash[:16]}…)")

        # Atualiza baseline sempre que conseguimos fetch — nunca sobrescreve
        # com erro, porque failed já caiu no `continue` acima.
        raw_file.write_bytes(content)
        hash_file.write_text(new_hash)

    # ── Monta relatório ──────────────────────────────────────────────────────
    report: list[str] = []
    if changed:
        report.append(f"### URLs com conteúdo alterado ({len(changed)})")
        report.append("")
        for key, old, new in changed:
            report.append(f"- **`{key}`** — {URLS[key]}")
            report.append(f"  - hash anterior: `{old[:16]}…`")
            report.append(f"  - hash atual:    `{new[:16]}…`")
        report.append("")

    if failed:
        report.append(f"### URLs indisponíveis neste run ({len(failed)})")
        report.append("")
        for k in failed:
            report.append(f"- `{k}` — {URLS[k]}")
        report.append("")

    if first:
        print(f"ℹ️  {len(first)} URL(s) capturada(s) pela primeira vez (baseline inicial)")

    if not changed:
        print("✅ Nenhuma mudança material detectada")
    else:
        print(f"⚠️  {len(changed)} mudança(s) detectada(s)")

    # ── Exporta para GITHUB_OUTPUT ───────────────────────────────────────────
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"has_changes={'true' if changed else 'false'}\n")
            f.write("report<<__NFSE_REPORT_EOF__\n")
            f.write("\n".join(report) if report else "(sem mudanças)")
            f.write("\n__NFSE_REPORT_EOF__\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
