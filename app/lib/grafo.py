"""Grafo de vínculos societários: empresas ↔ sócios (PF/PJ) ↔ representantes.

Mesma mecânica do projeto IE (BFS por níveis a partir de uma semente), mas
sobre o quadro societário da RFB (rfb.duckdb local). Identidade dos nós:
- empresa: cnpj_basico (8 dígitos) — filiais são atributo, não nó (opcional).
- sócio PF: (nome, cpf mascarado) — o CPF vem mascarado da RFB (***XXXXXX**),
  então nome+máscara é a melhor chave disponível; colisão é possível mas rara.
- sócio PJ: cnpj_basico da PJ (prefixo de cpf_cnpj_socio) — vira nó de empresa
  e pode ser expandido no nível seguinte.

NOTA: existe uma variante deste módulo adaptada pra Oracle (trabalho) em
grafo_oracle.py.bak — não é usada por este app.
"""

import re
from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

from lib import db

# Sócio ligado a mais empresas que isso não é expandido por padrão (fundos,
# bancos, holdings estatais, "sócio de fachada" explodiriam o grafo). O nó
# fica marcado como hub. Ajustável por chamada via montar(limite_hub=...) —
# ex.: investigação de fraude quer subir esse limite pra ver justamente as
# empresas de um hub suspeito.
LIMITE_HUB = 40


@dataclass
class Grafo:
    nos: dict = field(default_factory=dict)      # id -> dict de atributos
    arestas: dict = field(default_factory=dict)  # (origem, destino) -> (rótulo, desde)
    truncado: bool = False
    hubs: list = field(default_factory=list)


def _fmt_data(aaaammdd: str | None) -> str:
    d = (aaaammdd or "").strip()
    if len(d) == 8 and d.isdigit() and d != "00000000":
        return f"{d[6:8]}/{d[4:6]}/{d[0:4]}"
    return ""


def _chave_pf(nome: str, cpf: str) -> str:
    return f"pf:{(nome or '').strip()}|{(cpf or '').strip()}"


@st.cache_data
def _socios_das_empresas(basicos: tuple) -> pd.DataFrame:
    return db.query("""
        SELECT cnpj_basico, tipo_socio, nome_socio, cpf_cnpj_socio,
               qualificacao, faixa_etaria, data_entrada_sociedade, pais_socio,
               representante_legal, nome_representante, qualificacao_representante
        FROM socios_completos
        WHERE cnpj_basico IN (SELECT unnest(?::VARCHAR[]))
    """, [list(basicos)])


@st.cache_data
def _participacoes_das_empresas(basicos: tuple) -> pd.DataFrame:
    """Direção inversa: empresas em que estes CNPJs aparecem como SÓCIA PJ
    (sem isso, pesquisar uma holding/sócia estrangeira mostra estrela
    sozinha — o vínculo mora no quadro da investida, não no dela)."""
    return db.query("""
        SELECT cpf_cnpj_socio[1:8] AS basico_socia,
               cnpj_basico AS basico_investida,
               qualificacao, data_entrada_sociedade
        FROM socios_completos
        WHERE tipo_socio = 'pessoa juridica'
          AND cpf_cnpj_socio[1:8] IN (SELECT unnest(?::VARCHAR[]))
    """, [list(basicos)])


@st.cache_data
def _empresas_dos_socios_pf(pares: tuple) -> pd.DataFrame:
    """pares = tuplas (nome, cpf_mascarado). Volta todas as empresas em que
    essas pessoas aparecem como sócias."""
    nomes = [p[0] for p in pares]
    cpfs = [p[1] for p in pares]
    return db.query("""
        SELECT s.cnpj_basico, s.nome_socio, s.cpf_cnpj_socio,
               s.qualificacao, s.data_entrada_sociedade
        FROM socios_completos s
        JOIN (SELECT unnest(?::VARCHAR[]) AS nome, unnest(?::VARCHAR[]) AS cpf) alvo
          ON s.nome_socio = alvo.nome AND s.cpf_cnpj_socio = alvo.cpf
    """, [nomes, cpfs])


