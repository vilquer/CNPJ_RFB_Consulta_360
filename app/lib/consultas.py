"""Queries do app. Todas cacheadas por parâmetro (st.cache_data) — a primeira
execução agrega dezenas de milhões de linhas (segundos), as demais são
instantâneas. Espelham as queries validadas nos notebooks de EDA.

Todo acesso ao banco via db.query()/db.query_um() (serializado — ver db.py)."""

import json

import pandas as pd
import streamlit as st

from lib import db

UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
       "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
       "SE", "SP", "TO"]


def _filtro_uf(uf: str | None) -> str:
    return f"AND uf = '{uf}'" if uf else ""


# ---------- metadados ----------

@st.cache_data
def safra_atual() -> str:
    """Safra apontada pelas views do rfb.duckdb (ex: '2026-07')."""
    try:
        return db.query_um("SELECT safra FROM safra_atual")[0]
    except Exception:
        return "?"  # banco antigo, sem a view safra_atual — rodar criar_views.py


# ---------- panorama ----------

@st.cache_data
def resumo_geral() -> dict:
    est = db.query_um("""
        SELECT count(*) AS total,
               count(*) FILTER (situacao = 'ativa') AS ativos
        FROM estabelecimentos_completos
    """)
    emp = db.query_um("SELECT count(*) FROM empresas")[0]
    mei = db.query_um("SELECT count(*) FROM simples WHERE opcao_mei = 'S'")[0]
    return {"estabelecimentos": est[0], "ativos": est[1], "empresas": emp, "meis": mei}


@st.cache_data
def situacao_cadastral(uf: str | None = None) -> pd.DataFrame:
    return db.query(f"""
        SELECT COALESCE(situacao, 'sem informacao') AS situacao, count(*) AS n
        FROM estabelecimentos_completos
        WHERE 1=1 {_filtro_uf(uf)}
        GROUP BY 1 ORDER BY n DESC
    """)


@st.cache_data
def ativos_por_uf() -> pd.DataFrame:
    return db.query("""
        SELECT uf, count(*) AS ativos
        FROM estabelecimentos_completos
        WHERE situacao = 'ativa' AND uf IS NOT NULL
        GROUP BY uf ORDER BY ativos DESC
    """)


@st.cache_data
def top_cnaes(uf: str | None = None, limite: int = 15) -> pd.DataFrame:
    return db.query(f"""
        SELECT cnae_principal, count(*) AS ativos
        FROM estabelecimentos_completos
        WHERE situacao = 'ativa' AND cnae_principal IS NOT NULL {_filtro_uf(uf)}
        GROUP BY 1 ORDER BY ativos DESC LIMIT {limite}
    """)


# ---------- consulta pontual ----------

@st.cache_data
def ficha(cnpj: str) -> pd.DataFrame:
    return db.query("SELECT * FROM ficha_cnpj(?)", [cnpj])


@st.cache_data
def socios(cnpj: str) -> pd.DataFrame:
    return db.query("SELECT * FROM socios_cnpj(?)", [cnpj])


@st.cache_data
def busca_nome(termo: str, limite: int = 100) -> pd.DataFrame:
    # ORDER BY aqui fora: ordenacao dentro da macro e descartada pelo
    # otimizador quando consumida como subquery. Relevancia: match exato do
    # termo (fantasia ou razao) > matriz > ativa > nome mais curto.
    return db.query(f"""
        SELECT * FROM busca_nome($termo)
        ORDER BY (upper(coalesce(nome_fantasia, '')) = upper(trim($termo))
                  OR upper(razao_social) = upper(trim($termo))) DESC,
                 (matriz_filial = 'matriz') DESC,
                 (situacao = 'ativa') DESC,
                 length(razao_social) ASC
        LIMIT {limite}
    """, {"termo": termo})


@st.cache_data
def estabelecimentos_da_empresa(cnpj_basico: str) -> pd.DataFrame:
    return db.query("""
        SELECT cnpj, matriz_filial, situacao, uf, municipio, cnae_principal
        FROM estabelecimentos_completos WHERE cnpj_basico = ?
        ORDER BY matriz_filial, uf
    """, [cnpj_basico])


# ---------- integração IE (SEFAZ-RS, opcional) ----------

