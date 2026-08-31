# Radar de licitaciones

## 1. Qué problema busca resolver

La empresa quiere venderle neumáticos nuevos, recapado y servicio a **municipios y
organismos públicos**, pero ese canal se compra casi siempre por **licitación**. Hoy
nadie revisa de forma sistemática qué licitaciones de neumáticos / cubiertas / recapado
están abiertas en los municipios del AMBA. Cuando alguien se entera, muchas veces ya
venció el plazo para presentarse. La detección es manual, dispersa y tardía.

Este proyecto es la continuación natural de `busqueda-clientes-potenciales`: en ese
Excel ya identificamos ~32 municipios/organismos y marcamos que 36 prospectos compran
por **licitación pública**. Este proyecto se ocupa de **enterarse a tiempo de cada
llamado** de esos compradores.

## 2. Por qué es relevante para la empresa

- Las flotas municipales (camiones de residuos, volquetes, colectivos, utilitarios) son
  negocio de **volumen y recurrencia**, exactamente donde el recapado tiene mejor margen.
- El plazo entre publicación y apertura de una licitación suele ser corto (días). Perder
  el aviso = perder la oportunidad entera hasta el año siguiente.
- Un radar que avise "hoy salió esta licitación, vence tal día" permite que la empresa
  llegue a presentarse **preparada** en vez de a las corridas o directamente afuera.

## 3. Objetivo

Una **web propia que se actualiza sola todos los días** y muestra las licitaciones
públicas **activas** relacionadas con neumáticos / recapado / gomería de los municipios
y organismos que ya están en el Excel `Prospeccion_Clientes_Flotas.xlsx`, con su fecha
de apertura, estado y link al pliego.

## 4. Alcance

**Incluye:**
- Los municipios y organismos del AMBA que figuran en la hoja *Prospectos* del Excel
  (los que tienen *Modalidad de compra = licitación pública*).
- Licitaciones, contrataciones y compras cuyo objeto sea neumáticos, cubiertas, cámaras,
  llantas, recapado / recauchutaje, o servicio de gomería (alineación, balanceo,
  reparación de cubiertas).
- Un panel diario con: comprador, número y objeto del llamado, fecha de publicación,
  fecha de apertura, estado (abierta / por vencer / cerrada) y link a la fuente oficial.

**No incluye (por ahora):**
- Armar la oferta / preparar la documentación para presentarse (eso es trabajo del
  equipo comercial y administración).
- Licitaciones privadas y de flotas privadas (eso ya se trabaja desde el Excel).
- Provincias fuera del AMBA.
- Decidir bajo qué sector se presenta la empresa (**San Justo Neumáticos S.R.L.** o
  **Centro Integral de Neumáticos**): el radar sólo informa; la decisión es de los dueños.

## 5. Usuarios

- **Dueños / responsable comercial:** miran el panel para decidir a qué llamados vale la
  pena presentarse.
- **Administración:** usa la fecha de apertura y el link al pliego para preparar la
  presentación en término.

## 6. Información necesaria

| Dato | Fuente | Estado |
|---|---|---|
| Lista de municipios/organismos objetivo | Hoja *Prospectos* del Excel `Prospeccion_Clientes_Flotas.xlsx` | Ya existe |
| Portales de compras públicas donde publican esos municipios | PBAC (Provincia de Bs. As.), SIBOM (boletines oficiales municipales), BAC (CABA), COMPR.AR / CONTRAT.AR (Nación), y el sitio propio de cada municipio | A relevar por municipio |
| Palabras clave del rubro | neumático(s), cubierta(s), cámara(s), llanta(s), recapado, recauchutaje, gomería, alineación, balanceo | Definido (ajustable) |
| CUIT / datos de la empresa para registrarse como proveedor en cada portal | Interno | Pendiente de confirmar con la empresa |
| Cada cuánto publica cada municipio y con qué anticipación | Se aprende observando el radar unas semanas | Pendiente |

## 7. Funcionamiento esperado

1. Una vez por día, un proceso automático recorre los portales de compras públicas y los
   sitios de los municipios objetivo.
2. Filtra las publicaciones nuevas por las palabras clave del rubro.
3. Guarda las que coinciden en una base (comprador, objeto, fechas, link, estado).
4. Marca el estado según la fecha de apertura: **abierta**, **por vencer** (faltan ≤ X
   días) o **cerrada**.
5. La web muestra la lista ordenada por urgencia (las que están por vencer arriba).
6. *(Etapa posterior)* además del panel, un aviso diario por mail / WhatsApp con lo nuevo
   y lo que está por vencer.

## 8. Tecnologías y arquitectura (decidido 2026-08-27)

> El usuario no es programador avanzado: cada decisión técnica se explica antes de
> implementarla, y se elige siempre la opción más simple que funcione.

**Se construye el radar propio** (no se contrata un agregador comercial — ver
`docs/decisiones.md`). Detalle de fuentes en `docs/fuentes-y-adquisicion.md`.

