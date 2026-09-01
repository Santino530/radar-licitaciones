#!/usr/bin/env python3
"""
Genera el panel (dashboard) del radar de licitaciones a partir de los CSV del repo.

Lee:
  data/radar_sin_verificar.csv   (salida del motor: lo que hay que revisar)
  data/proveedores_conocidos.csv (contexto: quien ya le vende al Estado)
  data/contactos_compras.csv     (tabla manual: comprador -> mail/tel de compras)

Escribe:
  web/dashboard.html   -> una pagina HTML autocontenida (datos embebidos como JSON)

Esa pagina se publica como Artifact (privado, con link para compartir). Para
actualizarla: correr de nuevo este script y republicar el Artifact con la MISMA URL.

Uso:
  python scripts/build_dashboard.py
  python scripts/build_dashboard.py --out web/dashboard.html
"""

import argparse
import csv
import datetime as dt
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DATA = os.path.join(ROOT, "data")

FECHA_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")

# Palabra clave -> categoria de producto (para el filtro del panel). Una fila puede
# caer en varias. El orden define como se muestran.
CATEGORIA_PRODUCTO = [
    ("Neumáticos", ("neumatic", "cubierta", "recapado", "recauchut", "gomeria", "vulcaniz")),
    ("Llantas", ("llanta",)),
    ("Cámaras", ("camara",)),
    ("Baterías", ("bateria", "acumulador")),
    ("Protectores", ("protector",)),
]
DIAS_NUEVA = 7            # una deteccion de los ultimos N dias se marca "nueva"
DIAS_SIBOM_VIGENTE = 45   # SIBOM sin fecha de apertura: vigente hasta N dias de publicada
GRACIA_APERTURA = 1       # dias despues de la apertura antes de darla por cerrada


def productos_de(keywords):
    out = []
    for label, needles in CATEGORIA_PRODUCTO:
        if any(any(n in k.lower() for n in needles) for k in keywords):
            out.append(label)
    return out or ["Otros"]


def parse_fecha(s):
    """'dd/mm/aaaa' o 'dd/mm/aaaa HH:MM Hrs.' -> (iso 'aaaa-mm-dd', label legible)."""
    if not s:
        return None, ""
    m = FECHA_RE.search(s)
    if not m:
        return None, s.strip()
    d, mo, y, hh, mm = m.groups()
    iso = f"{y}-{mo}-{d}"
    label = f"{d}/{mo}/{y}"
    if hh:
        label += f" {int(hh):02d}:{mm}"
    return iso, label


def cargar_radar(contactos=None):
    path = os.path.join(DATA, "radar_sin_verificar.csv")
    hoy = dt.date.today()
    contactos = contactos or {}
    filas = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            es_ruido = (r.get("es_ruido") or "").strip().lower() == "si"
            estado = (r.get("estado") or "").strip().lower()

            pub_iso, pub_label = parse_fecha(r.get("fecha_publicacion"))
            ap_iso, ap_label = parse_fecha(r.get("fecha_apertura"))

            dias_apertura = None
            if ap_iso:
                try:
                    dias_apertura = (dt.date.fromisoformat(ap_iso) - hoy).days
                except ValueError:
                    dias_apertura = None

            dias_pub = None
            if pub_iso:
                try:
                    dias_pub = (hoy - dt.date.fromisoformat(pub_iso)).days
                except ValueError:
                    dias_pub = None

            # 3 grupos para las pestañas: vigente / cerrada / ruido.
            # "cierre" explica POR QUE una cerrada esta cerrada (para el cartel).
            cierre = ""
            if es_ruido:
                grupo = "ruido"
            elif estado in ("adjudicada", "cerrada"):
                grupo = "cerrada"
            elif dias_apertura is not None and dias_apertura < -GRACIA_APERTURA:
                grupo, cierre = "cerrada", "apertura_pasada"
            elif ap_iso is None and dias_pub is not None and dias_pub > DIAS_SIBOM_VIGENTE:
                grupo, cierre = "cerrada", "vencida_estimada"
            else:
                grupo = "vigente"

            por_vencer = (grupo == "vigente" and dias_apertura is not None
                          and 0 <= dias_apertura <= 21)

            fragmento = (r.get("fragmento") or "").strip()
            objeto = (r.get("objeto") or "").strip()
            fuente = (r.get("fuente") or "").strip()
            id_origen = (r.get("id_origen") or "").strip()
            tipo = (r.get("tipo") or "").strip()
            texto = fragmento or objeto
            # referencia secundaria: si el texto principal es el fragmento, mostramos
            # el "objeto" (la cita del boletin / el nro de proceso) como referencia.
            ref = objeto if fragmento else ""
            if not ref and fuente in ("pbac", "bac"):
                ref = f"Proceso {id_origen}"

            kws = [k.strip() for k in (r.get("keywords") or "").split(",") if k.strip()]

            detectada = (r.get("primera_vez_visto") or "").strip()
            nueva = False
            try:
                nueva = (hoy - dt.date.fromisoformat(detectada)).days <= DIAS_NUEVA
            except ValueError:
                pass

            comprador = (r.get("comprador") or "").strip()
            filas.append({
                "fuente": fuente,
                "comprador": comprador,
                "objeto": objeto,
                "fragmento": fragmento,
                "texto": texto,
                "ref": ref,
                "tipo": tipo,
                "id_origen": id_origen,
                "estado": estado,
                "grupo": grupo,
                "cierre": cierre,
                "por_vencer": por_vencer,
                "zona": (r.get("categoria_zona") or "").strip(),
                "keywords": kws,
                "productos": productos_de(kws),
                "nueva": nueva,
                "url": (r.get("url") or "").strip(),
                "fecha_pub_iso": pub_iso or "",
                "fecha_pub_label": pub_label,
                "fecha_ap_iso": ap_iso or "",
                "fecha_ap_label": ap_label,
                "dias_apertura": dias_apertura,
                "detectada": detectada,
                "corrida": (r.get("vista_ultima_corrida") or "").strip(),
                "contacto": buscar_contacto(comprador, fuente, contactos),
            })
    return filas


def ordenar(filas):
    def clave_vigente(x):
        # por vencer primero (por fecha de apertura), despues nuevas, despues por
        # publicacion mas reciente
        return (not x["por_vencer"],
                x["fecha_ap_iso"] or "9999-99-99",
                not x["nueva"],
                _neg(x["fecha_pub_iso"]))

    vig = sorted((f for f in filas if f["grupo"] == "vigente"), key=clave_vigente)
    cer = sorted((f for f in filas if f["grupo"] == "cerrada"),
                 key=lambda x: (x["fecha_ap_iso"] or x["fecha_pub_iso"] or ""),
                 reverse=True)
    rui = sorted((f for f in filas if f["grupo"] == "ruido"),
                 key=lambda x: x["fecha_pub_iso"], reverse=True)
    return vig + cer + rui