def tem_ie() -> bool:
    """True se a tabela ie_rs foi importada (scripts/importar_ie.py).
    SEM cache de propósito: cachear False antes da primeira importação
    esconderia o toggle/seção até alguém limpar o cache na mão."""
    try:
        db.query_um("SELECT 1 FROM ie_rs LIMIT 1")
        return True
    except Exception:
        return False


@st.cache_data
def ies_do_cnpj(cnpj_basico: str) -> pd.DataFrame:
    """Inscrições Estaduais (RS) dos estabelecimentos da empresa."""
    return db.query("""
        SELECT ie.inscricao, ie.categoria, ie.tipo, ie.data_abertura,
               ie.cnae_1, ie.cnpj14 AS cnpj
        FROM ie_rs ie
        WHERE ie.cnpj14 IS NOT NULL AND ie.cnpj14[1:8] = ?
        ORDER BY ie.inscricao
    """, [cnpj_basico])


# ---------- recorte UF / regiões ----------

@st.cache_data
def resumo_uf(uf: str) -> dict:
    r = db.query_um(f"""
        SELECT count(*) AS total,
               count(*) FILTER (situacao = 'ativa') AS ativos,
               count(DISTINCT municipio) AS municipios
        FROM estabelecimentos_completos WHERE uf = '{uf}'
    """)
    return {"total": r[0], "ativos": r[1], "municipios": r[2]}


@st.cache_data
def regioes_da_uf(uf: str) -> pd.DataFrame:
    return db.query(f"""
        SELECT regiao_intermediaria,
               count(*) AS estabelecimentos,
               count(*) FILTER (situacao = 'ativa') AS ativos,
               count(DISTINCT municipio) AS municipios
        FROM estabelecimentos_regioes
        WHERE uf = '{uf}' AND regiao_intermediaria IS NOT NULL
        GROUP BY 1 ORDER BY ativos DESC
    """)


@st.cache_data
def ativos_por_municipio_ibge(uf: str) -> pd.DataFrame:
    """Ativos por município da UF com o código IBGE de 7 dígitos (join com
    apoio/ibge_regioes_br.csv) — alimenta o mapa coroplético."""
    from lib.db import CSV_REGIOES
    return db.query(f"""
        SELECT r.codigo_ibge::VARCHAR AS codigo_ibge,
               r.municipio_ibge AS municipio,
               count(*) AS ativos
        FROM estabelecimentos_completos ec
        JOIN read_csv('{CSV_REGIOES.as_posix()}', header=true) r
          ON ec.uf = r.uf
         AND regexp_replace(
                 replace(replace(strip_accents(upper(ec.municipio)), '''', ''), '-', ' '),
                 ' +', ' ', 'g') = r.municipio_norm
        WHERE ec.uf = '{uf}' AND ec.situacao = 'ativa'
        GROUP BY 1, 2
    """)


def _area_assinada(anel: list) -> float:
    """Shoelace em lon/lat (aproximação planar — só o sinal importa)."""
    soma = 0.0
    for i in range(len(anel) - 1):
        x1, y1 = anel[i][0], anel[i][1]
        x2, y2 = anel[i + 1][0], anel[i + 1][1]
        soma += (x2 - x1) * (y2 + y1)
    return -soma  # >0 = anti-horário (CCW)


def _orientar_poligono(aneis: list) -> None:
    """Plotly renderiza traces 'geo' com d3-geo, que usa winding ESFÉRICO:
    anel externo HORÁRIO (CW), buracos anti-horário — o CONTRÁRIO da RFC
    7946. A malha do IBGE vem RFC-compliant (CCW), e polígono CCW no d3-geo
    vira 'o mundo inteiro menos o município' = retângulo azul sólido."""
    for i, anel in enumerate(aneis):
        area = _area_assinada(anel)  # >0 = anti-horário (CCW)
        if (i == 0 and area > 0) or (i > 0 and area < 0):
            anel.reverse()


MALHA_MUNICIPIOS_PATH = db.BASE_DIR / "apoio" / "malha_municipios_br.json"
MALHA_ESTADOS_PATH = db.BASE_DIR / "apoio" / "malha_estados_br.json"


