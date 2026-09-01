#!/usr/bin/env python3
"""
Conector COMPR.AR (Nacion) — Radar de licitaciones.

De donde saca los datos:
  https://comprar.gob.ar/Compras.aspx  ->  bloque "Apertura proxima" del portal
  nacional de compras publicas (Oficina Nacional de Contrataciones). Publico, sin
  login ni captcha. Lista TODOS los procesos de compra electronica del Estado
  nacional que todavia no abrieron sobres: ministerios, Fuerzas Armadas, Parques
  Nacionales, hospitales nacionales, universidades, y empresas del Estado
  adheridas (AySA, entre otras).

Es el MISMO software que PBAC y BAC (familia Compr.ar), asi que reutiliza de
`pbac_mvp.py` la lectura del xlsx, las palabras clave y el filtro de ruido.
Diferencias del portal nacional:
  1. La pagina de arranque hace el redirect tipico de ASP.NET con
     `AspxAutoDetectCookieSupport=1` para setear la cookie de sesion.
  2. NO lleva token anti-CSRF ni `hdnLogin` (mas simple que BAC).
  3. La URL util lleva un querystring cifrado (`Compras.aspx?qs=...`). Si el token
     hardcodeado dejara de servir, el script lo vuelve a sacar de Default.aspx.
  4. La columna "Unidad ejecutora" del reporte es un CODIGO interno, no un nombre.
     Por ahora se muestra como "Nacion · unidad NNNN"; resolver el nombre es una
     mejora pendiente (ver README).

Como lo lee:
  GET de arranque (crea sesion) -> GET de Compras.aspx -> POST del boton "Descargar
  Reporte Excel" (con reintentos) -> lee el .xlsx con openpyxl -> filtra -> escribe
  data/mvp_comprar_resultados.csv.

Nota SSL: mismo tratamiento que SIBOM/PBAC/BAC (contexto sin verificacion).

Uso:
  python src/comprar_mvp.py
  python src/comprar_mvp.py --incluir-ruido
  python src/comprar_mvp.py --guardar-xlsx
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

from pbac_mvp import KEYWORDS, RUIDO_PATRONES, es_ruido, leer_xlsx, matchea  # noqa: F401

BASE = "https://comprar.gob.ar"
# token del querystring de la vista publica de "Compras". Si caduca, se
# reobtiene de Default.aspx (ver _url_compras).
QS_COMPRAS = "W1HXHGHtH10="
URL = f"{BASE}/Compras.aspx?qs={QS_COMPRAS}"
DEFAULT_URL = f"{BASE}/Default.aspx"
EXPORT_TARGET = "ctl00$CPH1$btnDescargarReporteExcelAperturaProxima"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
SSL_CTX = ssl._create_unverified_context()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) radar-licitaciones",
      "Accept": "text/html,application/xhtml+xml,*/*"}


class Session:
    """Cookie jar manual, al estilo de los otros conectores."""

    def __init__(self):
        self.jar = {}

    def _headers(self, extra):
        h = dict(UA)
        if self.jar:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        h.update(extra or {})
        return h

    def _guardar_cookies(self, headers):
        for c in headers.get_all("Set-Cookie") or []:
            k, _, v = c.split(";")[0].partition("=")
            self.jar[k.strip()] = v

    def open(self, url, data=None, extra=None):
        h = self._headers(extra)
        if data is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, headers=h)
        r = urllib.request.urlopen(req, timeout=90, context=SSL_CTX)
        self._guardar_cookies(r.headers)
        return r


def _campo(html, nombre):
    m = (re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(nombre), html)
         or re.search(r'id="%s"[^>]*value="([^"]*)"' % re.escape(nombre), html))
    return m.group(1) if m else ""


def _url_compras(s):
    """Devuelve la URL de Compras.aspx que hoy tiene el boton de export.
    Primero prueba el token hardcodeado; si no aparece el boton, saca el link
    fresco de Default.aspx."""
    for intento in (URL, URL + "&AspxAutoDetectCookieSupport=1"):
        try:
            html = s.open(intento).read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if EXPORT_TARGET.split("$")[-1] in html:
            return intento, html

    home = s.open(DEFAULT_URL + "?AspxAutoDetectCookieSupport=1").read().decode(
        "utf-8", "replace")
    m = re.search(r'Compras\.aspx\?qs=([^"&\']+)', home)
    if not m:
        raise RuntimeError("no encuentro el link a Compras.aspx en Default.aspx")
    url = f"{BASE}/Compras.aspx?qs={m.group(1)}&AspxAutoDetectCookieSupport=1"
    html = s.open(url).read().decode("utf-8", "replace")
    return url, html


def descargar_reporte_xlsx(intentos=6):
    s = Session()
    for i in range(1, intentos + 1):
        try:
            url, html = _url_compras(s)
            if EXPORT_TARGET.split("$")[-1] not in html:
                print(f"   intento {i}: pagina sin boton de export, reintento ...",
                      file=sys.stderr)
                time.sleep(3 * i)
                continue

            form = {
                "__EVENTTARGET": EXPORT_TARGET,
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": _campo(html, "__VIEWSTATE"),
                "__VIEWSTATEGENERATOR": _campo(html, "__VIEWSTATEGENERATOR"),
                "__EVENTVALIDATION": _campo(html, "__EVENTVALIDATION"),
            }
            body = s.open(url, urllib.parse.urlencode(form).encode(),
                         {"Referer": url}).read()
            if body[:2] == b"PK":  # es un .xlsx
                return body
            print(f"   intento {i}: el server devolvio HTML, reintento ...",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001  (server lento/inestable)
            print(f"   intento {i}: error de red ({e}), reintento ...", file=sys.stderr)
            s = Session()
        time.sleep(3 * i)
    raise RuntimeError("COMPR.AR no devolvio el Excel despues de varios intentos")


def _comprador(codigo):
    """La columna 'unidad ejecutora' del reporte nacional es un codigo interno.
    Lo mostramos de forma legible hasta tener el diccionario de nombres."""
    codigo = (codigo or "").strip()
    return f"Nación · unidad {codigo}" if codigo else "Estado nacional"


def recolectar(incluir_ruido=True, verbose=False):
    """Devuelve una lista de dicts normalizados de COMPR.AR nacional. La usa el
    orquestador (src/radar.py). Mismo formato que pbac/bac:
    nro_proceso, nombre, tipo, fecha_apertura, estado, unidad_ejecutora,
    keywords_match, es_ruido."""
    body = descargar_reporte_xlsx()
    procesos = leer_xlsx(body)
    hits = []
    for r in procesos:
        m = matchea(r)
        if not m:
            continue
        r["unidad_ejecutora"] = _comprador(r.get("unidad_ejecutora"))
        r["keywords_match"] = ",".join(m)
        r["es_ruido"] = es_ruido(r["nombre"] + " " + r["unidad_ejecutora"])
        hits.append(r)
    if not incluir_ruido:
        hits = [r for r in hits if not r["es_ruido"]]
    if verbose:
        print(f"   comprar: {len(hits)} filas (de {len(procesos)} procesos abiertos)")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incluir-ruido", action="store_true")
    ap.add_argument("--guardar-xlsx", action="store_true")
    args = ap.parse_args()

    print("1) Descargando el reporte Excel de COMPR.AR (apertura proxima, Nacion) ...")
    body = descargar_reporte_xlsx()
    os.makedirs(DATA, exist_ok=True)
    if args.guardar_xlsx:
        with open(os.path.join(DATA, "comprar_ReporteProcesos.xlsx"), "wb") as f:
            f.write(body)
    procesos = leer_xlsx(body)
    print(f"2) {len(procesos)} procesos en el reporte")

    hits = []
    for r in procesos:
        m = matchea(r)
        if not m:
            continue
        r["unidad_ejecutora"] = _comprador(r.get("unidad_ejecutora"))
        r["keywords_match"] = ",".join(m)
        r["es_ruido"] = es_ruido(r["nombre"] + " " + r["unidad_ejecutora"])
        hits.append(r)
    print(f"3) {len(hits)} coinciden con palabras clave de neumaticos/recapado")

    n_ruido = sum(1 for r in hits if r["es_ruido"])
    if not args.incluir_ruido:
        hits = [r for r in hits if not r["es_ruido"]]
        print(f"   {len(hits)} tras descartar {n_ruido} marcados como ruido")

    out = os.path.join(DATA, "mvp_comprar_resultados.csv")
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
            print(f"  {r['fecha_apertura']:<22} {r['nro_proceso']:<20} {r['nombre'][:52]}{marca}")
    else:
        print("\n(Hoy no hay procesos de neumaticos/recapado abiertos en COMPR.AR. "
              "Es un snapshot: corriendo a diario se capturan cuando aparecen.)")


if __name__ == "__main__":
    main()