@st.cache_data
def _dados_empresas(basicos: tuple) -> pd.DataFrame:
    # Filtrar ANTES de agregar/juntar: sem o pre-filtro, o GROUP BY varre os
    # 72M de estabelecimentos e estoura memoria (spill de ~12GB em temp).
    return db.query("""
        WITH alvo AS (SELECT unnest(?::VARCHAR[]) AS b),
        est AS (
            SELECT * FROM estabelecimentos_completos
            WHERE cnpj_basico IN (SELECT b FROM alvo)
        ),
        matriz AS (
            SELECT cnpj_basico, cnpj, situacao, uf, municipio, cnae_principal
            FROM est WHERE matriz_filial = 'matriz'
        ),
        agreg AS (
            SELECT cnpj_basico, count(*) AS n_estab,
                   count(*) FILTER (situacao = 'ativa') AS n_ativos
            FROM est GROUP BY 1
        )
        SELECT e.cnpj_basico, e.razao_social, e.porte, e.natureza,
               m.situacao, m.uf, m.municipio, m.cnpj AS cnpj_matriz,
               m.cnae_principal, a.n_estab, a.n_ativos
        FROM empresas_completas e
        LEFT JOIN matriz m ON e.cnpj_basico = m.cnpj_basico
        LEFT JOIN agreg  a ON e.cnpj_basico = a.cnpj_basico
        WHERE e.cnpj_basico IN (SELECT b FROM alvo)
    """, [list(basicos)])


@st.cache_data
def _filiais(basico: str, limite: int = 30, priorizar: str = "ativas") -> pd.DataFrame:
    # priorizar segue o filtro de situacao da pagina: com 'inativas', as
    # baixadas vem primeiro (senao o limite so pega ativas e o filtro zera).
    ordem = "(situacao = 'ativa') DESC" if priorizar != "inativas" else "(situacao = 'ativa') ASC"
    return db.query(f"""
        SELECT cnpj, situacao, uf, municipio
        FROM estabelecimentos_completos
        WHERE cnpj_basico = ? AND matriz_filial = 'filial'
        ORDER BY {ordem}, uf LIMIT ?
    """, [basico, limite])


@st.cache_data
def _ies_das_empresas(basicos: tuple) -> pd.DataFrame:
    """IEs (SEFAZ-RS, tabela ie_rs) dos estabelecimentos das empresas.
    DataFrame vazio se a base não foi importada."""
    try:
        return db.query("""
            SELECT ie.cnpj14[1:8] AS basico, ie.inscricao, ie.categoria,
                   coalesce(c.descricao, ie.cnae_1) AS cnae_1,
                   ie.data_abertura, ie.cnpj14
            FROM ie_rs ie
            LEFT JOIN cnaes c ON ie.cnae_1 = c.codigo
            WHERE ie.cnpj14 IS NOT NULL
              AND ie.cnpj14[1:8] IN (SELECT unnest(?::VARCHAR[]))
        """, [list(basicos)])
    except Exception:
        return pd.DataFrame(
            columns=["basico", "inscricao", "categoria", "cnae_1",
                     "data_abertura", "cnpj14"])


@st.cache_data
def _titulares_das_ies(inscricoes: tuple) -> pd.DataFrame:
    """Todos os titulares (CPF de produtor ou CNPJ) das inscrições — uma IE
    pode ter vários condôminos. Vazio se ie_rs não existe."""
    try:
        return db.query("""
            SELECT inscricao, tipo, cpf_cnpj, cnpj14
            FROM ie_rs
            WHERE inscricao IN (SELECT unnest(?::VARCHAR[]))
        """, [list(inscricoes)])
    except Exception:
        return pd.DataFrame(columns=["inscricao", "tipo", "cpf_cnpj", "cnpj14"])


