#!/usr/bin/env python3
"""
MVP del conector PBAC (Provincia de Buenos Aires Compras) — Radar de licitaciones.

De donde saca los datos:
  https://pbac.cgp.gba.gov.ar/ListarAperturaProxima.aspx  ->  "Licitaciones de
  apertura proxima". Publica, sin login ni captcha. Lista TODOS los procesos de compra
  electronica de la Provincia (ministerios, Vialidad, hospitales, organismos y
  municipios adheridos a PBAC) que todavia no abrieron sobres.

Como lo lee:
  La pagina es ASP.NET y pagina de a 10 filas, pero tiene un boton "Descargar Reporte
  Excel" que devuelve un .xlsx con TODOS los procesos de una sola vez. El script:
    1. GET de la pagina -> toma __VIEWSTATE y la cookie de sesion.
    2. POST del boton de export (con reintentos: el server a veces devuelve el HTML
       en lugar del archivo).
    3. Lee el .xlsx con openpyxl, filtra por palabras clave y descarta ruido.
    4. Escribe data/mvp_pbac_resultados.csv.

Requisitos: openpyxl (ya instalado en el entorno).
Nota SSL: el cert del server puede fallar -> contexto sin verificacion, igual que SIBOM.

Uso:
  python src/pbac_mvp.py
  python src/pbac_mvp.py --incluir-ruido
  python src/pbac_mvp.py --guardar-xlsx     # deja tambien el ReporteProcesos.xlsx crudo
"""

import argparse
import csv
import io
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

import openpyxl

URL = "https://pbac.cgp.gba.gov.ar/ListarAperturaProxima.aspx"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
SSL_CTX = ssl._create_unverified_context()
UA = {"User-Agent": "Mozilla/5.0 (radar-licitaciones MVP; contacto interno)",
      "Accept": "text/html,application/xhtml+xml,*/*"}

KEYWORDS = [
    "neumatic", "cubierta", "camara", "llanta", "recapado", "recauchut",
    "gomeria", "vulcaniz",
]

# Ruido: aparece la palabra clave pero NO es una compra de neumaticos para la flota.
RUIDO_PATRONES = [
    "asfalt", "bitumin", "fresado",                       # repavimentacion
    "de desuso", "fuera de uso", "nfu", "reciclad",        # residuos
    "camara de video", "camara ip", "camara de seg", "camara frigor",
    "camara de frio", "camara gesell", "camara digital", "camara fotograf",
    "camara de conservacion", "mantenimiento y reparacion de camara",  # camaras de frio de hospitales
    "camara de flujo", "camara de bioseguridad", "camara hiperbarica",
]


def norm(s):
    s = (s or "").lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    return s


class Session:
    def __init__(self):
        self.jar = {}

    def _headers(self, extra):
        h = dict(UA)
        if self.jar:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        h.update(extra or {})
        return h

    def open(self, data=None, extra=None):
        h = self._headers(extra)
        if data is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(URL, data=data, headers=h)
        r = urllib.request.urlopen(req, timeout=60, context=SSL_CTX)
        for c in r.headers.get_all("Set-Cookie") or []:
            k, _, v = c.split(";")[0].partition("=")
            self.jar[k] = v
        return r


def descargar_reporte_xlsx(intentos=5):
    s = Session()
    for i in range(1, intentos + 1):
        html = s.open().read().decode("utf-8", "replace")

        def field(n):
            m = (re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(n), html)
                 or re.search(r'id="%s"[^>]*value="([^"]*)"' % re.escape(n), html))
            return m.group(1) if m else ""

        form = {
            "__EVENTTARGET": "ctl00$CPH1$btnDescargarReporteExcel",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": field("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": field("__VIEWSTATEGENERATOR"),
            "ctl00$CtrlMenuPortal$hdnLogin": field("ctl00$CtrlMenuPortal$hdnLogin"),
        }
        r = s.open(urllib.parse.urlencode(form).encode(), {"Referer": URL})
        body = r.read()
        if body[:2] == b"PK":  # es un .xlsx
            return body
        print(f"   intento {i}: el server devolvio HTML, reintento ...", file=sys.stderr)
        time.sleep(2)
    raise RuntimeError("PBAC no devolvio el Excel despues de varios intentos")


