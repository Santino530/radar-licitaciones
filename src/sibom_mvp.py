#!/usr/bin/env python3
"""
MVP del conector SIBOM — Radar de licitaciones (solo sector publico).

Que hace:
  1. Baja la lista de municipios de https://sibom.slyt.gba.gob.ar/cities y arma el
     diccionario nombre -> city_id (lo guarda en data/sibom_city_ids.csv).
  2. Busca en SIBOM cada palabra clave de neumaticos / recapado / gomeria.
  3. Parsea los resultados (titulo, municipio, fecha, link, tags, fragmento).
  4. Filtra a los municipios de la zona objetivo (AMBA + Gran La Plata) y, si se pide,
     a los publicados despues de una fecha de corte.
  5. Deduplica y escribe data/mvp_sibom_resultados.csv + un resumen por pantalla.

Es un MVP: sin dependencias externas (solo stdlib), para medir cuanto trae la fuente
antes de construir toda la infraestructura.

Nota tecnica: al 2026-08 el certificado SSL de sibom.slyt.gba.gob.ar esta VENCIDO.
Por eso se usa un contexto SSL sin verificacion. Es una fuente oficial del Estado y el
riesgo es aceptable, pero queda documentado aca y en docs/decisiones.md.

Uso:
  python src/sibom_mvp.py                      # todos los municipios objetivo, sin corte de fecha
  python src/sibom_mvp.py --desde 2025-01-01   # solo publicados desde esa fecha
  python src/sibom_mvp.py --municipios "Moron,Tres de Febrero,Ituzaingo"
  python src/sibom_mvp.py --todos              # no filtra por municipio (toda la provincia)
"""

import argparse
import csv
import datetime as dt
import html
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://sibom.slyt.gba.gob.ar"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))

# Contexto SSL sin verificacion (cert del server vencido, ver docstring).
SSL_CTX = ssl._create_unverified_context()

UA = {"User-Agent": "Mozilla/5.0 (radar-licitaciones MVP; contacto interno)"}

# --- Palabras clave del rubro -------------------------------------------------
# Traen resultado. Se buscan de a una (SIBOM hace AND entre palabras del query).
KEYWORDS = [
    "neumaticos",
    "neumatico",
    "cubiertas",
    "cubierta",
    "recapado",
    "recauchutaje",
    "gomeria",
    "camaras y llantas",
]

# --- Filtro de ruido -------------------------------------------------------
# Si el texto (titulo + tags + fragmento) contiene alguno de estos, NO es una
# compra de neumaticos/recapado: se marca como ruido y por defecto se descarta.
RUIDO_PATRONES = [
    "asfalt", "asfáltic", "mezcla bitum", "bituminos", "fresado",   # repavimentacion de calzada
    "de desuso", "fuera de uso", " nfu", "neumaticos en desuso",     # gestion de residuos
    "reciclad", "disposicion final",
    "habilitacion de comercio", "habilitacion comercial", "ampliacion de rubro",
    "cambio de razon social", "cambio razon social", "rubro gomeria",  # habilitaciones
    "ordenanza fiscal", "ordenanza impositiva", "impositiva",           # ordenanzas tributarias
    "convalida", "justifica gasto", "reparacion de chapa",             # rendiciones de gastos
]


def es_ruido(texto):
    t = texto.lower()
    return any(p in t for p in RUIDO_PATRONES)


# --- Municipios de la zona objetivo (AMBA + Gran La Plata) -------------------
# Se matchea contra el nombre tal cual lo escribe SIBOM (sin acento en algunos).
ZONA_OBJETIVO = {
    "La Matanza", "Moron", "Morón", "Tres de Febrero", "Ituzaingo", "Ituzaingó",
    "Hurlingham", "Merlo", "Moreno", "La Plata", "Lomas de Zamora", "Lanus", "Lanús",
    "Avellaneda", "Almirante Brown", "Quilmes", "Berazategui", "Florencio Varela",
    "Esteban Echeverria", "Esteban Echeverría", "Ezeiza", "Presidente Peron",
    "Presidente Perón", "San Vicente", "Canuelas", "Cañuelas", "General San Martin",
    "General San Martín", "Vicente Lopez", "Vicente López", "San Isidro", "San Fernando",
    "Tigre", "Malvinas Argentinas", "Jose C. Paz", "José C. Paz", "San Miguel",
    "Escobar", "Pilar", "Marcos Paz", "General Rodriguez", "General Rodríguez",
    "Berisso", "Ensenada", "Brandsen", "General Las Heras", "Exaltacion de la Cruz",
    "Exaltación de la Cruz", "Lujan", "Luján", "Campana", "Zarate", "Zárate",
}

