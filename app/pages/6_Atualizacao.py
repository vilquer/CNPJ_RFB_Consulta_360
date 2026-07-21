import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from lib import atualizacao, consultas, db

st.set_page_config(page_title="Atualização de dados", page_icon="📦", layout="wide")
st.title("Atualização de dados")
st.caption("Compara as safras locais com o site da RFB e roda o pipeline "
           "(download → conversão → views) daqui mesmo.")

locais = atualizacao.safras_locais()
c1, c2 = st.columns(2)
# instalação zerada: rfb.duckdb ainda não existe — esta página é justamente
# quem faz a primeira carga, então não pode depender do banco
safra_ativa = consultas.safra_atual() if db.DB_PATH.exists() else "— (primeira carga)"
c1.metric("Safra ativa nas views", safra_ativa)
c2.metric("Safras em disco", ", ".join(locais) or "nenhuma")

# ---------- pipeline em andamento? ----------
est = atualizacao.estado()
if est:
    @st.fragment(run_every="10s")
    def acompanhar():
        status, cauda = atualizacao.status_do_log(est["log"])
        if status == "rodando":
            st.info(f"Pipeline da safra **{est['safra']}** rodando "
                    f"(desde {est['iniciado']}). Evite usar as outras páginas "
                    "durante a etapa final (criação das views).")
        elif status == "concluido":
            st.success(f"Safra **{est['safra']}** processada! Limpe o cache "
                       "(⋮ → Clear cache) ou recarregue o app.")
        else:
            st.error("Pipeline terminou com erro — detalhe no log abaixo. "
                     "Os passos são idempotentes: rodar de novo retoma de onde parou.")
        with st.expander("Log (últimas linhas)", expanded=(status == "erro")):
            st.code(cauda)
        if status != "rodando":
            if st.button("Encerrar acompanhamento"):
                atualizacao.limpar_estado()
                st.rerun()

    acompanhar()
    st.stop()

# ---------- detecção de safra nova ----------
with st.spinner("Consultando o site da RFB..."):
    pendentes = atualizacao.safras_pendentes()

if not pendentes:
    st.success("Nenhuma safra nova — o dado local está em dia com a RFB.")
    try:
        st.caption("Disponíveis no site: " + ", ".join(atualizacao.safras_remotas()))
    except Exception:
        st.caption("(site da RFB inacessível agora; usando só o estado local)")
else:
    st.warning(f"**Safra{'s' if len(pendentes) > 1 else ''} "
               f"nova{'s' if len(pendentes) > 1 else ''} "
               f"na RFB: {', '.join(pendentes)}**")

    safra = st.selectbox("Processar safra", pendentes, index=len(pendentes) - 1)
    st.markdown(
        f"O processo baixa ~7 GB, converte pra Parquet e repointa as views — "
        f"**1h30 a 2h** no total. Roda em segundo plano; esta página acompanha o log.\n\n"
        f"⚠️ Feche notebooks Jupyter que usem o `rfb.duckdb`. O app solta a "
        f"própria conexão sozinho ao iniciar."
    )

    if st.button(f"Baixar e processar {safra}", type="primary"):
        atualizacao.liberar_banco()
        atualizacao.iniciar_pipeline(safra)
        st.rerun()

st.divider()

# ---------- Inscrições Estaduais (SEFAZ-RS) ----------
st.subheader("Inscrições Estaduais — SEFAZ-RS")
info_ie = atualizacao.ie_info()
if info_ie["linhas"] is None:
    st.warning("**Nenhum dado de IE importado ainda** — clique abaixo pra "
               "baixar a base de IEs ativas da SEFAZ-RS e fazer a primeira "
               "carga. Depois disso, a Consulta CNPJ mostra as IEs de cada "
               "empresa gaúcha.")
else:
    st.markdown(f"Base atual: **{info_ie['linhas']:,} inscrições** · "
                f"importada em {info_ie['importado_em'] or '?'} · "
                "a SEFAZ atualiza o arquivo periodicamente — reimporte "
                "quando quiser renovar.")

rotulo_ie = ("Baixar da SEFAZ e importar (primeira carga)"
             if info_ie["linhas"] is None else "Rebaixar e reimportar IEs")
if st.button(rotulo_ie):
    try:
        with st.spinner("Baixando PPR_ATIVO.zip da SEFAZ-RS..."):
            caminho = atualizacao.baixar_ie()
        atualizacao.liberar_banco()
        with st.spinner("Importando pra tabela ie_rs..."):
            r = atualizacao.importar_ie(caminho)
        st.code((r.stdout or "") + (r.stderr or ""))
        if r.returncode == 0:
            st.success("IEs importadas. A seção aparece na Consulta CNPJ "
                       "(limpe o cache do app se não aparecer: ⋮ → Clear cache).")
        else:
            st.error("Importação falhou — detalhe acima.")
    except Exception as erro:
        st.error(f"Download falhou (SEFAZ fora do ar?): {erro}")

st.divider()
with st.expander("Recuperação manual"):
    st.markdown(
        "Se o pipeline falhar na etapa final (`criar_views`) porque o app "
        "estava em uso, finalize por aqui — solta a conexão e roda só a "
        "criação das views:"
    )
    if st.button("Rodar só criar_views agora"):
        atualizacao.liberar_banco()
        with st.spinner("Recriando views..."):
            r = subprocess.run(
                [sys.executable, str(db.BASE_DIR / "scripts" / "criar_views.py")],
                capture_output=True, text=True, cwd=str(db.BASE_DIR),
            )
        st.code((r.stdout or "") + (r.stderr or ""))
        if r.returncode == 0:
            st.success("Views recriadas. Limpe o cache do app (⋮ → Clear cache).")
