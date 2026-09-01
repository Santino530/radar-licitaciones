#!/usr/bin/env python3
"""
Radar de licitaciones — orquestador.

Corre todos los conectores disponibles, junta lo que encuentran en una sola tabla
(`data/radar_sin_verificar.csv`) y marca lo NUEVO respecto de la corrida anterior.

Pensado para correr solo, todos los dias a ciertas horas, desde GitHub Actions
(ver .github/workflows/radar.yml). Cada corrida:
  1. Pide a cada conector su lista de licitaciones que matchean neumaticos/recapado.
  2. Normaliza todo al mismo formato.
  3. Lo cruza con `radar_sin_verificar.csv` (la foto de la corrida anterior):
     - fila que ya estaba  -> se actualiza `vista_ultima_corrida`
     - fila nueva          -> `primera_vez_visto` = hoy   (esto es "nueva apertura")
  4. Reescribe el CSV y muestra el resumen de novedades.

La verificacion del pliego y el paso a `pipeline.csv` es manual, en una sesion aparte
(ver docs/blueprint-referencia.md).

Uso:
  python src/radar.py
  python src/radar.py --incluir-ruido      # no descartar lo marcado como ruido
  python src/radar.py --solo pbac          # correr un conector puntual
"""

import argparse
import csv
import datetime as dt
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
CSV_RADAR = os.path.join(DATA, "radar_sin_verificar.csv")
CSV_HISTORICO = os.path.join(DATA, "historico.csv")

CAMPOS = [
    "fuente", "id_origen", "comprador", "objeto", "tipo",
    "fecha_publicacion", "fecha_apertura", "estado",
    "categoria_zona", "es_ruido", "keywords", "url", "fragmento",
    "primera_vez_visto", "vista_ultima_corrida",
]
# historico.csv: mismo formato + una fecha de ultima aparicion. NUNCA se borra
# nada de aca; alimenta la pestaña "Metricas" del panel.
CAMPOS_HIST = CAMPOS + ["ultima_vez_visto"]

sys.path.insert(0, HERE)
import bac_mvp  # noqa: E402
import pbac_mvp  # noqa: E402
import sibom_mvp  # noqa: E402


def de_sibom(incluir_ruido, dias=90):
    # Para un radar diario interesan las publicaciones recientes, no todo el
    # historico. Ventana movil de `dias` sobre la fecha de publicacion del boletin.
    desde = (dt.date.today() - dt.timedelta(days=dias))
    for r in sibom_mvp.recolectar(desde=desde, solo_zona=True,
                                  incluir_ruido=incluir_ruido, verbose=True):
        yield {
            "fuente": "sibom",
            "id_origen": r["url"],
            "comprador": r["municipio"],
            "objeto": r["titulo"],
            "tipo": "",
            "fecha_publicacion": r["fecha_publicacion"],
            "fecha_apertura": "",
            "estado": r.get("estado", ""),
            "categoria_zona": r["categoria_zona"],
            "es_ruido": "si" if r["es_ruido"] else "no",
            "keywords": ",".join(sorted(r["keywords"])),
            "url": r["url"],
            "fragmento": r["fragmento"],
        }


def _filas_comprar(items, fuente, url):
    """PBAC y BAC son el mismo software (Compr.ar) y devuelven el mismo dict.
    Esta funcion normaliza los dos al formato del radar."""
    for r in items:
        yield {
            "fuente": fuente,
            "id_origen": r["nro_proceso"],
            "comprador": r["unidad_ejecutora"],
            "objeto": r["nombre"],
            "tipo": r["tipo"],
            "fecha_publicacion": "",
            "fecha_apertura": r["fecha_apertura"],
            "estado": r["estado"],
            "categoria_zona": "",
            "es_ruido": "si" if r["es_ruido"] else "no",
            "keywords": r["keywords_match"],
            "url": url,
            "fragmento": "",
        }


def de_pbac(incluir_ruido):
    yield from _filas_comprar(
        pbac_mvp.recolectar(incluir_ruido=incluir_ruido, verbose=True),
        "pbac", "https://pbac.cgp.gba.gov.ar/ListarAperturaProxima.aspx")


