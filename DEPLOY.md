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

> **Primer deploy vs. redeploy.** El comando de abajo es el del primer deploy: fija `PAYPAL_ENV=sandbox` y reemplaza toda la configuración. **No usarlo para redeployar código.** Para un cambio de código o dependencias, desplegar sin `--set-env-vars` ni `--set-secrets` (la revisión hereda la configuración vigente), con `--no-traffic --tag <tag>`. Después, verificar con `gcloud run revisions describe <revision> --format="yaml(spec.containers[0].env)"` que sigue en Live, probar `/health` en la URL del tag y mover el tráfico con `gcloud run services update-traffic fluxtro-pagos --region us-central1 --project fluxtro-mvp --to-latest --remove-tags <tag>`. **No usar `--to-revisions`:** fija el tráfico en esa revisión y los cambios de configuración posteriores (precios, orígenes, secretos) crean revisiones que quedan sin tráfico. Pasó en v1.1 y se corrigió el 2026-11-01.

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

## Cambio de precios

    gcloud run services update fluxtro-pagos --region us-central1 --project fluxtro-mvp --update-env-vars PRECIO_INFORME=<monto>,PRECIO_COMBO=<monto>

Después, confirmar que la revisión nueva recibe el tráfico:

    gcloud run services describe fluxtro-pagos --region us-central1 --project fluxtro-mvp --format="yaml(status.traffic)"

Tiene que aparecer una sola entrada con `latestRevision: true` y `percent: 100`. Si la revisión nueva
aparece con 0%, el tráfico está fijado: correr `gcloud run services update-traffic fluxtro-pagos --region us-central1 --project fluxtro-mvp --to-latest`.

Los precios se cambian en tres lugares a la vez: estas variables, los textos de la landing
(index.html y formulario-exitoso.html) y los Payment Links de respaldo en PayPal.

## Historial

| Fecha | Revisión | Entorno PayPal | Nota |
|---|---|---|---|
| 2026-10-07 | `fluxtro-pagos-00001-pbr` | Sandbox | Primer deploy. Commit `dfec991`, build `9d413bf2-d13d-4b31-a61a-95f506c32e15` |
| 2026-10-07 | `fluxtro-pagos-00002-8vm` | Sandbox | Webhook ID de Sandbox |
| 2026-10-07 | `fluxtro-pagos-00003-xgv` | Sandbox | Preview de la landing en `ALLOWED_ORIGINS` |
| 2026-10-09 | `fluxtro-pagos-00004-2vq` | Live | Paso a Live |
| 2026-10-09 | `fluxtro-pagos-00005-twt` | Live | Preview en `ALLOWED_ORIGINS` para el pago real de prueba |
| 2026-10-09 | `fluxtro-pagos-00006-wff` | Live | `ALLOWED_ORIGINS` solo producción |
| 2026-10-29 | `fluxtro-pagos-00007-yiy` | Live | v1.1: flask 3.1.3 y requests 2.33.0. Commit `4606068`, build `04dfe145-4bcf-49e9-95bb-42467a0f7251`, tag `v11` |
| 2026-11-01 | `fluxtro-pagos-00008-vfw` | Live | `PRECIO_INFORME=10.00`, `PRECIO_COMBO=12.00` (fin de la promoción). Tráfico pasado a LATEST y tag `v11` quitado de `00007-yiy` |
