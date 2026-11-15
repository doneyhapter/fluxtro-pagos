# Changelog

## 1.3 · 2026-11-14

### Cambios

- Cupones de descuento: los códigos viven en la pestaña `Cupones` del Sheet, que se edita a mano; crear, cambiar o apagar un cupón no requiere deploy.
- `main.py`:
  - Constantes `HOJA_CUPONES`, `COL_CUPON`, `COL_CUPON_USOS`, `PISO_CON_CUPON`, `ZONA_CUPONES`, `MOTIVOS_CUPON` y `RE_CUPON`. `/validar-cupon` se suma a `RUTAS_CORS`.
  - Bloque "Cupones": `CuponRechazado`, `_hoja_cupones()`, `_normalizar()`, `_a_decimal()`, `_a_entero()`, `_a_fecha()`, `_buscar_cupon()`, `_evaluar_cupon()` y `_monto()`. La pestaña se lee con `UNFORMATTED_VALUE` para no depender de la configuración regional del Sheet.
  - `_monto_valido()` recibe el monto esperado en vez del producto.
  - `_procesar_con_lock()`: admite el tercer campo de `custom_id`, calcula el monto esperado con el cupón, escribe AD y suma el uso en `Cupones` cuando el pago queda `Pagado`.
  - `/crear-orden`: campo opcional `cupon`. Con cupón rechazado responde `409` con `{"error": "cupon", "motivo": ...}` y no llama a PayPal. Sin cupón, igual que antes.
  - Endpoint nuevo `POST /validar-cupon`: no escribe ni reserva nada.
  - Motivos: `invalido`, `vencido`, `agotado`, `no_aplica`, `no_disponible`.
- `tests/test_cupones.py`: copia literal de `Claude outputs/test_cupones_referencia.py` (sha256 `1b860c88572745d2a54f22e915560d4ca925375e0ccd328a8e5af8e39d561b75`).

| # | Decisión |
|---|---|
| 1 | Pestaña `Cupones`: `Codigo`, `Grupo`, `Descuento`, `Producto`, `Tope`, `Usos`, `Vence`, `Activo` (fila 1 encabezados, datos desde la 2). |
| 2 | Código sin distinguir mayúsculas, tildes ni espacios; de 3 a 30 caracteres entre letras, números, guion y guion bajo. Si dos filas quedan iguales, vale la primera. |
| 3 | Vale hasta el final del día de `Vence`, hora de Montevideo (UTC-3 fijo). |
| 4 | Si el precio con descuento queda por debajo de USD 3.00, el cupón se rechaza. |
| 5 | El uso se cuenta al capturar el pago. Un pago duplicado no suma. |
| 6 | Al capturar no se revisan de nuevo `Activo`, `Vence` ni `Tope`; puede quedar algún uso por encima del tope. |
| 7 | Regla 8: monto esperado = precio − descuento del cupón de `custom_id`. Si el cupón no se puede leer o ya no existe, se espera el precio completo y el pago queda en `Monto inválido`, sin liberar. |
| 8 | A PayPal va el monto final, sin `breakdown`. La descripción suma ` - descuento <código>`, solo ASCII. |
| 9 | `custom_id` admite un tercer campo opcional: `id_pedido\|producto\|cupon`. |
| 10 | Columna AD (30), `Cupon`, en `Leads`: código usado, también con `Monto inválido`. |
| 11 | Un solo código por compra. |
| 12 | Si `Cupones` no se puede leer: `/validar-cupon` responde no disponible y `/crear-orden` no crea la orden con cupón. Nunca se cobra un precio distinto del visto. |
| 13 | Sin variables de entorno nuevas. |

### Desplegado

- Desplegado junto con v1.2 el 2026-11-15, en un solo deploy. Commit `28171c0`. Build `c9de8825-10cc-4304-ab6f-88e535b1ceb0`.
- Revisión `fluxtro-pagos-00012-gij` (tag `v13` durante la verificación), al 100% del tráfico desde el 2026-11-15,
  con `latestRevision: true` y sin tags. Rollback: `fluxtro-pagos-00008-vfw`.
- Comando: `gcloud run deploy fluxtro-pagos --source . --region us-central1 --project fluxtro-mvp --no-traffic --tag v13 --update-env-vars PRECIO_TU2027=10.00,PRECIO_CARTA_TU2027=17.00`.
  La revisión heredó el resto de la configuración Live.
- Tráfico: `gcloud run services update-traffic fluxtro-pagos --region us-central1 --project fluxtro-mvp --to-latest --remove-tags v13`.
- Los productos nuevos y los cupones no se pueden comprar ni usar hasta que la landing los ofrezca.

### Verificado

- `96 passed` (`.venv/Scripts/python -m pytest -q tests`).
- Spec: `Claude outputs/SPEC_cupones_v1.3_2026-11-14.md`.
  Reporte: `Claude outputs/REPORTE_cupones_v1.3_2026-11-14.md`.
- **En Cloud Shell (2026-11-15)**, clon en `28171c0`: `gcloud meta list-files-for-upload` listó `Dockerfile`,
  `requirements.txt` y `main.py`.
- **Sheet (2026-11-15, antes del deploy):** fila 1 de `Leads`, AA a AD: `Estado_Tu2027`, `Texto_Tu2027`,
  `Link_Tu2027`, `Cupon`. Pestaña `Cupones`, A a H: `Codigo`, `Grupo`, `Descuento`, `Producto`, `Tope`, `Usos`,
  `Vence`, `Activo`.