def de_bac(incluir_ruido):
    yield from _filas_comprar(
        bac_mvp.recolectar(incluir_ruido=incluir_ruido, verbose=True),
        "bac", "https://www.buenosairescompras.gob.ar/ListarAperturaProxima.aspx")


CONECTORES = {"sibom": de_sibom, "pbac": de_pbac, "bac": de_bac}


def reclasificar(row):
    """Vuelve a pasar el filtro de ruido / estado sobre una fila ya guardada,
    usando el texto que quedo en el CSV. Sirve para que, cuando mejoramos los
    filtros, las mejoras alcancen tambien a lo que hoy no volvio a aparecer en
    la fuente (si no, esa fila conserva la clasificacion vieja para siempre)."""
    texto = f"{row.get('objeto', '')} {row.get('fragmento', '')}"
    if row.get("fuente") == "sibom":
        row["es_ruido"] = "si" if sibom_mvp.es_ruido(texto) else "no"
        est = sibom_mvp.detectar_estado(texto)
        if est:
            row["estado"] = est
    elif row.get("fuente") in ("pbac", "bac"):
        row["es_ruido"] = "si" if pbac_mvp.es_ruido(texto) else "no"


def cargar_previo():
    if not os.path.exists(CSV_RADAR):
        return {}
    with open(CSV_RADAR, newline="", encoding="utf-8") as f:
        return {(row["fuente"], row["id_origen"]): row for row in csv.DictReader(f)}


