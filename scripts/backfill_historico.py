#!/usr/bin/env python3
"""
Carga UNA VEZ todo el historico de SIBOM (todos los años que tenga el buscador)
en data/historico.csv, para que la pestaña Metricas tenga base larga.

SIBOM es la unica fuente con consulta historica: PBAC y BAC solo muestran
"apertura proxima" (una foto del momento) y acumulan hacia adelante.

Se corre a mano (o por workflow_dispatch), no en la corrida diaria:
  python scripts/backfill_historico.py                # zona objetivo + volumen alto
  python scripts/backfill_historico.py --max-pages 40 # mas profundo (mas lento)
  python scripts/backfill_historico.py --incluir-ruido

Es lento: SIBOM responde despacio y son muchas paginas. Dejalo correr.
NO enriquece (no baja el decreto completo de cada fila) — para las metricas
alcanza con comprador + fecha + palabras clave, y enriquecer cientos de filas
tardaria horas.
"""

import argparse
import datetime as dt
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import radar        # noqa: E402  (actualizar_historico, CAMPOS)
import sibom_mvp    # noqa: E402


def _iso(fecha_ddmmaaaa):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", fecha_ddmmaaaa or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=30,
                    help="paginas por palabra clave (default 30; SIBOM pagina de a 10)")
    ap.add_argument("--incluir-ruido", action="store_true")
    ap.add_argument("--todos", action="store_true",
                    help="no filtrar por zona (toda la provincia)")
    args = ap.parse_args()

    hoy = dt.date.today().isoformat()
    print(f"Backfill SIBOM — {hoy}. max-pages={args.max_pages}, "
          f"{'toda la provincia' if args.todos else 'zona objetivo + volumen alto'}. "
          "Esto tarda; SIBOM es lento.")

    crudas = sibom_mvp.recolectar(
        desde=None,                       # sin corte de fecha: todo el historico
        max_pages=args.max_pages,
        solo_zona=not args.todos,
        incluir_ruido=args.incluir_ruido,
        verbose=True,
        enriquecer_detalle=False,         # solo el snippet del buscador
    )
    print(f"\n{len(crudas)} filas crudas de SIBOM.")

    rows = []
    for r in crudas:
        pub_iso = _iso(r.get("fecha_publicacion", ""))
        rows.append({
            "fuente": "sibom",
            "id_origen": r["url"],
            "comprador": r["municipio"],
            "objeto": r["titulo"],
            "tipo": r.get("tipo", ""),
            "fecha_publicacion": r.get("fecha_publicacion", ""),
            "fecha_apertura": r.get("fecha_apertura", ""),
            "estado": r.get("estado", ""),
            "categoria_zona": r["categoria_zona"],
            "es_ruido": "si" if r["es_ruido"] else "no",
            "keywords": ",".join(sorted(r["keywords"])),
            "url": r["url"],
            "fragmento": r["fragmento"],
            # clave: la deteccion "historica" se fecha en la publicacion del
            # boletin, no en hoy, para que "detecciones por mes" sea real.
            "primera_vez_visto": pub_iso or hoy,
            "vista_ultima_corrida": hoy,
        })

    total, nuevas = radar.actualizar_historico(rows, hoy)
    print(f"\nhistorico.csv: {total} filas ({nuevas} agregadas en este backfill).")
    if rows:
        fechas = sorted(x["primera_vez_visto"] for x in rows if x["primera_vez_visto"])
        print(f"rango: {fechas[0]}  ->  {fechas[-1]}")


if __name__ == "__main__":
    main()
