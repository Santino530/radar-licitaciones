#!/usr/bin/env python3
"""
Conector webs municipales — Radar de licitaciones.

Algunos municipios donde ya somos proveedores publican sus licitaciones en su
propia web y NO (o no siempre) en SIBOM. Este conector lee esas paginas:

  Lanus       https://www.lanus.gob.ar/documentos-oficiales?categoria=<id>
              Seccion "Documentos oficiales", categorias "Licitaciones Publicas
              <año>" y "Licitaciones Privadas <año>". Cada item trae numero, fecha
              y hora de apertura, objeto y el link de descarga del pliego. Lanus
              no esta en SIBOM, asi que esta es la unica fuente del municipio.
  San Isidro  https://www.sanisidro.gob.ar/compras
              Listado "Licitaciones publicas" (numero + objeto + link a la ficha).
              La fecha de apertura esta en la ficha: se baja solo para las que
              matchean palabras clave.

Esteban Echeverria no tiene listado propio en su web: publica en el boletin
oficial, que ya cubre el conector SIBOM.

Nota SSL: la web de San Isidro no envia el certificado intermedio de Sectigo, asi
que se valida agregando ese intermedio (data/certs/) a la cadena de confianza del
sistema, en lugar de desactivar la verificacion.

Uso:
  python src/municipios_web_mvp.py              # muestra lo que matchea
  python src/municipios_web_mvp.py --todo       # muestra todo lo que lee (debug)
"""

import argparse
import datetime as dt
import html
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

from pbac_mvp import KEYWORDS, es_ruido, norm

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
CERTS_DIR = os.path.join(DATA, "certs")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) radar-licitaciones"}

LANUS_BASE = "https://www.lanus.gob.ar/documentos-oficiales"
LANUS_PAGINAS = 3          # ~10 items por pagina; alcanza para lo del ultimo mes y medio
SAN_ISIDRO_URL = "https://www.sanisidro.gob.ar/compras"
AVELLANEDA_URL = "https://www.mda.gob.ar/tramites/licitaciones/"
AVELLANEDA_MAX = 40
VARELA_URL = "https://varela.gob.ar/lic/"
BERAZATEGUI_API = ("https://berazategui.gob.ar/wp-json/wp/v2/posts"
                   "?categories=150&per_page=20&_fields=date,title,link,content")
VENTANA_DIAS = 90          # publicaciones sin fecha de apertura: solo lo reciente


def _ctx(con_sectigo=False):
    """Contexto TLS con verificacion. Varias webs municipales (San Isidro, Avellaneda)
    no envian el certificado intermedio de Sectigo: se agregan los de data/certs/
    a la cadena de confianza del sistema en lugar de desactivar la verificacion."""
    ctx = ssl.create_default_context()
    if con_sectigo and os.path.isdir(CERTS_DIR):
        for f in sorted(os.listdir(CERTS_DIR)):
            if f.endswith(".pem"):
                ctx.load_verify_locations(os.path.join(CERTS_DIR, f))
    return ctx


def _get_bytes(url, con_sectigo=False, timeout=60):
    req = urllib.request.Request(urllib.parse.quote(url, safe=":/?&=%#"), headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx(con_sectigo)) as r:
        return r.read()


def _get(url, con_sectigo=False, timeout=60):
    return _get_bytes(url, con_sectigo, timeout).decode("utf-8", "replace")


MESES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], 1)}


def _fecha_larga(texto):
    """'10 de diciembre de 2026' -> '10/12/2026'."""
    m = re.search(r"(\d{1,2}) de (\w+) de (\d{4})", norm(texto))
    if not m or m.group(2) not in MESES:
        return ""
    return f"{int(m.group(1)):02d}/{MESES[m.group(2)]:02d}/{m.group(3)}"


def _reciente(fecha_ddmmyyyy, hoy=None):
    hoy = hoy or dt.date.today()
    try:
        d, mth, y = (int(x) for x in fecha_ddmmyyyy.split("/"))
        return (hoy - dt.date(y, mth, d)).days <= VENTANA_DIAS
    except ValueError:
        return True


