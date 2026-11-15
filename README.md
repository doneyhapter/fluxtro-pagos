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
| `POST /crear-orden` | Cuerpo `{"id_pedido": "<uuid v4>", "producto": "informe" \| "combo" \| "tu2027" \| "carta_tu2027"}`. Responde `{"id": "<order_id>"}`; `400` si los datos son inválidos; `502` si PayPal falla. Campo opcional `"cupon": "<texto>"`: con cupón válido la orden lleva el total con descuento; con cupón rechazado responde `409` con `{"error": "cupon", "motivo": ...}` y no llama a PayPal. |
| `POST /validar-cupon` | Cuerpo `{"cupon": "<texto>", "producto": "<clave>"}`. Responde `200` con `{"valido": true, "cupon", "precio", "descuento", "total"}` o `{"valido": false, "motivo"}`; `400` si faltan datos o el producto no existe. No escribe ni reserva nada. |
| `POST /capturar-orden` | Cuerpo `{"order_id": "<id>"}`. Captura y registra el pago. Responde `200` con `COMPLETED` o `PENDING`, `402` con otro estado, `502` si PayPal falla. Un error del Sheet solo se registra en el log. |
| `POST /webhook` | Verifica la firma y procesa `PAYMENT.CAPTURE.COMPLETED`. `503` si `PAYPAL_WEBHOOK_ID` está vacía; `400` si la firma no es válida; con firma válida siempre `200`. |
| `GET /health` | `{"status": "ok"}`. |

CORS solo en `/crear-orden`, `/capturar-orden` y `/validar-cupon`, con coincidencia exacta contra `ALLOWED_ORIGINS`.

## Variables de entorno

| Variable | Contenido | Obligatoria |
|---|---|---|
| `PAYPAL_ENV` | `sandbox` o `live` | sí |
| `PAYPAL_CLIENT_ID` | Client ID de la app de PayPal | sí |
| `PAYPAL_CLIENT_SECRET` | Client Secret (llega desde Secret Manager) | sí |
| `PAYPAL_WEBHOOK_ID` | ID del webhook registrado en PayPal | no (sin ella `/webhook` responde 503) |
| `SPREADSHEET_ID` | ID del Sheet de leads | sí |
| `PRECIO_INFORME` | monto en USD, formato `10.00` | sí |
| `PRECIO_COMBO` | monto en USD, formato `12.00` | sí |
| `PRECIO_TU2027` | monto en USD, formato `10.00` | sí |
| `PRECIO_CARTA_TU2027` | monto en USD, formato `17.00` | sí |
| `ALLOWED_ORIGINS` | orígenes permitidos para CORS, separados por espacio | sí |
| `ENGINE_URL_PROCESAR` | URL completa de `/procesar` del engine | sí |
| `ENGINE_AUDIENCE` | *audience* del ID token para llamar al engine | sí |

Cualquier valor de `PAYPAL_ENV` distinto de `sandbox` o `live` hace fallar el arranque. La hoja es
`Leads`.

## Columnas del Sheet

El servicio solo lee las columnas N, R y V y solo escribe N, R, W, X, Y, Z, AA y AD.

| Columna | Nombre | Uso |
|---|---|---|
| N (14) | `Estado_Fase1` | lee; escribe `Pendiente` al liberar |
| R (18) | `Estado_Audio` | escribe `Pendiente` al liberar un combo |
| V (22) | `ID_Pedido` | lee para encontrar la fila (la escribe el receptor) |
| W (23) | `Estado_Pago` | `Pagado`, `Duplicado` o `Monto inválido` |
| X (24) | `Producto` | `Informe`, `Combo`, `Tu 2027` o `Carta + Tu 2027` |
| Y (25) | `Monto` | valor cobrado según PayPal, tal como viene |
| Z (26) | `Captura_ID` | ID de la captura; con duplicado, los IDs separados por `, ` |
| AA (27) | `Estado_Tu2027` | escribe `Pendiente` al liberar un producto que incluye "Tu 2027" (`tu2027` o `carta_tu2027`); la lee el engine |
| AD (30) | `Cupon` | código del cupón usado en la compra; vacía si no hubo |