@st.cache_data
def _ies_dos_produtores(cpfs: tuple) -> pd.DataFrame:
    """Outras IEs em que esses CPFs (produtores rurais, SEFAZ) aparecem."""
    try:
        return db.query("""
            SELECT ie.cpf_cnpj, ie.inscricao, ie.categoria,
                   coalesce(c.descricao, ie.cnae_1) AS cnae_1,
                   ie.data_abertura, ie.cnpj14
            FROM ie_rs ie
            LEFT JOIN cnaes c ON ie.cnae_1 = c.codigo
            WHERE ie.tipo = 'F'
              AND ie.cpf_cnpj IN (SELECT unnest(?::VARCHAR[]))
        """, [list(cpfs)])
    except Exception:
        return pd.DataFrame(columns=["cpf_cnpj", "inscricao", "categoria",
                                     "cnae_1", "data_abertura", "cnpj14"])


@st.cache_data
def buscar_socios_por_nome(termo: str, limite: int = 30) -> pd.DataFrame:
    """Pessoas físicas cujo nome contém o termo, com nº de participações —
    pra escolher a semente quando a busca do grafo é por nome."""
    return db.query(f"""
        SELECT nome_socio, cpf_cnpj_socio, count(*) AS participacoes
        FROM socios_completos
        WHERE tipo_socio = 'pessoa fisica' AND nome_socio ILIKE '%' || ? || '%'
        GROUP BY 1, 2
        ORDER BY participacoes DESC, nome_socio
        LIMIT {limite}
    """, [termo.strip()])


@st.cache_data
def buscar_socios_por_cpf(termo: str, limite: int = 30) -> pd.DataFrame:
    """Pessoas físicas cujo CPF mascarado contém o termo — aceita colar o
    CPF mascarado inteiro ('***293378**', como a RFB expõe) ou só os
    dígitos visíveis ('293378')."""
    digitos = re.sub(r"\D", "", termo)
    padrao = f"%{digitos}%" if digitos else f"%{termo.strip()}%"
    return db.query(f"""
        SELECT nome_socio, cpf_cnpj_socio, count(*) AS participacoes
        FROM socios_completos
        WHERE tipo_socio = 'pessoa fisica' AND cpf_cnpj_socio ILIKE ?
        GROUP BY 1, 2
        ORDER BY participacoes DESC, nome_socio
        LIMIT {limite}
    """, [padrao])


@st.cache_data
def pegada_geografica(basicos: tuple) -> pd.DataFrame:
    """Estabelecimentos (matriz+filiais) das empresas do grafo, agregados
    por (município, empresa) com código IBGE — alimenta o mapa de
    capilaridade. Granular por cnpj_basico (não soma tudo junto) pra a
    página poder: (1) aplicar o filtro ativas/inativas da sidebar contando
    n_ativos vs n_estab-n_ativos, e (2) colorir por empresa quando poucas
    estão selecionadas, em vez de só um agregado cego de todo o grafo."""
    from lib.db import CSV_REGIOES
    return db.query(f"""
        WITH alvo AS (SELECT unnest(?::VARCHAR[]) AS b)
        SELECT r.codigo_ibge::VARCHAR AS codigo_ibge,
               r.municipio_ibge AS municipio, ec.uf, ec.cnpj_basico,
               count(*) AS n_estab,
               count(*) FILTER (ec.situacao = 'ativa') AS n_ativos
        FROM estabelecimentos_completos ec
        JOIN read_csv('{CSV_REGIOES.as_posix()}', header=true) r
          ON ec.uf = r.uf
         AND regexp_replace(
                 replace(replace(strip_accents(upper(ec.municipio)), '''', ''), '-', ' '),
                 ' +', ' ', 'g') = r.municipio_norm
        WHERE ec.cnpj_basico IN (SELECT b FROM alvo)
        GROUP BY 1, 2, 3, ec.cnpj_basico
    """, [list(basicos)])