def _texto(fragmento_html):
    t = re.sub(r"<[^>]+>", " ", fragmento_html or "")
    t = html.unescape(t).replace("“", "").replace("”", "").replace('"', "")
    return re.sub(r"\s+", " ", t).strip()


def _keywords(texto):
    t = norm(texto)
    return [k for k in KEYWORDS if k in t]


def _fila(comprador, nro, objeto, tipo, apertura, url, publicacion=""):
    m = _keywords(objeto)
    return {
        "nro_proceso": nro,
        "nombre": objeto,
        "tipo": tipo,
        "fecha_apertura": apertura,
        "fecha_publicacion": publicacion,
        "estado": "",
        "unidad_ejecutora": comprador,
        "keywords_match": ",".join(m),
        "es_ruido": es_ruido(objeto),
        "url": url,
        "_match": bool(m),
    }


# --- Lanus -------------------------------------------------------------------

def _lanus_categorias(pagina_html, anios):
    """id de categoria -> nombre, para 'Licitaciones Publicas/Privadas <año>'."""
    out = {}
    for val, nombre in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', pagina_html):
        nombre = re.sub(r"\s+", " ", nombre).strip()
        n = norm(nombre)
        if n.startswith("licitaciones") and any(str(a) in n for a in anios):
            out[val] = nombre
    return out


def _lanus_items(pagina_html, tipo):
    for art in re.findall(r"(?s)<article>(.*?)</article>", pagina_html):
        volanta = _texto((re.search(r'(?s)class="volanta">(.*?)</div>', art) or [None, ""])[1])
        objeto = _texto((re.search(r"(?s)<h3>(.*?)</h3>", art) or [None, ""])[1])
        link = (re.search(r'class="cover" href="([^"]+)"', art) or [None, ""])[1]
        if not objeto or volanta.upper().startswith("CIRCULAR"):
            continue
        nro = re.search(r"N[°º]\s*(\d+)", volanta)
        ap = re.search(r"APERTURA\s*(\d{1,2}/\d{1,2}/\d{4})", volanta, re.I)
        nro_txt = f"{tipo} N°{nro.group(1)}" if nro else volanta[:60]
        yield nro_txt, objeto, (ap.group(1) if ap else ""), link


def recolectar_lanus(verbose=False):
    hoy = dt.date.today()
    anios = {hoy.year} | ({hoy.year - 1} if hoy.month <= 2 else set())
    portada = _get(LANUS_BASE)
    filas = []
    for cat, nombre in _lanus_categorias(portada, anios).items():
        tipo = "Licitación Privada" if "privada" in norm(nombre) else "Licitación Pública"
        for pag in range(1, LANUS_PAGINAS + 1):
            url = f"{LANUS_BASE}?categoria={cat}&buscar=&page={pag}"
            items = list(_lanus_items(_get(url), tipo))
            if not items:
                break
            for nro, objeto, apertura, link in items:
                filas.append(_fila("Lanús", nro, objeto, tipo, apertura, link or url))
    if verbose:
        print(f"   lanus: {len(filas)} licitaciones leidas")
    return filas


# --- San Isidro --------------------------------------------------------------

def _si_items(pagina_html):
    """(numero, objeto, link) del bloque 'Licitaciones publicas' de /compras."""
    bloques = re.findall(r'(?s)<a[^>]+href="([^"]*tramites-y-servicios/licitaci[^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a |</li>|</p>)',
                         pagina_html)
    for href, ancla, resto in bloques:
        texto = _texto(ancla + " " + resto)
        nro = re.search(r"N[°º]\s*(\d+)\s*/\s*(\d{4})", texto)
        objeto = re.sub(r"^N[°º]\s*\d+\s*/\s*\d{4}\s*", "", texto).strip()
        objeto = re.sub(r"^Contrataci[oó]n de\s+", "", objeto)
        url = urllib.parse.urljoin(SAN_ISIDRO_URL, html.unescape(href))
        nro_txt = f"LP N°{nro.group(1)}/{nro.group(2)}" if nro else texto[:40]
        yield nro_txt, objeto, url


