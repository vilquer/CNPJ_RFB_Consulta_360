# CNPJ RFB — Consulta 360°

Painel local (Streamlit + DuckDB) para consulta e análise dos Dados Abertos de CNPJ da Receita Federal do Brasil: 72M+ estabelecimentos, consulta individual, recortes por UF/região, dinâmica empresarial (aberturas/sobrevivência), grafo de vínculos societários com mapa de capilaridade geográfica e comparação entre safras.

Todo o processamento roda localmente — download da safra mensal, conversão para Parquet via DuckDB e consulta pelo app, sem depender de API externa ou banco de dados gerenciado.

> Evolução do [CNPJ_RFB](https://github.com/vilquer/CNPJ_RFB), que cuida do pipeline (download → conversão → `rfb.duckdb`) e da consulta via notebook/SQL. Este projeto reaproveita esse pipeline e adiciona a camada de visualização: um app Streamlit com painel, consulta interativa, recortes por UF/região e grafo de vínculos societários.

## Capturas de tela

**Home — panorama Brasil**
![Home](docs/images/home.png)

**Recorte por UF e região IBGE**
![Recorte UF](docs/images/recorte_uf.png)

**Dinâmica empresarial — aberturas, sobrevivência e longevidade**
![Dinâmica empresarial](docs/images/dinamica_empresarial.png)

**Grafo de vínculos societários**
![Grafo de vínculos](docs/images/grafo_vinculos.png)

## Funcionalidades

- **Home** — indicadores gerais (estabelecimentos, ativos, empresas, MEIs), situação cadastral, ranking de UFs e top CNAEs.
- **Consulta CNPJ** — ficha completa por CNPJ (14 ou 8 dígitos), busca por razão social/nome fantasia ou por sócio (nome ou CPF mascarado); mostra Inscrições Estaduais da SEFAZ-RS quando disponíveis.
- **Recorte UF** — indicadores por UF com quebra em regiões geográficas intermediárias do IBGE, mapa coroplético municipal, taxa de atividade e top CNAEs locais.
- **Dinâmica Empresarial** — série histórica de aberturas, sobrevivência (idade na baixa) e longevidade das empresas ativas, com recorte Brasil ou por UF.
- **Grafo de Vínculos** — rede interativa (D3 v7) empresa ↔ sócios ↔ representantes, nos dois sentidos (sócios da empresa e empresas em que ela é sócia), com semente por CNPJ, nome ou CPF mascarado; expansão por níveis ou fechamento do grupo econômico inteiro; filtro por papel do vínculo (gestão/capital/representação); limite de hub ajustável (sócio ligado a muitas empresas, tipo contador/despachante, não expande sozinho — dá pra subir o limite pra investigar um específico); export SVG/PNG. **Mapa de capilaridade**: presença geográfica das empresas do grafo em bolinhas coloridas por município (sem sobreposição, sem depender de internet — malha do IBGE local), com painel de detalhe por clique listando os estabelecimentos.
- **Comparar Safras** — deltas gerais entre dois meses, novos CNPJs vs. saídas de ativo por UF, diff do quadro societário de um CNPJ (precisa 2+ safras processadas).
- **Atualização** — compara as safras locais com o site da RFB e roda o pipeline (download → conversão → views) direto pelo app, com acompanhamento de log; também importa a base de Inscrições Estaduais da SEFAZ-RS.

## Arquitetura

```
Receita Federal (WebDAV) → raw/*.zip → DuckDB → parquet/ (particionado tabela+safra) → rfb.duckdb (views) → Streamlit
```

Pipeline em três passos, um script por etapa:

| Script | Função |
|---|---|
| `scripts/download.py AAAA-MM` | Baixa os arquivos da safra via WebDAV público (idempotente, com manifest) |
| `scripts/convert.py AAAA-MM` | Converte os zips para Parquet particionado por tabela e safra, via DuckDB |
| `scripts/criar_views.py` | Cria/atualiza `rfb.duckdb` na raiz do repo, com views apontando pra safra mais recente e macros de consulta (`ficha_cnpj`, `busca_nome`) |
| `scripts/run_pipeline.py AAAA-MM` | Orquestra os três passos acima num comando só (também disparável pela página Atualização do app) |
| `scripts/baixar_regioes_ibge.py` | Baixa o mapeamento de município → região intermediária/imediata do IBGE |
| `scripts/baixar_malha_ibge.py` | Baixa a malha geográfica (municípios + estados) do IBGE pra `apoio/` — o app não acessa internet em uso normal, os mapas leem esses arquivos locais |
| `scripts/importar_ie.py` | Importa a base de Inscrições Estaduais ativas da SEFAZ-RS pra `rfb.duckdb` (opcional, alimenta a seção de IEs na Consulta CNPJ) |

O app (`app/Home.py` + `app/pages/`) consulta `rfb.duckdb` em modo read-only, com conexão única (cache) serializada por lock — necessário porque o Streamlit roda páginas em threads concorrentes.

## Como rodar

```bash
git clone https://github.com/vilquer/CNPJ_RFB_Consulta_360.git
cd CNPJ_RFB_Consulta_360

pip install -r requirements.txt

# pipeline (rodar uma vez por safra nova) — um comando:
python scripts/run_pipeline.py 2026-07

# ou passo a passo:
python scripts/download.py 2026-07
python scripts/convert.py 2026-07
python scripts/criar_views.py

# malha do IBGE pros mapas (uma vez só, app fica offline depois disso)
python scripts/baixar_malha_ibge.py
python scripts/baixar_regioes_ibge.py

# app
streamlit run app/Home.py
```

> `requirements.txt` sem versões fixadas — levantei os pacotes a partir dos imports, mas não tenho como saber quais versões você testou. Vale rodar `pip freeze > requirements.txt` no seu ambiente e substituir, pra instalação ficar reprodutível de verdade.

## Estrutura

```
app/
  Home.py              # página inicial
  pages/               # Consulta CNPJ, Recorte UF, Dinâmica Empresarial,
                        # Grafo Vínculos, Comparar Safras, Atualização
  lib/                 # conexão DuckDB, consultas, estilo (Plotly), grafo, atualização
  static/d3.v7.min.js  # D3 local (grafo de vínculos), sem CDN
scripts/
  download.py          # download da safra (WebDAV)
  convert.py           # conversão zip → Parquet
  criar_views.py       # views + macros no rfb.duckdb
  run_pipeline.py       # orquestra os três passos acima
  baixar_regioes_ibge.py
  baixar_malha_ibge.py  # malha geográfica do IBGE (mapas offline)
  importar_ie.py        # Inscrições Estaduais SEFAZ-RS (opcional)
  *.json                # schemas das tabelas (empresas, estabelecimentos, socios, simples, domínios)
apoio/
  ibge_regioes_br.csv         # município → região IBGE
  malha_municipios_br.json    # GeoJSON dos municípios (mapas offline)
  malha_estados_br.json       # GeoJSON dos estados (mapas offline)
```

## Stack

Python · DuckDB · Streamlit · Plotly · Pandas

## Licença

MIT — ver [LICENSE](LICENSE).