def actualizar_historico(rows_actuales, hoy):
    """Upsert de todo lo detectado en `historico.csv`. No borra nada: las
    licitaciones vencidas / adjudicadas quedan para siempre como serie historica.
    Devuelve (total_filas, filas_nuevas_hoy)."""
    prev = {}
    if os.path.exists(CSV_HISTORICO):
        with open(CSV_HISTORICO, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                prev[(r.get("fuente"), r.get("id_origen"))] = r

    nuevas = 0
    for row in rows_actuales:
        k = (row.get("fuente"), row.get("id_origen"))
        h = prev.get(k)
        if h is None:
            h = {c: row.get(c, "") for c in CAMPOS}
            h["primera_vez_visto"] = row.get("primera_vez_visto") or hoy
            prev[k] = h
            nuevas += 1
        else:
            # campos que pueden cambiar con el tiempo
            if row.get("estado"):
                h["estado"] = row["estado"]
            if row.get("fecha_apertura"):
                h["fecha_apertura"] = row["fecha_apertura"]
            h["es_ruido"] = row.get("es_ruido", h.get("es_ruido", "no"))
        h["ultima_vez_visto"] = hoy

    with open(CSV_HISTORICO, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HIST)
        w.writeheader()
        for h in prev.values():
            w.writerow({c: h.get(c, "") for c in CAMPOS_HIST})
    return len(prev), nuevas


_FECHA_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _fecha(s):
    m = _FECHA_RE.search(s or "")
    if not m:
        return None
    d, mo, y = m.groups()
    try:
        return dt.date(int(y), int(mo), int(d))
    except ValueError:
        return None


# Cuanto tiempo se conserva cada tipo de fila en el CSV antes de borrarla sola.
VIDA_RUIDO_DIAS = 30       # el ruido se guarda un mes para poder auditarlo
VIDA_CERRADA_DIAS = 365    # adjudicadas/cerradas: ~1 año como comparativa
GRACIA_APERTURA_DIAS = 30  # con fecha de apertura: se borra 30 dias despues
VIDA_SIBOM_ABIERTA = 90    # SIBOM sin fecha de apertura: se borra a los 90 dias de publicada


def esta_muerta(row, hoy):
    """True si la licitacion ya no sirve para nada (terminada y vieja) y hay que
    sacarla del CSV. El panel ademas la oculta antes de que llegue a este punto."""
    es_ruido = (row.get("es_ruido") or "").strip().lower() == "si"
    estado = (row.get("estado") or "").strip().lower()
    try:
        dias_visto = (hoy - dt.date.fromisoformat(row.get("primera_vez_visto") or "")).days
    except ValueError:
        dias_visto = 0

    if es_ruido:
        return dias_visto > VIDA_RUIDO_DIAS
    if estado in ("adjudicada", "cerrada"):
        return dias_visto > VIDA_CERRADA_DIAS
    ap = _fecha(row.get("fecha_apertura"))
    if ap is not None:
        return (hoy - ap).days > GRACIA_APERTURA_DIAS
    pub = _fecha(row.get("fecha_publicacion"))
    if pub is not None:
        return (hoy - pub).days > VIDA_SIBOM_ABIERTA
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incluir-ruido", action="store_true")
    ap.add_argument("--solo", choices=list(CONECTORES), help="correr un solo conector")
    ap.add_argument("--dias", type=int, default=90,
                    help="ventana de dias hacia atras para SIBOM (default 90)")
    args = ap.parse_args()

    hoy = dt.date.today().isoformat()
    ahora = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    previo = cargar_previo()
    print(f"Corrida {ahora} — {len(previo)} filas en el radar de la corrida anterior")

    a_correr = [args.solo] if args.solo else list(CONECTORES)
    encontrados = {}
    for nombre in a_correr:
        print(f"» conector {nombre}")
        try:
            kw = {"dias": args.dias} if nombre == "sibom" else {}
            for row in CONECTORES[nombre](args.incluir_ruido, **kw):
                encontrados[(row["fuente"], row["id_origen"])] = row
        except Exception as e:  # noqa: BLE001
            print(f"  ! {nombre} fallo: {e}", file=sys.stderr)

    nuevas, siguen = [], []
    for key, row in encontrados.items():
        anterior = previo.get(key)
        if anterior:
            row["primera_vez_visto"] = anterior.get("primera_vez_visto") or hoy
            siguen.append(row)
        else:
            row["primera_vez_visto"] = hoy
            nuevas.append(row)
        row["vista_ultima_corrida"] = hoy

    # Filas del radar anterior que hoy no volvieron a aparecer: se conservan (para
    # tener historial). OJO: con --solo se corre un solo conector, asi que las filas
    # de las OTRAS fuentes tambien caen aca y hay que mantenerlas intactas -> no se
    # filtran por fuente.
    solo_antes = [previo[k] for k in previo if k not in encontrados]
    for row in solo_antes:
        reclasificar(row)

    todas = nuevas + siguen + solo_antes

    # Historico (antes de podar): acumula TODO lo detectado, nunca borra.
    hist_total, hist_nuevas = actualizar_historico(todas, hoy)

    # Vencimiento automatico: sacar del CSV lo que ya termino y quedo viejo.
    hoy_d = dt.date.today()
    vivas = [r for r in todas if not esta_muerta(r, hoy_d)]
    caducadas = len(todas) - len(vivas)
    todas = vivas
    todas.sort(key=lambda r: (r.get("primera_vez_visto", ""), r.get("fuente", "")),
               reverse=True)

    os.makedirs(DATA, exist_ok=True)
    with open(CSV_RADAR, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        for r in todas:
            w.writerow({k: r.get(k, "") for k in CAMPOS})

    print(f"\n=== RESUMEN corrida {hoy} ===")
    print(f"  {len(encontrados)} licitaciones activas encontradas hoy")
    print(f"  {len(nuevas)} NUEVAS respecto de la corrida anterior")
    print(f"  historico: {hist_total} filas ({hist_nuevas} nuevas hoy)")
    if caducadas:
        print(f"  {caducadas} caducadas (sacadas del CSV por vencimiento)")
    print(f"  {len(todas)} filas totales en {os.path.basename(CSV_RADAR)}")
    if nuevas:
        print("\n  --- NUEVAS APERTURAS ---")
        for r in nuevas:
            marca = " [ruido]" if r["es_ruido"] == "si" else ""
            if r.get("estado"):
                marca += f" [{r['estado']}]"
            fecha = r["fecha_apertura"] or r["fecha_publicacion"]
            print(f"  [{r['fuente']}] {fecha}  {r['comprador'][:35]}")
            print(f"         {r['objeto'][:80]}{marca}")


if __name__ == "__main__":
    main()