def _neg(iso):
    """clave para ordenar fechas ISO de mas nueva a mas vieja dentro de un sort asc."""
    if not iso:
        return "0000-00-00"
    # invierte cada digito para que 'mayor fecha' ordene primero
    return "".join(chr(ord("9") - (ord(c) - 48)) if c.isdigit() else c for c in iso)


def cargar_proveedores():
    path = os.path.join(DATA, "proveedores_conocidos.csv")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append({
                "nombre": (r.get("nombre") or "").strip(),
                "tipo": (r.get("tipo") or "").strip(),
                "detectado_como": (r.get("detectado_como") or "").strip(),
                "visto_en": (r.get("visto_en") or "").strip(),
            })
    return out


def _clave_comprador(s):
    """Normaliza el nombre del comprador para cruzar con contactos_compras.csv."""
    s = (s or "").lower().strip()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def cargar_contactos():
    """comprador (normalizado) -> {email, telefono, web_compras, notas}.
    contactos_compras.csv es una tabla de referencia manual: oficinas de compras
    del sector publico (dato semi-publico, esta en las webs de cada organismo).
    Arranca casi vacia y el equipo la va completando a medida que trabaja leads."""
    path = os.path.join(DATA, "contactos_compras.csv")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = _clave_comprador(r.get("comprador"))
            if not k:
                continue
            out[k] = {
                "email": (r.get("email") or "").strip(),
                "telefono": (r.get("telefono") or "").strip(),
                "web_compras": (r.get("web_compras") or "").strip(),
                "notas": (r.get("notas") or "").strip(),
            }
    return out


def buscar_contacto(comprador, fuente, contactos):
    """SIBOM: el 'comprador' ES el municipio -> se busca su oficina de compras.
    PBAC / BAC: el 'comprador' es una unidad ejecutora (hospital, ministerio...) ->
    se usa el contacto general del portal, NO se intenta adivinar por el nombre."""
    if not contactos:
        return None
    if fuente in ("pbac", "bac"):
        return contactos.get(fuente)
    k = _clave_comprador(comprador)
    if k in contactos:
        return contactos[k]
    # match parcial solo para SIBOM (nombre de municipio con/sin acento, "de", etc.)
    for ck, cv in contactos.items():
        if ck in ("pbac", "bac"):
            continue
        if ck and (ck in k or k in ck) and len(ck) > 4:
            return cv
    return None


def cargar_historico():
    path = os.path.join(DATA, "historico.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


MES_NOMBRE = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep",
              "oct", "nov", "dic"]


def _mes_label(iso):
    """'2026-08-xx' -> 'ago 2026'."""
    m = re.match(r"(\d{4})-(\d{2})", iso or "")
    if not m:
        return ""
    return f"{MES_NOMBRE[int(m.group(2))]} {m.group(1)}"


def construir_metricas(hist):
    """Agrega el historico para la pestaña Metricas. Ignora el ruido."""
    from collections import Counter

    reales = [h for h in hist if (h.get("es_ruido") or "").strip().lower() != "si"]
    estados = [(h.get("estado") or "").strip().lower() for h in reales]

    comp = Counter(h.get("comprador", "").strip() or "—" for h in reales)
    top = comp.most_common(12)
    otros = sum(n for _, n in comp.most_common()[12:])
    por_comprador = [{"label": c, "n": n} for c, n in top]
    if otros:
        por_comprador.append({"label": f"otros ({len(comp) - 12})", "n": otros})

    def _pub(h):
        """(anio, mes) de la fecha del boletin; respaldo: primera_vez_visto."""
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", h.get("fecha_publicacion") or "")
        if m:
            return int(m.group(3)), int(m.group(2))
        m = re.match(r"(\d{4})-(\d{2})", h.get("primera_vez_visto") or "")
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None

    anios, meses_est = Counter(), Counter()
    for h in reales:
        a, mm = _pub(h)
        if a:
            anios[a] += 1
        if mm:
            meses_est[mm] += 1
    por_anio = [{"label": str(a), "n": anios[a]} for a in sorted(anios)]
    por_mes = [{"label": MES_NOMBRE[mm], "n": meses_est.get(mm, 0)}
               for mm in range(1, 13)]

    prod = Counter()
    for h in reales:
        kws = [k.strip() for k in (h.get("keywords") or "").split(",") if k.strip()]
        for p in productos_de(kws):
            prod[p] += 1
    por_producto = [{"label": p, "n": n} for p, n in prod.most_common()]

    return {
        "total": len(reales),
        "municipios": len(comp),
        "adjudicadas": sum(1 for e in estados if e == "adjudicada"),
        "descartadas": sum(1 for h in hist
                           if (h.get("es_ruido") or "").strip().lower() == "si"),
        "desde": min((h.get("primera_vez_visto", "") for h in hist), default=""),
        "por_comprador": por_comprador,
        "por_anio": por_anio,
        "por_mes": por_mes,
        "por_producto": por_producto,
    }


def construir_meta(filas):
    corridas = [f["corrida"] for f in filas if f["corrida"]]
    ultima = max(corridas) if corridas else ""
    vigentes = [f for f in filas if f["grupo"] == "vigente"]
    return {
        "generado": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ultima_corrida": ultima,
        "vigentes": len(vigentes),
        "por_vencer": sum(1 for f in vigentes if f["por_vencer"]),
        "nuevas": sum(1 for f in vigentes if f["nueva"]),
        "cerradas": sum(1 for f in filas if f["grupo"] == "cerrada"),
        "ruido": sum(1 for f in filas if f["grupo"] == "ruido"),
        "fuentes": sorted({f["fuente"] for f in filas if f["fuente"]}),
        "dias_nueva": DIAS_NUEVA,
        "dias_sibom": DIAS_SIBOM_VIGENTE,
    }


def json_para_script(x):
    return json.dumps(x, ensure_ascii=False).replace("</", "<\\/")


def render(filas, proveedores, meta, metricas):
    html = PLANTILLA
    html = html.replace("__RADAR_JSON__", json_para_script(filas))
    html = html.replace("__PROVEEDORES_JSON__", json_para_script(proveedores))
    html = html.replace("__META_JSON__", json_para_script(meta))
    html = html.replace("__METRICAS_JSON__", json_para_script(metricas))
    return html


