# fluxtro-pagos

Servicio de pagos de Fluxtro con PayPal Checkout (Cloud Run, Python/Flask). Es independiente de
`fluxtro-engine`, `fluxtro-landing` y el receptor de Apps Script.

El servicio:

1. crea órdenes de PayPal con el monto fijado por el servidor y el ID de la fila en `custom_id`;
2. captura la orden cuando el comprador la aprueba;
3. recibe el webhook `PAYMENT.CAPTURE.COMPLETED` como respaldo;
4. registra el pago en el Sheet `Leads`, libera la fila (`Retenido` → `Pendiente`) y llama a
   `/procesar` del engine.

## Endpoints

| Endpoint | Descripción |
|---|---|
| `POST /crear-orden` | Cuerpo `{"id_pedido": "<uuid v4>", "producto": "informe" \| "combo"}`. Responde `{"id": "<order_id>"}`; `400` si los datos son inválidos; `502` si PayPal falla. |
| `POST /capturar-orden` | Cuerpo `{"order_id": "<id>"}`. Captura y registra el pago. Responde `200` con `COMPLETED` o `PENDING`, `402` con otro estado, `502` si PayPal falla. Un error del Sheet solo se registra en el log. |
| `POST /webhook` | Verifica la firma y procesa `PAYMENT.CAPTURE.COMPLETED`. `503` si `PAYPAL_WEBHOOK_ID` está vacía; `400` si la firma no es válida; con firma válida siempre `200`. |
| `GET /health` | `{"status": "ok"}`. |

CORS solo en `/crear-orden` y `/capturar-orden`, con coincidencia exacta contra `ALLOWED_ORIGINS`.

## Variables de entorno

| Variable | Contenido | Obligatoria |
|---|---|---|
| `PAYPAL_ENV` | `sandbox` o `live` | sí |
| `PAYPAL_CLIENT_ID` | Client ID de la app de PayPal | sí |
| `PAYPAL_CLIENT_SECRET` | Client Secret (llega desde Secret Manager) | sí |
| `PAYPAL_WEBHOOK_ID` | ID del webhook registrado en PayPal | no (sin ella `/webhook` responde 503) |
| `SPREADSHEET_ID` | ID del Sheet de leads | sí |
| `PRECIO_INFORME` | monto en USD, formato `3.00` | sí |
| `PRECIO_COMBO` | monto en USD, formato `4.00` | sí |
| `ALLOWED_ORIGINS` | orígenes permitidos para CORS, separados por espacio | sí |
| `ENGINE_URL_PROCESAR` | URL completa de `/procesar` del engine | sí |
| `ENGINE_AUDIENCE` | *audience* del ID token para llamar al engine | sí |

Cualquier valor de `PAYPAL_ENV` distinto de `sandbox` o `live` hace fallar el arranque. La hoja es
`Leads`.

## Columnas del Sheet

El servicio solo lee las columnas N, R y V y solo escribe N, R, W, X, Y, Z.

| Columna | Nombre | Uso |
|---|---|---|
| N (14) | `Estado_Fase1` | lee; escribe `Pendiente` al liberar |
| R (18) | `Estado_Audio` | escribe `Pendiente` al liberar un combo |
| V (22) | `ID_Pedido` | lee para encontrar la fila (la escribe el receptor) |
| W (23) | `Estado_Pago` | `Pagado`, `Duplicado` o `Monto inválido` |
| X (24) | `Producto` | `Informe` o `Combo` |
| Y (25) | `Monto` | valor cobrado según PayPal, tal como viene |
| Z (26) | `Captura_ID` | ID de la captura; con duplicado, los IDs separados por `, ` |

## Reglas de `procesar_captura()`

Corre dentro de un `threading.Lock` del proceso (el servicio usa una sola instancia y un solo
worker). En orden:

1. Si `status` no es `COMPLETED`: log y salir.
2. Separar `custom_id` en `id_pedido` y `producto`; si el formato no es válido, log de error y salir.
3. Buscar la fila cuyo valor en V sea exactamente `id_pedido`; si no existe, log de error y salir.
4. Leer N, R, W, X, Y, Z de la fila.
5. Si `capture_id` ya está en Z: "captura ya registrada" y salir (idempotencia).
6. Si Z tiene otra captura: W = `Duplicado`, agregar `, <capture_id>` a Z y salir sin liberar.
7. Monto válido = moneda `USD` y valor igual al precio del producto (como `Decimal`). Escribir
   W (`Pagado` o `Monto inválido`), X, Y, Z en una sola actualización.
8. Si W quedó en `Monto inválido`: salir sin liberar.
9. Si N es exactamente `Retenido`: N = `Pendiente`, R = `Pendiente` si es combo, y llamar a
   `/procesar` del engine tras soltar el lock. Si N tiene otro valor, no se toca N ni R.

## Pruebas

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt pytest
.venv/Scripts/python -m pytest -q
```

Las pruebas usan una hoja falsa en memoria; no usan red, PayPal ni el engine. `pytest` no está en
`requirements.txt`.

## Deploy

Ver `DEPLOY.md`.
