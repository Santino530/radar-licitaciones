# Radar de licitaciones — neumáticos

Detecta a diario **licitaciones públicas** cuyo objeto sean neumáticos, recapado,
llantas, cámaras, baterías de vehículo o protectores, publicadas por municipios y
organismos del sector estatal argentino (foco AMBA + Provincia de Buenos Aires).

## Cómo funciona

1. **Motor** (`src/radar.py`) — corre en GitHub Actions 3 veces por día. Consulta cuatro
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
2. **Filtros** — palabras clave del rubro + descarte de falsos positivos (cámara de
   video, batería de generador, recapado asfáltico de calzada, etc.). Cada licitación
   se clasifica en *vigente* / *cerrada* / *descartada* y caduca sola cuando pasa la
   fecha de apertura (o, si no hay fecha, a los 45 días de publicada).
3. **Panel** (`scripts/build_dashboard.py`) — genera `docs/index.html`, servido como
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
scripts/build_dashboard.py    arma el panel HTML desde los CSV
data/
  radar_sin_verificar.csv     salida del motor
  contactos_compras.csv       comprador → oficina de compras (dato de webs oficiales)
  proveedores_conocidos.csv   adjudicatarios detectados (dato de boletines oficiales)
  fuentes_objetivo.csv · sibom_city_ids.csv
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
