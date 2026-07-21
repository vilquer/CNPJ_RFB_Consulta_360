"""Detecção de safras novas na RFB e disparo do pipeline a partir do app."""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

from lib import db

# mesmos endpoints do scripts/download.py (duplicados de propósito:
# app/ não importa de scripts/)
SHARE_TOKEN = "YggdBLfdninEJX9"
WEBDAV_BASE = "https://arquivos.receitafederal.gov.br/public.php/webdav"

LOGS_DIR = db.BASE_DIR / "logs"
ESTADO_PATH = db.BASE_DIR / "apoio" / ".atualizacao.json"
SAFRA_RE = re.compile(r"(\d{4}-\d{2})")

# mesma lista do scripts/criar_views.py (TABELAS_PROPRIAS + TABELAS_DOMINIO,
# duplicada de propósito — ver comentário acima). safras_locais() só conta
# uma safra como pronta se as 10 tabelas existem: convert.py pode morrer no
# meio (ex.: disco cheio na Estabelecimentos) deixando só empresas/ convertida
# — contar só por essa pasta escondia a safra incompleta como "já local".
TABELAS_PIPELINE = ["empresas", "estabelecimentos", "socios", "simples",
                     "cnaes", "motivos", "municipios", "naturezas",
                     "paises", "qualificacoes"]


@st.cache_data(ttl=3600)
def safras_remotas() -> list[str]:
    """Pastas AAAA-MM disponíveis no Nextcloud da RFB (cache 1h)."""
    resp = requests.request(
        "PROPFIND", WEBDAV_BASE + "/",
        auth=(SHARE_TOKEN, ""), headers={"Depth": "1"}, timeout=30,
    )
    resp.raise_for_status()
    achadas = set()
    for href in re.findall(r"<d:href>(.*?)</d:href>", resp.text, re.IGNORECASE):
        m = SAFRA_RE.search(href.rstrip("/").split("/")[-1])
        if m:
            achadas.add(m.group(1))
    return sorted(achadas)


def safras_locais() -> list[str]:
    """Safras com as 10 tabelas convertidas — parcial (pipeline interrompido)
    não conta, senão o botão de reprocessar some achando que já terminou."""
    base = db.BASE_DIR / "parquet"
    candidatas = {p.name.split("=", 1)[1] for p in (base / "tabela=empresas").glob("safra=*")
                  if p.is_dir()}
    completas = [s for s in candidatas
                 if all((base / f"tabela={t}" / f"safra={s}").is_dir() for t in TABELAS_PIPELINE)]
    return sorted(completas)


def safras_pendentes() -> list[str]:
    """Safras remotas MAIS NOVAS que a mais recente local (a RFB mantém
    histórico desde 2023 — meses antigos não contam como 'novidade')."""
    try:
        remotas = safras_remotas()
    except Exception:
        return []
    locais = safras_locais()
    corte = max(locais) if locais else ""
    return [s for s in remotas if s > corte]


def liberar_banco() -> None:
    """Fecha a conexão do app e limpa o cache de recursos — solta o lock do
    rfb.duckdb pra outro processo poder escrever. Instalação zerada (banco
    ainda não existe): nada a soltar — db.conexao() faria st.error()+
    st.stop() (sinal que NÃO é Exception, não seria pego pelo try abaixo)
    e travaria a página aqui, antes do pipeline disparar."""
    if db.DB_PATH.exists():
        try:
            db.conexao().close()
        except Exception:
            pass
    st.cache_resource.clear()


def estado() -> dict | None:
    if ESTADO_PATH.exists():
        return json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
    return None


def _salvar_estado(dados: dict) -> None:
    ESTADO_PATH.parent.mkdir(exist_ok=True)
    ESTADO_PATH.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")


def limpar_estado() -> None:
    ESTADO_PATH.unlink(missing_ok=True)


def iniciar_pipeline(safra: str) -> Path:
    """Dispara run_pipeline.py destacado, com log em logs/pipeline_{safra}.log.
    Chame liberar_banco() antes."""
    LOGS_DIR.mkdir(exist_ok=True)
    log = LOGS_DIR / f"pipeline_{safra}.log"
    script = db.BASE_DIR / "scripts" / "run_pipeline.py"
    with open(log, "w", encoding="utf-8") as saida:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(script), safra],
            stdout=saida, stderr=subprocess.STDOUT,
            cwd=str(db.BASE_DIR),
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    _salvar_estado({
        "safra": safra, "log": str(log), "pid": proc.pid,
        "iniciado": datetime.now().isoformat(timespec="seconds"),
    })
    return log


# ---------- Inscrições Estaduais (SEFAZ-RS) ----------

IE_URL = "http://www.sefaz.rs.gov.br/ASP/Download/Sitagro/PPR_ATIVO.zip"
IE_ZIP = db.BASE_DIR / "apoio" / "PPR_ATIVO.zip"
IE_META = db.BASE_DIR / "apoio" / ".ie_import.json"


def ie_info() -> dict:
    """Estado da base de IEs: {'linhas': int|None, 'importado_em': str|None}."""
    info = {"linhas": None, "importado_em": None}
    if db.DB_PATH.exists():
        try:
            info["linhas"] = db.query_um("SELECT count(*) FROM ie_rs")[0]
        except Exception:
            pass
    if IE_META.exists():
        info["importado_em"] = json.loads(
            IE_META.read_text(encoding="utf-8")).get("importado_em")
    return info


def baixar_ie() -> Path:
    """Baixa o zip de IEs ativas da SEFAZ-RS pra apoio/."""
    IE_ZIP.parent.mkdir(exist_ok=True)
    with requests.get(IE_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(IE_ZIP, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    return IE_ZIP


def importar_ie(caminho: Path) -> subprocess.CompletedProcess:
    """Roda scripts/importar_ie.py (fonte única do SQL de import). Chame
    liberar_banco() antes — o import abre o rfb.duckdb pra escrita."""
    r = subprocess.run(
        [sys.executable, str(db.BASE_DIR / "scripts" / "importar_ie.py"),
         str(caminho)],
        capture_output=True, text=True, cwd=str(db.BASE_DIR),
    )
    if r.returncode == 0:
        IE_META.write_text(json.dumps(
            {"importado_em": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False), encoding="utf-8")
    return r


def status_do_log(log_path: str) -> tuple[str, str]:
    """('rodando'|'concluido'|'erro', cauda do log)."""
    p = Path(log_path)
    if not p.exists():
        return "rodando", "(log ainda não criado)"
    texto = p.read_text(encoding="utf-8", errors="replace")
    # 25 sumia inteiro debaixo das 37 linhas de "já baixado, pulando" que o
    # download.py idempotente reimprime — nunca sobrava nada do convert.py.
    cauda = "\n".join(texto.splitlines()[-120:])
    if "Pipeline da safra" in texto and "completo" in texto:
        return "concluido", cauda
    if "ERRO" in texto:
        return "erro", cauda
    return "rodando", cauda
