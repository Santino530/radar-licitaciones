#!/usr/bin/env python3
"""
Alerta por mail de licitaciones nuevas — Radar de licitaciones.

Despues de cada corrida, junta las licitaciones VIGENTES que pasan todos los
chequeos y que todavia no se avisaron, y manda UN mail con todas juntas.

Chequeos (una licitacion se avisa solo si pasa todos):
  1. Vigente con la fecha de hoy en hora argentina, y con la apertura de sobres
     mañana o despues (si abre hoy ya no da el tiempo). Si no tiene fecha de
     apertura (SIBOM), publicada hace 15 dias o menos.
  2. No es ruido: ni para el radar (es_ruido) ni para el filtro del panel
     (camaras frigorificas, techos, UPS, habilitaciones...).
  3. Fuera de zona no se avisa (zona objetivo, volumen alto o sin zona = Estado
     provincial / nacional, si).
  4. Señal clara de producto: neumatico, cubierta, llanta, camara de aire,
     gomeria, reconstruccion/recapado, o una medida de neumatico (175/70 R13,
     11R22.5...). Bateria solo si habla de vehiculos.
  5. No es una adjudicacion: ni por el estado que trae la fuente, ni (en SIBOM)
     por el texto del boletin (adjudica, convalida, aprueba el gasto...).
  6. Tiene link a la fuente y no se aviso antes (data/alertas_enviadas.csv).

Envio: SMTP con SSL. Los datos de la casilla van como secretos del repo en
GitHub (Settings -> Secrets and variables -> Actions), nunca en el codigo:
  ALERTA_SMTP_HOST, ALERTA_SMTP_USER, ALERTA_SMTP_PASS, ALERTA_PARA
  (ALERTA_SMTP_PORT opcional, por defecto 465). ALERTA_PARA admite varias
  direcciones separadas por coma.
Si faltan, no manda nada, lo dice en el log y no marca nada como avisado.

Uso:
  python scripts/alerta_mail.py                       # manda si hay credenciales
  python scripts/alerta_mail.py --prueba              # no manda: muestra el mail
  python scripts/alerta_mail.py --prueba --vista m.html   # y lo guarda en HTML
"""

import argparse
import csv
import datetime as dt
import html
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_dashboard as panel  # noqa: E402

ESTADO = os.path.join(panel.DATA, "alertas_enviadas.csv")
PANEL_URL = "https://santino530.github.io/radar-licitaciones/"
MAX_POR_MAIL = 25
DIAS_SIN_APERTURA = 15

PRODUCTO_RE = re.compile(
    r"neumatic|cubiertas?\b|llantas?\b|camaras? de (aire|neumatic)|gomeria|"
    r"reconstruc\w* de (neumatic|cubierta)|recapad|recauchut|vulcaniz|"
    r"\b\d{3}\s*/\s*\d{2}\s*z?r?\s*\d{2}\b|\b\d{2}(\.\d)?\s*r\s*\d{2}(\.\d)?\b")
BATERIA_RE = re.compile(r"bateria|acumulador")
VEHICULO_RE = re.compile(
    r"vehic|automotor|camion|camioneta|rodado|moto|ambulancia|flota|tractor|"
    r"maquinaria|repuesto|patrull|movil|\b12\s*v\b|colectivo|omnibus|"
    r"autoelevador|montacarga|motoniveladora|retroexcavadora")
NO_LLAMADO_RE = re.compile(r"adjudic|habilitaci|apruebase el gasto|convalid|"
                           r"reconocimiento de (deuda|gasto)")


def _texto(f):
    return panel._norm(" ".join((f.get("objeto") or "", f.get("fragmento") or "",
                                 f.get("texto") or "")))


