# Radar de licitaciones — neumáticos

Detecta a diario **licitaciones públicas** cuyo objeto sean neumáticos, recapado,
llantas, cámaras, baterías de vehículo o protectores, publicadas por municipios y
organismos del sector estatal argentino (foco AMBA + Provincia de Buenos Aires).

## Cómo funciona

1. **Motor** (`src/radar.py`) — corre en GitHub Actions 3 veces por día. Consulta seis
   fuentes y guarda lo que matchea en `data/radar_sin_verificar.csv`:
   - `src/sibom_mvp.py` — SIBOM, el boletín oficial digital de ~135 municipios de la
     Provincia de Buenos Aires.
   - `src/pbac_mvp.py` — PBAC, compras electrónicas de la Provincia de Buenos Aires.
   - `src/bac_mvp.py` — BAC, compras electrónicas de la Ciudad de Buenos Aires.
   - `src/comprar_mvp.py` — COMPR.AR, compras electrónicas del Estado nacional
     (ministerios, Fuerzas Armadas, Parques Nacionales, hospitales y universidades
     nacionales, y empresas del Estado adheridas como AySA). El reporte nacional
     identifica al comprador con un código interno; resolver el nombre del organismo
     es una mejora pendiente.
   - `src/corredores_mvp.py` — Corredores Viales S.A., la empresa estatal que
     administra las rutas nacionales concesionadas. Licita por su cuenta (no entra
     por COMPR.AR) y compra neumáticos y cubiertas para su flota vial. Usa la misma
     API JSON pública que su web de licitaciones.
   - `src/municipios_web_mvp.py` — webs propias de municipios donde ya somos
     proveedores y que publican sus licitaciones ahí: **Lanús** (Documentos
     oficiales → Licitaciones Públicas/Privadas del año; Lanús no está en SIBOM) y
     **San Isidro** (www.sanisidro.gob.ar/compras + la ficha de cada licitación para la
     fecha de apertura), **Avellaneda** (mda.gob.ar/tramites/licitaciones),
     **Florencio Varela** (varela.gob.ar/lic) y **Berazategui** (entradas de su web +
     el PDF de cada llamado, de donde sale objeto y apertura). Esteban Echeverría no
     tiene listado propio: publica en el boletín, que ya cubre SIBOM. El relevamiento
     de qué publica cada municipio está en `data/fuentes_objetivo.csv`.
2. **Filtros** — palabras clave del rubro + descarte de falsos positivos (cámara de
   video, batería de generador, recapado asfáltico de calzada, etc.). Cada licitación
   se clasifica en *vigente* / *cerrada* / *descartada* y caduca sola cuando pasa la
   fecha de apertura (o, si no hay fecha, a los 45 días de publicada).
3. **Municipios inscriptos** (`data/municipios_inscriptos.csv`) — tabla manual con los
   municipios donde la empresa ya está dada de alta como proveedor (hoy San Isidro,
   Lanús y Esteban Echeverría), con lo que contestó cada uno sobre cómo compra y dónde
   publica. Las licitaciones de esos municipios salen en el panel con la marca
   **✓ Ya somos proveedores** y, al abrir la tarjeta, el detalle de la inscripción.
   Ojo: en los tres, los concursos de precios y compras directas se invitan **por mail**
   a los inscriptos y no se publican; el radar solo ve las licitaciones públicas.
4. **Empresas privadas** (`data/fuentes_objetivo.csv`, filas con conector `privada_portal`) —
   portales y páginas donde empresas privadas (y algún ente como el Mercado Central) publican
   sus compras o licitaciones. Salen de lo que nos contestaron en la campaña de mails
   (repo campa-a-nuevos-clientes). Hoy se revisan a mano porque casi todos piden usuario
   (SAP Ariba, Oracle). La única abierta es la de contrataciones directas del Mercado Central,
   que es candidata a un conector. Cada fila tiene el estado del alta y quién lo indicó.
5. **Panel** (`scripts/build_dashboard.py`) — genera `docs/index.html`, servido como
   GitHub Pages: `https://santino530.github.io/radar-licitaciones/`. Se regenera y
   publica en cada corrida del motor.

## Estructura

```text
.github/workflows/radar.yml   corre el motor 3x/día y publica el panel
src/
  radar.py        orquestador
  sibom_mvp.py    conector SIBOM
  pbac_mvp.py     conector PBAC
  bac_mvp.py      conector BAC
  comprar_mvp.py  conector COMPR.AR (Nación)
  corredores_mvp.py  conector Corredores Viales S.A.
  municipios_web_mvp.py  conector webs municipales (Lanús, San Isidro, Avellaneda, F. Varela, Berazategui)
scripts/build_dashboard.py    arma el panel HTML desde los CSV
data/
  radar_sin_verificar.csv     salida del motor
  contactos_compras.csv       comprador → oficina de compras (dato de webs oficiales)
  municipios_inscriptos.csv   municipios donde ya somos proveedores + cómo compran
  proveedores_conocidos.csv   adjudicatarios detectados (dato de boletines oficiales)
  fuentes_objetivo.csv · sibom_city_ids.csv
  certs/                      intermedios Sectigo que algunas webs municipales no envían
docs/index.html               el panel (GitHub Pages)
web/dashboard.html            misma página, versión para publicar como Artifact
```

## Notas técnicas

- Los conectores usan sólo librería estándar de Python salvo `openpyxl` (lectura del
  reporte Excel de PBAC/BAC/COMPR.AR).
- Los certificados SSL de SIBOM y PBAC pueden estar vencidos → se usa contexto sin
  verificación (fuentes oficiales del Estado).
- Los datos se muestran **sin verificar**: antes de presentarse a una licitación hay que
  abrir el pliego en la fuente oficial y confirmar objeto, fechas y condiciones.
