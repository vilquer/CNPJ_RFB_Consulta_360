import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from lib import consultas, estilo

st.set_page_config(page_title="Recorte UF", page_icon="🗺️", layout="wide")
st.title("Recorte por UF e região IBGE")
st.caption(f"Safra {consultas.safra_atual()}")

uf = st.selectbox("UF", consultas.UFS, index=consultas.UFS.index("RS"))

r = consultas.resumo_uf(uf)
geral = consultas.resumo_geral()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Estabelecimentos", f"{r['total'] / 1e6:.2f} M",
          f"{r['total'] / geral['estabelecimentos']:.1%} do Brasil")
c2.metric("Ativos", f"{r['ativos'] / 1e6:.2f} M",
          f"{r['ativos'] / geral['ativos']:.1%} dos ativos BR")
taxa_uf = r["ativos"] / r["total"]
taxa_br = geral["ativos"] / geral["estabelecimentos"]
c3.metric("Taxa de atividade", f"{taxa_uf:.1%}", f"{(taxa_uf - taxa_br) * 100:+.1f} p.p. vs BR")
c4.metric("Municípios", f"{r['municipios']:,}")

st.divider()

st.subheader("Regiões geográficas intermediárias (IBGE)")
reg = consultas.regioes_da_uf(uf)
if reg.empty:
    st.warning("Sem mapeamento de região pra esta UF.")
    st.stop()

reg["taxa"] = reg.ativos / reg.estabelecimentos * 100
col_a, col_b = st.columns(2)
with col_a:
    st.plotly_chart(
        estilo.barras_h(reg, "regiao_intermediaria", "ativos",
                        "Estabelecimentos ativos por região"),
        use_container_width=True,
    )
with col_b:
    st.plotly_chart(
        estilo.barras_h(reg, "regiao_intermediaria", "taxa",
                        "Taxa de atividade por região (%)",
                        cor=estilo.AQUA, fmt_hover=".1f"),
        use_container_width=True,
    )

st.subheader("Mapa — estabelecimentos ativos por município")
if st.toggle("Exibir mapa coroplético", value=True,
             help="Malha municipal baixada da API do IBGE (precisa de internet "
                  "na primeira vez; depois fica em cache por 24h)."):
    malha = consultas.malha_municipal(uf)
    if malha is None:
        st.warning("Não consegui baixar a malha do IBGE (offline?). Tente de novo.")
    else:
        import plotly.graph_objects as go

        import numpy as np

        dados_mapa = consultas.ativos_por_municipio_ibge(uf)
        # escala log: linear deixa tudo pálido (a capital domina a régua)
        z_log = np.log10(dados_mapa["ativos"].clip(lower=1))
        ticks = [10, 100, 1_000, 10_000, 100_000]
        fig = go.Figure(go.Choropleth(
            geojson=malha,
            featureidkey="properties.codarea",
            locations=dados_mapa["codigo_ibge"],
            z=z_log,
            customdata=dados_mapa[["municipio", "ativos"]],
            colorscale=[[0, "#cde2fb"], [0.5, "#3987e5"], [1, "#0d366b"]],
            marker_line_color="#fcfcfb",
            marker_line_width=0.4,
            colorbar=dict(title="ativos", thickness=12,
                          tickvals=[np.log10(t) for t in ticks],
                          ticktext=["10", "100", "1 mil", "10 mil", "100 mil"]),
            hovertemplate="%{customdata[0]}: %{customdata[1]:,.0f} ativos<extra></extra>",
        ))
        fig.update_geos(fitbounds="locations", visible=False)
        fig.update_layout(
            height=560, margin=dict(l=0, r=0, t=8, b=0),
            paper_bgcolor="#fcfcfb",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Escala sequencial azul em **log** (claro = poucos ativos, "
                   "escuro = muitos). Hover mostra o município e o valor real.")

regiao = st.selectbox("Detalhar região", reg.regiao_intermediaria.tolist())
# carrega 50 de cada; sliders fatiam em memória, sem reconsulta
mun = consultas.municipios_da_regiao(uf, regiao, limite=50)
cnae = consultas.top_cnaes(uf, limite=50)
cnae["cnae_principal"] = cnae["cnae_principal"].str.slice(0, 55)
top_n = st.slider("Quantos itens nos rankings", 5, 50, 15, step=5)
col_c, col_d = st.columns(2)
with col_c:
    st.plotly_chart(
        estilo.barras_h(mun.head(top_n), "municipio", "ativos",
                        f"Top {min(top_n, len(mun))} municípios — {regiao}"),
        use_container_width=True,
    )
with col_d:
    st.plotly_chart(
        estilo.barras_h(cnae.head(top_n), "cnae_principal", "ativos",
                        f"Top {top_n} CNAEs — {uf}"),
        use_container_width=True,
    )