def motivo_rechazo(f, hoy):
    """Devuelve '' si la licitacion pasa todos los chequeos, o el motivo."""
    if f["grupo"] != "vigente":
        return "no vigente"
    if (f.get("estado") or "") in ("adjudicada", "cerrada"):
        return "adjudicada o cerrada"
    if f.get("motivo_ruido"):
        return "ruido (panel)"
    ap = f.get("fecha_ap_iso")
    if ap:
        if (dt.date.fromisoformat(ap) - hoy).days < 1:
            return "abre hoy o ya abrió"
    else:
        pub = f.get("fecha_pub_iso")
        if not pub or (hoy - dt.date.fromisoformat(pub)).days > DIAS_SIN_APERTURA:
            return "sin fecha de apertura y publicada hace más de 15 días"
    if f.get("zona") == "fuera":
        return "fuera de zona"
    t = _texto(f)
    if not PRODUCTO_RE.search(t):
        if not (BATERIA_RE.search(t) and VEHICULO_RE.search(t)):
            return "sin señal clara de neumáticos / baterías de vehículo"
    if f["fuente"] == "sibom" and NO_LLAMADO_RE.search(t):
        return "SIBOM: es una adjudicación, no un llamado"
    if not f.get("url"):
        return "sin link a la fuente"
    return ""


def clave(f):
    return f"{f['fuente']}|{f.get('id_origen') or f.get('url')}"


def leer_estado():
    if not os.path.exists(ESTADO):
        return set()
    with open(ESTADO, newline="", encoding="utf-8") as fh:
        return {r["clave"] for r in csv.DictReader(fh)}


def guardar_estado(filas, ahora):
    nuevo = not os.path.exists(ESTADO)
    with open(ESTADO, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if nuevo:
            w.writerow(["clave", "avisada", "comprador", "apertura"])
        for f in filas:
            w.writerow([clave(f), ahora, f["comprador"], f.get("fecha_ap_label") or ""])


def _cuando(f, hoy):
    if f.get("fecha_ap_iso"):
        d = (dt.date.fromisoformat(f["fecha_ap_iso"]) - hoy).days
        cuando = "mañana" if d == 1 else f"en {d} días"
        return f"Apertura {f['fecha_ap_label']} ({cuando})"
    return f"Publicada {f.get('fecha_pub_label') or 's/d'} · sin fecha de apertura informada"


def armar_mail(filas, hoy):
    n = len(filas)
    asunto = (f"Radar: {n} licitación nueva" if n == 1
              else f"Radar: {n} licitaciones nuevas") + " de neumáticos"
    fuente_lbl = {"sibom": "SIBOM", "pbac": "PBAC", "bac": "BAC (CABA)",
                  "comprar": "COMPR.AR (Nación)", "corredores": "Corredores Viales",
                  "municipio": "Web municipal"}
    txt = [asunto, ""]
    items = []
    for f in filas:
        fl = fuente_lbl.get(f["fuente"], f["fuente"])
        proc = f.get("id_origen") if f["fuente"] != "sibom" else ""
        unidad = f.get("unidad") or ""
        txt += [f"- {f['comprador']}" + (f" · {unidad}" if unidad else ""),
                f"  {f['texto'][:220]}",
                f"  {_cuando(f, hoy)} · {fl}" + (f" · Proceso {proc}" if proc else ""),
                f"  {f['url']}", ""]
        extra = ""
        if f["fuente"] == "comprar":
            extra = ("<div style='font-size:12px;color:#57544e'>COMPR.AR no tiene link "
                     "directo al proceso: en el portal, buscá el número "
                     f"<b>{html.escape(proc)}</b>.</div>")
        items.append(
            "<tr><td style='padding:12px 0;border-bottom:1px solid #e4e1da'>"
            f"<div style='font-weight:600'>{html.escape(f['comprador'])}</div>"
            + (f"<div style='font-size:12px;color:#57544e'>{html.escape(unidad)}</div>" if unidad else "")
            + f"<div style='margin:4px 0'>{html.escape(f['texto'][:300])}</div>"
            f"<div style='font-size:13px;color:#a8620a;font-weight:600'>{html.escape(_cuando(f, hoy))}</div>"
            f"<div style='font-size:12px;color:#57544e'>{html.escape(fl)}"
            + (f" · Proceso {html.escape(proc)}" if proc else "") + "</div>"
            + extra
            + f"<div style='margin-top:4px'><a href='{html.escape(f['url'])}'>Ver en la fuente</a></div>"
            "</td></tr>")
    txt += ["Todas las vigentes: " + PANEL_URL,
            "Datos sin verificar: antes de presentarse, abrir el pliego en la fuente oficial."]
    cuerpo_html = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;color:#1b1a18;max-width:640px'>"
        f"<h2 style='font-size:18px;margin:0 0 8px'>{html.escape(asunto)}</h2>"
        "<p style='color:#57544e;margin:0 0 8px'>Vigentes, con apertura mañana o después, "
        "que pasaron los chequeos del radar.</p>"
        "<table style='width:100%;border-collapse:collapse'>" + "".join(items) + "</table>"
        f"<p style='margin-top:14px'><a href='{PANEL_URL}'>Ver todas en el panel</a></p>"
        "<p style='font-size:12px;color:#57544e'>Datos sin verificar: antes de presentarse, "
        "abrir el pliego en la fuente oficial.</p></div>")
    return asunto, "\n".join(txt), cuerpo_html