@st.cache_data
def estabelecimentos_do_ponto(basicos: tuple, codigo_ibge: str, limite: int = 300) -> pd.DataFrame:
    """Estabelecimentos individuais (matriz+filiais) das empresas do grafo
    num único município — alimenta o painel de detalhe ao clicar numa
    bolinha do mapa de capilaridade (a query agregada de pegada_geografica
    só dá a contagem, não quem são)."""
    from lib.db import CSV_REGIOES
    return db.query(f"""
        WITH alvo AS (SELECT unnest(?::VARCHAR[]) AS b)
        SELECT ec.cnpj, ec.razao_social, ec.nome_fantasia, ec.matriz_filial,
               ec.situacao, ec.cnae_principal,
               trim(coalesce(ec.tipo_logradouro, '') || ' ' || coalesce(ec.logradouro, '')) AS logradouro,
               ec.numero, ec.bairro, ec.cnpj_basico
        FROM estabelecimentos_completos ec
        JOIN read_csv('{CSV_REGIOES.as_posix()}', header=true) r
          ON ec.uf = r.uf
         AND regexp_replace(
                 replace(replace(strip_accents(upper(ec.municipio)), '''', ''), '-', ' '),
                 ' +', ' ', 'g') = r.municipio_norm
        WHERE ec.cnpj_basico IN (SELECT b FROM alvo)
          AND r.codigo_ibge::VARCHAR = ?
        ORDER BY ec.situacao <> 'ativa', ec.razao_social
        LIMIT {limite}
    """, [list(basicos), codigo_ibge])


@st.cache_data
def conta_filiais(basico: str) -> tuple:
    """(total, ativas) de filiais da empresa — pra avisar quando o grafo corta."""
    return db.query_um("""
        SELECT count(*), count(*) FILTER (situacao = 'ativa')
        FROM estabelecimentos_completos
        WHERE cnpj_basico = ? AND matriz_filial = 'filial'
    """, [basico])


