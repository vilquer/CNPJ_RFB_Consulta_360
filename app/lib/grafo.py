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

from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

from lib import db

# Sócio ligado a mais empresas que isso não é expandido (fundos, bancos,
# holdings estatais explodem o grafo). O nó fica marcado como hub.
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
def conta_filiais(basico: str) -> tuple:
    """(total, ativas) de filiais da empresa — pra avisar quando o grafo corta."""
    return db.query_um("""
        SELECT count(*), count(*) FILTER (situacao = 'ativa')
        FROM estabelecimentos_completos
        WHERE cnpj_basico = ? AND matriz_filial = 'filial'
    """, [basico])


def montar(seed_basico: str, niveis: int, max_nos: int,
           incluir_representantes: bool, incluir_filiais: bool,
           max_filiais: int = 30, priorizar_filiais: str = "ativas") -> Grafo:
    """BFS alternado por nível:
    nível 1 (ímpar): sócios/administradores das empresas conhecidas;
    nível 2 (par): empresas ligadas aos sócios encontrados;
    nível 3: sócios dessas empresas novas — e assim por diante."""
    g = Grafo()
    g.nos[f"emp:{seed_basico}"] = {"tipo": "empresa", "basico": seed_basico}
    empresas_pendentes = {seed_basico}
    empresas_vistas: set = set()
    pf_pendentes: set = set()   # pares (nome, cpf) ainda não expandidos
    pf_vistos: set = set()

    for passo in range(1, max(1, niveis) + 1):
        if len(g.nos) >= max_nos:
            g.truncado = True
            break

        if passo % 2 == 1:
            # ímpar: empresas pendentes -> seus sócios
            if not empresas_pendentes:
                break
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
                            empresas_pendentes.add(pj_basico)
                    else:
                        id_socio = _chave_pf(s.nome_socio, s.cpf_cnpj_socio)
                        g.nos.setdefault(id_socio, {"tipo": "pj_ext", "nome": s.nome_socio})
                else:
                    id_socio = _chave_pf(s.nome_socio, s.cpf_cnpj_socio)
                    g.nos.setdefault(id_socio, {
                        "tipo": "pf" if s.tipo_socio == "pessoa fisica" else "estrangeiro",
                        "nome": s.nome_socio, "cpf": s.cpf_cnpj_socio,
                        "faixa": s.faixa_etaria, "pais": s.pais_socio,
                    })
                    if id_socio not in pf_vistos:
                        pf_pendentes.add((s.nome_socio, s.cpf_cnpj_socio))
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
        else:
            # par: sócios PF pendentes -> outras empresas em que participam
            if not pf_pendentes:
                break
            pares = tuple(sorted(pf_pendentes))
            pf_pendentes = set()

            outras = _empresas_dos_socios_pf(pares)
            contagem = outras.groupby(["nome_socio", "cpf_cnpj_socio"]).size()
            chaves_hub = set()
            for (nome, cpf), n in contagem.items():
                if n > LIMITE_HUB:
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
                    empresas_pendentes.add(r.cnpj_basico)

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

    return g


# Palavras que indicam poder de gestão/governança na qualificação da RFB.
_GESTAO = ("ADMINISTRADOR", "DIRETOR", "PRESIDENTE", "CONSELHEIRO", "GERENTE",
           "LIQUIDANTE", "INTERVENTOR", "INVENTARIANTE", "CURADOR", "TUTOR")


def papel(qualificacao: str | None) -> str:
    """Classifica o vínculo: 'gestão' (administra), 'capital' (só sócio) ou
    'representação'. Sócio-Administrador conta como gestão."""
    q = (qualificacao or "").upper()
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
