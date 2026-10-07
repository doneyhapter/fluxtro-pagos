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