def montar(seed_basico: str | None, niveis: int, max_nos: int,
           incluir_representantes: bool, incluir_filiais: bool,
           max_filiais: int = 30, priorizar_filiais: str = "ativas",
           seed_pf: tuple | None = None,
           seeds_extras: tuple = (),
           incluir_ies: bool = False,
           niveis_ie: int = 0,
           limite_hub: int = LIMITE_HUB) -> Grafo:
    """BFS alternado por nível:
    nível 1 (ímpar): sócios/administradores das empresas conhecidas;
    nível 2 (par): empresas ligadas aos sócios encontrados;
    nível 3: sócios dessas empresas novas — e assim por diante.

    Semente: um CNPJ básico (seed_basico) OU uma pessoa (seed_pf =
    (nome, cpf_mascarado) — o grafo parte das participações dela).
    seeds_extras: básicos adicionais (expansão sob demanda) que entram
    junto na primeira rodada."""
    g = Grafo()
    empresas_pendentes: set = set()
    empresas_vistas: set = set()
    pf_pendentes: set = set()   # pares (nome, cpf) ainda não expandidos
    pf_vistos: set = set()

    if seed_pf:
        nome, cpf = seed_pf
        id_seed = _chave_pf(nome, cpf)
        g.nos[id_seed] = {"tipo": "pf", "nome": nome, "cpf": cpf, "seed_pf": True}
        pf_vistos.add(id_seed)
        # participações da pessoa viram o "nível 0": empresas + arestas
        for r in _empresas_dos_socios_pf(((nome, cpf),)).itertuples(index=False):
            id_emp = f"emp:{r.cnpj_basico}"
            g.nos.setdefault(id_emp, {"tipo": "empresa", "basico": r.cnpj_basico})
            g.arestas.setdefault(
                (id_seed, id_emp),
                (r.qualificacao or "sócio", _fmt_data(r.data_entrada_sociedade)),
            )
            empresas_pendentes.add(r.cnpj_basico)
        if not seed_basico and empresas_pendentes:
            seed_basico = sorted(empresas_pendentes)[0]  # âncora p/ filiais/caminho
    else:
        g.nos[f"emp:{seed_basico}"] = {"tipo": "empresa", "basico": seed_basico}
        empresas_pendentes = {seed_basico}

    for extra in seeds_extras:
        if extra and extra not in empresas_vistas:
            g.nos.setdefault(f"emp:{extra}", {"tipo": "empresa", "basico": extra})
            empresas_pendentes.add(extra)

    # Cada nível processa a fronteira INTEIRA descoberta no nível anterior
    # (empresas E pessoas juntas), não alterna ímpar/par. Alternar fazia
    # uma empresa achada via direção inversa (ela é sócia de outra) esperar
    # +2 níveis pra revelar os PRÓPRIOS sócios — descompassado com o hop
    # real de distância no grafo (e com o resultado de pesquisá-la sozinha).
    for nivel in range(1, max(1, niveis) + 1):
        if len(g.nos) >= max_nos:
            g.truncado = True
            break
        if not empresas_pendentes and not pf_pendentes:
            break

        novas_empresas: set = set()
        novas_pf: set = set()

        if empresas_pendentes:
            lote = tuple(sorted(empresas_pendentes))
            empresas_vistas |= empresas_pendentes
            empresas_pendentes = set()

            for s in _socios_das_empresas(lote).itertuples(index=False):
                if len(g.nos) >= max_nos:
                    g.truncado = True
                    break
                id_empresa = f"emp:{s.cnpj_basico}"

                if s.tipo_socio == "pessoa juridica":
                    pj_basico = (s.cpf_cnpj_socio or "")[:8]
                    if len(pj_basico) == 8 and pj_basico.isdigit():
                        id_socio = f"emp:{pj_basico}"
                        g.nos.setdefault(id_socio, {"tipo": "empresa", "basico": pj_basico})
                        if pj_basico not in empresas_vistas:
                            novas_empresas.add(pj_basico)
                    else:
                        id_socio = _chave_pf(s.nome_socio, s.cpf_cnpj_socio)
                        g.nos.setdefault(id_socio, {"tipo": "pj_ext", "nome": s.nome_socio})
                    # sócia PJ "domiciliada no exterior" tem pais_socio
                    # preenchido mesmo virando nó de empresa/pj_ext — sem
                    # isso o badge internacional do mapa perde CMPC PULP,
                    # BB Cayman Islands etc. (achado testando a feature nova)
                    if s.pais_socio:
                        g.nos[id_socio]["pais"] = s.pais_socio
                else:
                    id_socio = _chave_pf(s.nome_socio, s.cpf_cnpj_socio)
                    g.nos.setdefault(id_socio, {
                        "tipo": "pf" if s.tipo_socio == "pessoa fisica" else "estrangeiro",
                        "nome": s.nome_socio, "cpf": s.cpf_cnpj_socio,
                        "faixa": s.faixa_etaria, "pais": s.pais_socio,
                    })
                    if id_socio not in pf_vistos:
                        novas_pf.add((s.nome_socio, s.cpf_cnpj_socio))
                        pf_vistos.add(id_socio)

                g.arestas.setdefault(
                    (id_socio, id_empresa),
                    (s.qualificacao or "sócio", _fmt_data(s.data_entrada_sociedade)),
                )

                if incluir_representantes and s.nome_representante:
                    id_rep = _chave_pf(s.nome_representante, s.representante_legal)
                    g.nos.setdefault(id_rep, {
                        "tipo": "pf", "nome": s.nome_representante,
                        "cpf": s.representante_legal, "representante": True,
                    })
                    g.arestas.setdefault(
                        (id_rep, id_socio),
                        (f"representante ({s.qualificacao_representante or '?'})", ""),
                    )

            # direção inversa: onde as empresas deste lote são SÓCIAS —
            # a investida entra e expande já no nível seguinte, igual
            # qualquer outra empresa nova (sem o atraso de antes).
            for p in _participacoes_das_empresas(lote).itertuples(index=False):
                if len(g.nos) >= max_nos:
                    g.truncado = True
                    break
                id_socia = f"emp:{p.basico_socia}"
                id_investida = f"emp:{p.basico_investida}"
                g.nos.setdefault(id_investida,
                                 {"tipo": "empresa", "basico": p.basico_investida})
                g.arestas.setdefault(
                    (id_socia, id_investida),
                    (p.qualificacao or "sócio", _fmt_data(p.data_entrada_sociedade)),
                )
                if p.basico_investida not in empresas_vistas:
                    novas_empresas.add(p.basico_investida)

        if pf_pendentes:
            pares = tuple(sorted(pf_pendentes))
            pf_pendentes = set()

            outras = _empresas_dos_socios_pf(pares)
            contagem = outras.groupby(["nome_socio", "cpf_cnpj_socio"]).size()
            chaves_hub = set()
            for (nome, cpf), n in contagem.items():
                if n > limite_hub:
                    chave = _chave_pf(nome, cpf)
                    chaves_hub.add(chave)
                    if chave in g.nos:
                        g.nos[chave]["hub"] = int(n)
                    g.hubs.append((nome, int(n)))

            for r in outras.itertuples(index=False):
                if len(g.nos) >= max_nos:
                    g.truncado = True
                    break
                id_socio = _chave_pf(r.nome_socio, r.cpf_cnpj_socio)
                if id_socio in chaves_hub:
                    continue
                id_emp = f"emp:{r.cnpj_basico}"
                g.nos.setdefault(id_emp, {"tipo": "empresa", "basico": r.cnpj_basico})
                g.arestas.setdefault(
                    (id_socio, id_emp),
                    (r.qualificacao or "sócio", _fmt_data(r.data_entrada_sociedade)),
                )
                if r.cnpj_basico not in empresas_vistas:
                    novas_empresas.add(r.cnpj_basico)

        empresas_pendentes = novas_empresas
        pf_pendentes = novas_pf

    # enriquece nós de empresa com dados cadastrais
    basicos = tuple(sorted({v["basico"] for v in g.nos.values() if v.get("tipo") == "empresa"}))
    if basicos:
        dados = _dados_empresas(basicos).set_index("cnpj_basico")
        for no in g.nos.values():
            if no.get("tipo") == "empresa" and no["basico"] in dados.index:
                d = dados.loc[no["basico"]]
                no.update({
                    "razao": d.razao_social, "situacao": d.situacao, "uf": d.uf,
                    "municipio": d.municipio, "porte": d.porte, "natureza": d.natureza,
                    "cnpj_matriz": d.cnpj_matriz, "cnae": d.cnae_principal,
                    "n_estab": int(d.n_estab) if pd.notna(d.n_estab) else 0,
                    "n_ativos": int(d.n_ativos) if pd.notna(d.n_ativos) else 0,
                })

    # filiais como nós (opcional, só da empresa semente)
    if incluir_filiais:
        for f in _filiais(seed_basico, max_filiais, priorizar_filiais).itertuples(index=False):
            id_fil = f"fil:{f.cnpj}"
            g.nos[id_fil] = {"tipo": "filial", "cnpj": f.cnpj, "situacao": f.situacao,
                             "uf": f.uf, "municipio": f.municipio}
            g.arestas[(f"emp:{seed_basico}", id_fil)] = ("filial", "")

    # Inscrições Estaduais (SEFAZ-RS) de TODAS as empresas do grafo — a
    # ponte com o projeto IE/. Só entra se a tabela ie_rs foi importada.
    if incluir_ies and basicos:
        ies_vistas: set = set()
        for ie in _ies_das_empresas(basicos).itertuples(index=False):
            id_ie = f"ie:{ie.inscricao}"
            g.nos[id_ie] = {
                "tipo": "ie", "inscricao": ie.inscricao,
                "categoria": ie.categoria, "cnae": ie.cnae_1,
                "data_abertura": ie.data_abertura, "cnpj": ie.cnpj14,
            }
            g.arestas[(f"emp:{ie.basico}", id_ie)] = ("IE", _fmt_data_br(ie.data_abertura))
            ies_vistas.add(ie.inscricao)

        # BFS no universo IE (mesma mecânica do projeto IE/): nível ímpar
        # busca os titulares das IEs (condôminos: produtores PF de CPF
        # aberto ou outras PJs), nível par busca as outras IEs desses
        # produtores. Não alimenta o BFS societário (CPF SEFAZ aberto não
        # cruza com CPF RFB mascarado de forma confiável).
        ies_pendentes = set(ies_vistas)
        produtores_vistos: set = set()
        produtores_pendentes: set = set()

        for passo_ie in range(1, max(0, niveis_ie) + 1):
            if len(g.nos) >= max_nos:
                g.truncado = True
                break

            if passo_ie % 2 == 1:
                if not ies_pendentes:
                    break
                lote_ies = tuple(sorted(ies_pendentes))
                ies_pendentes = set()
                for t in _titulares_das_ies(lote_ies).itertuples(index=False):
                    if len(g.nos) >= max_nos:
                        g.truncado = True
                        break
                    id_ie = f"ie:{t.inscricao}"
                    if t.tipo == "F":
                        id_tit = f"prod:{t.cpf_cnpj}"
                        g.nos.setdefault(id_tit, {"tipo": "produtor", "cpf": t.cpf_cnpj})
                        if t.cpf_cnpj not in produtores_vistos:
                            produtores_pendentes.add(t.cpf_cnpj)
                            produtores_vistos.add(t.cpf_cnpj)
                    else:
                        pj = (t.cnpj14 or "")[:8]
                        if len(pj) != 8 or not pj.isdigit():
                            continue
                        id_tit = f"emp:{pj}"
                        g.nos.setdefault(id_tit, {"tipo": "empresa", "basico": pj})
                    g.arestas.setdefault((id_tit, id_ie), ("titular", ""))
            else:
                if not produtores_pendentes:
                    break
                lote_prod = tuple(sorted(produtores_pendentes))
                produtores_pendentes = set()
                for r in _ies_dos_produtores(lote_prod).itertuples(index=False):
                    if len(g.nos) >= max_nos:
                        g.truncado = True
                        break
                    id_ie = f"ie:{r.inscricao}"
                    g.nos.setdefault(id_ie, {
                        "tipo": "ie", "inscricao": r.inscricao,
                        "categoria": r.categoria, "cnae": r.cnae_1,
                        "data_abertura": r.data_abertura, "cnpj": r.cnpj14,
                    })
                    g.arestas.setdefault(
                        (f"prod:{r.cpf_cnpj}", id_ie),
                        ("titular", _fmt_data_br(r.data_abertura)),
                    )
                    if r.inscricao not in ies_vistas:
                        ies_vistas.add(r.inscricao)
                        ies_pendentes.add(r.inscricao)

        # empresas que entraram via IE (PJ condômina) precisam de cadastro
        novos_basicos = tuple(sorted(
            {v["basico"] for v in g.nos.values()
             if v.get("tipo") == "empresa" and "razao" not in v}
        ))
        if novos_basicos:
            dados = _dados_empresas(novos_basicos).set_index("cnpj_basico")
            for no in g.nos.values():
                if (no.get("tipo") == "empresa" and "razao" not in no
                        and no["basico"] in dados.index):
                    d = dados.loc[no["basico"]]
                    no.update({
                        "razao": d.razao_social, "situacao": d.situacao, "uf": d.uf,
                        "municipio": d.municipio, "porte": d.porte,
                        "natureza": d.natureza, "cnpj_matriz": d.cnpj_matriz,
                        "cnae": d.cnae_principal,
                        "n_estab": int(d.n_estab) if pd.notna(d.n_estab) else 0,
                        "n_ativos": int(d.n_ativos) if pd.notna(d.n_ativos) else 0,
                    })

    return g