def leer_xlsx(body):
    wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        out.append({
            "nro_proceso": str(r[0]).strip(),
            "nombre": str(r[1] or "").strip(),
            "tipo": str(r[2] or "").strip(),
            "fecha_apertura": str(r[3] or "").strip(),
            "estado": str(r[4] or "").strip(),
            "unidad_ejecutora": str(r[5] or "").strip(),
        })
    return out


def matchea(row):
    t = norm(row["nombre"] + " " + row["unidad_ejecutora"])
    return [k for k in KEYWORDS if k in t]


def es_ruido(texto):
    t = norm(texto)
    return any(p in t for p in RUIDO_PATRONES)


def recolectar(incluir_ruido=True, verbose=False):
    """Devuelve una lista de dicts normalizados de PBAC. La usa el orquestador
    (src/radar.py) y tambien main() de este script.

    Campos: nro_proceso, nombre, tipo, fecha_apertura, estado, unidad_ejecutora,
    keywords_match, es_ruido.
    """
    body = descargar_reporte_xlsx()
    procesos = leer_xlsx(body)
    hits = []
    for r in procesos:
        m = matchea(r)
        if not m:
            continue
        r["keywords_match"] = ",".join(m)
        r["es_ruido"] = es_ruido(r["nombre"] + " " + r["unidad_ejecutora"])
        hits.append(r)
    if not incluir_ruido:
        hits = [r for r in hits if not r["es_ruido"]]
    if verbose:
        print(f"   pbac: {len(hits)} filas (de {len(procesos)} procesos abiertos)")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incluir-ruido", action="store_true")
    ap.add_argument("--guardar-xlsx", action="store_true")
    args = ap.parse_args()

    print("1) Descargando el reporte Excel de PBAC (apertura proxima) ...")
    body = descargar_reporte_xlsx()
    os.makedirs(DATA, exist_ok=True)
    if args.guardar_xlsx:
        with open(os.path.join(DATA, "pbac_ReporteProcesos.xlsx"), "wb") as f:
            f.write(body)
    procesos = leer_xlsx(body)
    print(f"2) {len(procesos)} procesos en el reporte")

    hits = []
    for r in procesos:
        m = matchea(r)
        if not m:
            continue
        r["keywords_match"] = ",".join(m)
        r["es_ruido"] = es_ruido(r["nombre"] + " " + r["unidad_ejecutora"])
        hits.append(r)
    print(f"3) {len(hits)} coinciden con palabras clave de neumaticos/recapado")

    n_ruido = sum(1 for r in hits if r["es_ruido"])
    if not args.incluir_ruido:
        hits = [r for r in hits if not r["es_ruido"]]
        print(f"   {len(hits)} tras descartar {n_ruido} marcados como ruido")

    out = os.path.join(DATA, "mvp_pbac_resultados.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nro_proceso", "es_ruido", "fecha_apertura", "estado", "nombre",
                    "tipo", "unidad_ejecutora", "keywords_match"])
        for r in hits:
            w.writerow([r["nro_proceso"], "si" if r["es_ruido"] else "no",
                        r["fecha_apertura"], r["estado"], r["nombre"], r["tipo"],
                        r["unidad_ejecutora"], r["keywords_match"]])
    print(f"4) Escrito {out}  ({len(hits)} filas)")

    if hits:
        print("\n--- coincidencias ---")
        for r in hits:
            marca = " [RUIDO]" if r["es_ruido"] else ""
            print(f"  {r['fecha_apertura']:<20} {r['nro_proceso']:<18} {r['nombre'][:55]}{marca}")
            print(f"  {'':<39} {r['unidad_ejecutora'][:70]}")
    else:
        print("\n(Hoy no hay procesos de neumaticos/recapado abiertos en PBAC. "
              "Es un snapshot: corriendo a diario se van a capturar cuando aparezcan.)")


if __name__ == "__main__":
    main()
