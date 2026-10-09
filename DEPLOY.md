# fluxtro-pagos · Deploy

Proyecto `fluxtro-mvp`, región `us-central1`. Los comandos se ejecutan en Cloud Shell, cada uno
en un bloque propio.

## Preparación (una sola vez)

1. Crear la cuenta de servicio:

    gcloud iam service-accounts create fluxtro-pagos --display-name="Fluxtro pagos" --project fluxtro-mvp

2. Activar Secret Manager:

    gcloud services enable secretmanager.googleapis.com --project fluxtro-mvp

3. Crear el secreto desde la consola: Secret Manager → Crear secreto → nombre
   `paypal-client-secret-sandbox` → pegar el Client Secret de Sandbox → Crear.
   (Para Live, otro secreto: `paypal-client-secret-live`.)

4. Permiso para leer el secreto:

    gcloud secrets add-iam-policy-binding paypal-client-secret-sandbox --member="serviceAccount:fluxtro-pagos@fluxtro-mvp.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" --project fluxtro-mvp

5. Permiso para invocar el engine:

    gcloud run services add-iam-policy-binding fluxtro-engine --region us-central1 --member="serviceAccount:fluxtro-pagos@fluxtro-mvp.iam.gserviceaccount.com" --role="roles/run.invoker" --project fluxtro-mvp

   Para revertirlo: el mismo comando con `remove-iam-policy-binding`.

6. Compartir el Sheet de leads con `fluxtro-pagos@fluxtro-mvp.iam.gserviceaccount.com` como
   Editor, y escribir en la fila 1 los encabezados V a Z: `ID_Pedido`, `Estado_Pago`,
   `Producto`, `Monto`, `Captura_ID`.

7. Obtener el ID del Sheet que usa el engine:

    gcloud run services describe fluxtro-engine --region us-central1 --format="value(spec.template.spec.containers[0].env)"

## Credenciales por entorno

| Entorno | App en developer.paypal.com | Secreto en Secret Manager | Webhook ID |
|---|---|---|---|
| Sandbox | `Default Application` | `paypal-client-secret-sandbox` | `7LN479651L9942313` |
| Live | `Fluxtro` | `paypal-client-secret-live` | `9HK87803SX890961V` |

Los dos webhooks apuntan a `https://fluxtro-pagos-72277594068.us-central1.run.app/webhook` con
el evento `Payment capture completed`. Con el servicio en Live, los eventos de Sandbox fallan la
verificación de firma (400) y no se procesan.

Cada secreto necesita su propio permiso de lectura para la cuenta de servicio (comando del
paso 4 de la preparación, cambiando el nombre del secreto).

Para pasar de un entorno a otro, en un solo comando:

    gcloud run services update fluxtro-pagos --region us-central1 --project fluxtro-mvp --update-env-vars "PAYPAL_ENV=<sandbox|live>,PAYPAL_CLIENT_ID=<CLIENT_ID>,PAYPAL_WEBHOOK_ID=<WEBHOOK_ID>" --update-secrets "PAYPAL_CLIENT_SECRET=<nombre-del-secreto>:latest"

El Client ID también está en `formulario-exitoso.html` de `fluxtro-landing` (constante
`PAYPAL_CLIENT_ID`): los dos tienen que ser del mismo entorno.

## Verificar archivos antes de desplegar

    gcloud meta list-files-for-upload

Debe listar exactamente `main.py`, `requirements.txt` y `Dockerfile`.

## Deploy

    gcloud run deploy fluxtro-pagos --source . --region us-central1 --project fluxtro-mvp --service-account fluxtro-pagos@fluxtro-mvp.iam.gserviceaccount.com --allow-unauthenticated --max-instances 1 --memory 512Mi --timeout 60 --set-env-vars "PAYPAL_ENV=sandbox,PAYPAL_CLIENT_ID=<CLIENT_ID>,SPREADSHEET_ID=<SPREADSHEET_ID>,PRECIO_INFORME=3.00,PRECIO_COMBO=4.00,ALLOWED_ORIGINS=https://fluxtro.app https://www.fluxtro.app,ENGINE_URL_PROCESAR=https://fluxtro-engine-72277594068.us-central1.run.app/procesar,ENGINE_AUDIENCE=https://fluxtro-engine-72277594068.us-central1.run.app/generar-informe" --set-secrets "PAYPAL_CLIENT_SECRET=paypal-client-secret-sandbox:latest"

`--max-instances 1` es obligatorio: el lock de `procesar_captura()` solo protege dentro de una
instancia.

## Registrar el webhook

1. En developer.paypal.com (Sandbox o Live según corresponda) → app Fluxtro → Webhooks → Add
   Webhook. URL: `<URL del servicio>/webhook`. Evento: `Payment capture completed`.
2. Copiar el Webhook ID y cargarlo:

    gcloud run services update fluxtro-pagos --region us-central1 --project fluxtro-mvp --update-env-vars PAYPAL_WEBHOOK_ID=<WEBHOOK_ID>

Mientras `PAYPAL_WEBHOOK_ID` esté vacía, `/webhook` responde 503 y no procesa nada.

## Si `/procesar` responde 401

El engine rechazó el token. Cambiar `ENGINE_AUDIENCE` por el client ID que usa Apps Script
(`72277594068-1hsplv06bkeribsmb59eej58ahe741fs.apps.googleusercontent.com`) con
`gcloud run services update ... --update-env-vars ENGINE_AUDIENCE=<valor>` y repetir la prueba.
No requiere cambios en el engine.

## Cambio de precios (fin de la promo)

    gcloud run services update fluxtro-pagos --region us-central1 --project fluxtro-mvp --update-env-vars PRECIO_INFORME=<monto>,PRECIO_COMBO=<monto>

Los textos de precio de la landing se cambian aparte.

## Historial

| Fecha | Revisión | Entorno PayPal | Nota |
|---|---|---|---|
| 2026-10-07 | `fluxtro-pagos-00001-pbr` | Sandbox | Primer deploy. Commit `dfec991`, build `9d413bf2-d13d-4b31-a61a-95f506c32e15` |
| 2026-10-07 | `fluxtro-pagos-00002-8vm` | Sandbox | Webhook ID de Sandbox |
| 2026-10-07 | `fluxtro-pagos-00003-xgv` | Sandbox | Preview de la landing en `ALLOWED_ORIGINS` |
| 2026-10-09 | `fluxtro-pagos-00004-2vq` | Live | Paso a Live |
| 2026-10-09 | `fluxtro-pagos-00005-twt` | Live | Preview en `ALLOWED_ORIGINS` para el pago real de prueba |
| 2026-10-09 | `fluxtro-pagos-00006-wff` | Live | `ALLOWED_ORIGINS` solo producción |
