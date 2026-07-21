"""
Baixa do IBGE a malha geografica (GeoJSON) dos municipios e dos estados do
Brasil inteiro e grava em apoio/ — o app Streamlit nao acessa internet em
tempo de uso, so le esses arquivos locais (ver app/lib/consultas.py).

Rodar uma vez (e de novo so se quiser atualizar a malha do IBGE):
    python scripts/baixar_malha_ibge.py
"""

import json
from pathlib import Path

import requests

API = "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
BASE_DIR = Path(__file__).resolve().parent.parent
DESTINO_MUNICIPIOS = BASE_DIR / "apoio" / "malha_municipios_br.json"
DESTINO_ESTADOS = BASE_DIR / "apoio" / "malha_estados_br.json"


def baixar(intrarregiao: str, destino: Path) -> None:
    print(f"Baixando malha ({intrarregiao})...")
    r = requests.get(
        API,
        params={"formato": "application/vnd.geo+json",
                "intrarregiao": intrarregiao, "qualidade": "minima"},
        timeout=60,
    )
    r.raise_for_status()
    gj = r.json()
    destino.parent.mkdir(exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(gj, f)
    print(f"  {len(gj.get('features', []))} features -> {destino} "
          f"({destino.stat().st_size / 1_048_576:.1f} MB)")


def main():
    baixar("municipio", DESTINO_MUNICIPIOS)
    baixar("UF", DESTINO_ESTADOS)


if __name__ == "__main__":
    main()
