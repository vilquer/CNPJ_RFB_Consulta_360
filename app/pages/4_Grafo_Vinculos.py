# # grafo_vinculos.py
# app_pages/grafo_vinculos.py

import colorsys
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from lib import consultas, grafo
from lib.estilo import AQUA, AZUL, AMARELO, VERMELHO


# ---------------------------------------------------------------------------
# Configuração visual
# ---------------------------------------------------------------------------

CORES = {
    "empresa": AZUL,
    "pf": AQUA,
    "estrangeiro": AMARELO,
    "pj_ext": AMARELO,
    "filial": "#9ec5f4",
    "ie": "#e87ba4",
    "produtor": "#d55181",
}

COR_PAPEL = {
    "gestão": "#eb6834",
    "capital": "#c3c2b7",
    "representação": "#9085e9",
    "filial": "#9ec5f4",
    "ie": "#e87ba4",
}

ROTULO_TIPO = {
    "empresa": "Empresa",
    "pf": "Pessoa física",
    "estrangeiro": "Sócio estrangeiro",
    "pj_ext": "Pessoa jurídica externa",
    "filial": "Filial",
    "ie": "Inscrição Estadual (RS)",
    "produtor": "Produtor rural (SEFAZ-RS)",
}


# ---------------------------------------------------------------------------
# Leitura local do D3
# ---------------------------------------------------------------------------

RAIZ_PROJETO = Path(__file__).resolve().parents[1]
CAMINHO_D3 = RAIZ_PROJETO / "static" / "d3.v7.min.js"

if not CAMINHO_D3.exists():
    st.error(
        "O arquivo D3 não foi encontrado em "
        f"`{CAMINHO_D3}`."
    )
    st.info(
        "Crie a pasta `static` na raiz do projeto e coloque nela o arquivo "
        "`d3.v7.min.js`."
    )
    st.stop()

try:
    D3_JS = CAMINHO_D3.read_text(encoding="utf-8")
except UnicodeDecodeError:
    D3_JS = CAMINHO_D3.read_text(encoding="latin-1")
except OSError as erro:
    st.error(f"Não foi possível ler o arquivo D3: {erro}")
    st.stop()


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def valor_json(valor: Any) -> Any:
    """
    Converte valores retornados pelo pandas/Oracle em valores compatíveis
    com JSON e JavaScript.
    """
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(valor, (str, bool, int)):
        return valor

    if isinstance(valor, float):
        if math.isnan(valor) or math.isinf(valor):
            return None
        return valor

    if hasattr(valor, "isoformat"):
        try:
            return valor.isoformat()
        except (TypeError, ValueError):
            pass

    if hasattr(valor, "item"):
        try:
            return valor.item()
        except (TypeError, ValueError):
            pass

    return str(valor)


def texto(valor: Any, padrao: str = "") -> str:
    valor = valor_json(valor)

    if valor is None:
        return padrao

    resultado = str(valor).strip()
    return resultado if resultado else padrao


def montar_titulo_no(no: dict, tipo: str, basico: str) -> str:
    """
    Texto exibido no tooltip do navegador.
    """
    if tipo == "empresa":
        return "\n".join(
            [
                texto(no.get("razao"), texto(no.get("basico"), "?")),
                f"CNPJ matriz: {texto(no.get('cnpj_matriz'), texto(no.get('basico'), '?'))}",
                (
                    f"Situação: {texto(no.get('situacao'), '?')} | "
                    f"{texto(no.get('municipio'), '?')}/"
                    f"{texto(no.get('uf'), '?')}"
                ),
                f"CNAE: {texto(no.get('cnae'), '?')}",
                f"Porte: {texto(no.get('porte'), '?')}",
                (
                    f"Estabelecimentos: {texto(no.get('n_estab'), '?')} "
                    f"({texto(no.get('n_ativos'), '?')} ativos)"
                ),
                "Clique para abrir os detalhes.",
            ]
        )

    if tipo == "filial":
        return "\n".join(
            [
                f"Filial: {texto(no.get('cnpj'), '?')}",
                (
                    f"{texto(no.get('municipio'), '?')}/"
                    f"{texto(no.get('uf'), '?')}"
                ),
                f"Situação: {texto(no.get('situacao'), '?')}",
                "Clique para abrir os detalhes.",
            ]
        )

    if tipo == "ie":
        return "\n".join(
            [
                f"Inscrição Estadual RS: {texto(no.get('inscricao'), '?')}",
                f"Categoria: {texto(no.get('categoria'), '?')}",
                f"CNAE: {texto(no.get('cnae'), '?')}",
                f"Abertura: {texto(no.get('data_abertura'), '?')}",
                "Clique para abrir os detalhes.",
            ]
        )

    if tipo == "produtor":
        return "\n".join(
            [
                f"Produtor rural (SEFAZ-RS)",
                f"CPF: {texto(no.get('cpf'), '?')}",
                "Titular das IEs conectadas. Clique para detalhes.",
            ]
        )

    partes = [
        texto(no.get("nome"), "?"),
        f"CPF/CNPJ: {texto(no.get('cpf'), 'não informado')}",
    ]

    if no.get("faixa"):
        partes.append(f"Faixa etária: {texto(no.get('faixa'))}")

    if no.get("pais"):
        partes.append(f"País: {texto(no.get('pais'))}")

    if no.get("hub"):
        partes.append(
            f"Hub: participa de {texto(no.get('hub'))} empresas"
        )

    if no.get("representante"):
        partes.append("Representante legal")

    partes.append("Clique para abrir os detalhes.")

    return "\n".join(partes)


def preparar_dados_d3(grafo_atual, basico: str) -> tuple[list, list]:
    """
    Converte lib.grafo.Grafo em listas serializáveis para D3.
    """
    nodes = []
    edges = []

    for id_no, no_original in grafo_atual.nos.items():
        no = {
            chave: valor_json(valor)
            for chave, valor in no_original.items()
        }

        tipo = texto(no.get("tipo"), "pf")
        eh_seed = tipo == "empresa" and texto(no.get("basico")) == basico
        eh_representante = bool(no.get("representante"))
        situacao = texto(no.get("situacao"), "?").lower()

        if tipo == "empresa":
            rotulo = texto(
                no.get("razao"),
                texto(no.get("basico"), "?"),
            )[:42]

            cor_fundo = "#eda100" if eh_seed else CORES["empresa"]

            if situacao not in ("ativa", "?"):
                cor_borda = VERMELHO
            elif eh_seed:
                cor_borda = "#c98500"
            else:
                cor_borda = AZUL

            tamanho = 260 if eh_seed else 150
            formato = "star" if eh_seed else "circle"

        elif tipo == "filial":
            rotulo = (
                f"Filial {texto(no.get('uf'))} "
                f"{texto(no.get('cnpj'))}"
            ).strip()[:42]

            cor_fundo = CORES["filial"]
            cor_borda = (
                VERMELHO
                if situacao not in ("ativa", "?")
                else "#739fca"
            )
            tamanho = 90
            formato = "square"

        elif tipo == "ie":
            rotulo = f"IE {texto(no.get('inscricao'), '?')}"[:42]
            cor_fundo = CORES["ie"]
            cor_borda = "#d55181"
            tamanho = 90
            formato = "diamond"

        elif tipo == "produtor":
            rotulo = f"CPF {texto(no.get('cpf'), '?')}"[:42]
            cor_fundo = CORES["produtor"]
            cor_borda = "#a33c62"
            tamanho = 120
            formato = "circle"

        else:
            rotulo = texto(no.get("nome"), "?")[:36]
            cor_fundo = CORES.get(tipo, AQUA)
            cor_borda = VERMELHO if no.get("hub") else cor_fundo
            tamanho = 190 if no.get("hub") else 115
            formato = "triangle" if eh_representante else "circle"

        node_d3 = {
            "id": str(id_no),
            "label": rotulo,
            "tipo": tipo,
            "tipoLabel": ROTULO_TIPO.get(tipo, tipo),
            "seed": eh_seed,
            "representante": eh_representante,
            "hub": valor_json(no.get("hub")),
            "shape": formato,
            "size": tamanho,
            "color": cor_fundo,
            "borderColor": cor_borda,
            "title": montar_titulo_no(no, tipo, basico),

            # Dados cadastrais usados pelo painel HTML.
            "nome": valor_json(no.get("nome")),
            "cpf": valor_json(no.get("cpf")),
            "faixa": valor_json(no.get("faixa")),
            "pais": valor_json(no.get("pais")),
            "razao": valor_json(no.get("razao")),
            "basico": valor_json(no.get("basico")),
            "cnpj": valor_json(no.get("cnpj")),
            "cnpjMatriz": valor_json(no.get("cnpj_matriz")),
            "situacao": valor_json(no.get("situacao")),
            "uf": valor_json(no.get("uf")),
            "municipio": valor_json(no.get("municipio")),
            "porte": valor_json(no.get("porte")),
            "natureza": valor_json(no.get("natureza")),
            "cnae": valor_json(no.get("cnae")),
            "nEstab": valor_json(no.get("n_estab")),
            "nAtivos": valor_json(no.get("n_ativos")),
            "inscricao": valor_json(no.get("inscricao")),
            "categoria": valor_json(no.get("categoria")),
            "dataAbertura": valor_json(no.get("data_abertura")),
        }

        nodes.append(node_d3)

    ids_validos = {node["id"] for node in nodes}

    for (origem, destino), (rotulo, desde) in grafo_atual.arestas.items():
        origem = str(origem)
        destino = str(destino)

        # Evita erro no D3 caso uma aresta órfã tenha sobrevivido ao filtro.
        if origem not in ids_validos or destino not in ids_validos:
            continue

        papel_vinculo = (
            "filial"
            if rotulo == "filial"
            else grafo.papel(rotulo)
        )

        titulo = texto(rotulo, papel_vinculo)

        if desde:
            titulo += f"\nDesde {texto(desde)}"

        edges.append(
            {
                "source": origem,
                "target": destino,
                "label": texto(rotulo, papel_vinculo),
                "papel": papel_vinculo,
                "desde": valor_json(desde),
                "title": titulo,
                "color": COR_PAPEL.get(
                    papel_vinculo,
                    "#c3c2b7",
                ),
                "width": 1.7,
            }
        )

    return nodes, edges


