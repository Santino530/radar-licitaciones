#!/usr/bin/env python3
"""
Conector BAC (Buenos Aires Compras — CABA) — Radar de licitaciones.

De donde saca los datos:
  https://www.buenosairescompras.gob.ar/ListarAperturaProxima.aspx  ->  "Licitaciones
  de apertura proxima" de la Ciudad de Buenos Aires. Publica, sin login ni captcha.

Es el MISMO software que PBAC (familia Compr.ar), asi que reutiliza casi todo de
`pbac_mvp.py` (lectura del xlsx, palabras clave, filtro de ruido). Diferencias de BAC:
  1. Hay que pedir la pagina dos veces: el primer GET redirige (302) a Default.aspx y
     recien ahi el server crea la sesion (cookie ASP.NET_SessionId). El segundo GET,
     ya con esa cookie, devuelve la pagina real.
  2. El formulario lleva un token anti-CSRF extra: `ctl00$CSRFToken`.

Como lo lee:
  GET de arranque (crea sesion) -> GET de la pagina -> POST del boton "Descargar Reporte
  Excel" (con reintentos) -> lee el .xlsx con openpyxl -> filtra -> escribe
  data/mvp_bac_resultados.csv.

Nota SSL: mismo tratamiento que SIBOM/PBAC (contexto sin verificacion).

Uso:
  python src/bac_mvp.py
  python src/bac_mvp.py --incluir-ruido
  python src/bac_mvp.py --guardar-xlsx
"""

import argparse
import csv
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pbac_mvp
from pbac_mvp import KEYWORDS, RUIDO_PATRONES, es_ruido, leer_xlsx, matchea  # noqa: F401

BASE = "https://www.buenosairescompras.gob.ar"
URL = BASE + "/ListarAperturaProxima.aspx"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
SSL_CTX = ssl._create_unverified_context()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) radar-licitaciones",
      "Accept": "text/html,application/xhtml+xml,*/*"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """No seguir el 302 de arranque: necesitamos leer su Set-Cookie."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Cookie jar MANUAL (http.cookiejar descarta la cookie `redirectInfo` de BAC por
# sintaxis "rara"; y BAC la exige para no rebotarte a Default.aspx).
class Session:
    def __init__(self):
        self.jar = {}
        self._plain = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=SSL_CTX))
        self._noredir = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=SSL_CTX))

    def _guardar_cookies(self, headers):
        for c in headers.get_all("Set-Cookie") or []:
            k, _, v = c.split(";")[0].partition("=")
            self.jar[k.strip()] = v

    def _headers(self, extra):
        h = dict(UA)
        if self.jar:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        h.update(extra or {})
        return h

    def arrancar(self):
        """GET inicial: el server responde 302 y set-ea ASP.NET_SessionId +
        redirectInfo. Las guardamos sin seguir el redirect."""
        try:
            r = self._noredir.open(
                urllib.request.Request(URL, headers=self._headers(None)), timeout=60)
            self._guardar_cookies(r.headers)
        except urllib.error.HTTPError as e:
            self._guardar_cookies(e.headers)  # el 302 llega como HTTPError

    def get(self, extra=None):
        r = self._plain.open(
            urllib.request.Request(URL, headers=self._headers(extra)), timeout=60)
        self._guardar_cookies(r.headers)
        return r.read()

    def post(self, data, extra=None):
        h = self._headers(extra)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        r = self._plain.open(
            urllib.request.Request(URL, data=data, headers=h), timeout=90)
        self._guardar_cookies(r.headers)
        return r.read()


def _campo(html, nombre):
    m = (re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(nombre), html)
         or re.search(r'id="%s"[^>]*value="([^"]*)"' % re.escape(nombre), html))
    return m.group(1) if m else ""


def descargar_reporte_xlsx(intentos=5):
    s = Session()
    s.arrancar()

    for i in range(1, intentos + 1):
        html = s.get({"Referer": BASE + "/Default.aspx"}).decode("utf-8", "replace")
        if "btnDescargarReporteExcel" not in html:
            print(f"   intento {i}: pagina sin boton de export, reintento ...",
                  file=sys.stderr)
            time.sleep(2)
            continue

        form = {
            "__EVENTTARGET": "ctl00$CPH1$btnDescargarReporteExcel",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": _campo(html, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _campo(html, "__VIEWSTATEGENERATOR"),
            "ctl00$CSRFToken": _campo(html, "ctl00$CSRFToken"),
            "ctl00$CtrlMenuPortal$hdnLogin": _campo(html, "ctl00$CtrlMenuPortal$hdnLogin"),
        }
        body = s.post(urllib.parse.urlencode(form).encode(), {"Referer": URL})
        if body[:2] == b"PK":  # es un .xlsx
            return body
        print(f"   intento {i}: el server devolvio HTML, reintento ...", file=sys.stderr)
        time.sleep(2)
    raise RuntimeError("BAC no devolvio el Excel despues de varios intentos")


def recolectar(incluir_ruido=True, verbose=False):
    """Devuelve una lista de dicts normalizados de BAC. La usa el orquestador
    (src/radar.py). Campos: nro_proceso, nombre, tipo, fecha_apertura, estado,
    unidad_ejecutora, keywords_match, es_ruido."""
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
        print(f"   bac: {len(hits)} filas (de {len(procesos)} procesos abiertos)")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incluir-ruido", action="store_true")
    ap.add_argument("--guardar-xlsx", action="store_true")
    args = ap.parse_args()

    print("1) Descargando el reporte Excel de BAC (apertura proxima, CABA) ...")
    body = descargar_reporte_xlsx()
    os.makedirs(DATA, exist_ok=True)
    if args.guardar_xlsx:
        with open(os.path.join(DATA, "bac_ReporteProcesos.xlsx"), "wb") as f:
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

    out = os.path.join(DATA, "mvp_bac_resultados.csv")
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
            print(f"  {r['fecha_apertura']:<20} {r['nro_proceso']:<20} {r['nombre'][:52]}{marca}")
    else:
        print("\n(Hoy no hay procesos de neumaticos/recapado abiertos en BAC. "
              "Es un snapshot: corriendo a diario se capturan cuando aparecen.)")


if __name__ == "__main__":
    main()