def _si_apertura(ficha_html):
    m = re.search(r"Fecha de apertura\s*</?[^>]*>?\s*(?:<[^>]+>\s*)*(\d{1,2})[-/](\d{1,2})[-/](\d{4})",
                  ficha_html, re.I)
    if not m:
        t = _texto(ficha_html)
        m = re.search(r"Fecha de apertura\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})", t, re.I)
    if not m:
        return ""
    d, mth, y = m.groups()
    return f"{int(d):02d}/{int(mth):02d}/{y}"


def recolectar_san_isidro(verbose=False, todas=False):
    pagina = _get(SAN_ISIDRO_URL, con_sectigo=True)
    filas = []
    for nro, objeto, url in _si_items(pagina):
        fila = _fila("San Isidro", nro, objeto, "Licitación Pública", "", url)
        if fila["_match"] or todas:
            try:
                fila["fecha_apertura"] = _si_apertura(_get(url, con_sectigo=True))
            except Exception as e:  # noqa: BLE001  (si la ficha falla, queda sin fecha)
                print(f"   san isidro: ficha {nro} no disponible ({e})", file=sys.stderr)
        filas.append(fila)
    if verbose:
        print(f"   san isidro: {len(filas)} licitaciones leidas")
    return filas


# --- Avellaneda ----------------------------------------------------------------

def recolectar_avellaneda(verbose=False):
    """Listado de mda.gob.ar/tramites/licitaciones: cada item trae numero, objeto y
    fecha del decreto de llamado (la apertura esta en la nota PDF). Se usa la fecha
    del decreto como fecha de publicacion y se toma solo lo de VENTANA_DIAS."""
    pagina = _get(AVELLANEDA_URL, con_sectigo=True, timeout=120)
    filas = []
    # la pagina lista TODO el historico (700+ llamados), del mas nuevo al mas viejo;
    # alcanza con los primeros AVELLANEDA_MAX (los viejos a veces no traen fecha)
    cajas = re.findall(r'(?s)<div class="caja-sombra">(.*?)</div>\s*</li>', pagina)
    for caja in cajas[:AVELLANEDA_MAX]:
        cab = _texto(caja.split("<br")[0])
        cuerpo = _texto((re.search(r"(?s)<strong>(.*?)</strong>", caja) or [None, ""])[1])
        if not cab.upper().startswith(("LICITACI", "CONCURSO")):
            continue
        fecha = (re.search(r"fechado el (\d{1,2}/\d{1,2}/\d{4})", cuerpo) or [None, ""])[1]
        if fecha and not _reciente(fecha):
            continue
        objeto = re.sub(r"\s*DECRETO.*$", "", cuerpo, flags=re.I).strip()
        nota = (re.search(r'href="([^"]+)"', caja) or [None, AVELLANEDA_URL])[1]
        tipo = "Licitación Privada" if "PRIVADA" in cab.upper() else "Licitación Pública"
        filas.append(_fila("Avellaneda", cab.title(), objeto, tipo, "", nota, fecha))
    if verbose:
        print(f"   avellaneda: {len(filas)} licitaciones recientes")
    return filas


# --- Florencio Varela ----------------------------------------------------------

def recolectar_varela(verbose=False):
    """varela.gob.ar/lic: bloques con 'LICITACION PUBLICA N X/AAAA', OBJETO y APERTURA,
    o links al PDF con el objeto en el titulo. Se recorta el texto por cada llamado."""
    pagina = _get(VARELA_URL, timeout=90)
    txt = _texto(re.sub(r"(?s)<(script|style).*?</\1>", "", pagina))
    patron = re.compile(r"LICITACI[OÓ]N (P[UÚ]BLICA|PRIVADA) N[°º]\s*(\d+)\s*[-/]\s*(\d{4})", re.I)
    marcas = list(patron.finditer(txt))
    vistos, filas = set(), []
    for k, m in enumerate(marcas):
        nro = f"{m.group(2)}/{m.group(3)}"
        if nro in vistos:
            continue
        vistos.add(nro)
        seg = txt[m.end(): marcas[k + 1].start() if k + 1 < len(marcas) else m.end() + 1500]
        obj = re.search(r"OBJETO:\s*(.+?)(?:\. [A-Z]{4,}|FICHA|CONSULTAS|APERTURA|$)", seg)
        objeto = (obj.group(1) if obj else re.sub(r"^[^:]*:\s*", "", seg[:250])).strip(" .:-—")
        ap = (re.search(r"APERTURA:\s*(\d{1,2}/\d{1,2}/\d{4})", seg) or [None, ""])[1]
        tipo = "Licitación Privada" if "PRIV" in m.group(1).upper() else "Licitación Pública"
        filas.append(_fila("Florencio Varela", f"LP N°{nro}", objeto[:300], tipo, ap, VARELA_URL))
    if verbose:
        print(f"   florencio varela: {len(filas)} licitaciones leidas")
    return filas