def json_seguro(dados: Any) -> str:
    """
    Serializa para JSON e impede que algum texto vindo do banco encerre
    acidentalmente a tag script.
    """
    resultado = json.dumps(
        dados,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )

    return resultado.replace("</", "<\\/")


# 8 hues categóricas fixas (mesma ordem/paleta do padrão de dataviz usado
# no resto do app) — cor por empresa quando poucas estão selecionadas.
_CORES_EMPRESA = [AZUL, AQUA, "#eda100", "#008300", "#4a3aa7", VERMELHO, "#e87ba4", "#eb6834"]
_LIMIAR_CORES_POR_EMPRESA = 30


def _cor_empresa(indice: int, total: int) -> str:
    """Cor por empresa: usa a paleta curada enquanto couber; além disso gera
    cores extras espalhadas pela roda de matiz (HSL), pra nunca repetir uma
    cor mesmo com muitas empresas selecionadas — sobreposição de bolinhas
    não pode parecer 'tudo uma cor só'."""
    if indice < len(_CORES_EMPRESA):
        return _CORES_EMPRESA[indice]
    matiz = ((indice - len(_CORES_EMPRESA)) / max(total - len(_CORES_EMPRESA), 1)) % 1.0
    r, g, b = colorsys.hls_to_rgb(matiz, 0.5, 0.65)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def renderizar_mapa_capilaridade(grafo_atual, basico_seed: str, filtro_situacao: str) -> None:
    """Aba 'Mapa de capilaridade': onde as empresas do grafo atual têm
    presença física (matriz+filiais), num coroplético nacional que
    auto-enquadra — uma empresa só numa cidade dá zoom de cidade, uma como
    o BB some pro Brasil inteiro. Respeita o filtro ativas/inativas da
    sidebar e deixa escolher quais empresas do grafo entram no mapa (poucas
    = uma cor por empresa; muitas = mapa de calor agregado)."""
    import plotly.graph_objects as go

    # badge internacional: os nós já carregam o país quando a RFB registra
    # sócio/representante domiciliado no exterior — zero query nova.
    paises = sorted({
        texto(no.get("pais"))
        for no in grafo_atual.nos.values()
        if texto(no.get("pais")) and texto(no.get("pais")).upper() != "BRASIL"
    })
    if paises:
        st.info(
            "🌐 **Vínculos internacionais**: " + ", ".join(paises) + " — "
            "sócio(s) ou representante(s) domiciliados no exterior neste "
            "recorte. A RFB só registra o país (sem coordenadas), por isso "
            "aparecem aqui como aviso, não como pontos no mapa."
        )

    empresas_no_grafo = {
        no["basico"]: texto(no.get("razao"), no["basico"])
        for no in grafo_atual.nos.values()
        if no.get("tipo") == "empresa" and no.get("basico")
    }
    if not empresas_no_grafo:
        st.caption("Nenhuma empresa com estabelecimento localizável neste recorte.")
        return

    todos_basicos = tuple(sorted(empresas_no_grafo, key=lambda b: empresas_no_grafo[b]))
    selecionadas = st.multiselect(
        "Empresas incluídas no mapa",
        options=todos_basicos,
        default=todos_basicos,
        format_func=lambda b: f"{empresas_no_grafo[b]} ({b})",
        help=f"Até {_LIMIAR_CORES_POR_EMPRESA} empresas selecionadas ganham "
             "cor própria (compare a capilaridade de cada uma); mais que "
             "isso vira um único mapa de calor somando todas.",
    )
    if not selecionadas:
        st.caption("Selecione ao menos uma empresa.")
        return

    pegada = grafo.pegada_geografica(tuple(sorted(selecionadas)))
    if pegada.empty:
        st.caption("Sem estabelecimentos localizáveis pras empresas selecionadas.")
        return

    # filtro ativas/inativas — o mesmo controle da sidebar que já filtra a
    # rede (achado: o mapa mostrava ativas+inativas sempre, inconsistente
    # com o que a aba Rede exibia ao lado).
    if filtro_situacao == "ativas":
        pegada = pegada.assign(n=pegada["n_ativos"])
    elif filtro_situacao == "inativas":
        pegada = pegada.assign(n=pegada["n_estab"] - pegada["n_ativos"])
    else:
        pegada = pegada.assign(n=pegada["n_estab"])
    pegada = pegada[pegada["n"] > 0]
    if pegada.empty:
        st.caption(f"Nenhum estabelecimento {filtro_situacao} pras empresas selecionadas.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Empresas no mapa", len(selecionadas))
    c2.metric("Municípios com presença", pegada["municipio"].nunique())
    c3.metric("UFs com presença", pegada["uf"].nunique())

    malha = consultas.malha_nacional()
    if malha is None:
        st.warning("Malha do IBGE indisponível agora (offline?). Tente de novo.")
        return

    import numpy as np

    # trace de fundo: TODOS os ~5.570 municípios do Brasil, preenchimento
    # neutro — sem isso, só os municípios com presença aparecem "flutuando"
    # sem nenhuma referência de fronteira ao redor.
    todos_codigos = [f["properties"]["codarea"] for f in malha["features"]]
    camadas = [go.Choropleth(
        geojson=malha,
        featureidkey="properties.codarea",
        locations=todos_codigos,
        z=[0] * len(todos_codigos),
        colorscale=[[0, "#fcfcfb"], [1, "#fcfcfb"]],
        marker_line_color="#c2bfb2",
        marker_line_width=0.3,
        showscale=False,
        hoverinfo="skip",
    )]

    # contorno estadual, mais escuro que a fronteira municipal — só a
    # LINHA importa aqui, então o preenchimento fica transparente.
    malha_uf = consultas.malha_estados()
    if malha_uf:
        codigos_uf = [f["properties"]["codarea"] for f in malha_uf["features"]]
        camadas.append(go.Choropleth(
            geojson=malha_uf,
            featureidkey="properties.codarea",
            locations=codigos_uf,
            z=[0] * len(codigos_uf),
            colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
            marker_line_color="#8a8880",
            marker_line_width=1.1,
            showscale=False,
            hoverinfo="skip",
        ))

    # bolinhas em vez de preencher o município inteiro: com fill, só a
    # última cor desenhada aparece (uma empresa esconde a outra); bolinha
    # translúcida deixa mais de uma empresa visível no mesmo município.
    # Não há base de lat/lon própria — o centro de cada município é
    # calculado a partir do próprio polígono da malha (centroides_municipios).
    centros = consultas.centroides_municipios()
    if not centros:
        st.warning("Centróides indisponíveis agora (malha offline?). Tente de novo.")
        return
    faltando = 0

    def _com_centroide(df: pd.DataFrame) -> pd.DataFrame:
        nonlocal faltando
        lat = df["codigo_ibge"].map(lambda c: centros.get(c, (None, None))[0])
        lon = df["codigo_ibge"].map(lambda c: centros.get(c, (None, None))[1])
        df = df.assign(lat=lat, lon=lon)
        faltando += int(df["lat"].isna().sum())
        return df.dropna(subset=["lat", "lon"])

    def _jitter(lat: float, lon: float, indice: int, total: int) -> tuple[float, float]:
        """Afasta bolinhas que cairiam no mesmo centro (mais de uma empresa
        selecionada no mesmo município) num pequeno círculo ao redor dele —
        senão ficam perfeitamente sobrepostas."""
        if total <= 1:
            return lat, lon
        angulo = 2 * math.pi * indice / total
        raio_lat = 0.045
        raio_lon = raio_lat / max(math.cos(math.radians(lat)), 0.15)
        return lat + raio_lat * math.sin(angulo), lon + raio_lon * math.cos(angulo)

    # índice do primeiro trace com dado de verdade (tudo antes é fundo/
    # fronteira, não clicável) e, no modo colorido, qual empresa cada trace
    # representa — usado pra montar o painel de detalhe ao clicar.
    indice_primeiro_dado = len(camadas)
    indice_para_basico: dict[int, str] = {}

    colorido = len(selecionadas) <= _LIMIAR_CORES_POR_EMPRESA
    if colorido:
        pegada_c = _com_centroide(pegada)
        ordem = (pegada_c[["codigo_ibge", "cnpj_basico"]]
                 .drop_duplicates()
                 .sort_values(["codigo_ibge", "cnpj_basico"]))
        ordem["indice"] = ordem.groupby("codigo_ibge").cumcount()
        ordem["total"] = ordem.groupby("codigo_ibge")["cnpj_basico"].transform("count")
        pegada_c = pegada_c.merge(ordem, on=["codigo_ibge", "cnpj_basico"])
        pos = pegada_c.apply(
            lambda r: _jitter(r["lat"], r["lon"], r["indice"], r["total"]), axis=1)
        pegada_c["lat_j"] = [p[0] for p in pos]
        pegada_c["lon_j"] = [p[1] for p in pos]

        for i, basico in enumerate(sorted(selecionadas)):
            fatia = pegada_c[pegada_c["cnpj_basico"] == basico]
            if fatia.empty:
                continue
            cor = _cor_empresa(i, len(selecionadas))
            tamanho = np.clip(6 + 5 * np.sqrt(fatia["n"]), 6, 34)
            camadas.append(go.Scattergeo(
                lat=fatia["lat_j"], lon=fatia["lon_j"],
                mode="markers",
                marker=dict(size=tamanho, color=cor, opacity=0.78,
                            line=dict(width=0.8, color="#fcfcfb")),
                customdata=fatia[["codigo_ibge", "municipio", "uf", "n"]],
                name=empresas_no_grafo[basico][:40],
                showlegend=True,
                hovertemplate=empresas_no_grafo[basico][:40] + " — "
                              "%{customdata[1]}/%{customdata[2]}: "
                              "%{customdata[3]:,.0f} estabelecimento(s)<extra></extra>",
            ))
            indice_para_basico[len(camadas) - 1] = basico
        legenda_txt = ("Uma bolinha por empresa selecionada em cada município "
                       "(tamanho ~ nº de estabelecimentos) — quando mais de uma "
                       "empresa tem presença no mesmo lugar, as bolinhas se "
                       "afastam em vez de se sobrepor.")
    else:
        def _lista_empresas(basicos) -> str:
            nomes = [empresas_no_grafo[b][:30] for b in sorted(set(basicos))
                     if b in empresas_no_grafo]
            if len(nomes) <= 3:
                return ", ".join(nomes)
            return ", ".join(nomes[:3]) + f" e mais {len(nomes) - 3}"

        agregado = (pegada.groupby(["codigo_ibge", "municipio", "uf"], as_index=False)
                          .agg(n=("n", "sum")))
        nomes_por_municipio = (pegada.groupby("codigo_ibge")["cnpj_basico"]
                               .apply(_lista_empresas).rename("empresas"))
        agregado = agregado.merge(nomes_por_municipio, on="codigo_ibge")
        agregado = _com_centroide(agregado)
        tamanho = np.clip(5 + 6 * np.sqrt(agregado["n"]), 5, 38)
        camadas.append(go.Scattergeo(
            lat=agregado["lat"], lon=agregado["lon"],
            mode="markers",
            marker=dict(size=tamanho, color=AZUL, opacity=0.7,
                        line=dict(width=0.6, color="#fcfcfb")),
            customdata=agregado[["codigo_ibge", "municipio", "uf", "n", "empresas"]],
            hovertemplate="<b>%{customdata[4]}</b><br>"
                          "%{customdata[1]}/%{customdata[2]}: "
                          "%{customdata[3]:,.0f} estabelecimento(s)<extra></extra>",
        ))
        pegada = agregado  # bbox abaixo usa o mesmo nome
        legenda_txt = ("Tamanho da bolinha ~ total de estabelecimentos somando "
                       f"as {len(selecionadas)} empresas selecionadas.")
    if faltando:
        st.caption(f"⚠️ {faltando} localização(ões) sem centróide calculável "
                   "(malha incompleta) ficaram fora do mapa.")

    fig = go.Figure(camadas)

    # zoom manual pelo bbox do recorte (com múltiplos traces, fitbounds
    # enquadraria sempre o Brasil inteiro por causa do trace de fundo — ver
    # bbox_dos_codigos em consultas.py). Altura dinâmica pela proporção real
    # do recorte: altura fixa deixava sobra branca grande nas laterais
    # (Brasil é mais alto que largo — um grau de longitude "pesa" menos que
    # um de latitude nessa faixa de latitude, cos(lat_média) corrige isso).
    altura = 560
    if bbox := consultas.bbox_dos_codigos(malha, set(pegada["codigo_ibge"])):
        min_lon, max_lon, min_lat, max_lat = bbox
        pad_lon = max((max_lon - min_lon) * 0.15, 0.5)
        pad_lat = max((max_lat - min_lat) * 0.15, 0.5)
        fig.update_geos(
            lonaxis_range=[min_lon - pad_lon, max_lon + pad_lon],
            lataxis_range=[min_lat - pad_lat, max_lat + pad_lat],
            visible=False,
        )
        lat_media = (min_lat + max_lat) / 2
        largura_geo = (max_lon - min_lon + 2 * pad_lon) * np.cos(np.radians(lat_media))
        altura_geo = max_lat - min_lat + 2 * pad_lat
        aspecto = largura_geo / altura_geo if altura_geo > 0 else 1.0
        largura_coluna = 480  # aproximação da largura útil da coluna do mapa (2/3 da área)
        altura = int(np.clip(largura_coluna / max(aspecto, 0.1), 380, 900))
    else:
        fig.update_geos(fitbounds="locations", visible=False)

    fig.update_layout(height=altura, margin=dict(l=0, r=0, t=8, b=0),
                      paper_bgcolor="#fcfcfb",
                      legend=dict(
                          orientation="h", y=-0.02, x=0,
                          font=dict(size=11, color="#343330"),
                          bgcolor="#fcfcfb", bordercolor="#e1e0d9", borderwidth=1,
                      ))

    col_mapa, col_detalhe = st.columns([2, 1])
    with col_mapa:
        evento = st.plotly_chart(
            fig, use_container_width=True,
            on_select="rerun", selection_mode=["points"],
            key="mapa_capilaridade",
        )
        st.caption(legenda_txt + " Fronteiras municipais e estaduais sempre "
                   "visíveis como referência; o mapa enquadra na extensão da "
                   "presença encontrada.")

    with col_detalhe:
        st.markdown("**Detalhe**")
        pontos = [p for p in evento.selection.points
                  if p["curve_number"] >= indice_primeiro_dado]
        if not pontos:
            st.caption("Clique numa bolinha do mapa pra ver o detalhe aqui.")
        for p in pontos:
            cd = p["customdata"]
            codigo_ibge, municipio, uf, n = cd[0], cd[1], cd[2], cd[3]
            if len(cd) >= 5:
                empresas_txt = cd[4]  # modo agregado: lista já vem pronta
                basicos_ponto = tuple(sorted(selecionadas))
            else:
                basico = indice_para_basico.get(p["curve_number"])
                empresas_txt = empresas_no_grafo.get(basico, "?")
                basicos_ponto = (basico,) if basico else ()
            st.markdown(f"**{municipio}/{uf}**")
            st.write(empresas_txt)
            st.write(f"{n:,.0f} estabelecimento(s)".replace(",", "."))
            if basicos_ponto:
                with st.expander("Ver estabelecimentos"):
                    detalhe = grafo.estabelecimentos_do_ponto(basicos_ponto, codigo_ibge)
                    truncado = len(detalhe) == 300
                    if filtro_situacao == "ativas":
                        detalhe = detalhe[detalhe["situacao"] == "ativa"]
                    elif filtro_situacao == "inativas":
                        detalhe = detalhe[detalhe["situacao"] != "ativa"]
                    if detalhe.empty:
                        st.caption("Nenhum estabelecimento nessa condição.")
                    for _, row in detalhe.iterrows():
                        endereco = " ".join(
                            t for t in (texto(row["logradouro"]), texto(row["numero"])) if t
                        )
                        if texto(row["bairro"]):
                            endereco = f"{endereco} — {row['bairro']}" if endereco else row["bairro"]
                        st.markdown(
                            f"**{texto(row['nome_fantasia'], row['razao_social'])}** "
                            f"({row['matriz_filial']}, {row['situacao']})"
                        )
                        st.caption(
                            f"CNPJ {row['cnpj']} · {texto(row['cnae_principal'], 'CNAE não informado')}"
                            + (f"  \n{endereco}" if endereco else "")
                        )
                    if truncado:
                        st.caption("Lista truncada em 300 — refine a seleção de empresas.")
            st.divider()