- `gcloud run revisions describe fluxtro-pagos-00012-gij`: `PAYPAL_ENV=live`, `PRECIO_INFORME='10.00'`,
  `PRECIO_COMBO='12.00'`, `PRECIO_TU2027='10.00'`, `PRECIO_CARTA_TU2027='17.00'`, `PAYPAL_CLIENT_SECRET` →
  `paypal-client-secret-live:latest`, `PAYPAL_WEBHOOK_ID=9HK87803SX890961V`.
- **Contra el tag `v13`, antes de mover el tráfico:**
  - `GET /health`: `{"status":"ok"}`.
  - `POST /validar-cupon` con `{"cupon":"noexiste","producto":"tu2027"}`: `{"motivo":"invalido","valido":false}`.
    Prueba que la revisión lee la pestaña `Cupones` del Sheet real.
  - `POST /crear-orden` con `informe`: `{"id":"26B35022R39623403"}`. Con `tu2027`: `{"id":"1X273624HM910720R"}`.
    Las dos órdenes se crearon en PayPal Live con un `id_pedido` aleatorio y no se pagaron.
- **Después de mover el tráfico:** `status.traffic` con una sola entrada, `latestRevision: true`, `percent: 100`,
  `fluxtro-pagos-00012-gij`, sin tags. `/health` en la URL de producción: `{"status":"ok"}`.

### PENDIENTE DE VERIFICAR

- La captura de un pago real de `tu2027`, de `carta_tu2027` y de una compra con cupón. Dependen de la landing
  (specs 3 y 3b). Hasta ahora solo las cubren las pruebas automáticas.
- Por qué no existen las revisiones `00009` a `00011`: `gcloud run revisions list` del 2026-11-15 muestra
  `00012-gij` seguida de `00008-vfw`. Se puede buscar en Cloud Logging, registros de auditoría del servicio
  `fluxtro-pagos` entre el 2026-11-01 y el 2026-11-15.

## 1.2 · 2026-11-14

### Cambios

- Dos productos nuevos: "Tu 2027" suelto (`tu2027`) y Carta natal + "Tu 2027" (`carta_tu2027`).
- `PRODUCTOS` en `main.py` lleva un campo `pendientes` con las columnas que, además de N, pasan a
  `Pendiente` al liberar la fila. `_procesar_con_lock()` recorre `pendientes` en lugar del
  `if producto == "combo"`; `informe` y `combo` se comportan igual que antes.
- Constante nueva `COL_ESTADO_TU2027 = 27` (columna AA, `Estado_Tu2027`). N y AA se escriben en la
  misma actualización del Sheet. El engine lee AA para disparar `/generar-tu2027`; `fluxtro-pagos` no la lee.
- `PRECIO_TU2027` y `PRECIO_CARTA_TU2027` son obligatorias: sin ellas el servicio no arranca.
- Regla 8 sin cambios: cada producto se valida contra su precio fijo. Si N no es `Retenido`, no se
  toca N, R ni AA (regla 9). `carta_tu2027` no marca R: no incluye audio.

| Clave (`producto`) | Columna X | Descripción en PayPal | Variable de precio | Columnas que pasan a `Pendiente` |
|---|---|---|---|---|
| `informe` | `Informe` | `Informe Fluxtro` | `PRECIO_INFORME` | N |
| `combo` | `Combo` | `Informe Fluxtro + Audio` | `PRECIO_COMBO` | N y R |
| `tu2027` | `Tu 2027` | `Tu 2027` | `PRECIO_TU2027` | N y AA |
| `carta_tu2027` | `Carta + Tu 2027` | `Informe Fluxtro + Tu 2027` | `PRECIO_CARTA_TU2027` | N y AA |

### Desplegado

- Desplegado junto con v1.3 el 2026-11-15, en la revisión `fluxtro-pagos-00012-gij`. Detalle en el tramo 1.3.

### Verificado

- `22 passed` (`.venv/Scripts/python -m pytest -q tests`).
- Spec: `Claude outputs/SPEC_productos_tu2027_v1.2_2026-11-14.md`.
  Reporte: `Claude outputs/REPORTE_productos_tu2027_v1.2_2026-11-14.md`.

## Infraestructura · 2026-11-02 — Limpieza de Artifact Registry

- Política en `cloud-run-source-deploy`: se conservan las 5 imágenes más recientes por paquete y se borran las de más de 7 días que queden fuera (activa desde 2026-11-01).
- `fluxtro-pagos` no perdió imágenes (tiene 2). Sin deploy ni cambio de revisión.

## Configuración · 2026-11-01 — Fin de la promoción

### Cambios

- `PRECIO_INFORME` 3.00 → 10.00 y `PRECIO_COMBO` 4.00 → 12.00. Sin cambios de código.

### Desplegado

- Revisión `fluxtro-pagos-00008-vfw`, creada por
  `gcloud run services update fluxtro-pagos --region us-central1 --project fluxtro-mvp --update-env-vars PRECIO_INFORME=10.00,PRECIO_COMBO=12.00`.
- La revisión nueva quedó con 0% de tráfico: el redeploy de v1.1 había fijado el tráfico en
  `fluxtro-pagos-00007-yiy` con `--to-revisions`, así que los cambios de configuración posteriores
  no entraban a producción. Se corrigió con `update-traffic --to-latest`: 100% LATEST
  (`fluxtro-pagos-00008-vfw`). Se quitó el tag `v11` de `00007-yiy` (su URL seguía cobrando 3.00 y 4.00).
- Ningún pago entró mientras el tráfico estuvo fijado con los precios de la promoción.

### Verificado

- `gcloud run services describe`: `PRECIO_INFORME='10.00'`, `PRECIO_COMBO='12.00'`;
  `status.traffic` con una sola entrada, `latestRevision: true`, `percent: 100`,
  `fluxtro-pagos-00008-vfw`, sin tags.

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
