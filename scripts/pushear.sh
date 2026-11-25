#!/usr/bin/env bash
# Push con reintentos para el workflow. Si alguien pusheo mientras corria
# (backfill, sesion interactiva), hace pull --rebase y reintenta. Si despues de
# 3 intentos no pudo, FALLA (exit 1) para que el error se vea en Actions en vez
# de pasar en silencio.
set -u
for intento in 1 2 3; do
  if git push; then
    exit 0
  fi
  echo "push rechazado, intento $intento: pull --rebase y reintento"
  git pull --rebase origin main || { echo "pull --rebase fallo"; exit 1; }
  sleep 5
done
echo "ERROR: no se pudo pushear despues de 3 intentos"
exit 1