# ---------------------------------------------------------------------------
# Cabeçalho
# ---------------------------------------------------------------------------

# layout="wide": sem isso o Streamlit centraliza o conteúdo numa coluna
# estreita (~730px) e o grafo fica espremido.
st.set_page_config(page_title="Grafo de vínculos", page_icon="🕸️", layout="wide")

st.title("Grafo de vínculos societários")

st.caption(
    f"Safra {consultas.safra_atual()} · "
    "empresas ↔ sócios ↔ representantes · "
    "clique em um nó para abrir os detalhes"
)


# ---------------------------------------------------------------------------
# Parâmetros
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Parâmetros")

    niveis = st.slider(
        "Níveis de expansão",
        min_value=1,
        max_value=20,
        value=1,
        help=(
            "Cada nível soma 1 grau de separação a partir da semente — "
            "sócios, empresas em que ela é sócia, sócios dessas empresas, "
            "e assim por diante. Uma empresa nova sempre revela os "
            "PRÓPRIOS vínculos já no nível seguinte ao em que apareceu."
        ),
    )

    max_nos = st.slider(
        "Máximo de nós",
        min_value=20,
        max_value=1000,
        value=150,
        step=10,
    )

    limite_hub = st.slider(
        "Limite de hub (sócio ligado a mais empresas que isso não expande)",
        min_value=10,
        max_value=500,
        value=40,
        step=10,
        help=(
            "Protege o grafo de explodir quando um sócio participa de "
            "dezenas/centenas de empresas (contador, despachante, 'sócio "
            "de fachada'). Suba pra ver justamente as empresas de um hub "
            "suspeito — o aviso no grafo mostra quantas empresas cada hub "
            "tem, pra você calibrar o valor."
        ),
    )

    filtro_situacao = st.radio(
        "Empresas no grafo",
        ["ativas", "inativas", "todas"],
        horizontal=True,
        help=(
            "Filtra empresas e filiais pela situação cadastral. "
            "A empresa pesquisada sempre permanece no grafo."
        ),
    )

    incluir_rep = st.toggle(
        "Representantes legais",
        value=True,
    )

    incluir_fil = st.toggle(
        "Filiais da empresa buscada",
        value=False,
    )

    max_filiais = st.slider(
        "Máximo de filiais exibidas",
        min_value=10,
        max_value=700,
        value=50,
        step=10,
        disabled=not incluir_fil,
        help=(
            "O limite segue o filtro de situação. Empresas grandes podem "
            "possuir centenas de filiais."
        ),
    )

    fisica = st.toggle(
        "Física dos nós",
        value=True,
        help=(
            "Quando ligada, os nós se acomodam automaticamente. "
            "Quando desligada, os nós são organizados em círculo."
        ),
    )

    espacamento = st.slider(
        "Espaçamento entre nós",
        min_value=80,
        max_value=400,
        value=180,
        step=20,
        disabled=not fisica,
        help=(
            "Valores maiores afastam os nós e reduzem a sobreposição "
            "dos nomes."
        ),
    )

    mostrar_rotulos_vinculos = st.toggle(
        "Nomes nos vínculos",
        value=True,
        help="Exibe a qualificação societária sobre cada linha.",
    )

    mostrar_rotulos_nos = st.toggle(
        "Nomes dos nós",
        value=True,
    )

    incluir_ies = False
    niveis_ie = 0
    if consultas.tem_ie():
        incluir_ies = st.toggle(
            "Inscrições Estaduais (RS)",
            value=False,
            help="Losangos rosa ligados às empresas do grafo — base de "
                 "produtores rurais da SEFAZ-RS (tabela ie_rs).",
        )
        niveis_ie = st.slider(
            "Níveis de expansão IE", 0, 6, 0,
            disabled=not incluir_ies,
            help="0 = só as IEs das empresas. 1 = + titulares das IEs "
                 "(condôminos: produtores de CPF aberto e outras PJs). "
                 "2 = + outras IEs desses produtores. E assim alternando — "
                 "mesma mecânica do grafo do projeto IE.",
        )

    filtro_papel = st.multiselect(
        "Tipos de vínculo",
        ["gestão", "capital", "representação", "filial"],
        default=["gestão", "capital", "representação", "filial"],
        help="Ex.: deixe só 'gestão' pra ver o esqueleto de comando.",
    )

    grupo_economico = st.toggle(
        "Fechar grupo econômico",
        value=False,
        help="Ignora o nível e expande até esgotar os vínculos alcançáveis "
             "(ou bater no máximo de nós). Mostra o grupo inteiro.",
    )


