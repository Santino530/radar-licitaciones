#!/usr/bin/env python3
"""
Conector Corredores Viales S.A. (CVSA) — Radar de licitaciones.

De donde saca los datos:
  https://corredoresvialessa.com.ar/api/v1/licitaciones  ->  la MISMA API JSON que
  usa la pagina publica de licitaciones de Corredores Viales S.A. (empresa del
  Estado que administra las rutas nacionales concesionadas). Publica, sin login ni
  token: una sola llamada GET devuelve TODAS las contrataciones (licitaciones
  publicas y privadas, contrataciones directas, compulsas de precios).

Por que sirve para el radar:
  CVSA licita por su cuenta (no entra por COMPR.AR) y compra neumaticos y cubiertas
  para toda su flota vial: ~1.200 cubiertas por ano en una sola licitacion, mas
  alquiler de maquinaria vial sobre neumaticos.

Como lo lee:
  GET del endpoint JSON -> filtra por palabras clave y descarta ruido (reusa el
  mismo diccionario que PBAC/BAC/COMPR.AR) -> se queda con lo reciente (ventana de
  VENTANA_DIAS sobre la fecha de apertura, para no re-inyectar licitaciones viejas)
  -> escribe data/mvp_corredores_resultados.csv.

Nota SSL: mismo tratamiento que el resto de los conectores (contexto sin verificacion).

Uso:
  python src/corredores_mvp.py
  python src/corredores_mvp.py --incluir-ruido
  python src/corredores_mvp.py --guardar-json    # deja tambien el JSON crudo
"""

import argparse
import csv
import datetime as dt
import json
import os
import ssl
import sys
import time
import urllib.request

from pbac_mvp import KEYWORDS, es_ruido, norm  # noqa: F401

URL = "https://corredoresvialessa.com.ar/api/v1/licitaciones"
COMPRADOR = "Corredores Viales S.A."
CONTACTO = "ofertas@cvsa.com.ar"

# Solo nos interesan las licitaciones sin fecha o con apertura reciente / futura:
# la API devuelve TODO el historico y no queremos re-inyectar cosas de 2020.
VENTANA_DIAS = 120

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
SSL_CTX = ssl._create_unverified_context()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) radar-licitaciones",
      "Accept": "application/json, text/javascript, */*",
      "X-Requested-With": "XMLHttpRequest"}


def descargar_json(intentos=4):
    """Baja el listado completo de contrataciones de CVSA (formato DataTables:
    {'aaData': [ ... ]}). Con reintentos: el server a veces tarda."""
    ultimo_error = None
    for i in range(1, intentos + 1):
        try:
            req = urllib.request.Request(URL, headers=UA)
            with urllib.request.urlopen(req, timeout=90, context=SSL_CTX) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            filas = data.get("aaData") if isinstance(data, dict) else data
            if isinstance(filas, list) and filas:
                return filas
            ultimo_error = "respuesta vacia o inesperada"
        except Exception as e:  # noqa: BLE001  (server lento/inestable)
            ultimo_error = e
        print(f"   intento {i}: {ultimo_error}, reintento ...", file=sys.stderr)
        time.sleep(3 * i)
    raise RuntimeError(f"CVSA no devolvio el listado ({ultimo_error})")


def _fecha_ddmmyyyy(valor):
    """La API trae la fecha como '2026-08-26 00:00:00-03'. La devolvemos como
    'DD/MM/YYYY' (el formato que espera el radar) y tambien como date, para poder
    filtrar por ventana. Devuelve (texto, date|None)."""
    s = (valor or "").strip()
    if len(s) < 10:
        return "", None
    try:
        d = dt.date.fromisoformat(s[:10])
    except ValueError:
        return "", None
    return d.strftime("%d/%m/%Y"), d


def _normalizar(fila):
    """Pasa un registro de la API al dict que consume el orquestador (radar.py),
    con la misma forma que devuelven pbac/bac/comprar."""
    objeto = str(fila.get("objeto") or "").strip()
    requerimiento = str(fila.get("requerimiento") or "").strip()
    texto = objeto if objeto else requerimiento
    fecha_txt, fecha_d = _fecha_ddmmyyyy(fila.get("fecha_acto_de_apertura"))

    m = [k for k in KEYWORDS if k in norm(objeto + " " + requerimiento)]
    numero = str(fila.get("numero") or "").strip() or str(fila.get("id") or "").strip()
    return {
        "nro_proceso": numero,
        "nombre": texto,
        "tipo": str(fila.get("tipo_nombre") or "").strip(),
        "fecha_apertura": fecha_txt,
        "_fecha_apertura_d": fecha_d,
        "estado": "",   # la API no distingue abierta/cerrada -> lo maneja el radar por fecha
        "unidad_ejecutora": COMPRADOR,
        "keywords_match": ",".join(m),
        "es_ruido": es_ruido(objeto + " " + requerimiento),
        "_match": bool(m),
    }