def _carregar_malha_local(caminho) -> dict | None:
    """GeoJSON de malha do IBGE lido de apoio/ — o app não acessa internet
    em tempo de uso; os arquivos são baixados uma vez por
    scripts/baixar_malha_ibge.py. Orientação dos anéis corrigida pro
    winding esférico do d3-geo/Plotly (ver _orientar_poligono; idempotente,
    então tanto faz se o arquivo já veio corrigido ou não)."""
    if not caminho.exists():
        return None
    with open(caminho, encoding="utf-8") as f:
        gj = json.load(f)
    for feat in gj.get("features", []):
        geom = feat.get("geometry", {})
        if geom.get("type") == "Polygon":
            _orientar_poligono(geom["coordinates"])
        elif geom.get("type") == "MultiPolygon":
            for poligono in geom["coordinates"]:
                _orientar_poligono(poligono)
    return gj


@st.cache_data
def _codigos_da_uf(uf: str) -> set[str]:
    df = pd.read_csv(db.CSV_REGIOES, dtype={"codigo_ibge": str})
    return set(df.loc[df["uf"] == uf, "codigo_ibge"])


@st.cache_data
def malha_municipal(uf: str) -> dict | None:
    """Malha municipal da UF, recortada da malha nacional local (mesma
    fonte/qualidade que antes vinha do endpoint /malhas/estados/{uf} — não
    precisa de fetch por UF)."""
    nacional = malha_nacional()
    if nacional is None:
        return None
    codigos = _codigos_da_uf(uf)
    feats = [f for f in nacional["features"]
             if f.get("properties", {}).get("codarea") in codigos]
    return {"type": "FeatureCollection", "features": feats}


def bbox_dos_codigos(malha: dict, codigos: set[str]) -> tuple[float, float, float, float] | None:
    """Bounding box (min_lon, max_lon, min_lat, max_lat) das features cujo
    codarea está em `codigos`. Usado pra zoom manual quando o mapa combina
    um trace de fundo (todos os municípios, pra sempre mostrar as
    fronteiras) com um trace de destaque (só o recorte) — com dois traces
    Choropleth no mesmo geo, `fitbounds='locations'` enquadra a UNIÃO de
    ambos (sempre o Brasil inteiro), então o zoom no recorte precisa ser
    calculado à mão a partir da geometria."""
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    achou = False
    for feat in malha.get("features", []):
        if feat.get("properties", {}).get("codarea") not in codigos:
            continue
        geom = feat.get("geometry", {})
        poligonos = ([geom["coordinates"]] if geom.get("type") == "Polygon"
                    else geom.get("coordinates", []))
        for pol in poligonos:
            for anel in pol:
                for lon, lat in anel:
                    achou = True
                    min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
                    min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
    return (min_lon, max_lon, min_lat, max_lat) if achou else None


@st.cache_data
def malha_nacional() -> dict | None:
    """GeoJSON dos ~5.570 municípios do Brasil inteiro, lido de apoio/ local
    — usado no mapa de capilaridade do grafo e recortado por UF em
    malha_municipal(). ~3,4 MB, leitura local instantânea."""
    return _carregar_malha_local(MALHA_MUNICIPIOS_PATH)


def _centroide_anel(anel: list) -> tuple[float, float, float]:
    """Centroide (cx, cy) de um anel fechado (lon, lat) pela fórmula do
    polígono (momento de área), e a área ABSOLUTA como peso — não depende
    do sentido do anel (CW/CCW): inverter a ordem dos pontos troca o sinal
    de toda soma (numerador e área) igualmente, a razão não muda."""
    area = cx = cy = 0.0
    for i in range(len(anel) - 1):
        x1, y1 = anel[i][0], anel[i][1]
        x2, y2 = anel[i + 1][0], anel[i + 1][1]
        cruz = x1 * y2 - x2 * y1
        area += cruz
        cx += (x1 + x2) * cruz
        cy += (y1 + y2) * cruz
    area *= 0.5
    if area == 0:
        return anel[0][0], anel[0][1], 0.0
    return cx / (6 * area), cy / (6 * area), abs(area)


