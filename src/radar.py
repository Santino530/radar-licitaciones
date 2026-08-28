#!/usr/bin/env python3
"""
Radar de licitaciones — orquestador.

Corre todos los conectores disponibles, junta lo que encuentran en una sola tabla
(`data/radar_sin_verificar.csv`) y marca lo NUEVO respecto de la corrida anterior.

Pensado para correr solo, todos los dias a ciertas horas, desde GitHub Actions
(ver .github/workflows/radar.yml). Cada corrida:
  1. Pide a cada conector su lista de licitaciones que matchean neumaticos/recapado.
  2. Normaliza todo al mismo formato.
  3. Lo cruza con `radar_sin_verificar.csv` (la foto de la corrida anterior):
     - fila que ya estaba  -> se actualiza `vista_ultima_corrida`
     - fila nueva          -> `primera_vez_visto` = hoy   (esto es "nueva apertura")
  4. Reescribe el CSV y muestra el resumen de novedades.

La verificacion del pliego y el paso a `pipeline.csv` es manual, en una sesion aparte
(ver docs/blueprint-referencia.md).

Uso:
  python src/radar.py
  python src/radar.py --incluir-ruido      # no descartar lo marcado como ruido
  python src/radar.py --solo pbac          # correr un conector puntual
"""

import argparse
import csv
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
CSV_RADAR = os.path.join(DATA, "radar_sin_verificar.csv")

CAMPOS = [
    "fuente", "id_origen", "comprador", "objeto", "tipo",
    "fecha_publicacion", "fecha_apertura", "estado",
    "categoria_zona", "es_ruido", "keywords", "url", "fragmento",
    "primera_vez_visto", "vista_ultima_corrida",
]

sys.path.insert(0, HERE)
import pbac_mvp  # noqa: E402
import sibom_mvp  # noqa: E402


def de_sibom(incluir_ruido, dias=90):
    # Para un radar diario interesan las publicaciones recientes, no todo el
    # historico. Ventana movil de `dias` sobre la fecha de publicacion del boletin.
    desde = (dt.date.today() - dt.timedelta(days=dias))
    for r in sibom_mvp.recolectar(desde=desde, solo_zona=True,
                                  incluir_ruido=incluir_ruido, verbose=True):
        yield {
            "fuente": "sibom",
            "id_origen": r["url"],
            "comprador": r["municipio"],
            "objeto": r["titulo"],
            "tipo": "",
            "fecha_publicacion": r["fecha_publicacion"],
            "fecha_apertura": "",
            "estado": "",
            "categoria_zona": r["categoria_zona"],
            "es_ruido": "si" if r["es_ruido"] else "no",
            "keywords": ",".join(sorted(r["keywords"])),
            "url": r["url"],
            "fragmento": r["fragmento"],
        }


def de_pbac(incluir_ruido):
    for r in pbac_mvp.recolectar(incluir_ruido=incluir_ruido, verbose=True):
        yield {
            "fuente": "pbac",
            "id_origen": r["nro_proceso"],
            "comprador": r["unidad_ejecutora"],
            "objeto": r["nombre"],
            "tipo": r["tipo"],
            "fecha_publicacion": "",
            "fecha_apertura": r["fecha_apertura"],
            "estado": r["estado"],
            "categoria_zona": "",
            "es_ruido": "si" if r["es_ruido"] else "no",
            "keywords": r["keywords_match"],
            "url": "https://pbac.cgp.gba.gov.ar/ListarAperturaProxima.aspx",
            "fragmento": "",
        }


CONECTORES = {"sibom": de_sibom, "pbac": de_pbac}


def cargar_previo():
    if not os.path.exists(CSV_RADAR):
        return {}
    with open(CSV_RADAR, newline="", encoding="utf-8") as f:
        return {(row["fuente"], row["id_origen"]): row for row in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incluir-ruido", action="store_true")
    ap.add_argument("--solo", choices=list(CONECTORES), help="correr un solo conector")
    ap.add_argument("--dias", type=int, default=90,
                    help="ventana de dias hacia atras para SIBOM (default 90)")
    args = ap.parse_args()

    hoy = dt.date.today().isoformat()
    ahora = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    previo = cargar_previo()
    print(f"Corrida {ahora} — {len(previo)} filas en el radar de la corrida anterior")

    a_correr = [args.solo] if args.solo else list(CONECTORES)
    encontrados = {}
    for nombre in a_correr:
        print(f"» conector {nombre}")
        try:
            kw = {"dias": args.dias} if nombre == "sibom" else {}
            for row in CONECTORES[nombre](args.incluir_ruido, **kw):
                encontrados[(row["fuente"], row["id_origen"])] = row
        except Exception as e:  # noqa: BLE001
            print(f"  ! {nombre} fallo: {e}", file=sys.stderr)

    nuevas, siguen = [], []
    for key, row in encontrados.items():
        anterior = previo.get(key)
        if anterior:
            row["primera_vez_visto"] = anterior.get("primera_vez_visto") or hoy
            siguen.append(row)
        else:
            row["primera_vez_visto"] = hoy
            nuevas.append(row)
        row["vista_ultima_corrida"] = hoy

    # Filas del radar anterior que hoy no aparecieron (p. ej. ya cerraron): se
    # conservan si son de PBAC/apertura (para tener historial), marcando que no se vieron.
    solo_antes = [previo[k] for k in previo if k not in encontrados
                  and (not args.solo or previo[k]["fuente"] == args.solo)]

    todas = nuevas + siguen + solo_antes
    todas.sort(key=lambda r: (r.get("primera_vez_visto", ""), r.get("fuente", "")),
               reverse=True)

    os.makedirs(DATA, exist_ok=True)
    with open(CSV_RADAR, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        for r in todas:
            w.writerow({k: r.get(k, "") for k in CAMPOS})

    print(f"\n=== RESUMEN corrida {hoy} ===")
    print(f"  {len(encontrados)} licitaciones activas encontradas hoy")
    print(f"  {len(nuevas)} NUEVAS respecto de la corrida anterior")
    print(f"  {len(todas)} filas totales en {os.path.basename(CSV_RADAR)}")
    if nuevas:
        print("\n  --- NUEVAS APERTURAS ---")
        for r in nuevas:
            marca = " [ruido]" if r["es_ruido"] == "si" else ""
            fecha = r["fecha_apertura"] or r["fecha_publicacion"]
            print(f"  [{r['fuente']}] {fecha}  {r['comprador'][:35]}")
            print(f"         {r['objeto'][:80]}{marca}")


if __name__ == "__main__":
    main()