# --- Berazategui ---------------------------------------------------------------

def _pdf_texto(datos):
    try:
        import io
        from pypdf import PdfReader
        return " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(datos)).pages[:2])
    except BaseException as e:  # noqa: BLE001  (sin pypdf o PDF escaneado: queda el titulo)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return ""


def recolectar_berazategui(verbose=False):
    """Las licitaciones se publican como entradas de WordPress (categoria 150). El
    objeto y la apertura estan en el PDF adjunto a cada entrada: se baja solo para
    las de los ultimos VENTANA_DIAS."""
    import json
    posts = json.loads(_get(BERAZATEGUI_API, timeout=90))
    filas = []
    for p in posts:
        pub = dt.date.fromisoformat(p["date"][:10]).strftime("%d/%m/%Y")
        if not _reciente(pub):
            continue
        titulo = _texto(p["title"]["rendered"])
        pdf = (re.search(r'href="([^"]+\.pdf)"', p["content"]["rendered"]) or [None, ""])[1]
        pdf = urllib.parse.urljoin("https://berazategui.gob.ar/", pdf) if pdf else p["link"]
        texto = re.sub(r"\s+", " ", _pdf_texto(_get_bytes(pdf, timeout=90)) if pdf.endswith(".pdf") else "")
        obj = re.search(r"objeto:\s*[“\"]?(.+?)[”\"]?\s*(?:Presupuesto|Venta|$)", texto, re.I)
        objeto = obj.group(1).strip() if obj else titulo
        ap = _fecha_larga((re.search(r"Apertura de ofertas:(.{0,120})", texto, re.I) or [None, ""])[1])
        nro = (re.search(r"N[°º]\s*(\d+/\d{4})", titulo) or [None, titulo])[1]
        if "segundo llamado" in titulo.lower():
            nro += " (2do llamado)"
        filas.append(_fila("Berazategui", f"LP N°{nro}", objeto, "Licitación Pública", ap, pdf, pub))
    if verbose:
        print(f"   berazategui: {len(filas)} licitaciones recientes")
    return filas


FUENTES = {"lanus": recolectar_lanus, "san_isidro": recolectar_san_isidro,
           "avellaneda": recolectar_avellaneda, "florencio_varela": recolectar_varela,
           "berazategui": recolectar_berazategui}


def recolectar(incluir_ruido=True, verbose=False):
    """Lista de dicts con la misma forma que pbac/bac/comprar, mas `url` por item.
    Solo devuelve lo que matchea palabras clave del rubro."""
    hits = []
    for nombre, fn in FUENTES.items():
        try:
            filas = fn(verbose=verbose)
        except Exception as e:  # noqa: BLE001  (una web caida no frena a la otra)
            print(f"   ! {nombre} fallo: {e}", file=sys.stderr)
            continue
        for f in filas:
            if f.pop("_match"):
                hits.append(f)
    if not incluir_ruido:
        hits = [r for r in hits if not r["es_ruido"]]
    if verbose:
        print(f"   municipios_web: {len(hits)} filas que matchean el rubro")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", action="store_true", help="mostrar todo lo leido, matchee o no")
    args = ap.parse_args()
    if args.todo:
        filas = recolectar_lanus(verbose=True) + recolectar_san_isidro(verbose=True, todas=True)
    else:
        filas = recolectar(verbose=True)
    for f in filas:
        marca = " *" if f.get("keywords_match") else ""
        print(f"[{f['unidad_ejecutora']}] {f['nro_proceso']} | ap {f['fecha_apertura'] or '-'} | "
              f"{f['nombre'][:70]}{marca}")


if __name__ == "__main__":
    main()