def _fmt_data_br(d: str | None) -> str:
    """Data da SEFAZ já vem DD/MM/AAAA (ou vazia) — só higieniza."""
    d = (d or "").strip()
    return d if len(d) == 10 else ""


# Palavras que indicam poder de gestão/governança na qualificação da RFB.
_GESTAO = ("ADMINISTRADOR", "DIRETOR", "PRESIDENTE", "CONSELHEIRO", "GERENTE",
           "LIQUIDANTE", "INTERVENTOR", "INVENTARIANTE", "CURADOR", "TUTOR")


def papel(qualificacao: str | None) -> str:
    """Classifica o vínculo: 'gestão' (administra), 'capital' (só sócio),
    'representação' ou 'ie'. Sócio-Administrador conta como gestão."""
    q = (qualificacao or "").upper()
    if q in ("IE", "TITULAR"):
        return "ie"
    if q.startswith("REPRESENTANTE"):
        return "representação"
    if any(p in q for p in _GESTAO):
        return "gestão"
    return "capital"


def filtrar_situacao(g: Grafo, modo: str, seed_basico: str) -> Grafo:
    """modo: 'todas' | 'ativas' | 'inativas'. Remove nós de empresa/filial
    fora do filtro (semente sempre fica), arestas órfãs e pessoas que
    ficarem sem nenhum vínculo."""
    if modo == "todas":
        return g

    def situacao_passa(no: dict) -> bool:
        if no.get("basico") == seed_basico:
            return True
        situacao = no.get("situacao")
        return (situacao == "ativa") if modo == "ativas" else (situacao != "ativa")

    g2 = Grafo(truncado=g.truncado, hubs=list(g.hubs))
    g2.nos = {
        id_no: no for id_no, no in g.nos.items()
        if no.get("tipo") not in ("empresa", "filial") or situacao_passa(no)
    }
    g2.arestas = {
        (o, d): r for (o, d), r in g.arestas.items()
        if o in g2.nos and d in g2.nos
    }
    conectados = {n for aresta in g2.arestas for n in aresta}
    g2.nos = {
        id_no: no for id_no, no in g2.nos.items()
        if id_no in conectados or no.get("basico") == seed_basico
    }
    return g2