def enviar(asunto, texto, cuerpo_html):
    host = os.environ.get("ALERTA_SMTP_HOST", "").strip()
    user = os.environ.get("ALERTA_SMTP_USER", "").strip()
    clave_smtp = os.environ.get("ALERTA_SMTP_PASS", "")
    para = [a.strip() for a in os.environ.get("ALERTA_PARA", "").split(",") if a.strip()]
    if not (host and user and clave_smtp and para):
        return False
    port = int(os.environ.get("ALERTA_SMTP_PORT") or 465)
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = f"Radar de licitaciones <{user}>"
    msg["To"] = ", ".join(para)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=user.split("@")[-1])
    msg.set_content(texto)
    msg.add_alternative(cuerpo_html, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, clave_smtp)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(user, clave_smtp)
            s.send_message(msg)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prueba", action="store_true", help="no manda ni marca nada")
    ap.add_argument("--vista", help="con --prueba: guarda el mail en este HTML")
    ap.add_argument("--todas", action="store_true",
                    help="con --prueba: incluir tambien las ya avisadas")
    args = ap.parse_args()

    hoy = panel.hoy_ar()
    filas = panel.cargar_radar(panel.cargar_contactos(), panel.cargar_inscriptos(),
                               panel.cargar_uoc_comprar())
    avisadas = set() if (args.prueba and args.todas) else leer_estado()

    candidatas, vistas = [], set()
    for f in filas:
        if f["grupo"] != "vigente":
            continue
        motivo = motivo_rechazo(f, hoy)
        k = clave(f)
        if motivo:
            print(f"  no se avisa ({motivo}): {f['comprador']} · {f['texto'][:80]}")
        elif k in avisadas or k in vistas:
            print(f"  ya avisada: {f['comprador']} · {f['texto'][:80]}")
        else:
            vistas.add(k)
            candidatas.append(f)

    candidatas.sort(key=lambda f: f.get("fecha_ap_iso") or "9999")
    if len(candidatas) > MAX_POR_MAIL:
        print(f"  {len(candidatas)} para avisar: van las primeras {MAX_POR_MAIL}, "
              "el resto en la próxima corrida")
        candidatas = candidatas[:MAX_POR_MAIL]
    print(f"Para avisar: {len(candidatas)}")
    if not candidatas:
        return

    asunto, texto, cuerpo_html = armar_mail(candidatas, hoy)
    if args.prueba:
        print("\n" + texto)
        if args.vista:
            with open(args.vista, "w", encoding="utf-8") as fh:
                fh.write("<!doctype html><meta charset='utf-8'><title>" +
                         html.escape(asunto) + "</title><body style='padding:16px'>" +
                         cuerpo_html + "</body>")
            print(f"\nVista guardada en {args.vista}")
        return

    if not enviar(asunto, texto, cuerpo_html):
        print("Faltan los secretos ALERTA_SMTP_* / ALERTA_PARA: no se envía nada "
              "y no se marca nada como avisado.")
        return
    guardar_estado(candidatas, dt.datetime.now(panel.AR).strftime("%Y-%m-%d %H:%M"))
    print(f"Mail enviado: {asunto}")


if __name__ == "__main__":
    main()
