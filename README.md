# ecolima-backend
FastAPI backend for EcoLima — recycling point optimization using GIS + LightGBM for Lima Metropolitana

## Deploy en Azure — Notas

- El startup command usa 1 worker (`-w 1`) en lugar de 4 debido a memoria limitada del plan App Service. Las librerías ML (lightgbm, shap, geopandas, scipy) consumen suficiente RAM por proceso que 4 workers simultáneos causan errores SIGKILL out of memory.
- Si se escala a un plan con más RAM (B2 o superior), se puede aumentar el número de workers proporcionalmente.
- Startup command actual:
  ```
  gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000 --timeout 120
  ```
- Base de datos de pruebas: Neon (PostgreSQL serverless) con PostGIS activado.
- Variables de entorno requeridas en Azure: `DATABASE_URL`, `SECRET_KEY`, `AZURE_BLOB_CONNECTION_STRING`, `AZURE_BLOB_CONTAINER_MODELS`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `PYTHONPATH=/home/site/wwwroot`

## Demo deployeada: 
https://ecolima-backend-fbg6fzb4epd0eucr.canadacentral-01.azurewebsites.net/docs
