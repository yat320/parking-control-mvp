#!/usr/bin/env bash
# Arranca el panel web en el Codespace (si ya estaba corriendo, no hace nada).
cd "$(dirname "$0")/.."
if curl -s -o /dev/null http://localhost:8000/api/estado; then
  echo "El panel ya está corriendo: pestaña PORTS → puerto 8000."
  exit 0
fi
echo "Arrancando el panel web en el puerto 8000..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
