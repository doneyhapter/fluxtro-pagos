# Changelog

## 1.1 · 2026-10-29

### Cambios

- Dependencias: `flask` 3.1.3 y `requests` 2.33.0, por vulnerabilidades publicadas
  (DEUDA_TECNICA §19 del engine). Sin cambios de código.

### Desplegado

- Commit `4606068`. Build `04dfe145-4bcf-49e9-95bb-42467a0f7251`.
- Revisión `fluxtro-pagos-00007-yiy` (tag `v11`), al 100% del tráfico desde el 2026-10-29.
  Rollback: `fluxtro-pagos-00006-wff`.
- Comando: `gcloud run deploy fluxtro-pagos --source . --region us-central1 --project fluxtro-mvp --no-traffic --tag v11`,
  sin `--set-env-vars` ni `--set-secrets`: la revisión heredó la configuración Live.

### Verificado

- Antes del deploy, en Windows: `tests/test_proceso.py` dio 8 passed y
  `pip-audit -r requirements.txt` dio `No known vulnerabilities found`.
- `gcloud run revisions describe`: `PAYPAL_ENV=live`, `PAYPAL_CLIENT_SECRET` →
  `paypal-client-secret-live:latest`, `PAYPAL_WEBHOOK_ID=9HK87803SX890961V`.
- `/health` respondió `{"status":"ok"}` en la URL del tag y en la de producción después de
  mover el tráfico.

## 1.0 · 2026-10-07

- Servicio nuevo `fluxtro-pagos` (Flask, Cloud Run) con PayPal Checkout.
- `POST /crear-orden`: crea la orden con el monto fijado por el servidor y `custom_id` = `<id_pedido>|<producto>`.
- `POST /capturar-orden`: captura idempotente (`PayPal-Request-Id`) y registro del pago.
- `POST /webhook`: respaldo con verificación de firma para `PAYMENT.CAPTURE.COMPLETED`.
- `GET /health` y CORS por lista de orígenes para los endpoints del navegador.
- `procesar_captura()`: registra el pago en el Sheet `Leads` (columnas W a Z), detecta duplicados y montos inválidos, libera la fila (`Retenido` → `Pendiente`) y llama a `/procesar` del engine.
- Pruebas con `pytest` de las ocho reglas de `procesar_captura()`.
- `README.md` y `DEPLOY.md`.

### Ajuste previo al primer deploy

- `capturar_orden()` pide a PayPal `Prefer: return=representation` en la captura y en la
  consulta de una orden ya capturada. La respuesta por defecto (`return=minimal`) trae solo
  `id`, `status` y links, sin el detalle de la captura. Incluido en el commit `dfec991`.

### Desplegado

- Commit `dfec991`. Build `9d413bf2-d13d-4b31-a61a-95f506c32e15`.
- Revisiones (todas al 100% del tráfico al crearse):

| Revisión | Fecha (UTC) | Cambio |
|---|---|---|
| `fluxtro-pagos-00001-pbr` | 2026-10-07 20:27 | Primer deploy, PayPal Sandbox |
| `fluxtro-pagos-00002-8vm` | 2026-10-07 20:39 | `PAYPAL_WEBHOOK_ID` de Sandbox |
| `fluxtro-pagos-00003-xgv` | 2026-10-07 21:36 | `ALLOWED_ORIGINS` suma el preview de la landing |
| `fluxtro-pagos-00004-2vq` | 2026-10-09 13:20 | Paso a Live: `PAYPAL_ENV`, `PAYPAL_CLIENT_ID`, `PAYPAL_WEBHOOK_ID`, secreto `paypal-client-secret-live`; se quita el preview |
| `fluxtro-pagos-00005-twt` | 2026-10-09 13:30 | `ALLOWED_ORIGINS` vuelve a sumar el preview para el pago real de prueba |
| `fluxtro-pagos-00006-wff` | 2026-10-09 14:08 | `ALLOWED_ORIGINS` queda solo con `fluxtro.app` y `www.fluxtro.app` |

### Verificado

- Sandbox, 2026-10-07: informe (fila 44, captura `3XB00338CX2271934`) y combo, desde el
  preview de la landing. Informe y audio recibidos por email. Log: la captura liberó la fila
  (regla 9), `/procesar` respondió por timeout de lectura esperado y el webhook de la misma
  captura se ignoró (regla 5).
- Live, 2026-10-09: pago real de USD 3 desde el preview (fila 46, captura
  `6UR68652XU7712642`). Llegó a `pago-exitoso.html`, la fila quedó con W `Pagado`,
  X `Informe`, Y `3.00`, Z con la captura, y el informe llegó por email. Log: regla 9 a las
  13:51:07 UTC y webhook de Live ignorado por regla 5 a las 13:51:10 UTC.
- El engine aceptó la llamada a `/procesar` con `ENGINE_AUDIENCE` = URL de
  `/generar-informe`; no hizo falta el plan B del 401.