@st.cache_data(ttl=86400)
def centroides_municipios() -> dict:
    """Ponto central (lat, lon) de cada município, calculado a partir do
    próprio desenho das bordas na malha nacional do IBGE (não há base de
    lat/lon própria no projeto) — usado pra ancorar as bolinhas do mapa de
    capilaridade. Município com ilhas/partes separadas (MultiPolygon):
    centroide de cada parte ponderado pela área da parte."""
    malha = malha_nacional()
    if malha is None:
        return {}
    centros = {}
    for feat in malha.get("features", []):
        codigo = feat.get("properties", {}).get("codarea")
        if not codigo:
            continue
        geom = feat.get("geometry", {})
        poligonos = ([geom["coordinates"]] if geom.get("type") == "Polygon"
                    else geom.get("coordinates", []))
        soma_x = soma_y = soma_area = 0.0
        for pol in poligonos:
            if not pol:
                continue
            cx, cy, area = _centroide_anel(pol[0])  # só o anel externo
            soma_x += cx * area
            soma_y += cy * area
            soma_area += area
        if soma_area > 0:
            centros[codigo] = (soma_y / soma_area, soma_x / soma_area)
    return centros


@st.cache_data
def malha_estados() -> dict | None:
    """GeoJSON das 27 UFs (contorno estadual, não municipal), lido de
    apoio/ local — camada de referência mais escura sobre o fundo municipal
    claro no mapa de capilaridade."""
    return _carregar_malha_local(MALHA_ESTADOS_PATH)


@st.cache_data
def municipios_da_regiao(uf: str, regiao: str, limite: int = 15) -> pd.DataFrame:
    return db.query("""
        SELECT municipio, count(*) AS ativos
        FROM estabelecimentos_regioes
        WHERE uf = ? AND regiao_intermediaria = ? AND situacao = 'ativa'
        GROUP BY 1 ORDER BY ativos DESC LIMIT ?
    """, [uf, regiao, limite])


# ---------- dinâmica empresarial ----------

@st.cache_data
def aberturas_por_ano(uf: str | None = None) -> pd.DataFrame:
    if uf:
        return db.query(f"""
            WITH base AS (
                SELECT CAST(substr(data_inicio_atividade, 1, 4) AS INT) AS ano,
                       count(*) AS brasil,
                       count(*) FILTER (uf = '{uf}') AS na_uf
                FROM estabelecimentos
                WHERE length(data_inicio_atividade) = 8
                  AND substr(data_inicio_atividade, 1, 4) BETWEEN '1985' AND '2025'
                GROUP BY 1
            )
            SELECT ano, na_uf AS aberturas, na_uf * 100.0 / brasil AS fatia_pct
            FROM base ORDER BY ano
        """)
    return db.query("""
        SELECT CAST(substr(data_inicio_atividade, 1, 4) AS INT) AS ano,
               count(*) AS aberturas
        FROM estabelecimentos
        WHERE length(data_inicio_atividade) = 8
          AND substr(data_inicio_atividade, 1, 4) BETWEEN '1985' AND '2025'
        GROUP BY 1 ORDER BY 1
    """)


@st.cache_data
def idade_na_baixa(uf: str | None = None) -> pd.DataFrame:
    return db.query(f"""
        SELECT least(floor(datediff('year',
                   strptime(data_inicio_atividade, '%Y%m%d'),
                   strptime(data_situacao_cadastral, '%Y%m%d'))), 30) AS anos,
               count(*) AS n
        FROM estabelecimentos_completos
        WHERE situacao = 'baixada' {_filtro_uf(uf)}
          AND length(data_inicio_atividade) = 8 AND data_inicio_atividade > '19000101'
          AND length(data_situacao_cadastral) = 8
          AND data_situacao_cadastral > data_inicio_atividade
        GROUP BY 1 HAVING anos >= 0 ORDER BY 1
    """)


@st.cache_data
def idade_das_ativas(uf: str | None = None) -> pd.DataFrame:
    return db.query(f"""
        SELECT least(floor(datediff('year',
                   strptime(data_inicio_atividade, '%Y%m%d'),
                   (SELECT data_referencia FROM safra_atual))), 50) AS anos,
               count(*) AS n
        FROM estabelecimentos_completos
        WHERE situacao = 'ativa' {_filtro_uf(uf)}
          AND length(data_inicio_atividade) = 8 AND data_inicio_atividade > '19000101'
        GROUP BY 1 HAVING anos >= 0 ORDER BY 1
    """)
