# Changelog

## 1.0 · 2026-10-07

- Servicio nuevo `fluxtro-pagos` (Flask, Cloud Run) con PayPal Checkout.
- `POST /crear-orden`: crea la orden con el monto fijado por el servidor y `custom_id` = `<id_pedido>|<producto>`.
- `POST /capturar-orden`: captura idempotente (`PayPal-Request-Id`) y registro del pago.
- `POST /webhook`: respaldo con verificación de firma para `PAYMENT.CAPTURE.COMPLETED`.
- `GET /health` y CORS por lista de orígenes para los endpoints del navegador.
- `procesar_captura()`: registra el pago en el Sheet `Leads` (columnas W a Z), detecta duplicados y montos inválidos, libera la fila (`Retenido` → `Pendiente`) y llama a `/procesar` del engine.
- Pruebas con `pytest` de las ocho reglas de `procesar_captura()`.
- `README.md` y `DEPLOY.md`.