# --- Partidos del interior con flota vial grande (mucha compra de neumaticos /
# recapado). Fuera de la zona operable, pero por volumen se siguen igual, en un
# grupo aparte (como hace la planilla de prospectos). ------------------------
ZONA_VOLUMEN_ALTO = {
    "Mercedes", "General Pueyrredon", "General Pueyrredón", "Bahia Blanca",
    "Bahía Blanca", "Trenque Lauquen", "General Villegas", "General Viamonte",
    "Chacabuco", "Tapalque", "Tapalqué", "Saladillo", "Roque Perez", "Roque Pérez",
    "Puan", "General Alvear", "Coronel Dorrego", "Coronel Suarez", "Coronel Suárez",
    "Coronel Rosales", "Coronel Pringles", "Balcarce", "Tandil", "Olavarria",
    "Olavarría", "Azul", "Nueve de Julio", "Pehuajó", "Pehuajo", "Lincoln",
    "Bragado", "Chivilcoy", "Junin", "Junín", "Pergamino", "Necochea", "Tres Arroyos",
}


def clasificar_zona(muni):
    m = muni.lower()
    if m in {z.lower() for z in ZONA_OBJETIVO}:
        return "objetivo"
    if m in {z.lower() for z in ZONA_VOLUMEN_ALTO}:
        return "volumen_alto"
    return "fuera"


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45, context=SSL_CTX) as r:
        raw = r.read()
    # SIBOM declara charset=utf-8. Algunos cuerpos de boletin traen bytes sueltos
    # malformados: se reemplazan (no se cae a latin-1, que romperia todos los acentos).
    return raw.decode("utf-8", "replace")


def get_city_ids():
    """nombre -> city_id, y lo persiste en data/sibom_city_ids.csv"""
    h = fetch(f"{BASE}/cities")
    pairs = re.findall(r'href="/cities/(\d+)"[^>]*>\s*([^<]+?)\s*<', h)
    seen = {}
    for cid, name in pairs:
        name = html.unescape(name).strip()
        if name and name not in seen:
            seen[name] = cid
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "sibom_city_ids.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["municipio", "city_id"])
        for name, cid in sorted(seen.items()):
            w.writerow([name, cid])
    return seen


BLOCK_RE = re.compile(r'<div class="search-result">(.*?)(?=<div class="search-result">|<ul class="pagination|</div>\s*</div>\s*</div>\s*<footer)', re.S)
TITLE_RE = re.compile(r'<p class="content-title"><a href="([^"]+)">([^<]+)</a>')
DATE_RE = re.compile(r'<span class="text-muted">(\d{2}/\d{2}/\d{4})</span>')
TAG_RE = re.compile(r'<span class="label label-primary">(?:<i[^>]*></i>)?([^<]+)</span>')
FRAG_RE = re.compile(r'<div style="padding: 10px 0px;">(.*?)</div>', re.S)
MUNI_RE = re.compile(r'\bde\s+(.+?)\s*$')


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_results(page_html):
    out = []
    for block in BLOCK_RE.findall(page_html):
        mt = TITLE_RE.search(block)
        if not mt:
            continue
        href, title = mt.group(1), strip_tags(mt.group(2))
        md = DATE_RE.search(block)
        fecha = md.group(1) if md else ""
        muni = ""
        mm = MUNI_RE.search(title)
        if mm:
            muni = mm.group(1).strip().rstrip(".")
        tags = [strip_tags(t) for t in TAG_RE.findall(block)]
        frags = [strip_tags(f) for f in FRAG_RE.findall(block)]
        frags = [f for f in frags if f and f != "..."]
        out.append({
            "municipio": muni,
            "fecha_publicacion": fecha,
            "titulo": title,
            "url": urllib.parse.urljoin(BASE, href),
            "tags": " | ".join(tags),
            "fragmento": " […] ".join(frags[:3])[:600],
        })
    return out


def fetch_reintentos(url, intentos=3):
    for i in range(1, intentos + 1):
        try:
            return fetch(url)
        except Exception as e:  # noqa: BLE001  (server lento/inestable)
            if i == intentos:
                raise
            print(f"  . reintento {i} ({e})", file=sys.stderr)
            time.sleep(3 * i)


def search_keyword(kw, max_pages=10, pause=1.0):
    results = []
    for page in range(1, max_pages + 1):
        params = {"utf8": "✓", "q[simple_query_string]": kw, "page": str(page)}
        url = f"{BASE}/search?" + urllib.parse.urlencode(params)
        try:
            h = fetch_reintentos(url)
        except Exception as e:  # noqa: BLE001
            print(f"  ! error en '{kw}' pagina {page}: {e}", file=sys.stderr)
            break
        got = parse_results(h)
        if not got:
            break
        results.extend(got)
        # SIBOM pagina de a 10; si vino menos, no hay mas.
        if len(got) < 10:
            break
        time.sleep(pause)
    return results


def to_date(s):
    try:
        return dt.datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