def render_full(html_bare):
    """Envuelve el HTML "pelado" (pensado para el Artifact) en un documento completo,
    para servirlo como pagina estatica en GitHub Pages. Corta en `</style>`: lo de
    antes va al <head>, lo de despues al <body>."""
    corte = html_bare.index("</style>") + len("</style>")
    head, body = html_bare[:corte], html_bare[corte:]
    return (
        "<!doctype html>\n<html lang=\"es\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"robots\" content=\"noindex, nofollow\">\n"
        "<meta name=\"color-scheme\" content=\"light dark\">\n"
        + head + "\n</head>\n<body>\n" + body.lstrip() + "\n</body>\n</html>\n"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "web", "dashboard.html"),
                    help="HTML 'pelado' para publicar como Artifact")
    ap.add_argument("--pages", default=os.path.join(ROOT, "docs", "index.html"),
                    help="documento HTML completo para GitHub Pages (link publico)")
    ap.add_argument("--sin-pages", action="store_true", help="no generar el de Pages")
    args = ap.parse_args()

    contactos = cargar_contactos()
    filas = ordenar(cargar_radar(contactos))
    proveedores = cargar_proveedores()
    meta = construir_meta(filas)
    metricas = construir_metricas(cargar_historico())

    bare = render(filas, proveedores, meta, metricas)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(bare)
    print(f"Panel (Artifact) generado: {args.out}")

    if not args.sin_pages:
        os.makedirs(os.path.dirname(args.pages), exist_ok=True)
        with open(args.pages, "w", encoding="utf-8") as f:
            f.write(render_full(bare))
        print(f"Panel (GitHub Pages) generado: {args.pages}")

    print(f"  {meta['vigentes']} vigentes ({meta['por_vencer']} por vencer, "
          f"{meta['nuevas']} nuevas) · {meta['cerradas']} cerradas/adjudicadas · "
          f"{meta['ruido']} descartadas")
    print(f"  ultima corrida del motor: {meta['ultima_corrida'] or 's/d'}")


