"""
Orquestra o ciclo mensal completo de uma safra:
    download.py -> convert.py -> criar_views.py

Uso:
    python scripts/run_pipeline.py 2026-08
    python scripts/run_pipeline.py 2026-08 --forcar      # reconverte tudo
    python scripts/run_pipeline.py 2026-08 --manter-raw  # preserva os zips

Antes de começar confere se o rfb.duckdb está livre (app Streamlit e kernels
Jupyter abertos seguram o lock e derrubariam o criar_views no fim de um
processo de horas).
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DB_PATH = SCRIPTS.parent / "rfb.duckdb"


def checar_lock() -> None:
    """Falha cedo se o banco está aberto em outro processo."""
    if not DB_PATH.exists():
        return
    import duckdb
    try:
        con = duckdb.connect(str(DB_PATH))
        con.close()
    except duckdb.IOException as e:
        print("ERRO: rfb.duckdb está aberto em outro processo "
              "(app Streamlit? kernel Jupyter?). Feche antes de rodar o pipeline.")
        print(f"      {e}")
        sys.exit(1)


def rodar(script: str, *args: str) -> None:
    # -u: desliga o buffer de stdout/stderr do processo filho. Sem isso, o
    # log em logs/pipeline_{safra}.log (lido ao vivo pela página Atualização)
    # fica em branco por dezenas de minutos e some tudo se o processo morrer
    # antes de flushar (visto: OSError de disco cheio na conversão sumiu com
    # todo o progresso já feito, log pulou direto de download.py pro erro).
    comando = [sys.executable, "-u", str(SCRIPTS / script), *args]
    print(f"\n=== {script} {' '.join(args)} " + "=" * 30)
    inicio = time.time()
    r = subprocess.run(comando)
    if r.returncode != 0:
        print(f"\nERRO em {script} (exit {r.returncode}) — pipeline interrompido. "
              "Os passos são idempotentes: corrija e rode de novo que retoma.")
        sys.exit(r.returncode)
    print(f"=== {script} ok em {(time.time() - inicio) / 60:.1f} min")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("safra", help="Safra AAAA-MM")
    parser.add_argument("--forcar", action="store_true")
    parser.add_argument("--manter-raw", action="store_true")
    args = parser.parse_args()

    checar_lock()

    rodar("download.py", args.safra)
    extras = []
    if args.forcar:
        extras.append("--forcar")
    if args.manter_raw:
        extras.append("--manter-raw")
    rodar("convert.py", args.safra, *extras)
    checar_lock()  # app pode ter sido aberto durante as horas de conversão
    rodar("criar_views.py")

    print(f"\nPipeline da safra {args.safra} completo. "
          "Reabra o app/notebooks — as views já apontam pra safra nova.")


if __name__ == "__main__":
    main()