# ---------------------------------------------------------------------------
# Entrada e validação (CNPJ ou nome de sócio)
# ---------------------------------------------------------------------------

entrada = st.text_input(
    "CNPJ, nome de sócio ou CPF mascarado",
    placeholder="12.345.678/0001-90  ·  FULANO DA SILVA  ·  ***123456**",
    help="CPF mascarado: cole como a RFB expõe ('***293378**') ou só os "
         "dígitos visíveis ('293378').",
)

if not entrada:
    st.info(
        "Digite um CNPJ, um nome de sócio ou um CPF mascarado (o CPF "
        "sempre vem parcialmente oculto pela própria RFB). A partir da "
        "semente o grafo expande sócios, administradores, representantes "
        "e empresas relacionadas."
    )
    st.stop()

digitos = re.sub(r"\D", "", entrada)
tem_letra = bool(re.search(r"[A-Za-zÀ-ÿ]", entrada))
tem_asterisco = "*" in entrada
cnpj_valido = (
    bool(digitos) and len(digitos) in (8, 14)
    and not tem_asterisco and not tem_letra
    and bool(re.fullmatch(r"[\d./\-\s]+", entrada.strip()))
)
cpf_mascarado = tem_asterisco or (
    bool(digitos) and not tem_letra and len(digitos) not in (8, 14)
)

seed_pf = None
basico = None

if cnpj_valido:
    basico = digitos[:8]
elif cpf_mascarado:
    candidatos = grafo.buscar_socios_por_cpf(entrada)
    if candidatos.empty:
        st.warning("Nenhum sócio pessoa física com esse CPF na safra.")
        st.stop()
    opcoes = {
        f"{r.nome_socio} · CPF {r.cpf_cnpj_socio} · {r.participacoes} participações": (
            r.nome_socio, r.cpf_cnpj_socio)
        for r in candidatos.itertuples(index=False)
    }
    escolha = st.selectbox("Escolha a pessoa (nome + CPF mascarado)", list(opcoes))
    seed_pf = opcoes[escolha]
else:
    candidatos = grafo.buscar_socios_por_nome(entrada)
    if candidatos.empty:
        st.warning("Nenhum sócio pessoa física com esse nome na safra.")
        st.stop()
    opcoes = {
        f"{r.nome_socio} · CPF {r.cpf_cnpj_socio} · {r.participacoes} participações": (
            r.nome_socio, r.cpf_cnpj_socio)
        for r in candidatos.itertuples(index=False)
    }
    escolha = st.selectbox("Escolha a pessoa (nome + CPF mascarado)", list(opcoes))
    seed_pf = opcoes[escolha]

niveis_efetivos = 99 if grupo_economico else niveis


# ---------------------------------------------------------------------------
# Construção do grafo
# ---------------------------------------------------------------------------

try:
    with st.spinner("Expandindo o grafo de vínculos..."):
        g = grafo.montar(
            basico,
            niveis_efetivos,
            max_nos,
            incluir_rep,
            incluir_fil,
            max_filiais,
            priorizar_filiais=filtro_situacao,
            seed_pf=seed_pf,
            incluir_ies=incluir_ies,
            niveis_ie=niveis_ie,
            limite_hub=limite_hub,
        )
        if basico is None:
            basico = next(
                (v["basico"] for v in g.nos.values() if v.get("tipo") == "empresa"),
                "",
            )

        g = grafo.filtrar_situacao(
            g,
            filtro_situacao,
            basico,
        )

        g = grafo.filtrar_papel(g, tuple(filtro_papel), basico)

