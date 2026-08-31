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
DIAS_NUEVA = 7  # una deteccion de los ultimos N dias se marca "nueva"


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
            if es_ruido:
                grupo = "ruido"
            elif estado in ("adjudicada", "cerrada"):
                grupo = "cerrada"
            else:
                grupo = "abierta"

            pub_iso, pub_label = parse_fecha(r.get("fecha_publicacion"))
            ap_iso, ap_label = parse_fecha(r.get("fecha_apertura"))

            dias_apertura = None
            if ap_iso:
                try:
                    dias_apertura = (dt.date.fromisoformat(ap_iso) - hoy).days
                except ValueError:
                    dias_apertura = None

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
    def clave_abierta(x):
        sin_fecha = x["fecha_ap_iso"] == ""
        return (sin_fecha, x["fecha_ap_iso"] or "9999-99-99",
                _neg(x["fecha_pub_iso"]))

    abiertas = sorted((f for f in filas if f["grupo"] == "abierta"), key=clave_abierta)
    cerradas = sorted((f for f in filas if f["grupo"] == "cerrada"),
                      key=lambda x: x["fecha_pub_iso"], reverse=True)
    ruido = sorted((f for f in filas if f["grupo"] == "ruido"),
                   key=lambda x: x["fecha_pub_iso"], reverse=True)
    return abiertas + cerradas + ruido


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
    """Busca contacto por nombre de comprador; si no hay, cae al contacto general
    de la fuente (PBAC / BAC)."""
    if not contactos:
        return None
    k = _clave_comprador(comprador)
    if k in contactos:
        return contactos[k]
    # match parcial: el comprador contiene (o esta contenido en) una clave cargada
    for ck, cv in contactos.items():
        if ck and (ck in k or k in ck) and len(ck) > 4:
            return cv
    if fuente and fuente.upper() in contactos:
        return contactos[fuente.upper()]
    return contactos.get(_clave_comprador(fuente))


def construir_meta(filas):
    corridas = [f["corrida"] for f in filas if f["corrida"]]
    ultima = max(corridas) if corridas else ""
    abiertas = [f for f in filas if f["grupo"] == "abierta"]
    por_vencer = [f for f in abiertas
                  if f["dias_apertura"] is not None and 0 <= f["dias_apertura"] <= 21]
    return {
        "generado": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ultima_corrida": ultima,
        "total": len(filas),
        "abiertas": len(abiertas),
        "por_vencer": len(por_vencer),
        "nuevas": sum(1 for f in filas if f["nueva"] and f["grupo"] != "ruido"),
        "cerradas": sum(1 for f in filas if f["grupo"] == "cerrada"),
        "ruido": sum(1 for f in filas if f["grupo"] == "ruido"),
        "fuentes": sorted({f["fuente"] for f in filas if f["fuente"]}),
        "dias_nueva": DIAS_NUEVA,
    }


def json_para_script(x):
    return json.dumps(x, ensure_ascii=False).replace("</", "<\\/")