- **Ingesta = conectores por tipo de fuente**, no por municipio:
  1. `sibom` — boletines de ~135 municipios de PBA (búsqueda GET, filtro `type=Licitación`).
  2. `pbac` — Provincia + organismos + municipios adheridos.
  3. `bac_ocds` — datos abiertos de CABA (formato Open Contracting).
  4. `comprar_ocds` — datos abiertos de Nación (opcional, sólo si entra un organismo nacional).
  5. `municipal_html` — sólo para huecos comprobados (RAFAM primero, es multi-municipio).
- **El scraper corre en GitHub Actions** (cron 1–2x/día). Hace `git commit` del CSV del
  radar. No corre en la rutina de la nube de Anthropic (tiene `WebFetch` bloqueado) ni en
  la PC de Santino (tendría que estar prendida).
- **Dashboard + aviso al celular:** se reusa el molde de `busqueda-laboral`
  (`docs/blueprint-referencia.md`): Artifact publicado + `PushNotification`, disparado por
  la rutina en la nube o por una sesión interactiva.
- **MVP (primero):** un script del conector `sibom` que consulta 3 municipios de prueba y
  devuelve la lista filtrada por palabras clave. Sirve para medir cuánto trae cada fuente
  antes de armar toda la infra.
- **Lenguaje:** a definir al escribir el MVP (Python es lo que ya se usó en
  `busqueda-clientes-potenciales`).

## 9. Estado actual (2026-08-28)

- **Fuentes relevadas:** `docs/fuentes-y-adquisicion.md`, `docs/inventario-fuentes-completo.md`,
  `data/fuentes_objetivo.csv`.
- **Conectores funcionando:**
  - `src/sibom_mvp.py` — boletines de los ~135 municipios de PBA. Grupos "objetivo"
    (AMBA) y "volumen_alto" (partidos del interior con Dirección Vial grande), filtro de
    ruido, ventana de días.
  - `src/pbac_mvp.py` — "apertura próxima" de toda la Provincia, vía el botón
    "Descargar Reporte Excel" (una request → xlsx con ~467 procesos).
- **Motor / orquestador:** `src/radar.py` corre todos los conectores, normaliza a
  `data/radar_sin_verificar.csv` y marca lo **nuevo respecto de la corrida anterior**
  (`primera_vez_visto` = "nueva apertura").
- **Automatización:** `.github/workflows/radar.yml` — corre `radar.py` 3 veces por día
  (09/13/18 hs ART) y commitea el CSV. **Se activa cuando el repo se suba a GitHub.**
- **Falta:** conectores de boletines oficiales (Provincia/CABA/Nación), dashboard web +
  notificación push, y subir el repo a GitHub.

## 10. Próximos pasos

1. Subir el repo a un GitHub privado y probar el workflow con "Run workflow" a mano.
2. Conector `bac` (CABA): el listado de procesos y aperturas de
   `buenosairescompras.gob.ar` es público sin cuenta; se modela sobre `pbac_mvp.py`
   (misma familia de software). Los datos abiertos OCDS de CABA se actualizan sólo
   **trimestralmente** → no sirven para el radar diario, sólo para análisis histórico.
3. Portales municipales de los partidos del AMBA que no publican en SIBOM.
4. Conector `comprar` (Nación) y, como respaldo legal, el Boletín Oficial PBA.
5. Afinar filtros de ruido y palabras clave con lo que vaya juntando el radar
   (hecho una primera vez el 2026-08-31: ruido "playón deportivo", estado inferido
   `adjudicada` / `cerrada`).
6. Dashboard web (Artifact) + `PushNotification`, reusando el molde de `busqueda-laboral`.
7. Confirmar con la empresa con qué CUIT / sector (San Justo Neumáticos S.R.L. o Centro
   Integral de Neumáticos) se presenta a licitaciones.

---

### Estructura de la carpeta

```text
/radar-licitaciones
    README.md
    .github/workflows/radar.yml   ← corre el motor 3x/día (al subir a GitHub)
    /docs
        decisiones.md
        fuentes-y-adquisicion.md
        inventario-fuentes-completo.md
        blueprint-referencia.md
    /data
        fuentes_objetivo.csv          ← fuentes clasificadas por tipo de conector
        proveedores_conocidos.csv     ← proveedores/competidores que ya venden al Estado
        sibom_city_ids.csv            ← municipio → id en SIBOM
        radar_sin_verificar.csv       ← salida del motor (lo que hay que verificar)
        planilla_*.csv                ← export de la Google Sheet (ignorado por git)
    /src
        radar.py       ← orquestador: corre todo y arma radar_sin_verificar.csv
        sibom_mvp.py    ← conector SIBOM
        pbac_mvp.py     ← conector PBAC
```

### Relación con otros proyectos

- **`busqueda-clientes-potenciales`**: le da la lista de municipios objetivo. Este
  proyecto no la reemplaza; se enfoca sólo en el canal licitación pública.
- Ambos son del área **comercial** de la empresa, pero se mantienen separados: distinto
  objetivo, distintos datos, distinto desarrollo.