def filtrar_papel(g: Grafo, papeis: tuple, seed_basico: str) -> Grafo:
    """Mantém só arestas cujo papel está em `papeis`; remove nós que ficarem
    órfãos (semente sempre fica)."""
    todos = ("gestão", "capital", "representação", "filial")
    if not papeis or set(papeis) >= set(todos):
        return g

    g2 = Grafo(truncado=g.truncado, hubs=list(g.hubs))
    g2.arestas = {
        chave: (rotulo, desde)
        for chave, (rotulo, desde) in g.arestas.items()
        # arestas de IE não entram no filtro de papel societário (o toggle
        # próprio de IEs já controla a presença delas)
        if papel(rotulo) == "ie"
        or ("filial" if rotulo == "filial" else papel(rotulo)) in papeis
    }
    conectados = {n for aresta in g2.arestas for n in aresta}
    g2.nos = {
        id_no: no for id_no, no in g.nos.items()
        if id_no in conectados
        or no.get("basico") == seed_basico
        or no.get("seed_pf")
    }
    g2.arestas = {
        (o, d): r for (o, d), r in g2.arestas.items()
        if o in g2.nos and d in g2.nos
    }
    return g2


@st.cache_data
def participacoes_pf(nome: str, cpf: str) -> pd.DataFrame:
    """Todas as participações societárias de uma pessoa (nome + CPF mascarado),
    com razão social e situação da matriz — alimenta o painel de detalhe."""
    return db.query("""
        SELECT s.cnpj_basico, e.razao_social, s.qualificacao,
               s.data_entrada_sociedade, m.situacao, m.uf
        FROM socios_completos s
        LEFT JOIN empresas e ON s.cnpj_basico = e.cnpj_basico
        LEFT JOIN (
            SELECT cnpj_basico, situacao, uf FROM estabelecimentos_completos
            WHERE matriz_filial = 'matriz'
        ) m ON s.cnpj_basico = m.cnpj_basico
        WHERE s.nome_socio = ? AND s.cpf_cnpj_socio = ?
        ORDER BY s.data_entrada_sociedade DESC
    """, [nome, cpf])


def para_dataframe(g: Grafo) -> pd.DataFrame:
    """Arestas como tabela (pra exibir e exportar CSV do recorte atual)."""
    linhas = []
    for (origem, destino), (rotulo, desde) in g.arestas.items():
        o, d = g.nos.get(origem, {}), g.nos.get(destino, {})
        linhas.append({
            "origem": o.get("nome") or o.get("razao") or origem,
            "tipo_origem": o.get("tipo"),
            "vinculo": rotulo,
            "papel": "filial" if rotulo == "filial" else papel(rotulo),
            "desde": desde,
            "destino": d.get("razao") or d.get("nome") or d.get("cnpj") or destino,
            "tipo_destino": d.get("tipo"),
            "cnpj_destino": d.get("cnpj_matriz") or d.get("cnpj") or "",
        })
    return pd.DataFrame(linhas)