def render(filas, proveedores, meta):
    html = PLANTILLA
    html = html.replace("__RADAR_JSON__", json_para_script(filas))
    html = html.replace("__PROVEEDORES_JSON__", json_para_script(proveedores))
    html = html.replace("__META_JSON__", json_para_script(meta))
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "web", "dashboard.html"))
    args = ap.parse_args()

    contactos = cargar_contactos()
    filas = ordenar(cargar_radar(contactos))
    proveedores = cargar_proveedores()
    meta = construir_meta(filas)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(filas, proveedores, meta))

    print(f"Panel generado: {args.out}")
    print(f"  {meta['abiertas']} abiertas · {meta['por_vencer']} por vencer · "
          f"{meta['cerradas']} adjudicadas/cerradas · {meta['ruido']} descartadas")
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
  .toggle {
    display: inline-flex; align-items: center; gap: 7px;
    font-size: 13px; color: var(--ink-soft); cursor: pointer; user-select: none;
  }
  .toggle input { accent-color: var(--accent); width: 15px; height: 15px; }

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

  <div class="controls">
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
    <label class="toggle">
      <input type="checkbox" id="verRuido"> Mostrar descartadas por el filtro
    </label>
  </div>

  <details class="legend">
    <summary>Cómo leer este panel</summary>
    <div class="legend__body">
      <span><b>Tocá el nombre del comprador</b> en cualquier tarjeta para desplegar el detalle completo del pliego y el contacto.</span>
      <span><span class="legend__dot" style="background:var(--accent)"></span><b>Nueva</b> — el motor la detectó en los últimos 7 días. Es lo que hay que mirar primero.</span>
      <span><span class="legend__dot" style="background:var(--open)"></span><b>Abierta</b> — llamado vigente, se puede presentar oferta.</span>
      <span><span class="legend__dot" style="background:var(--soon)"></span><b>Por vencer</b> — la apertura de sobres es en 21 días o menos.</span>
      <span><span class="legend__dot" style="background:var(--closed)"></span><b>Adjudicada / cerrada</b> — ya no se puede ofertar; sirve para ver competencia.</span>
      <span><span class="legend__dot" style="background:var(--noise)"></span><b>Descartada</b> — el filtro la marcó como falso positivo (cámara de video, batería de generador, recapado de asfalto…).</span>
      <span>Los datos vienen sin verificar. Antes de presentarse, siempre abrir el pliego en la fuente oficial.</span>
    </div>
  </details>

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
<script>
(function () {
  "use strict";
  var RADAR = JSON.parse(document.getElementById("radar-data").textContent);
  var PROV = JSON.parse(document.getElementById("proveedores-data").textContent);
  var META = JSON.parse(document.getElementById("meta-data").textContent);

  var ZONA_LABEL = {
    objetivo: "Zona objetivo", volumen_alto: "Volumen alto", fuera: "Fuera de zona"
  };
  var FUENTE_LABEL = { sibom: "SIBOM", pbac: "PBAC", bac: "BAC · CABA" };

  var state = { q: "", zona: "", producto: "", verRuido: false, tile: "" };

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
      return { cls: "closed", txt: row.estado === "cerrada" ? "Cerrada" : "Adjudicada" };
    }
    var d = row.dias_apertura;
    if (d != null && d >= 0 && d <= 7) return { cls: "soon", txt: "Vence en " + d + " día" + (d === 1 ? "" : "s") };
    if (d != null && d > 7 && d <= 21) return { cls: "soon", txt: "Apertura en " + d + " días" };
    if (d != null && d < 0) return { cls: "closed", txt: "Apertura pasó" };
    return { cls: "open", txt: "Abierta" };
  }

  function cardClass(row, p) {
    var c = "card";
    if (row.grupo === "ruido") c += " card--noise";
    else if (p.cls === "soon") c += " card--soon";
    else if (row.grupo === "abierta") c += " card--open";
    if (row.nueva && row.grupo !== "ruido") c += " card--flag";
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
      (row.grupo === "abierta" ? "abierta (sin confirmar)" : ""));
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
    var nueva = (row.nueva && row.grupo !== "ruido")
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

  function pasaFiltro(row) {
    if (state.zona && row.zona !== state.zona) return false;
    if (state.producto && (row.productos || []).indexOf(state.producto) === -1) return false;
    if (state.q) {
      var hay = (row.comprador + " " + row.texto + " " + row.ref + " " +
                 (row.keywords || []).join(" ")).toLowerCase();
      if (hay.indexOf(state.q.toLowerCase()) === -1) return false;
    }
    if (state.tile === "abiertas" && row.grupo !== "abierta") return false;
    if (state.tile === "nuevas" && !(row.nueva && row.grupo !== "ruido")) return false;
    if (state.tile === "porvencer") {
      if (row.grupo !== "abierta") return false;
      var d = row.dias_apertura;
      if (d == null || d < 0 || d > 21) return false;
    }
    if (state.tile === "cerradas" && row.grupo !== "cerrada") return false;
    if (state.tile === "ruido" && row.grupo !== "ruido") return false;
    return true;
  }

  function nuevasPrimero(a, b) {
    return (a.nueva ? 0 : 1) - (b.nueva ? 0 : 1);
  }

  function seccion(titulo, nota, filas) {
    var body = filas.length
      ? '<div class="cards">' + filas.map(renderCard).join("") + "</div>"
      : '<div class="empty">Nada por acá en esta corrida.</div>';
    return '<section class="section">' +
      '<div class="section__head"><h2 class="section__title">' + esc(titulo) +
      '</h2><span class="section__count">' + filas.length + "</span></div>" +
      (nota ? '<p class="section__note">' + esc(nota) + "</p>" : "") +
      body + "</section>";
  }

  function render() {
    var vis = RADAR.filter(pasaFiltro);
    var abiertas = vis.filter(function (r) { return r.grupo === "abierta"; }).slice().sort(nuevasPrimero);
    var cerradas = vis.filter(function (r) { return r.grupo === "cerrada"; });
    var ruido = vis.filter(function (r) { return r.grupo === "ruido"; });

    var html = seccion("Oportunidades abiertas",
      "Las nuevas (detectadas en los últimos " + META.dias_nueva + " días) van primero; " +
      "después, por fecha de apertura más próxima.", abiertas);
    html += seccion("Adjudicadas y cerradas",
      "No son oportunidades para presentarse. Sirven para saber quién compra y quién gana.",
      cerradas);
    if (state.verRuido || state.tile === "ruido") {
      html += seccion("Descartadas por el filtro de ruido",
        "El radar las marcó como falsos positivos (cámara de video, batería de generador, " +
        "recapado asfáltico de calzada, etc.). Quedan a la vista para poder auditarlas.",
        ruido);
    }
    document.getElementById("lista").innerHTML = html;
  }

  function renderResumen() {
    var tiles = [
      { k: "", n: META.total, l: "En el radar" },
      { k: "nuevas", n: META.nuevas, l: "Nuevas (" + META.dias_nueva + " d)", mod: "nueva" },
      { k: "abiertas", n: META.abiertas, l: "Abiertas", mod: "open" },
      { k: "porvencer", n: META.por_vencer, l: "Por vencer (≤21 d)", mod: "soon" },
      { k: "cerradas", n: META.cerradas, l: "Adjudicadas" },
      { k: "ruido", n: META.ruido, l: "Descartadas" }
    ];
    document.getElementById("summary").innerHTML = tiles.map(function (t) {
      return '<button class="tile' + (t.mod ? " tile--" + t.mod : "") +
        '" data-tile="' + t.k + '" aria-pressed="' + (state.tile === t.k) + '">' +
        '<span class="tile__n">' + t.n + "</span>" +
        '<span class="tile__l">' + esc(t.l) + "</span></button>";
    }).join("");
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
  document.getElementById("verRuido").addEventListener("change", function (e) {
    state.verRuido = e.target.checked; render();
  });
  document.getElementById("summary").addEventListener("click", function (e) {
    var b = e.target.closest(".tile"); if (!b) return;
    var k = b.getAttribute("data-tile");
    state.tile = (state.tile === k) ? "" : k;
    if (state.tile === "ruido") document.getElementById("verRuido").checked = state.verRuido = true;
    renderResumen(); render();
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
  renderResumen();
  renderProveedores();
  render();
})();
</script>
"""


if __name__ == "__main__":
    main()
