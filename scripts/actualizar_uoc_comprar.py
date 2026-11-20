#!/usr/bin/env python3
"""
Arma data/comprar_uoc.csv: codigo de unidad de compras de COMPR.AR -> nombre.

El reporte de "Apertura proxima" de COMPR.AR trae la unidad ejecutora como un
codigo interno ("Nacion · unidad 1499"). El numero de proceso, en cambio, empieza
con el codigo de la Unidad Operativa de Contrataciones (UOC): "74/43-0626-LPR26"
es la UOC 74/43 (Parque Nacional Patagonia, de Parques Nacionales). Los datos
abiertos de COMPR.AR (datos.gob.ar, dataset "sistema-de-contrataciones-
electronicas", recurso "Convocatorias 2016 - 2026") traen ese codigo con el
nombre de la unidad y del organismo (SAF).

El panel (build_dashboard.py) usa esta tabla para mostrar el nombre real del
comprador. No toca el conector ni los datos del radar.

Uso (solo libreria estandar; tarda ~1 minuto, baja ~55 MB):
  python scripts/actualizar_uoc_comprar.py
  python scripts/actualizar_uoc_comprar.py --csv ruta/a/Convocatorias.csv
"""

import argparse
import csv
import io
import os
import re
import urllib.request

URL = ("https://infra.datos.gob.ar/catalog/jgm/dataset/4/distribution/4.21/"
       "download/Convocatorias.csv")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "data", "comprar_uoc.csv"))

NUM_RE = re.compile(r"^(\d+)(?:/(\d+))?-")
PREFIJO_RE = re.compile(r"^\d+(?:/\d+)?\s*-\s*")


def codigo_uoc(nro_proceso):
    """'74/43-0626-LPR26' -> '74/43' ; '87-0082-CDI26' -> '87/0'."""
    m = NUM_RE.match((nro_proceso or "").strip())
    if not m:
        return ""
    return f"{int(m.group(1))}/{int(m.group(2) or 0)}"


def leer(fuente):
    """Devuelve {codigo: (unidad, organismo, ejercicio)} quedandose con el
    nombre del ejercicio mas reciente de cada codigo."""
    tabla = {}
    for r in csv.DictReader(fuente, delimiter=";"):
        cod = codigo_uoc(r.get("Número Procedimiento"))
        if not cod:
            continue
        unidad = PREFIJO_RE.sub("", (r.get("Descripcion UOC") or "").strip())
        organismo = PREFIJO_RE.sub("", (r.get("Descripcion SAF") or "").strip())
        ejercicio = (r.get("Ejercicio") or "").strip()
        previo = tabla.get(cod)
        if previo is None or ejercicio >= previo[2]:
            tabla[cod] = (unidad, organismo, ejercicio)
    return tabla


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="usar un Convocatorias.csv ya descargado")
    args = ap.parse_args()

    if args.csv:
        with open(args.csv, encoding="utf-8-sig", newline="") as f:
            tabla = leer(f)
    else:
        req = urllib.request.Request(URL, headers={"User-Agent": "radar-licitaciones"})
        with urllib.request.urlopen(req, timeout=300) as r:
            texto = r.read().decode("utf-8-sig", "replace")
        tabla = leer(io.StringIO(texto, newline=""))

    def clave(c):
        a, b = c.split("/")
        return int(a), int(b)

    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["uoc", "unidad", "organismo", "ultimo_ejercicio"])
        for cod in sorted(tabla, key=clave):
            w.writerow([cod, *tabla[cod]])
    print(f"{len(tabla)} unidades de compra -> {OUT}")


if __name__ == "__main__":
    main()
