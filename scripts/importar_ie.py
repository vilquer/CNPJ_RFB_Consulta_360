"""
Importa a base de Inscrições Estaduais ativas da SEFAZ-RS (o mesmo CSV que o
projeto da pasta IE/ carrega no navegador) para a tabela `ie_rs` dentro do
rfb.duckdb — primeira ponte entre os dois projetos.

Uso:
    python scripts/importar_ie.py caminho/para/DVL_..._Publico.csv
    (aceita também o .zip baixado da SEFAZ, sem extrair)

Layout esperado (com ou sem header):
    Inscrição;Data Abertura;Categoria;CNAE_1;CNAE_2;CNAE_3;Tipo;CPF/CNPJ

Depois de importar, a página Consulta CNPJ do app mostra as IEs do CNPJ
consultado automaticamente (a seção só aparece se a tabela existir).
"""

import argparse
import sys
import zipfile
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "rfb.duckdb"
COLUNAS = "{'inscricao': 'VARCHAR', 'data_abertura': 'VARCHAR', 'categoria': 'VARCHAR', " \
          "'cnae_1': 'VARCHAR', 'cnae_2': 'VARCHAR', 'cnae_3': 'VARCHAR', " \
          "'tipo': 'VARCHAR', 'cpf_cnpj': 'VARCHAR'}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="CSV (ou .zip) da SEFAZ-RS")
    args = parser.parse_args()

    origem = Path(args.csv)
    if not origem.exists():
        print(f"ERRO: {origem} não existe.")
        sys.exit(1)

    if origem.suffix.lower() == ".zip":
        destino = origem.with_suffix("")
        with zipfile.ZipFile(origem) as zf:
            membro = zf.namelist()[0]
            zf.extract(membro, origem.parent)
            origem = origem.parent / membro
        print(f"Extraído: {origem}")

    con = duckdb.connect(str(DB_PATH))
    # Formato real do PPR_ATIVO: cpf_cnpj SEMPRE com 14 dígitos zero-padded;
    # a coluna `tipo` distingue: F = produtor rural PF (CPF aberto),
    # J = pessoa jurídica (CNPJ). Só J vira cnpj14 pra join com a RFB.
    con.execute(f"""
        CREATE OR REPLACE TABLE ie_rs AS
        SELECT inscricao,
               data_abertura,
               categoria,
               cnae_1, cnae_2, cnae_3,
               tipo,
               regexp_replace(cpf_cnpj, '^0+', '') AS cpf_cnpj,
               CASE WHEN tipo = 'J'
                    THEN regexp_replace(cpf_cnpj, '[^0-9]', '', 'g')[-14:]
               END AS cnpj14
        FROM read_csv('{origem.as_posix()}', delim=';', header=true,
                      all_varchar=true, normalize_names=true,
                      names=['inscricao', 'data_abertura', 'categoria',
                             'cnae_1', 'cnae_2', 'cnae_3', 'tipo', 'cpf_cnpj'])
    """)
    n, n_cnpj = con.execute(
        "SELECT count(*), count(cnpj14) FROM ie_rs").fetchone()
    con.close()
    print(f"Tabela ie_rs criada no rfb.duckdb: {n:,} inscrições "
          f"({n_cnpj:,} vinculadas a CNPJ). A seção de IEs aparece na "
          "página Consulta CNPJ do app.")


if __name__ == "__main__":
    main()