def _reciente(row, hoy):
    """True si conviene pasarla al radar: sin fecha, o con apertura dentro de la
    ventana (hacia atras) o en el futuro."""
    d = row.get("_fecha_apertura_d")
    if d is None:
        return True
    return (hoy - d).days <= VENTANA_DIAS


def recolectar(incluir_ruido=True, verbose=False):
    """Devuelve una lista de dicts normalizados de CVSA. La usa el orquestador
    (src/radar.py). Campos: nro_proceso, nombre, tipo, fecha_apertura, estado,
    unidad_ejecutora, keywords_match, es_ruido."""
    filas = descargar_json()
    hoy = dt.date.today()
    hits = []
    for fila in filas:
        row = _normalizar(fila)
        if not row["_match"] or not _reciente(row, hoy):
            continue
        row.pop("_fecha_apertura_d", None)
        row.pop("_match", None)
        hits.append(row)
    if not incluir_ruido:
        hits = [r for r in hits if not r["es_ruido"]]
    if verbose:
        print(f"   corredores: {len(hits)} filas (de {len(filas)} contrataciones publicadas)")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incluir-ruido", action="store_true")
    ap.add_argument("--guardar-json", action="store_true")
    args = ap.parse_args()

    print("1) Bajando el listado de contrataciones de Corredores Viales S.A. ...")
    filas = descargar_json()
    os.makedirs(DATA, exist_ok=True)
    if args.guardar_json:
        with open(os.path.join(DATA, "corredores_licitaciones.json"), "w",
                  encoding="utf-8") as f:
            json.dump(filas, f, ensure_ascii=False, indent=1)
    print(f"2) {len(filas)} contrataciones en el listado")

    hoy = dt.date.today()
    hits = []
    for fila in filas:
        row = _normalizar(fila)
        if not row["_match"]:
            continue
        marca_vieja = not _reciente(row, hoy)
        row.pop("_fecha_apertura_d", None)
        row.pop("_match", None)
        row["_fuera_de_ventana"] = marca_vieja
        hits.append(row)
    print(f"3) {len(hits)} coinciden con palabras clave de neumaticos/recapado")

    n_ruido = sum(1 for r in hits if r["es_ruido"])
    n_viejas = sum(1 for r in hits if r["_fuera_de_ventana"])
    if not args.incluir_ruido:
        hits = [r for r in hits if not r["es_ruido"]]
        print(f"   {len(hits)} tras descartar {n_ruido} marcados como ruido")
    print(f"   ({n_viejas} quedan fuera de la ventana de {VENTANA_DIAS} dias y no van al radar)")

    out = os.path.join(DATA, "mvp_corredores_resultados.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nro_proceso", "es_ruido", "en_ventana", "fecha_apertura", "estado",
                    "nombre", "tipo", "unidad_ejecutora", "keywords_match"])
        for r in hits:
            w.writerow([r["nro_proceso"], "si" if r["es_ruido"] else "no",
                        "no" if r["_fuera_de_ventana"] else "si",
                        r["fecha_apertura"], r["estado"], r["nombre"], r["tipo"],
                        r["unidad_ejecutora"], r["keywords_match"]])
    print(f"4) Escrito {out}  ({len(hits)} filas)")

    if hits:
        print("\n--- coincidencias ---")
        for r in hits:
            marca = " [RUIDO]" if r["es_ruido"] else ""
            marca += " [fuera de ventana]" if r["_fuera_de_ventana"] else ""
            print(f"  {r['fecha_apertura']:<12} {r['nro_proceso']:<12} {r['nombre'][:58]}{marca}")
    else:
        print("\n(Hoy no hay contrataciones de neumaticos/recapado recientes en CVSA. "
              "Es un snapshot: corriendo a diario se capturan cuando aparecen.)")


if __name__ == "__main__":
    main()
