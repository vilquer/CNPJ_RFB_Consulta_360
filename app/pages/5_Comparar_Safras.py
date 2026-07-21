import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from lib import consultas, db, estilo

st.set_page_config(page_title="Comparar safras", page_icon="🔁", layout="wide")
st.title("Comparação entre safras")
st.caption("Evolução mês a mês: contagens, aberturas e mudanças societárias. "
           "Lê o Parquet de cada safra direto (as views apontam só pra mais recente).")

PARQUET = db.BASE_DIR / "parquet"


@st.cache_data
def safras_disponiveis() -> list[str]:
    base = PARQUET / "tabela=empresas"
    return sorted(p.name.split("=", 1)[1] for p in base.glob("safra=*") if p.is_dir())


def caminho(tabela: str, safra: str) -> str:
    return (PARQUET / f"tabela={tabela}" / f"safra={safra}" / "part-*.parquet").as_posix()


safras = safras_disponiveis()
if len(safras) < 2:
    st.info(
        f"Só a safra **{safras[0] if safras else '—'}** está em disco — a comparação "
        "precisa de pelo menos duas. Depois do próximo ciclo mensal "
        "(`download.py` + `convert.py` da safra nova, **sem apagar a anterior**), "
        "esta página ativa sozinha."
    )
    st.stop()

c_a, c_b = st.columns(2)
safra_a = c_a.selectbox("Safra base (anterior)", safras, index=len(safras) - 2)
safra_b = c_b.selectbox("Safra comparada (nova)", safras, index=len(safras) - 1)
if safra_a == safra_b:
    st.warning("Escolha safras diferentes.")
    st.stop()


# ---------- contagens gerais ----------

@st.cache_data
def contagens(safra: str) -> dict:
    est = db.query_um(f"""
        SELECT count(*),
               count(*) FILTER (situacao_cadastral IN ('02', '2'))
        FROM read_parquet('{caminho("estabelecimentos", safra)}')
    """)
    emp = db.query_um(
        f"SELECT count(*) FROM read_parquet('{caminho('empresas', safra)}')")[0]
    soc = db.query_um(
        f"SELECT count(*) FROM read_parquet('{caminho('socios', safra)}')")[0]
    return {"estabelecimentos": est[0], "ativos": est[1], "empresas": emp, "socios": soc}


st.subheader(f"Números gerais — {safra_a} → {safra_b}")
a, b = contagens(safra_a), contagens(safra_b)
cols = st.columns(4)
for col, chave, rotulo in zip(
        cols,
        ["estabelecimentos", "ativos", "empresas", "socios"],
        ["Estabelecimentos", "Ativos", "Empresas", "Vínculos societários"]):
    col.metric(rotulo, f"{b[chave] / 1e6:.2f} M", f"{b[chave] - a[chave]:+,}")

st.divider()


# ---------- movimento por UF ----------

@st.cache_data
def movimento_uf(safra_a: str, safra_b: str) -> "object":
    return db.query(f"""
        WITH a AS (
            SELECT cnpj_basico || cnpj_ordem AS chave, uf,
                   situacao_cadastral IN ('02', '2') AS ativa
            FROM read_parquet('{caminho("estabelecimentos", safra_a)}')
        ),
        b AS (
            SELECT cnpj_basico || cnpj_ordem AS chave, uf,
                   situacao_cadastral IN ('02', '2') AS ativa
            FROM read_parquet('{caminho("estabelecimentos", safra_b)}')
        )
        SELECT
            coalesce(b.uf, a.uf) AS uf,
            count(*) FILTER (a.chave IS NULL)                     AS novos,
            count(*) FILTER (a.ativa AND b.chave IS NOT NULL
                             AND NOT b.ativa)                     AS deixaram_de_ser_ativos
        FROM b FULL JOIN a ON a.chave = b.chave
        WHERE coalesce(b.uf, a.uf) IS NOT NULL
        GROUP BY 1 ORDER BY novos DESC
    """)


st.subheader("Movimento por UF (novos CNPJs × saíram de ativo)")
mov = movimento_uf(safra_a, safra_b)
col_a, col_b = st.columns(2)
with col_a:
    st.plotly_chart(
        estilo.barras_h(mov.head(15), "uf", "novos",
                        f"Novos estabelecimentos ({safra_b})"),
        use_container_width=True)
with col_b:
    st.plotly_chart(
        estilo.barras_h(mov.sort_values("deixaram_de_ser_ativos", ascending=False).head(15),
                        "uf", "deixaram_de_ser_ativos",
                        "Deixaram de ser ativos", cor=estilo.VERMELHO),
        use_container_width=True)

st.divider()


# ---------- diff societário de um CNPJ ----------

st.subheader("Diff societário de um CNPJ")
entrada = st.text_input("CNPJ (14 ou 8 dígitos)", placeholder="12.345.678/0001-90")
if entrada:
    basico = re.sub(r"\D", "", entrada)[:8]

    @st.cache_data
    def socios_da_safra(safra: str, basico: str):
        return db.query(f"""
            SELECT nome_socio_razao_social AS nome, cpf_cnpj_socio AS cpf,
                   qualificacao_socio
            FROM read_parquet('{caminho("socios", safra)}')
            WHERE cnpj_basico = ?
        """, [basico])

    sa = socios_da_safra(safra_a, basico)
    sb = socios_da_safra(safra_b, basico)
    chave_a = set(zip(sa.nome, sa.cpf))
    chave_b = set(zip(sb.nome, sb.cpf))

    entraram = sb[[tuple(x) not in chave_a for x in zip(sb.nome, sb.cpf)]]
    sairam = sa[[tuple(x) not in chave_b for x in zip(sa.nome, sa.cpf)]]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Entraram ({len(entraram)})** — em {safra_b}, não em {safra_a}")
        st.dataframe(entraram, use_container_width=True, hide_index=True) \
            if not entraram.empty else st.caption("ninguém")
    with c2:
        st.markdown(f"**Saíram ({len(sairam)})** — em {safra_a}, não em {safra_b}")
        st.dataframe(sairam, use_container_width=True, hide_index=True) \
            if not sairam.empty else st.caption("ninguém")

    if entraram.empty and sairam.empty:
        st.success("Quadro societário idêntico nas duas safras.")