except Exception as erro:
    st.error("Não foi possível montar o grafo.")
    st.exception(erro)
    st.stop()

if not g.nos:
    st.warning(
        "CNPJ não encontrado ou sem sócios registrados nesta safra. "
        "Isso também pode ocorrer com MEI."
    )
    st.stop()


# ---------------------------------------------------------------------------
# Informações sobre filiais e limites
# ---------------------------------------------------------------------------

if incluir_fil:
    total_fil, ativas_fil = grafo.conta_filiais(basico)

    exibidas = sum(
        1
        for no in g.nos.values()
        if no.get("tipo") == "filial"
    )

    if total_fil > exibidas:
        st.info(
            f"A empresa possui **{total_fil} filiais**, sendo "
            f"**{ativas_fil} ativas**. Estão sendo exibidas "
            f"**{exibidas} filiais**."
        )


# ---------------------------------------------------------------------------
# Indicadores
# ---------------------------------------------------------------------------

tipos = [
    no.get("tipo")
    for no in g.nos.values()
]

c1, c2, c3, c4 = st.columns(4)

c1.metric("Nós", len(g.nos))
c2.metric("Vínculos", len(g.arestas))
c3.metric("Empresas", tipos.count("empresa"))
c4.metric(
    "Pessoas",
    tipos.count("pf") + tipos.count("estrangeiro"),
)

if g.truncado:
    st.warning(
        f"O grafo foi interrompido no limite de {max_nos} nós."
    )

for nome, quantidade in g.hubs:
    st.caption(
        f"⚠️ **{nome}** participa de {quantidade} empresas e não foi "
        "expandido por ser um hub."
    )


# ---------------------------------------------------------------------------
# Abas: rede (grafo D3) e mapa de capilaridade geográfica
# ---------------------------------------------------------------------------

tab_rede, tab_mapa = st.tabs(["🕸️ Rede", "🗺️ Mapa de capilaridade"])

# A aba do mapa é montada só DEPOIS da rede (mais abaixo no script) — como
# o Streamlit envia cada elemento assim que fica pronto, isso faz a rede
# (aba ativa por padrão) aparecer na hora, em vez de ficar em branco
# enquanto o mapa (malha nacional, ~5.600 municípios) é construído nos
# bastidores mesmo sem estar visível.


# ---------------------------------------------------------------------------
# Legenda
# ---------------------------------------------------------------------------