PLANTILLA = r"""<title>Licitaciones de Neumáticos</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {
    --ground: #f4f4f1;
    --surface: #ffffff;
    --surface-2: #fbfaf8;
    --ink: #1b1a18;
    --ink-soft: #57544e;
    --line: #e4e1da;
    --accent: #2f4b6e;
    --accent-soft: #eef1f5;
    --open: #1f7a4d;
    --soon: #a8620a;
    --closed: #6b7280;
    --noise: #9b9992;
    --shadow: 0 1px 2px rgba(20, 18, 15, .06), 0 8px 24px rgba(20, 18, 15, .05);
  }
  :root:not([data-theme="light"]) {
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --ground: #15171a;
      --surface: #1e2126;
      --surface-2: #23272c;
      --ink: #e9e6e0;
      --ink-soft: #a4a09a;
      --line: #33373d;
      --accent: #8fb3dd;
      --accent-soft: #23303f;
      --open: #4fb183;
      --soon: #d0913f;
      --closed: #9aa1ab;
      --noise: #7d7b75;
      --shadow: 0 1px 2px rgba(0, 0, 0, .3), 0 8px 24px rgba(0, 0, 0, .25);
      color-scheme: dark;
    }
  }
  :root[data-theme="dark"] {
    --ground: #15171a;
    --surface: #1e2126;
    --surface-2: #23272c;
    --ink: #e9e6e0;
    --ink-soft: #a4a09a;
    --line: #33373d;
    --accent: #8fb3dd;
    --accent-soft: #23303f;
    --open: #4fb183;
    --soon: #d0913f;
    --closed: #9aa1ab;
    --noise: #7d7b75;
    --shadow: 0 1px 2px rgba(0, 0, 0, .3), 0 8px 24px rgba(0, 0, 0, .25);
    color-scheme: dark;
  }

  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--ground);
    color: var(--ink);
    font-family: "IBM Plex Sans", system-ui, -apple-system, sans-serif;
    font-size: 15px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }

  .masthead { border-bottom: 2px solid var(--ink); padding-bottom: 16px; margin-bottom: 24px; }
  .masthead__eyebrow {
    font-family: "IBM Plex Mono", monospace;
    font-size: 11px; letter-spacing: .14em; text-transform: uppercase;
    color: var(--accent); margin: 0 0 6px;
  }
  .masthead__bar { display: flex; align-items: flex-start; gap: 12px; justify-content: space-between; }
  .masthead__title {
    font-family: "Libre Franklin", sans-serif; font-weight: 700;
    font-size: clamp(1.6rem, 4vw, 2.15rem); line-height: 1.1;
    margin: 0; text-wrap: balance;
  }
  .masthead__sub { color: var(--ink-soft); margin: 8px 0 0; max-width: 62ch; }
  .theme-toggle {
    flex: 0 0 auto; appearance: none; cursor: pointer;
    background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
    color: var(--ink-soft); font: inherit; font-size: 13px; padding: 6px 10px;
    display: inline-flex; align-items: center; gap: 6px;
    transition: border-color 120ms ease, color 120ms ease;
  }
  .theme-toggle:hover { border-color: var(--accent); color: var(--ink); }
  .masthead__meta {
    font-family: "IBM Plex Mono", monospace; font-size: 12px;
    color: var(--ink-soft); margin-top: 12px; display: flex; flex-wrap: wrap; gap: 4px 18px;
  }
  .masthead__meta b { color: var(--ink); font-weight: 500; }

  .summary {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px; margin-bottom: 22px;
  }
  .tile {
    appearance: none; text-align: left; cursor: pointer;
    background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
    padding: 12px 14px; color: inherit; font: inherit;
    transition: border-color 120ms ease, background 120ms ease;
  }
  .tile:hover { border-color: var(--accent); }
  .tile[aria-pressed="true"] {
    border-color: var(--accent); background: var(--accent-soft);
    box-shadow: inset 0 0 0 1px var(--accent);
  }
  .tile__n {
    font-family: "IBM Plex Mono", monospace; font-weight: 500;
    font-size: 1.7rem; line-height: 1; font-variant-numeric: tabular-nums;
    display: block; margin-bottom: 4px;
  }
  .tile__l {
    font-family: "Libre Franklin", sans-serif; font-weight: 600;
    font-size: 12px; letter-spacing: .03em; text-transform: uppercase;
    color: var(--ink-soft);
  }
  .tile--soon .tile__n { color: var(--soon); }
  .tile--open .tile__n { color: var(--open); }
  .tile--nueva .tile__n { color: var(--accent); }

  .controls {
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    margin-bottom: 26px;
  }
  .controls input[type="search"], .controls select {
    font: inherit; color: var(--ink); background: var(--surface);
    border: 1px solid var(--line); border-radius: 8px; padding: 8px 11px;
  }
  .controls input[type="search"] { flex: 1 1 220px; min-width: 0; }
  .controls select { flex: 0 0 auto; }
  .tabs {
    display: flex; flex-wrap: wrap; gap: 2px; margin-bottom: 22px;
    border-bottom: 2px solid var(--line);
  }
  .tab {
    appearance: none; cursor: pointer; background: none; border: 0;
    border-bottom: 2px solid transparent; margin-bottom: -2px;
    font-family: "Libre Franklin", sans-serif; font-weight: 600; font-size: 14px;
    color: var(--ink-soft); padding: 9px 14px;
    transition: color 120ms ease, border-color 120ms ease;
  }
  .tab:hover { color: var(--ink); }
  .tab[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent); }
  .tab__n {
    font-family: "IBM Plex Mono", monospace; font-weight: 500; font-size: 11px;
    color: var(--ink-soft); margin-left: 5px;
  }
  .tab[aria-selected="true"] .tab__n { color: var(--accent); }

  .section { margin-bottom: 34px; }
  .section__head {
    display: flex; align-items: baseline; gap: 10px;
    border-bottom: 1px solid var(--line); padding-bottom: 6px; margin-bottom: 14px;
  }
  .section__title {
    font-family: "Libre Franklin", sans-serif; font-weight: 700;
    font-size: 1.05rem; margin: 0;
  }
  .section__count {
    font-family: "IBM Plex Mono", monospace; font-size: 12px; color: var(--ink-soft);
  }
  .section__note { font-size: 12px; color: var(--ink-soft); margin: -6px 0 14px; }

  .cards { display: flex; flex-direction: column; gap: 10px; }
  .card {
    position: relative; background: var(--surface);
    border: 1px solid var(--line); border-radius: 10px;
    padding: 14px 16px 14px 20px; box-shadow: var(--shadow);
    transition: border-color 120ms ease, transform 120ms ease;
  }
  .card:hover { border-color: var(--accent); }
  .card::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
    border-radius: 10px 0 0 10px; background: var(--closed);
  }
  .card--open::before { background: var(--open); }
  .card--soon::before { background: var(--soon); }
  .card--noise::before { background: var(--noise); }

  .card__head {
    appearance: none; width: 100%; background: none; border: 0; padding: 0;
    margin: 0; cursor: pointer; color: inherit; font: inherit; text-align: left;
    display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
  }
  .card__head:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 4px; }
  .card__buyer {
    font-family: "Libre Franklin", sans-serif; font-weight: 600; font-size: 1rem;
    margin: 0; color: var(--ink); flex: 1 1 auto;
  }
  .card__pills { display: inline-flex; gap: 6px; align-items: baseline; flex: 0 0 auto; }
  .card__chevron {
    flex: 0 0 auto; color: var(--ink-soft); font-size: 12px; transition: transform 120ms ease;
    align-self: center;
  }
  .card--expanded .card__chevron { transform: rotate(90deg); }
  .pill {
    font-family: "IBM Plex Mono", monospace; font-size: 10.5px; font-weight: 500;
    letter-spacing: .04em; text-transform: uppercase;
    padding: 3px 7px; border-radius: 999px; white-space: nowrap;
    border: 1px solid currentColor;
  }
  .pill--open { color: var(--open); }
  .pill--soon { color: var(--soon); }
  .pill--closed { color: var(--closed); }
  .pill--noise { color: var(--noise); }
  .pill--nueva {
    color: var(--surface); background: var(--accent); border-color: var(--accent);
  }
  .card--flag { border-color: var(--accent); }
  .card--flag::after {
    content: ""; position: absolute; inset: 0; border-radius: 10px;
    box-shadow: inset 0 0 0 1px var(--accent); pointer-events: none;
  }

  .card__detail {
    margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line);
  }
  .detail {
    display: grid; grid-template-columns: max-content 1fr; gap: 7px 16px;
    font-size: 13px; margin-bottom: 12px;
  }
  .detail__k {
    font-family: "IBM Plex Mono", monospace; font-size: 10.5px; letter-spacing: .04em;
    text-transform: uppercase; color: var(--ink-soft); padding-top: 2px;
  }
  .detail__v { color: var(--ink); white-space: pre-wrap; word-break: break-word; }
  .detail__v a { color: var(--accent); }
  .contacto {
    background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px;
    padding: 11px 13px; font-size: 13px;
  }
  .contacto__t {
    font-family: "Libre Franklin", sans-serif; font-weight: 700; font-size: 12px;
    text-transform: uppercase; letter-spacing: .03em; color: var(--ink-soft); margin: 0 0 7px;
  }
  .contacto__row { display: flex; flex-wrap: wrap; gap: 3px 8px; margin: 3px 0; align-items: baseline; }
  .contacto__row b {
    font-family: "IBM Plex Mono", monospace; font-size: 10.5px; text-transform: uppercase;
    color: var(--ink-soft); font-weight: 500; flex: 0 0 62px;
  }
  .contacto a { color: var(--accent); word-break: break-all; }
  .btn-buscar {
    appearance: none; display: inline-block; margin-top: 4px;
    background: var(--accent); color: var(--surface); text-decoration: none;
    font-size: 12px; font-weight: 500; padding: 6px 11px; border-radius: 7px;
  }
  .detail__note { font-size: 11.5px; color: var(--ink-soft); margin: 10px 0 0; }
  @media (max-width: 560px) {
    .detail { grid-template-columns: 1fr; gap: 2px 0; }
    .detail__k { padding-top: 8px; }
  }

  .card__object { margin: 8px 0 0; color: var(--ink); }
  .card__ref {
    font-family: "IBM Plex Mono", monospace; font-size: 11.5px; color: var(--ink-soft);
    margin: 6px 0 0; word-break: break-word;
  }
  .card__chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .chip {
    font-size: 11px; padding: 2px 8px; border-radius: 6px;
    background: var(--surface-2); border: 1px solid var(--line); color: var(--ink-soft);
  }
  .chip--src { color: var(--accent); border-color: var(--accent); }
  .chip--zone { color: var(--ink); }
  .card__foot {
    display: flex; flex-wrap: wrap; gap: 4px 16px; align-items: center;
    margin-top: 11px; padding-top: 10px; border-top: 1px dashed var(--line);
    font-family: "IBM Plex Mono", monospace; font-size: 11.5px; color: var(--ink-soft);
  }
  .card__link {
    margin-left: auto; color: var(--accent); text-decoration: none; font-weight: 500;
  }
  .card__link:hover { text-decoration: underline; }

  .empty {
    background: var(--surface-2); border: 1px dashed var(--line); border-radius: 10px;
    padding: 18px; color: var(--ink-soft); font-size: 13px; text-align: center;
  }

  .aside {
    background: var(--surface-2); border: 1px solid var(--line); border-radius: 10px;
    padding: 16px 18px; margin-top: 8px;
  }
  .aside__title {
    font-family: "Libre Franklin", sans-serif; font-weight: 700; font-size: .95rem;
    margin: 0 0 4px;
  }
  .aside__note { font-size: 12px; color: var(--ink-soft); margin: 0 0 12px; }
  .prov { display: flex; flex-direction: column; gap: 8px; }
  .prov__row { display: flex; flex-wrap: wrap; gap: 2px 10px; align-items: baseline; }
  .prov__name {
    font-family: "IBM Plex Mono", monospace; font-size: 12.5px; font-weight: 500; color: var(--ink);
  }
  .prov__what { font-size: 12px; color: var(--ink-soft); }

  footer {
    margin-top: 40px; padding-top: 18px; border-top: 1px solid var(--line);
    font-size: 12.5px; color: var(--ink-soft);
  }
  footer p { margin: 6px 0; }
  footer b { color: var(--ink); font-weight: 600; }
  .disclaimer {
    background: var(--accent-soft); border: 1px solid var(--line);
    border-left: 3px solid var(--accent); border-radius: 6px;
    padding: 10px 13px; color: var(--ink); margin: 14px 0;
  }

  .legend {
    background: var(--surface-2); border: 1px solid var(--line); border-radius: 10px;
    padding: 0 16px; margin-bottom: 26px; font-size: 13px;
  }
  .legend > summary {
    cursor: pointer; padding: 12px 0; color: var(--ink-soft);
    font-family: "Libre Franklin", sans-serif; font-weight: 600; list-style: none;
  }
  .legend > summary::-webkit-details-marker { display: none; }
  .legend > summary::before { content: "▸ "; color: var(--accent); }
  .legend[open] > summary::before { content: "▾ "; }
  .legend__body { padding: 0 0 14px; display: flex; flex-direction: column; gap: 7px; color: var(--ink-soft); }
  .legend__body b { color: var(--ink); font-weight: 600; }
  .legend__dot {
    display: inline-block; width: 9px; height: 9px; border-radius: 2px;
    margin-right: 6px; vertical-align: middle;
  }

  /* --- pestaña Métricas --- */
  .met { display: flex; flex-direction: column; gap: 30px; }
  .met__stats {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px;
  }
  .met__stat {
    background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
    padding: 12px 14px;
  }
  .met__stat b {
    font-family: "IBM Plex Mono", monospace; font-weight: 500; font-size: 1.6rem;
    display: block; line-height: 1; margin-bottom: 4px; font-variant-numeric: tabular-nums;
  }
  .met__stat span {
    font-family: "Libre Franklin", sans-serif; font-weight: 600; font-size: 11px;
    letter-spacing: .03em; text-transform: uppercase; color: var(--ink-soft);
  }
  .chart__t {
    font-family: "Libre Franklin", sans-serif; font-weight: 700; font-size: 1rem;
    margin: 0 0 3px;
  }
  .chart__sub { font-size: 12px; color: var(--ink-soft); margin: 0 0 14px; }
  .barh { display: flex; flex-direction: column; gap: 5px; }
  .barh__row {
    display: grid; grid-template-columns: 148px 1fr auto; gap: 10px; align-items: center;
    font-size: 12.5px;
  }
  .barh__lbl {
    color: var(--ink-soft); text-align: right; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
  }
  .barh__track { background: var(--surface-2); border-radius: 3px; height: 16px; }
  .barh__fill {
    height: 16px; min-width: 2px; background: var(--accent);
    border-radius: 3px; transition: filter 120ms ease;
  }
  .barh__row:hover .barh__fill { filter: brightness(1.12); }
  .barh__val {
    font-family: "IBM Plex Mono", monospace; color: var(--ink);
    font-variant-numeric: tabular-nums; min-width: 2ch; text-align: right;
  }
  @media (max-width: 560px) {
    .barh__row { grid-template-columns: 96px 1fr auto; }
  }

  @media (prefers-reduced-motion: reduce) {
    * { transition: none !important; }
  }
  @media (max-width: 560px) {
    .wrap { padding: 24px 14px 48px; }
    .card__link { margin-left: 0; }
    .masthead__bar { flex-wrap: wrap; }
  }
</style>

<div class="wrap">
  <header class="masthead">
    <p class="masthead__eyebrow">Sector público · actualización diaria</p>
    <div class="masthead__bar">
      <h1 class="masthead__title">Radar de Licitaciones — Neumáticos</h1>
      <button type="button" class="theme-toggle" id="tema" aria-label="Cambiar tema">
        <span id="temaIcon">◐</span> <span id="temaTxt">Tema</span>
      </button>
    </div>
    <p class="masthead__sub">
      Licitaciones públicas de neumáticos, recapado, llantas, cámaras, baterías de
      vehículo y protectores, en municipios de la Provincia de Buenos Aires, el Estado
      provincial y la Ciudad de Buenos Aires.
    </p>
    <p class="masthead__meta" id="meta"></p>
  </header>

  <section class="summary" id="summary" aria-label="Resumen"></section>

  <div class="controls" id="controls">
    <input type="search" id="q" placeholder="Buscar comprador, objeto, palabra clave…" aria-label="Buscar">
    <select id="producto" aria-label="Filtrar por producto">
      <option value="">Todos los productos</option>
      <option value="Neumáticos">Neumáticos</option>
      <option value="Llantas">Llantas</option>
      <option value="Cámaras">Cámaras</option>
      <option value="Baterías">Baterías</option>
      <option value="Protectores">Protectores</option>
    </select>
    <select id="zona" aria-label="Filtrar por zona">
      <option value="">Todas las zonas</option>
      <option value="objetivo">Zona objetivo (AMBA + La Plata)</option>
      <option value="volumen_alto">Volumen alto (interior)</option>
      <option value="fuera">Fuera de zona</option>
    </select>
  </div>

  <details class="legend">
    <summary>Cómo leer este panel</summary>
    <div class="legend__body">
      <span><b>Tocá el nombre del comprador</b> en cualquier tarjeta para desplegar el detalle completo del pliego y el contacto.</span>
      <span><span class="legend__dot" style="background:var(--accent)"></span><b>Nueva</b> — el motor la detectó en los últimos 7 días. Es lo que hay que mirar primero.</span>
      <span><span class="legend__dot" style="background:var(--open)"></span><b>Vigente</b> — llamado abierto, se puede presentar oferta.</span>
      <span><span class="legend__dot" style="background:var(--soon)"></span><b>Por vencer</b> — la apertura de sobres es en 21 días o menos.</span>
      <span><span class="legend__dot" style="background:var(--closed)"></span><b>Cerrada</b> — adjudicada, sin efecto, o ya venció el plazo. Pasa sola a la pestaña "Cerradas". Sirve de comparación (quién compra, quién gana, a qué precio).</span>
      <span><span class="legend__dot" style="background:var(--noise)"></span><b>Descartada</b> — el filtro la marcó como falso positivo (cámara de video, batería de generador, recapado de asfalto…).</span>
      <span>Las licitaciones caducan solas: cuando pasa la apertura (o, si no hay fecha, a los 45 días de publicadas) dejan de aparecer en "Vigentes". Los datos vienen sin verificar: antes de presentarse, abrir siempre el pliego en la fuente oficial.</span>
    </div>
  </details>

  <div class="tabs" id="tabs" role="tablist">
    <button class="tab" data-tab="vigentes" role="tab" aria-selected="true">Vigentes y por vencer<span class="tab__n" id="tabn-vigentes"></span></button>
    <button class="tab" data-tab="cerradas" role="tab" aria-selected="false">Cerradas / adjudicadas<span class="tab__n" id="tabn-cerradas"></span></button>
    <button class="tab" data-tab="metricas" role="tab" aria-selected="false">Métricas<span class="tab__n" id="tabn-metricas"></span></button>
    <button class="tab" data-tab="ruido" role="tab" aria-selected="false">Descartadas<span class="tab__n" id="tabn-ruido"></span></button>
  </div>

  <main id="lista"></main>

  <aside class="aside" id="proveedores"></aside>

  <footer>
    <div class="disclaimer">
      <b>Datos sin verificar.</b> El radar detecta y ordena; antes de presentarse hay que
      abrir el pliego en la fuente oficial y confirmar objeto, fechas y condiciones.
    </div>
    <p><b>Qué cubre hoy:</b> <span id="cobertura"></span></p>
    <p><b>Falta sumar:</b> Nación (COMPR.AR), portales propios de municipios que no
      publican en SIBOM, y el Boletín Oficial de la Provincia como respaldo.</p>
    <p id="pie"></p>
  </footer>
</div>

<script id="radar-data" type="application/json">__RADAR_JSON__</script>
<script id="proveedores-data" type="application/json">__PROVEEDORES_JSON__</script>
<script id="meta-data" type="application/json">__META_JSON__</script>
<script id="metricas-data" type="application/json">__METRICAS_JSON__</script>
<script>
(function () {
  "use strict";
  var RADAR = JSON.parse(document.getElementById("radar-data").textContent);
  var PROV = JSON.parse(document.getElementById("proveedores-data").textContent);
  var META = JSON.parse(document.getElementById("meta-data").textContent);
  var MET = JSON.parse(document.getElementById("metricas-data").textContent);

  var ZONA_LABEL = {
    objetivo: "Zona objetivo", volumen_alto: "Volumen alto", fuera: "Fuera de zona"
  };
  var FUENTE_LABEL = { sibom: "SIBOM", pbac: "PBAC", bac: "BAC · CABA" };

  var GRUPO_DE_TAB = { vigentes: "vigente", cerradas: "cerrada", ruido: "ruido" };
  var TABS_VALIDAS = { vigentes: 1, cerradas: 1, metricas: 1, ruido: 1 };

  function leerTab() {
    try {
      var t = localStorage.getItem("radar-tab");
      return TABS_VALIDAS[t] ? t : "vigentes";
    } catch (e) { return "vigentes"; }
  }

  // tab: pestaña activa · sub: filtro rapido dentro de "vigentes" ("" | "porvencer" | "nuevas")
  var state = { q: "", zona: "", producto: "", tab: leerTab(), sub: "" };

  // --- Tema (claro / oscuro / auto), recordado por navegador -----------------
  var TEMAS = ["auto", "light", "dark"];
  var TEMA_LABEL = { auto: "Auto", light: "Claro", dark: "Oscuro" };
  var TEMA_ICON = { auto: "◐", light: "☀", dark: "☾" };

  function leerTema() {
    try { return localStorage.getItem("radar-tema") || "auto"; } catch (e) { return "auto"; }
  }
  function aplicarTema(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    var i = document.getElementById("temaIcon"), x = document.getElementById("temaTxt");
    if (i) i.textContent = TEMA_ICON[t];
    if (x) x.textContent = TEMA_LABEL[t];
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function pill(row) {
    if (row.grupo === "ruido") return { cls: "noise", txt: "Ruido" };
    if (row.grupo === "cerrada") {
      if (row.estado === "adjudicada") return { cls: "closed", txt: "Adjudicada" };
      if (row.estado === "cerrada") return { cls: "closed", txt: "Cerrada" };
      if (row.cierre === "apertura_pasada") return { cls: "closed", txt: "Apertura pasó" };
      if (row.cierre === "vencida_estimada") return { cls: "closed", txt: "Vencida (est.)" };
      return { cls: "closed", txt: "Cerrada" };
    }
    var d = row.dias_apertura;
    if (d != null && d >= 0 && d <= 7) return { cls: "soon", txt: "Vence en " + d + " día" + (d === 1 ? "" : "s") };
    if (d != null && d > 7 && d <= 21) return { cls: "soon", txt: "Apertura en " + d + " días" };
    return { cls: "open", txt: "Vigente" };
  }

  function cardClass(row, p) {
    var c = "card";
    if (row.grupo === "ruido") c += " card--noise";
    else if (p.cls === "soon") c += " card--soon";
    else if (row.grupo === "vigente") c += " card--open";
    if (row.nueva && row.grupo === "vigente") c += " card--flag";
    return c;
  }

  function fechasLinea(row) {
    var out = [];
    if (row.fecha_pub_label) out.push("publicada " + esc(row.fecha_pub_label));
    if (row.fecha_ap_label) out.push("apertura " + esc(row.fecha_ap_label));
    if (row.detectada) out.push("detectada " + esc(row.detectada));
    return out.join("  ·  ");
  }

  function detFila(k, v) {
    return v ? '<div class="detail__k">' + esc(k) + '</div><div class="detail__v">' + v + "</div>" : "";
  }

  function contactoHTML(row) {
    var c = row.contacto;
    var q = encodeURIComponent((row.comprador || "") + " compras licitaciones proveedores contacto");
    var buscar = '<a class="btn-buscar" href="https://www.google.com/search?q=' + q +
      '" target="_blank" rel="noopener">Buscar contacto en la web</a>';
    if (!c || (!c.email && !c.telefono && !c.web_compras)) {
      return '<div class="contacto"><p class="contacto__t">Contacto</p>' +
        '<p style="margin:0 0 8px">No hay un contacto cargado para este comprador. ' +
        'El mail y el teléfono de la oficina de compras suelen figurar dentro del pliego.</p>' +
        buscar + "</div>";
    }
    var r = "";
    if (c.email)
      r += '<div class="contacto__row"><b>Mail</b><a href="mailto:' + esc(c.email) + '">' + esc(c.email) + "</a></div>";
    if (c.telefono)
      r += '<div class="contacto__row"><b>Tel</b><a href="tel:' + esc(c.telefono.replace(/[^0-9+]/g, "")) +
           '">' + esc(c.telefono) + "</a></div>";
    if (c.web_compras)
      r += '<div class="contacto__row"><b>Web</b><a href="' + esc(c.web_compras) +
           '" target="_blank" rel="noopener">' + esc(c.web_compras) + "</a></div>";
    if (c.notas)
      r += '<div class="contacto__row" style="color:var(--ink-soft)">' + esc(c.notas) + "</div>";
    return '<div class="contacto"><p class="contacto__t">Contacto de compras</p>' + r +
      '<p style="margin:8px 0 0">' + buscar + "</p></div>";
  }

  function detalleHTML(row) {
    var d = "";
    d += detFila("Comprador", esc(row.comprador));
    d += detFila("Objeto / referencia", esc(row.objeto));
    d += detFila("Detalle del boletín", esc(row.fragmento));
    d += detFila("Tipo de proceso", esc(row.tipo));
    d += detFila("N° de proceso / expediente", esc(row.id_origen));
    d += detFila("Fuente", esc(FUENTE_LABEL[row.fuente] || row.fuente));
    d += detFila("Estado", esc(row.estado) ||
      (row.grupo === "vigente" ? "vigente (sin confirmar)"
       : row.cierre === "apertura_pasada" ? "cerrada — la apertura de sobres ya pasó"
       : row.cierre === "vencida_estimada" ? "cerrada — estimado por antigüedad (sin fecha de apertura publicada)"
       : ""));
    d += detFila("Zona", esc(ZONA_LABEL[row.zona] || ""));
    d += detFila("Productos", esc((row.productos || []).join(", ")));
    d += detFila("Palabras clave que la detectaron", esc((row.keywords || []).join(", ")));
    d += detFila("Publicada", esc(row.fecha_pub_label));
    d += detFila("Apertura de sobres", esc(row.fecha_ap_label));
    d += detFila("Detectada por el radar", esc(row.detectada));
    d += detFila("Vista por última vez", esc(row.corrida));
    d += detFila("Pliego", row.url
      ? '<a href="' + esc(row.url) + '" target="_blank" rel="noopener">Abrir en la fuente oficial ↗</a>'
      : "sin link");
    return '<div class="card__detail" hidden>' +
      '<div class="detail">' + d + "</div>" +
      contactoHTML(row) +
      '<p class="detail__note">Datos sin verificar. Antes de presentarse, confirmá objeto, ' +
      "fechas y condiciones en el pliego oficial.</p></div>";
  }

  function renderCard(row) {
    var p = pill(row);
    var chips = [];
    var fl = FUENTE_LABEL[row.fuente] || row.fuente;
    if (fl) chips.push('<span class="chip chip--src">' + esc(fl) + "</span>");
    if (row.zona && ZONA_LABEL[row.zona]) {
      chips.push('<span class="chip chip--zone">' + esc(ZONA_LABEL[row.zona]) + "</span>");
    }
    (row.productos || []).forEach(function (pr) {
      chips.push('<span class="chip chip--zone">' + esc(pr) + "</span>");
    });
    (row.keywords || []).forEach(function (k) {
      chips.push('<span class="chip">' + esc(k) + "</span>");
    });

    var link = row.url
      ? '<a class="card__link" href="' + esc(row.url) + '" target="_blank" rel="noopener">Ver en la fuente ↗</a>'
      : "";
    var nueva = (row.nueva && row.grupo === "vigente")
      ? '<span class="pill pill--nueva">Nueva</span> ' : "";

    return '' +
      '<article class="' + cardClass(row, p) + '">' +
      '  <button class="card__head" type="button" aria-expanded="false">' +
      '    <span class="card__buyer">' + esc(row.comprador || "—") + "</span>" +
      '    <span class="card__pills">' + nueva +
             '<span class="pill pill--' + p.cls + '">' + esc(p.txt) + "</span></span>" +
      '    <span class="card__chevron" aria-hidden="true">▸</span>' +
      "  </button>" +
      (row.texto ? '  <p class="card__object">' + esc(row.texto) + "</p>" : "") +
      (row.ref ? '  <p class="card__ref">' + esc(row.ref) + "</p>" : "") +
      '  <div class="card__chips">' + chips.join("") + "</div>" +
      '  <div class="card__foot"><span>' + (fechasLinea(row) || "sin fechas") + "</span>" + link + "</div>" +
      detalleHTML(row) +
      "</article>";
  }

  function pasaBusqueda(row) {
    if (state.zona && row.zona !== state.zona) return false;
    if (state.producto && (row.productos || []).indexOf(state.producto) === -1) return false;
    if (state.q) {
      var hay = (row.comprador + " " + row.texto + " " + row.ref + " " +
                 (row.keywords || []).join(" ")).toLowerCase();
      if (hay.indexOf(state.q.toLowerCase()) === -1) return false;
    }
    return true;
  }

  var NOTA_TAB = {
    vigentes: "Llamados abiertos. Los que están por vencer (apertura en ≤21 días) y las " +
      "detecciones nuevas van primero. Cuando pasa la apertura, la licitación se mueve sola " +
      "a la pestaña “Cerradas”.",
    cerradas: "Ya no se puede ofertar (adjudicadas, sin efecto, o venció el plazo). Quedan " +
      "acá de comparación: quién compra, quién gana y a qué precio.",
    ruido: "Falsos positivos que el filtro descartó (cámara de video, batería de generador, " +
      "recapado asfáltico de calzada, etc.). A la vista para poder auditarlos."
  };

  function barChart(datos, subtitulo) {
    if (!datos.length) return '<p class="chart__sub">Todavía sin datos.</p>';
    var max = datos.reduce(function (m, d) { return Math.max(m, d.n); }, 1);
    var rows = datos.map(function (d) {
      var pct = Math.round(d.n / max * 100);
      return '<div class="barh__row" title="' + esc(d.label + ": " + d.n) + '">' +
        '<span class="barh__lbl">' + esc(d.label) + "</span>" +
        '<span class="barh__track"><span class="barh__fill" style="width:' + pct + '%"></span></span>' +
        '<span class="barh__val">' + d.n + "</span></div>";
    }).join("");
    return (subtitulo ? '<p class="chart__sub">' + esc(subtitulo) + "</p>" : "") +
      '<div class="barh">' + rows + "</div>";
  }

  function renderMetricas() {
    if (!MET.total) {
      return '<div class="empty">La sección de métricas se llena a medida que el radar ' +
        "acumula corridas. Todavía hay muy pocos datos.</div>";
    }
    var stat = function (n, l) {
      return '<div class="met__stat"><b>' + n + "</b><span>" + esc(l) + "</span></div>";
    };
    return '<div class="met">' +
      '<div class="met__stats">' +
        stat(MET.total, "licitaciones detectadas") +
        stat(MET.municipios, "compradores distintos") +
        stat(MET.adjudicadas, "adjudicadas registradas") +
        stat(MET.descartadas, "descartadas por el filtro") +
      "</div>" +
      '<div><p class="chart__t">Compradores con más licitaciones</p>' +
        barChart(MET.por_comprador, "acumulado histórico, sin contar el ruido") + "</div>" +
      '<div><p class="chart__t">Licitaciones por año</p>' +
        barChart(MET.por_anio, "para ver si es un canal constante o si sube/baja") + "</div>" +
      '<div><p class="chart__t">Estacionalidad — por mes del año</p>' +
        barChart(MET.por_mes, "todos los años juntos: en qué meses suele haber más") + "</div>" +
      '<div><p class="chart__t">Por producto</p>' +
        barChart(MET.por_producto, "una licitación puede contar en más de una categoría") + "</div>" +
      (MET.desde ? '<p class="chart__sub">Datos desde ' + esc(MET.desde) +
        ". SIBOM tiene histórico; PBAC y BAC solo suman desde ahora.</p>" : "") +
      "</div>";
  }

  function render() {
    var lista = document.getElementById("lista");
    var ctrl = document.getElementById("controls");

    if (state.tab === "metricas") {
      if (ctrl) ctrl.hidden = true;
      lista.innerHTML = '<section class="section">' +
        '<p class="section__note">Resumen de todo lo que el radar viene juntando. ' +
        "Sirve para ver qué municipios compran más y en qué épocas.</p>" +
        renderMetricas() + "</section>";
    } else {
      if (ctrl) ctrl.hidden = false;
      var grupo = GRUPO_DE_TAB[state.tab] || "vigente";
      var rows = RADAR.filter(function (r) { return r.grupo === grupo && pasaBusqueda(r); });
      if (state.tab === "vigentes" && state.sub === "porvencer")
        rows = rows.filter(function (r) { return r.por_vencer; });
      if (state.tab === "vigentes" && state.sub === "nuevas")
        rows = rows.filter(function (r) { return r.nueva; });

      var subTxt = state.sub === "porvencer" ? " · filtrando: por vencer"
        : state.sub === "nuevas" ? " · filtrando: nuevas" : "";
      var body = rows.length
        ? '<div class="cards">' + rows.map(renderCard).join("") + "</div>"
        : '<div class="empty">Nada por acá en esta corrida.</div>';
      lista.innerHTML = '<section class="section">' +
        '<p class="section__note">' + esc(NOTA_TAB[state.tab] + subTxt) + "</p>" +
        body + "</section>";
    }

    ["vigentes", "cerradas", "metricas", "ruido"].forEach(function (t) {
      var el = document.querySelector('.tab[data-tab="' + t + '"]');
      if (el) el.setAttribute("aria-selected", String(t === state.tab));
    });
  }

  // cada tile lleva a una pestaña (y opcionalmente a un sub-filtro dentro de "vigentes")
  var TILES = [
    { id: "vigentes", tab: "vigentes", sub: "", l: "Vigentes", mod: "open", n: "vigentes" },
    { id: "porvencer", tab: "vigentes", sub: "porvencer", l: "Por vencer (≤21 d)", mod: "soon", n: "por_vencer" },
    { id: "nuevas", tab: "vigentes", sub: "nuevas", l: "Nuevas (" + META.dias_nueva + " d)", mod: "nueva", n: "nuevas" },
    { id: "cerradas", tab: "cerradas", sub: "", l: "Cerradas", n: "cerradas" },
    { id: "ruido", tab: "ruido", sub: "", l: "Descartadas", n: "ruido" }
  ];

  function tileActivo(t) {
    return state.tab === t.tab && (t.tab !== "vigentes" || state.sub === t.sub);
  }

  function renderResumen() {
    document.getElementById("summary").innerHTML = TILES.map(function (t) {
      return '<button class="tile' + (t.mod ? " tile--" + t.mod : "") +
        '" data-tile="' + t.id + '" aria-pressed="' + tileActivo(t) + '">' +
        '<span class="tile__n">' + META[t.n] + "</span>" +
        '<span class="tile__l">' + esc(t.l) + "</span></button>";
    }).join("");
  }

  function renderTabCounts() {
    var c = { vigentes: META.vigentes, cerradas: META.cerradas,
              metricas: MET.total, ruido: META.ruido };
    ["vigentes", "cerradas", "metricas", "ruido"].forEach(function (t) {
      var el = document.getElementById("tabn-" + t);
      if (el) el.textContent = c[t];
    });
  }

  function irA(tab, sub) {
    state.tab = tab;
    state.sub = tab === "vigentes" ? (sub || "") : "";
    try { localStorage.setItem("radar-tab", tab); } catch (e) {}
    renderResumen();
    render();
  }

  function renderMeta() {
    var m = document.getElementById("meta");
    var fuentes = (META.fuentes || []).map(function (f) {
      return FUENTE_LABEL[f] ? FUENTE_LABEL[f].split(" ")[0] : f.toUpperCase();
    }).join(" + ") || "—";
    m.innerHTML =
      "Última corrida del motor: <b>" + esc(META.ultima_corrida || "s/d") + "</b>" +
      "<span>Página generada: <b>" + esc(META.generado) + "</b></span>" +
      "<span>Fuentes: <b>" + esc(fuentes) + "</b></span>";
    document.getElementById("cobertura").textContent =
      "135 municipios de la Provincia de Buenos Aires (vía SIBOM, el boletín oficial " +
      "municipal compartido); el Estado provincial —ministerios, Vialidad, hospitales, " +
      "organismos y municipios adheridos— vía PBAC; y la Ciudad de Buenos Aires vía BAC.";
    document.getElementById("pie").textContent =
      "Radar de Licitaciones · proyecto interno · datos de fuentes oficiales del Estado.";
  }

  function renderProveedores() {
    if (!PROV.length) { document.getElementById("proveedores").hidden = true; return; }
    var rows = PROV.map(function (p) {
      var what = [p.detectado_como, p.visto_en].filter(Boolean).join(" — ");
      return '<div class="prov__row"><span class="prov__name">' + esc(p.nombre) +
        '</span><span class="prov__what">' + esc(what) + "</span></div>";
    }).join("");
    document.getElementById("proveedores").innerHTML =
      '<h2 class="aside__title">Proveedores que ya le venden al Estado</h2>' +
      '<p class="aside__note">Detectados en las adjudicaciones del propio radar. ' +
      "Contexto de competencia, no oportunidades.</p>" +
      '<div class="prov">' + rows + "</div>";
  }

  document.getElementById("q").addEventListener("input", function (e) {
    state.q = e.target.value; render();
  });
  document.getElementById("producto").addEventListener("change", function (e) {
    state.producto = e.target.value; render();
  });
  document.getElementById("zona").addEventListener("change", function (e) {
    state.zona = e.target.value; render();
  });
  document.getElementById("tabs").addEventListener("click", function (e) {
    var b = e.target.closest(".tab"); if (!b) return;
    irA(b.getAttribute("data-tab"), "");
  });
  document.getElementById("summary").addEventListener("click", function (e) {
    var b = e.target.closest(".tile"); if (!b) return;
    var t = null, id = b.getAttribute("data-tile");
    for (var i = 0; i < TILES.length; i++) if (TILES[i].id === id) t = TILES[i];
    if (!t) return;
    // segundo clic sobre un tile de sub-filtro ya activo -> lo saca
    if (tileActivo(t) && t.sub) irA("vigentes", "");
    else irA(t.tab, t.sub);
  });
  document.getElementById("lista").addEventListener("click", function (e) {
    var head = e.target.closest(".card__head"); if (!head) return;
    var card = head.closest(".card");
    var det = card.querySelector(".card__detail");
    var abierto = card.classList.toggle("card--expanded");
    head.setAttribute("aria-expanded", abierto ? "true" : "false");
    if (det) det.hidden = !abierto;
  });
  document.getElementById("tema").addEventListener("click", function () {
    var actual = leerTema();
    var prox = TEMAS[(TEMAS.indexOf(actual) + 1) % TEMAS.length];
    try { localStorage.setItem("radar-tema", prox); } catch (e) {}
    aplicarTema(prox);
  });

  aplicarTema(leerTema());
  renderMeta();
  renderTabCounts();
  renderResumen();
  renderProveedores();
  render();
})();
</script>
"""


if __name__ == "__main__":
    main()