def recolectar(desde=None, max_pages=8, solo_zona=True, incluir_ruido=True,
               verbose=False):
    """Devuelve una lista de dicts normalizados de SIBOM. La usa el orquestador
    (src/radar.py) y tambien main() de este script.

    Campos de cada dict: municipio, categoria_zona, es_ruido, fecha_publicacion,
    titulo, url, tags, keywords (set), fragmento.
    """
    corte = None
    if isinstance(desde, str):
        corte = dt.datetime.strptime(desde, "%Y-%m-%d").date()
    elif isinstance(desde, dt.date):
        corte = desde

    by_url = {}
    for kw in KEYWORDS:
        for r in search_keyword(kw, max_pages=max_pages):
            if r["url"] in by_url:
                by_url[r["url"]]["keywords"].add(kw)
            else:
                r["keywords"] = {kw}
                by_url[r["url"]] = r

    rows = list(by_url.values())
    for r in rows:
        r["municipio"] = r["municipio"]
        r["categoria_zona"] = clasificar_zona(r["municipio"])
        r["es_ruido"] = es_ruido(r["titulo"] + " " + r["tags"] + " " + r["fragmento"])

    if solo_zona:
        rows = [r for r in rows if r["categoria_zona"] in ("objetivo", "volumen_alto")]
    if corte:
        rows = [r for r in rows
                if (d := to_date(r["fecha_publicacion"])) and d >= corte]
    if not incluir_ruido:
        rows = [r for r in rows if not r["es_ruido"]]

    rows.sort(key=lambda r: (to_date(r["fecha_publicacion"]) or dt.date.min), reverse=True)
    if verbose:
        print(f"   sibom: {len(rows)} filas")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="fecha de corte YYYY-MM-DD (solo publicados desde)")
    ap.add_argument("--municipios", help="lista separada por comas; sobreescribe los grupos")
    ap.add_argument("--todos", action="store_true",
                    help="no filtrar por municipio (toda la provincia)")
    ap.add_argument("--incluir-ruido", action="store_true",
                    help="no descartar los resultados marcados como ruido")
    ap.add_argument("--max-pages", type=int, default=10)
    args = ap.parse_args()

    corte = None
    if args.desde:
        corte = dt.datetime.strptime(args.desde, "%Y-%m-%d").date()

    lista_manual = None
    if args.municipios:
        lista_manual = {m.strip().lower() for m in args.municipios.split(",")}

    print("1) Bajando lista de municipios de SIBOM ...")
    cities = get_city_ids()
    print(f"   {len(cities)} municipios. Guardado en data/sibom_city_ids.csv")

    print("2) Buscando por palabra clave ...")
    by_url = {}
    for kw in KEYWORDS:
        got = search_keyword(kw, max_pages=args.max_pages)
        print(f"   '{kw}': {len(got)} resultados crudos")
        for r in got:
            if r["url"] in by_url:
                by_url[r["url"]]["keywords"].add(kw)
            else:
                r["keywords"] = {kw}
                by_url[r["url"]] = r

    rows = list(by_url.values())
    print(f"3) {len(rows)} resultados unicos (deduplicados por URL)")

    # Clasificar cada fila: grupo de zona y si es ruido
    for r in rows:
        r["categoria_zona"] = clasificar_zona(r["municipio"])
        r["es_ruido"] = es_ruido(r["titulo"] + " " + r["tags"] + " " + r["fragmento"])

    # Filtro por municipio
    if args.todos:
        pass
    elif lista_manual is not None:
        rows = [r for r in rows if r["municipio"].lower() in lista_manual]
        print(f"   {len(rows)} en la lista pedida")
    else:
        rows = [r for r in rows if r["categoria_zona"] in ("objetivo", "volumen_alto")]
        n_obj = sum(1 for r in rows if r["categoria_zona"] == "objetivo")
        n_vol = sum(1 for r in rows if r["categoria_zona"] == "volumen_alto")
        print(f"   {len(rows)} en zona objetivo ({n_obj}) + volumen alto ({n_vol})")

    # Filtro por fecha
    if corte:
        rows = [r for r in rows
                if (d := to_date(r["fecha_publicacion"])) and d >= corte]
        print(f"   {len(rows)} publicados desde {corte}")

    # Filtro de ruido
    n_ruido = sum(1 for r in rows if r["es_ruido"])
    if not args.incluir_ruido:
        rows = [r for r in rows if not r["es_ruido"]]
        print(f"   {len(rows)} tras descartar {n_ruido} marcados como ruido")
    else:
        print(f"   ({n_ruido} marcados como ruido, se mantienen por --incluir-ruido)")

    rows.sort(key=lambda r: (to_date(r["fecha_publicacion"]) or dt.date.min), reverse=True)

    out_path = os.path.join(DATA, "mvp_sibom_resultados.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["municipio", "categoria_zona", "es_ruido", "fecha_publicacion",
                    "titulo", "url", "tags", "keywords_match", "fragmento"])
        for r in rows:
            w.writerow([r["municipio"], r["categoria_zona"],
                        "si" if r["es_ruido"] else "no", r["fecha_publicacion"],
                        r["titulo"], r["url"], r["tags"],
                        ",".join(sorted(r["keywords"])), r["fragmento"]])
    print(f"4) Escrito {out_path}  ({len(rows)} filas)")

    # Resumen
    from collections import Counter
    print("\n--- RESUMEN por municipio ---")
    c = Counter((r["categoria_zona"], r["municipio"]) for r in rows)
    for (cat, muni), n in c.most_common():
        print(f"  {n:3}  [{cat:12}] {muni}")
    print("\n--- 15 mas recientes ---")
    for r in rows[:15]:
        print(f"  {r['fecha_publicacion']}  {r['municipio']:<22}  {r['titulo'][:66]}")


if __name__ == "__main__":
    main()