## Cupones

Los cupones viven en la pestaña `Cupones` del mismo Sheet, con encabezados en la fila 1 y datos desde la fila 2. Crear, cambiar o apagar un cupón es editar una fila, sin deploy.

| Col | Encabezado | Contenido |
|---|---|---|
| A | `Codigo` | el código |
| B | `Grupo` | referencia de Doney; el servicio no la usa |
| C | `Descuento` | monto fijo en USD, mayor que 0 |
| D | `Producto` | `Todos`, o los nombres de la columna X de `Leads` separados por coma |
| E | `Tope` | usos máximos, entero mayor o igual a 1 |
| F | `Usos` | la escribe el servicio; vacía = 0 |
| G | `Vence` | fecha; vacía = no vence |
| H | `Activo` | `Sí` (o casilla marcada) para que funcione |

Reglas:

- El código se compara sin distinguir mayúsculas, tildes ni espacios, tanto lo que escribe el comprador como la columna A. Limpio, debe tener de 3 a 30 caracteres entre letras, números, guion y guion bajo. Si dos filas quedan iguales, vale la primera.
- Vence al final del día de `Vence`, hora de Montevideo (UTC-3 fijo).
- Si el precio con descuento queda por debajo de USD 3.00, el cupón se rechaza.
- El uso se cuenta al capturar el pago, no al validar el código ni al crear la orden. Un pago duplicado no suma.
- Al capturar se respeta el descuento ya visto: no se revisan de nuevo `Activo`, `Vence` ni `Tope`. Puede quedar algún uso por encima del tope.
- Si `Cupones` no se puede leer, `/validar-cupon` responde que el cupón no está disponible y `/crear-orden` no crea la orden con cupón: nunca se cobra un precio distinto del que el comprador vio.

Motivos de rechazo: `invalido`, `vencido`, `agotado`, `no_aplica`, `no_disponible`.

## Reglas de `procesar_captura()`

Corre dentro de un `threading.Lock` del proceso (el servicio usa una sola instancia y un solo
worker). En orden:

1. Si `status` no es `COMPLETED`: log y salir.
2. Separar `custom_id` en `id_pedido`, `producto` y, si viene, `cupon`; si el formato no es válido, log de error y salir.
3. Buscar la fila cuyo valor en V sea exactamente `id_pedido`; si no existe, log de error y salir.
4. Leer N, R, W, X, Y, Z de la fila.
5. Si `capture_id` ya está en Z: "captura ya registrada" y salir (idempotencia).
6. Si Z tiene otra captura: W = `Duplicado`, agregar `, <capture_id>` a Z y salir sin liberar.
7. Monto válido = moneda `USD` y valor igual al precio del producto menos el descuento del cupón,
   si hubo (como `Decimal`; si el cupón no se puede leer o ya no existe, se espera el precio
   completo). Escribir W (`Pagado` o `Monto inválido`), X, Y, Z y, si hubo cupón, AD en una sola
   actualización. Si W quedó en `Pagado` y hubo cupón, sumar 1 a `Usos` en `Cupones`; si esa suma
   falla, queda en el log y la fila se libera igual.
8. Si W quedó en `Monto inválido`: salir sin liberar.
9. Si N es exactamente `Retenido`: N = `Pendiente`, R = `Pendiente` si es `combo`, AA = `Pendiente` si es
   `tu2027` o `carta_tu2027`, y llamar a `/procesar` del engine tras soltar el lock. Si N tiene
   otro valor, no se toca N, R ni AA.

## Pruebas

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt pytest
.venv/Scripts/python -m pytest -q tests
```

Se corre sobre la carpeta `tests`: `Claude outputs/` puede tener archivos de referencia con nombre `test_*.py`, que pytest recogería desde la raíz.

Las pruebas usan una hoja falsa en memoria; no usan red, PayPal ni el engine. `pytest` no está en
`requirements.txt`.

## Deploy

Ver `DEPLOY.md`.