with tab_rede:
    st.markdown(
    """
    <div style="
        display:flex;
        flex-wrap:wrap;
        gap:14px;
        align-items:center;
        font-size:13px;
        background:#f7f7f4;
        border:1px solid #ecebe5;
        border-radius:8px;
        padding:10px 14px;
        margin-bottom:10px;
        color:#52514e;
    ">
      <span>
        <span style="color:#eda100; font-size:16px;">★</span>
        empresa pesquisada
      </span>

      <span>
        <span style="color:#2a78d6; font-size:16px;">●</span>
        empresa
      </span>

      <span>
        <span style="color:#1baf7a; font-size:16px;">●</span>
        sócio PF
      </span>

      <span>
        <span style="color:#eda100; font-size:16px;">●</span>
        estrangeiro ou PJ externa
      </span>

      <span>
        <span style="color:#1baf7a; font-size:16px;">▲</span>
        representante
      </span>

      <span>
        <span style="color:#9ec5f4; font-size:16px;">■</span>
        filial
      </span>

      <span>
        <span style="color:#e87ba4; font-size:16px;">◆</span>
        Inscrição Estadual RS
      </span>

      <span>
        <span style="color:#d55181; font-size:16px;">●</span>
        produtor rural (CPF SEFAZ)
      </span>

      <span>
        <span style="display:inline-block; width:18px; height:3px;
              background:#eb6834; border-radius:2px; vertical-align:middle;
              margin-right:4px;"></span>gestão
      </span>

      <span>
        <span style="display:inline-block; width:18px; height:3px;
              background:#c3c2b7; border-radius:2px; vertical-align:middle;
              margin-right:4px;"></span>capital
      </span>

      <span>
        <span style="display:inline-block; width:18px; height:3px;
              background:#9085e9; border-radius:2px; vertical-align:middle;
              margin-right:4px;"></span>representação
      </span>

      <span>
        <span style="color:#e34948;">◯</span>
        borda vermelha = não ativa ou hub
      </span>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Conversão dos dados para D3
# ---------------------------------------------------------------------------

nodes, edges = preparar_dados_d3(g, basico)

nodes_json = json_seguro(nodes)
edges_json = json_seguro(edges)

fisica_js = "true" if fisica else "false"
rotulos_vinculos_js = (
    "true" if mostrar_rotulos_vinculos else "false"
)
rotulos_nos_js = "true" if mostrar_rotulos_nos else "false"


# ---------------------------------------------------------------------------
# HTML completo com D3
# ---------------------------------------------------------------------------

graph_html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">

<script>
{D3_JS}
</script>

<style>
    * {{
        box-sizing: border-box;
    }}

    html,
    body {{
        width: 100%;
        height: 100%;
        margin: 0;
        overflow: hidden;
        background: #fcfcfb;
        color: #343330;
        font-family:
            -apple-system,
            BlinkMacSystemFont,
            "Segoe UI",
            Arial,
            sans-serif;
    }}

    #app {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) 320px;
        width: 100%;
        height: 760px;
        border: 1px solid #e1e0d9;
        border-radius: 9px;
        overflow: hidden;
        background: #fcfcfb;
    }}

    #graph-area {{
        position: relative;
        min-width: 0;
        height: 100%;
        overflow: hidden;
        background:
            radial-gradient(
                circle at center,
                #ffffff 0%,
                #fcfcfb 70%,
                #f6f6f2 100%
            );
    }}

    #graph {{
        width: 100%;
        height: 100%;
        display: block;
        cursor: grab;
    }}

    #graph:active {{
        cursor: grabbing;
    }}

    #details {{
        height: 100%;
        overflow-y: auto;
        border-left: 1px solid #e1e0d9;
        background: #ffffff;
    }}

    .details-header {{
        position: sticky;
        top: 0;
        z-index: 5;
        padding: 17px 18px 13px 18px;
        border-bottom: 1px solid #ecebe5;
        background: rgba(255, 255, 255, 0.96);
        backdrop-filter: blur(5px);
    }}

    .details-header h2 {{
        margin: 0;
        font-size: 17px;
        color: #343330;
    }}

    #details-content {{
        padding: 15px 18px 24px 18px;
    }}

    .empty-details {{
        color: #77756f;
        font-size: 13px;
        line-height: 1.55;
    }}

    .detail-title {{
        margin: 0 0 4px 0;
        color: #252421;
        font-size: 18px;
        line-height: 1.25;
        overflow-wrap: anywhere;
    }}

    .detail-badge {{
        display: inline-block;
        margin-bottom: 15px;
        padding: 4px 8px;
        border-radius: 99px;
        color: #ffffff;
        font-size: 11px;
        font-weight: 600;
    }}

    .detail-grid {{
        display: grid;
        grid-template-columns: 1fr;
        gap: 0;
        margin: 0;
    }}

    .detail-row {{
        padding: 8px 0;
        border-bottom: 1px solid #efeee9;
    }}

    .detail-label {{
        margin-bottom: 3px;
        color: #77756f;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }}

    .detail-value {{
        color: #343330;
        font-size: 13px;
        line-height: 1.4;
        overflow-wrap: anywhere;
    }}

    .relations-title {{
        margin: 20px 0 8px 0;
        color: #343330;
        font-size: 14px;
    }}

    .relation {{
        margin-bottom: 8px;
        padding: 9px 10px;
        border: 1px solid #e5e4de;
        border-left-width: 4px;
        border-radius: 5px;
        background: #fafaf8;
        font-size: 12px;
        line-height: 1.4;
    }}

    .relation-name {{
        color: #343330;
        font-weight: 600;
        overflow-wrap: anywhere;
    }}

    .relation-role {{
        margin-top: 2px;
        color: #6e6c66;
    }}

    .link {{
        stroke-opacity: 0.82;
        transition:
            stroke-opacity 120ms ease,
            stroke-width 120ms ease;
    }}

    .link.dimmed {{
        stroke-opacity: 0.08;
    }}

    .link.highlighted {{
        stroke-opacity: 1;
    }}

    .edge-label {{
        fill: #63615c;
        font-size: 8px;
        font-weight: 500;
        paint-order: stroke;
        stroke: #fcfcfb;
        stroke-width: 4px;
        stroke-linejoin: round;
        pointer-events: none;
        text-anchor: middle;
        transition: opacity 120ms ease;
    }}

    .edge-label.dimmed {{
        opacity: 0.05;
    }}

    .edge-label.highlighted {{
        opacity: 1;
        font-weight: 700;
    }}

    .node {{
        cursor: pointer;
        transition: opacity 120ms ease;
    }}

    .node.dimmed {{
        opacity: 0.12;
    }}

    .node-shape {{
        stroke-width: 2.2px;
        filter: drop-shadow(0 1px 1px rgba(0, 0, 0, 0.12));
    }}

    .node.selected .node-shape {{
        stroke: #111111 !important;
        stroke-width: 4px;
    }}

    .node-label {{
        fill: #343330;
        font-size: 9px;
        font-weight: 500;
        paint-order: stroke;
        stroke: #fcfcfb;
        stroke-width: 3px;
        stroke-linejoin: round;
        pointer-events: none;
        transition: opacity 120ms ease;
    }}

    .node-label.seed-label {{
        font-size: 11px;
        font-weight: 700;
    }}

    .node-label.dimmed {{
        opacity: 0.08;
    }}

    .toolbar {{
        position: absolute;
        z-index: 10;
        top: 10px;
        left: 10px;
        display: flex;
        gap: 6px;
    }}

    .toolbar button {{
        min-width: 32px;
        height: 31px;
        padding: 0 9px;
        border: 1px solid #d9d8d1;
        border-radius: 5px;
        background: rgba(255, 255, 255, 0.94);
        color: #4b4944;
        font-size: 12px;
        cursor: pointer;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.07);
    }}

    .toolbar button:hover {{
        background: #f2f1ec;
    }}


    .status {{
        position: absolute;
        z-index: 10;
        bottom: 10px;
        left: 10px;
        padding: 5px 8px;
        border: 1px solid #e0dfd8;
        border-radius: 5px;
        background: rgba(255, 255, 255, 0.9);
        color: #77756f;
        font-size: 10px;
        pointer-events: none;
    }}

    .tooltip {{
        position: absolute;
        z-index: 30;
        display: none;
        max-width: 330px;
        padding: 8px 10px;
        border: 1px solid #d7d6cf;
        border-radius: 6px;
        background: rgba(35, 34, 32, 0.94);
        color: #ffffff;
        font-size: 11px;
        line-height: 1.45;
        white-space: pre-line;
        pointer-events: none;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.18);
    }}

    @media (max-width: 800px) {{
        #app {{
            grid-template-columns: 1fr;
            grid-template-rows: 510px 250px;
        }}

        #details {{
            border-top: 1px solid #e1e0d9;
            border-left: none;
        }}
    }}
</style>
</head>

<body>

<div id="app">

    <div id="graph-area">

        <div class="toolbar">
            <button id="zoom-in" title="Aumentar zoom">+</button>
            <button id="zoom-out" title="Reduzir zoom">−</button>
            <button id="fit" title="Ajustar o grafo à tela">Ajustar</button>
            <button id="clear-selection" title="Limpar seleção">Limpar</button>
            <button id="export-svg" title="Baixar o grafo como SVG">SVG</button>
            <button id="export-png" title="Baixar o grafo como PNG">PNG</button>
        </div>

        <svg id="graph"></svg>

        <div class="status">
            Arraste os nós · use a roda do mouse para zoom · clique para detalhar
        </div>

        <div id="tooltip" class="tooltip"></div>

    </div>

    <aside id="details">

        <div class="details-header">
            <h2>Detalhes do nó</h2>
        </div>

        <div id="details-content">
            <div class="empty-details">
                Clique em uma empresa, pessoa, representante ou filial para
                visualizar os dados disponíveis e os vínculos relacionados.
            </div>
        </div>

    </aside>

</div>

<script>
(function () {{
    "use strict";

    var nodes = {nodes_json};
    var links = {edges_json};

    var physicsEnabled = {fisica_js};
    var showEdgeLabels = {rotulos_vinculos_js};
    var showNodeLabels = {rotulos_nos_js};
    var spacing = {int(espacamento)};

    var svg = d3.select("#graph");
    var graphArea = document.getElementById("graph-area");

    var width = Math.max(
        graphArea.clientWidth || 900,
        500
    );

    var height = Math.max(
        graphArea.clientHeight || 760,
        500
    );

    svg.attr("viewBox", "0 0 " + width + " " + height);

    var root = svg.append("g")
        .attr("class", "graph-root");

    var linkLayer = root.append("g")
        .attr("class", "link-layer");

    var edgeLabelLayer = root.append("g")
        .attr("class", "edge-label-layer");

    var nodeLayer = root.append("g")
        .attr("class", "node-layer");

    var nodeLabelLayer = root.append("g")
        .attr("class", "node-label-layer");

    var tooltip = d3.select("#tooltip");
    var detailsContent = document.getElementById("details-content");

    var zoom = d3.zoom()
        .scaleExtent([0.15, 7])
        .on("zoom", function (event) {{
            root.attr("transform", event.transform);
        }});

    svg.call(zoom);

    function nodeRadius(d) {{
        return Math.max(7, Math.sqrt(d.size || 100));
    }}

    function symbolType(d) {{
        if (d.shape === "star" && d3.symbolStar) {{
            return d3.symbolStar;
        }}

        if (d.shape === "square") {{
            return d3.symbolSquare;
        }}

        if (d.shape === "diamond" && d3.symbolDiamond) {{
            return d3.symbolDiamond;
        }}

        if (d.shape === "triangle") {{
            return d3.symbolTriangle;
        }}

        return d3.symbolCircle;
    }}

    var link = linkLayer
        .selectAll("line")
        .data(links)
        .enter()
        .append("line")
        .attr("class", "link")
        .attr("stroke", function (d) {{
            return d.color || "#c3c2b7";
        }})
        .attr("stroke-width", function (d) {{
            return d.width || 1.7;
        }});

    var edgeLabel = edgeLabelLayer
        .selectAll("text")
        .data(links)
        .enter()
        .append("text")
        .attr("class", "edge-label")
        .style("display", showEdgeLabels ? null : "none")
        .text(function (d) {{
            return d.label || "";
        }});

    var node = nodeLayer
        .selectAll("g.node")
        .data(nodes)
        .enter()
        .append("g")
        .attr("class", "node")
        .on("click", function (event, d) {{
            event.stopPropagation();
            selectNode(d);
        }})
        .on("mouseover", function (event, d) {{
            tooltip
                .style("display", "block")
                .text(d.title || d.label || "");
        }})
        .on("mousemove", function (event) {{
            var areaRect = graphArea.getBoundingClientRect();

            tooltip
                .style(
                    "left",
                    (event.clientX - areaRect.left + 13) + "px"
                )
                .style(
                    "top",
                    (event.clientY - areaRect.top + 13) + "px"
                );
        }})
        .on("mouseout", function () {{
            tooltip.style("display", "none");
        }});

    node.append("path")
        .attr("class", "node-shape")
        .attr("d", function (d) {{
            return d3.symbol()
                .type(symbolType(d))
                .size(d.size || 100)();
        }})
        .attr("fill", function (d) {{
            return d.color || "#999999";
        }})
        .attr("stroke", function (d) {{
            return d.borderColor || d.color || "#666666";
        }});

    var nodeLabel = nodeLabelLayer
        .selectAll("text")
        .data(nodes)
        .enter()
        .append("text")
        .attr("class", function (d) {{
            return d.seed
                ? "node-label seed-label"
                : "node-label";
        }})
        .style("display", showNodeLabels ? null : "none")
        .attr("dx", function (d) {{
            return nodeRadius(d) + 5;
        }})
        .attr("dy", 3)
        .text(function (d) {{
            return d.label || "";
        }});

    function escapeHtml(value) {{
        if (value === null || value === undefined) {{
            return "";
        }}

        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }}

    function hasValue(value) {{
        return (
            value !== null &&
            value !== undefined &&
            String(value).trim() !== ""
        );
    }}

    function detailRow(label, value) {{
        if (!hasValue(value)) {{
            return "";
        }}

        return (
            '<div class="detail-row">' +
                '<div class="detail-label">' +
                    escapeHtml(label) +
                '</div>' +
                '<div class="detail-value">' +
                    escapeHtml(value) +
                '</div>' +
            '</div>'
        );
    }}

    function nodeById(id) {{
        for (var i = 0; i < nodes.length; i += 1) {{
            if (String(nodes[i].id) === String(id)) {{
                return nodes[i];
            }}
        }}

        return null;
    }}

    function endpointId(endpoint) {{
        if (
            endpoint !== null &&
            typeof endpoint === "object"
        ) {{
            return String(endpoint.id);
        }}

        return String(endpoint);
    }}

    function connectedLinks(nodeId) {{
        return links.filter(function (edge) {{
            return (
                endpointId(edge.source) === String(nodeId) ||
                endpointId(edge.target) === String(nodeId)
            );
        }});
    }}

    function displayName(d) {{
        return (
            d.razao ||
            d.nome ||
            d.cnpj ||
            d.cnpjMatriz ||
            d.basico ||
            d.label ||
            d.id
        );
    }}

    function showDetails(d) {{
        var title = displayName(d);
        var html = "";

        html +=
            '<h3 class="detail-title">' +
                escapeHtml(title) +
            '</h3>';

        html +=
            '<span class="detail-badge" style="background:' +
                escapeHtml(d.color || "#777777") +
            ';">' +
                escapeHtml(d.tipoLabel || d.tipo || "Nó") +
            '</span>';

        html += '<div class="detail-grid">';

        if (d.tipo === "empresa") {{
            html += detailRow(
                "CNPJ básico",
                d.basico
            );

            html += detailRow(
                "CNPJ da matriz",
                d.cnpjMatriz
            );

            html += detailRow(
                "Situação cadastral",
                d.situacao
            );

            html += detailRow(
                "Município",
                d.municipio
            );

            html += detailRow(
                "UF",
                d.uf
            );

            html += detailRow(
                "CNAE principal",
                d.cnae
            );

            html += detailRow(
                "Natureza jurídica",
                d.natureza
            );

            html += detailRow(
                "Porte",
                d.porte
            );

            html += detailRow(
                "Estabelecimentos",
                d.nEstab
            );

            html += detailRow(
                "Estabelecimentos ativos",
                d.nAtivos
            );
        }}
        else if (d.tipo === "filial") {{
            html += detailRow(
                "CNPJ",
                d.cnpj
            );

            html += detailRow(
                "Situação cadastral",
                d.situacao
            );

            html += detailRow(
                "Município",
                d.municipio
            );

            html += detailRow(
                "UF",
                d.uf
            );
        }}
        else if (d.tipo === "ie") {{
            html += detailRow("Inscrição Estadual", d.inscricao);
            html += detailRow("Categoria", d.categoria);
            html += detailRow("CNAE", d.cnae);
            html += detailRow("Data de abertura", d.dataAbertura);
            html += detailRow("CNPJ vinculado", d.cnpj);
        }}
        else if (d.tipo === "produtor") {{
            html += detailRow("CPF (aberto na SEFAZ)", d.cpf);
            html += detailRow("Origem", "Base de produtores rurais SEFAZ-RS");
        }}
        else {{
            html += detailRow(
                "Nome",
                d.nome
            );

            html += detailRow(
                "CPF/CNPJ mascarado",
                d.cpf
            );

            html += detailRow(
                "Faixa etária",
                d.faixa
            );

            html += detailRow(
                "País",
                d.pais
            );

            if (d.representante) {{
                html += detailRow(
                    "Representante legal",
                    "Sim"
                );
            }}

            if (hasValue(d.hub)) {{
                html += detailRow(
                    "Participações como hub",
                    d.hub + " empresas"
                );
            }}
        }}

        html += "</div>";

        var relations = connectedLinks(d.id);

        html +=
            '<h4 class="relations-title">' +
                'Vínculos no recorte (' +
                relations.length +
                ')' +
            '</h4>';

        if (relations.length === 0) {{
            html +=
                '<div class="empty-details">' +
                    'Nenhum vínculo disponível no recorte atual.' +
                '</div>';
        }}
        else {{
            relations.forEach(function (edge) {{
                var sourceId = endpointId(edge.source);
                var targetId = endpointId(edge.target);

                var otherId = (
                    sourceId === String(d.id)
                    ? targetId
                    : sourceId
                );

                var otherNode = nodeById(otherId);
                var otherName = (
                    otherNode
                    ? displayName(otherNode)
                    : otherId
                );

                html +=
                    '<div class="relation" style="border-left-color:' +
                        escapeHtml(edge.color || "#999999") +
                    ';">' +
                        '<div class="relation-name">' +
                            escapeHtml(otherName) +
                        '</div>' +
                        '<div class="relation-role">' +
                            escapeHtml(edge.label || edge.papel || "Vínculo") +
                            (
                                hasValue(edge.desde)
                                ? " · desde " + escapeHtml(edge.desde)
                                : ""
                            ) +
                        '</div>' +
                    '</div>';
            }});
        }}

        // pro CNPJ de empresa, deixa fácil copiar e colar na busca
        if (d.tipo === "empresa" && hasValue(d.basico)) {{
            html +=
                '<div style="margin-top:12px; font-size:12px; color:#77756f;">' +
                    'Pra recentralizar: copie o CNPJ acima e cole no campo de busca.' +
                '</div>';
        }}

        detailsContent.innerHTML = html;
    }}

    // --- caminho até o nó raiz (empresa pesquisada) -----------------------
    var seedIdGlobal = (function () {{
        for (var i = 0; i < nodes.length; i += 1) {{
            if (nodes[i].seed) {{
                return String(nodes[i].id);
            }}
        }}
        return null;
    }})();

    var adjacency = (function () {{
        var mapa = {{}};
        links.forEach(function (edge) {{
            var a = endpointId(edge.source);
            var b = endpointId(edge.target);
            (mapa[a] = mapa[a] || []).push(b);
            (mapa[b] = mapa[b] || []).push(a);
        }});
        return mapa;
    }})();

    function caminhoAteRaiz(fromId) {{
        // BFS do nó clicado até a raiz; devolve lista de ids do caminho
        // mais curto (inclui as pontas) ou null se não há caminho.
        if (!seedIdGlobal) {{
            return null;
        }}
        if (fromId === seedIdGlobal) {{
            return [fromId];
        }}
        var prev = {{}};
        var visitado = {{}};
        visitado[fromId] = true;
        var fila = [fromId];
        while (fila.length) {{
            var atual = fila.shift();
            if (atual === seedIdGlobal) {{
                break;
            }}
            (adjacency[atual] || []).forEach(function (vizinho) {{
                if (!visitado[vizinho]) {{
                    visitado[vizinho] = true;
                    prev[vizinho] = atual;
                    fila.push(vizinho);
                }}
            }});
        }}
        if (!visitado[seedIdGlobal]) {{
            return null;
        }}
        var caminho = [seedIdGlobal];
        while (caminho[caminho.length - 1] !== fromId) {{
            caminho.push(prev[caminho[caminho.length - 1]]);
        }}
        return caminho;
    }}

    function selectNode(d) {{
        var selectedId = String(d.id);
        var neighborIds = {{}};

        neighborIds[selectedId] = true;

        links.forEach(function (edge) {{
            var sourceId = endpointId(edge.source);
            var targetId = endpointId(edge.target);

            if (sourceId === selectedId) {{
                neighborIds[targetId] = true;
            }}

            if (targetId === selectedId) {{
                neighborIds[sourceId] = true;
            }}
        }});

        // caminho do nó clicado até a raiz: nós entram no conjunto aceso e
        // as arestas consecutivas do caminho ganham destaque
        var pathEdges = {{}};
        var caminho = caminhoAteRaiz(selectedId);
        if (caminho) {{
            caminho.forEach(function (id) {{
                neighborIds[id] = true;
            }});
            for (var i = 0; i < caminho.length - 1; i += 1) {{
                pathEdges[caminho[i] + "|" + caminho[i + 1]] = true;
                pathEdges[caminho[i + 1] + "|" + caminho[i]] = true;
            }}
        }}

        function arestaAcesa(edge) {{
            var s = endpointId(edge.source);
            var t = endpointId(edge.target);
            return (
                s === selectedId ||
                t === selectedId ||
                pathEdges[s + "|" + t] === true
            );
        }}

        node
            .classed("selected", function (n) {{
                return String(n.id) === selectedId;
            }})
            .classed("dimmed", function (n) {{
                return !neighborIds[String(n.id)];
            }});

        nodeLabel
            .classed("dimmed", function (n) {{
                return !neighborIds[String(n.id)];
            }});

        link
            .classed("highlighted", arestaAcesa)
            .classed("dimmed", function (edge) {{
                return !arestaAcesa(edge);
            }});

        edgeLabel
            .classed("highlighted", arestaAcesa)
            .classed("dimmed", function (edge) {{
                return !arestaAcesa(edge);
            }});

        showDetails(d);
    }}

    function clearSelection() {{
        node
            .classed("selected", false)
            .classed("dimmed", false);

        nodeLabel.classed("dimmed", false);

        link
            .classed("highlighted", false)
            .classed("dimmed", false);

        edgeLabel
            .classed("highlighted", false)
            .classed("dimmed", false);

        detailsContent.innerHTML =
            '<div class="empty-details">' +
                'Clique em uma empresa, pessoa, representante ou filial ' +
                'para visualizar os dados disponíveis e os vínculos ' +
                'relacionados.' +
            '</div>';
    }}

    svg.on("click", function (event) {{
        if (event.target === svg.node()) {{
            clearSelection();
        }}
    }});

    var linkForce = d3.forceLink()
        .id(function (d) {{
            return d.id;
        }})
        .distance(function () {{
            return spacing;
        }})
        .strength(0.45);

    var simulation = d3.forceSimulation(nodes);

    if (physicsEnabled) {{
        simulation
            .force("link", linkForce.links(links))
            .force(
                "charge",
                d3.forceManyBody()
                    .strength(function (d) {{
                        return d.seed ? -1800 : -900;
                    }})
                    .distanceMin(20)
                    .distanceMax(1200)
            )
            .force(
                "center",
                d3.forceCenter(
                    width / 2,
                    height / 2
                )
            )
            .force(
                "collision",
                d3.forceCollide()
                    .radius(function (d) {{
                        return nodeRadius(d) + 20;
                    }})
                    .strength(0.9)
                    .iterations(2)
            )
            .velocityDecay(0.30)
            .alphaDecay(0.025);
    }}
    else {{
        var radius = Math.max(
            120,
            Math.min(width, height) * 0.34
        );

        nodes.forEach(function (d, index) {{
            var angle =
                (2 * Math.PI * index) /
                Math.max(nodes.length, 1);

            d.x = width / 2 + radius * Math.cos(angle);
            d.y = height / 2 + radius * Math.sin(angle);
            d.fx = d.x;
            d.fy = d.y;
        }});

        simulation.stop();
    }}

    function dragStarted(event, d) {{
        if (
            physicsEnabled &&
            !event.active
        ) {{
            simulation
                .alphaTarget(0.25)
                .restart();
        }}

        d.fx = d.x;
        d.fy = d.y;
    }}

    function dragged(event, d) {{
        d.fx = event.x;
        d.fy = event.y;

        if (!physicsEnabled) {{
            d.x = event.x;
            d.y = event.y;
            render();
        }}
    }}

    function dragEnded(event, d) {{
        if (physicsEnabled) {{
            if (!event.active) {{
                simulation.alphaTarget(0);
            }}

            d.fx = null;
            d.fy = null;
        }}
    }}

    node.call(
        d3.drag()
            .on("start", dragStarted)
            .on("drag", dragged)
            .on("end", dragEnded)
    );

    function render() {{
        link
            .attr("x1", function (d) {{
                return d.source.x;
            }})
            .attr("y1", function (d) {{
                return d.source.y;
            }})
            .attr("x2", function (d) {{
                return d.target.x;
            }})
            .attr("y2", function (d) {{
                return d.target.y;
            }});

        node.attr("transform", function (d) {{
            return "translate(" + d.x + "," + d.y + ")";
        }});

        nodeLabel
            .attr("x", function (d) {{
                return d.x;
            }})
            .attr("y", function (d) {{
                return d.y;
            }});

        edgeLabel
            .attr("x", function (d) {{
                return (
                    d.source.x +
                    d.target.x
                ) / 2;
            }})
            .attr("y", function (d) {{
                return (
                    d.source.y +
                    d.target.y
                ) / 2 - 4;
            }});
    }}

    simulation.on("tick", render);

    if (!physicsEnabled) {{
        linkForce.links(links);
        render();
    }}

    function fitGraph() {{
        if (!nodes.length) {{
            return;
        }}

        var xs = nodes.map(function (d) {{
            return d.x || width / 2;
        }});

        var ys = nodes.map(function (d) {{
            return d.y || height / 2;
        }});

        var minX = d3.min(xs);
        var maxX = d3.max(xs);
        var minY = d3.min(ys);
        var maxY = d3.max(ys);

        var graphWidth = Math.max(maxX - minX, 100);
        var graphHeight = Math.max(maxY - minY, 100);

        var scale = Math.min(
            1.6,
            0.86 / Math.max(
                graphWidth / width,
                graphHeight / height
            )
        );

        var translateX =
            width / 2 -
            scale * (minX + maxX) / 2;

        var translateY =
            height / 2 -
            scale * (minY + maxY) / 2;

        svg.transition()
            .duration(450)
            .call(
                zoom.transform,
                d3.zoomIdentity
                    .translate(translateX, translateY)
                    .scale(scale)
            );
    }}

    document
        .getElementById("zoom-in")
        .addEventListener("click", function () {{
            svg.transition()
                .duration(220)
                .call(zoom.scaleBy, 1.3);
        }});

    document
        .getElementById("zoom-out")
        .addEventListener("click", function () {{
            svg.transition()
                .duration(220)
                .call(zoom.scaleBy, 0.77);
        }});

    document
        .getElementById("fit")
        .addEventListener("click", fitGraph);

    document
        .getElementById("clear-selection")
        .addEventListener("click", clearSelection);

    // ---- export SVG / PNG --------------------------------------------
    function svgSerializado() {{
        var clone = svg.node().cloneNode(true);
        clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
        clone.setAttribute("width", width);
        clone.setAttribute("height", height);
        clone.style.background = "#fcfcfb";
        // estilos relevantes inline (o SVG exportado nao leva o <style> da pagina)
        var css = ".link{{stroke-opacity:.82}}" +
                  ".node-shape{{stroke-width:2.2px}}" +
                  ".node-label{{fill:#343330;font-size:9px;font-family:sans-serif;" +
                  "paint-order:stroke;stroke:#fcfcfb;stroke-width:3px}}" +
                  ".node-label.seed-label{{font-size:11px;font-weight:700}}" +
                  ".edge-label{{fill:#63615c;font-size:8px;font-family:sans-serif;" +
                  "paint-order:stroke;stroke:#fcfcfb;stroke-width:4px;text-anchor:middle}}";
        var style = document.createElementNS("http://www.w3.org/2000/svg", "style");
        style.textContent = css;
        clone.insertBefore(style, clone.firstChild);
        return new XMLSerializer().serializeToString(clone);
    }}

    function baixar(blob, nome) {{
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = nome;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(a.href);
    }}

    document
        .getElementById("export-svg")
        .addEventListener("click", function () {{
            baixar(
                new Blob([svgSerializado()], {{type: "image/svg+xml"}}),
                "grafo_vinculos.svg"
            );
        }});

    document
        .getElementById("export-png")
        .addEventListener("click", function () {{
            var img = new Image();
            var url = URL.createObjectURL(
                new Blob([svgSerializado()], {{type: "image/svg+xml"}})
            );
            img.onload = function () {{
                var escala = 2;  // PNG em 2x pra nitidez
                var canvas = document.createElement("canvas");
                canvas.width = width * escala;
                canvas.height = height * escala;
                var ctx = canvas.getContext("2d");
                ctx.fillStyle = "#fcfcfb";
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.scale(escala, escala);
                ctx.drawImage(img, 0, 0);
                URL.revokeObjectURL(url);
                canvas.toBlob(function (blob) {{
                    baixar(blob, "grafo_vinculos.png");
                }}, "image/png");
            }};
            img.src = url;
        }});

    if (physicsEnabled) {{
        window.setTimeout(fitGraph, 1800);
    }}
    else {{
        window.setTimeout(fitGraph, 120);
    }}
}})();
</script>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Renderização
# ---------------------------------------------------------------------------

with tab_rede:
    with st.spinner("Montando o grafo..."):
        components.html(
            graph_html,
            height=780,
            scrolling=False,
        )

    st.caption(
        "Arraste os nós, use a roda do mouse para zoom e clique em um nó "
        "para visualizar os detalhes e destacar os vínculos relacionados."
    )


# ---------------------------------------------------------------------------
# Mapa de capilaridade (montado depois da rede — ver nota acima dos tabs)
# ---------------------------------------------------------------------------

with tab_mapa:
    with st.spinner("Montando o mapa..."):
        renderizar_mapa_capilaridade(g, basico, filtro_situacao)


# ---------------------------------------------------------------------------
# Tabela e download
# ---------------------------------------------------------------------------

df = grafo.para_dataframe(g)

with st.expander(f"Vínculos em tabela ({len(df)})"):
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
    )

    csv = df.to_csv(
        index=False,
        sep=";",
        encoding="utf-8-sig",
    )

    st.download_button(
        "Baixar CSV do recorte atual",
        data=csv,
        file_name=f"vinculos_{basico}.csv",
        mime="text/csv",
    )
