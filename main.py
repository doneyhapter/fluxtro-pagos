"""fluxtro-pagos: servicio de pagos con PayPal Checkout (Cloud Run, Flask)."""

import logging
import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import google.auth
import gspread
import requests
from flask import Flask, jsonify, request
from google.auth.transport.requests import Request
from google.oauth2 import id_token

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("fluxtro-pagos")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

HOJA = "Leads"
HOJA_CUPONES = "Cupones"
SCOPE_SHEETS = "https://www.googleapis.com/auth/spreadsheets"
TIMEOUT_PAYPAL = 15

URLS_PAYPAL = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}

OBLIGATORIAS = (
    "PAYPAL_ENV",
    "PAYPAL_CLIENT_ID",
    "PAYPAL_CLIENT_SECRET",
    "SPREADSHEET_ID",
    "PRECIO_INFORME",
    "PRECIO_COMBO",
    "PRECIO_TU2027",
    "PRECIO_CARTA_TU2027",
    "ALLOWED_ORIGINS",
    "ENGINE_URL_PROCESAR",
    "ENGINE_AUDIENCE",
)

# Columnas del Sheet (índice base 1)
COL_ESTADO_FASE1 = 14  # N
COL_ESTADO_AUDIO = 18  # R
COL_ID_PEDIDO = 22  # V
COL_ESTADO_TU2027 = 27  # AA
COL_CUPON = 30  # AD

# Pestaña Cupones: A Codigo · B Grupo · C Descuento · D Producto · E Tope · F Usos · G Vence · H Activo
COL_CUPON_USOS = 6  # F
PISO_CON_CUPON = Decimal("3.00")  # precio mínimo después del descuento (USD)
ZONA_CUPONES = timezone(timedelta(hours=-3))  # Montevideo, sin horario de verano
MOTIVOS_CUPON = ("invalido", "vencido", "agotado", "no_aplica", "no_disponible")

RE_UUID4 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
RE_ORDER_ID = re.compile(r"[A-Za-z0-9]{1,64}")
RE_CUPON = re.compile(r"[a-z0-9_-]{3,30}")

# "pendientes": columnas que, además de N, pasan a `Pendiente` al liberar la fila.
# R dispara el audio de la carta natal y AA el informe "Tu 2027" (los lee el engine).
PRODUCTOS = {
    "informe": {
        "nombre": "Informe",
        "descripcion": "Informe Fluxtro",
        "precio": "PRECIO_INFORME",
        "pendientes": (),
    },
    "combo": {
        "nombre": "Combo",
        "descripcion": "Informe Fluxtro + Audio",
        "precio": "PRECIO_COMBO",
        "pendientes": ("R",),
    },
    "tu2027": {
        "nombre": "Tu 2027",
        "descripcion": "Tu 2027",
        "precio": "PRECIO_TU2027",
        "pendientes": ("AA",),
    },
    "carta_tu2027": {
        "nombre": "Carta + Tu 2027",
        "descripcion": "Informe Fluxtro + Tu 2027",
        "precio": "PRECIO_CARTA_TU2027",
        "pendientes": ("AA",),
    },
}


def _cfg(nombre):
    """Lee una variable de entorno (vacía si no existe)."""
    return os.environ.get(nombre, "").strip()


def _validar_arranque():
    faltan = [v for v in OBLIGATORIAS if not _cfg(v)]
    if faltan:
        raise RuntimeError("Faltan variables de entorno: " + ", ".join(faltan))
    if _cfg("PAYPAL_ENV") not in URLS_PAYPAL:
        raise RuntimeError("PAYPAL_ENV debe ser 'sandbox' o 'live'")


_validar_arranque()
PAYPAL_BASE = URLS_PAYPAL[_cfg("PAYPAL_ENV")]

app = Flask(__name__)

# ---------------------------------------------------------------------------
# CORS (solo /crear-orden y /capturar-orden)
# ---------------------------------------------------------------------------

RUTAS_CORS = ("/crear-orden", "/capturar-orden", "/validar-cupon")


@app.after_request
def agregar_cors(resp):
    if request.path in RUTAS_CORS:
        origen = request.headers.get("Origin", "")
        if origen and origen in _cfg("ALLOWED_ORIGINS").split():
            resp.headers["Access-Control-Allow-Origin"] = origen
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Vary"] = "Origin"
    return resp


# ---------------------------------------------------------------------------
# PayPal
# ---------------------------------------------------------------------------

_token_lock = threading.Lock()
_token = {"valor": None, "vence": 0.0}


def _token_paypal():
    """Token OAuth en memoria; se renueva 60 s antes de vencer."""
    with _token_lock:
        if _token["valor"] and time.time() < _token["vence"] - 60:
            return _token["valor"]
        r = requests.post(
            PAYPAL_BASE + "/v1/oauth2/token",
            auth=(_cfg("PAYPAL_CLIENT_ID"), _cfg("PAYPAL_CLIENT_SECRET")),
            data={"grant_type": "client_credentials"},
            timeout=TIMEOUT_PAYPAL,
        )
        r.raise_for_status()
        datos = r.json()
        _token["valor"] = datos["access_token"]
        _token["vence"] = time.time() + int(datos.get("expires_in", 0))
        return _token["valor"]


def _paypal(metodo, ruta, **kwargs):
    """Llama a la API de PayPal con el token vigente."""
    headers = dict(kwargs.pop("headers", {}))
    headers["Authorization"] = "Bearer " + _token_paypal()
    return requests.request(
        metodo, PAYPAL_BASE + ruta, headers=headers, timeout=TIMEOUT_PAYPAL, **kwargs
    )


# ---------------------------------------------------------------------------
# Sheet
# ---------------------------------------------------------------------------

_hoja_cache = {"ws": None}


def _hoja():
    """Devuelve la hoja Leads usando las credenciales por defecto."""
    if _hoja_cache["ws"] is None:
        creds, _ = google.auth.default(scopes=[SCOPE_SHEETS])
        cliente = gspread.authorize(creds)
        _hoja_cache["ws"] = cliente.open_by_key(_cfg("SPREADSHEET_ID")).worksheet(HOJA)
    return _hoja_cache["ws"]


def _celdas(rango, n):
    """Normaliza un rango leído a una lista de n textos."""
    valores = list(rango[0]) if rango and rango[0] else []
    valores += [""] * (n - len(valores))
    return [str(v) for v in valores[:n]]


def _leer_fila(ws, fila):
    """Lee N, R y W:Z de la fila. Devuelve (N, R, W, X, Y, Z)."""
    rangos = ws.batch_get([f"N{fila}", f"R{fila}", f"W{fila}:Z{fila}"])
    n = _celdas(rangos[0], 1)[0]
    r = _celdas(rangos[1], 1)[0]
    w, x, y, z = _celdas(rangos[2], 4)
    return n, r, w, x, y, z


def _escribir(ws, cambios):
    """Escribe en una sola actualización una lista de (rango, valores)."""
    data = [{"range": rango, "values": [valores]} for rango, valores in cambios]
    ws.batch_update(data, value_input_option="RAW")


# ---------------------------------------------------------------------------
# Cupones (pestaña Cupones del mismo Sheet)
# ---------------------------------------------------------------------------

_cupones_cache = {"ws": None}


class CuponRechazado(Exception):
    """El cupón no se puede usar. `motivo` es uno de MOTIVOS_CUPON."""

    def __init__(self, motivo):
        super().__init__(motivo)
        self.motivo = motivo


def _hoja_cupones():
    """Devuelve la pestaña Cupones (misma planilla que Leads)."""
    if _cupones_cache["ws"] is None:
        _cupones_cache["ws"] = _hoja().spreadsheet.worksheet(HOJA_CUPONES)
    return _cupones_cache["ws"]


def _normalizar(texto):
    """Minúsculas, sin tildes y sin espacios: 'Amigos del Colegio' -> 'amigosdelcolegio'."""
    if isinstance(texto, bool):
        texto = "true" if texto else "false"
    if not isinstance(texto, str):
        texto = "" if texto is None else str(texto)
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", "", sin_tildes).lower()


def _a_decimal(valor):
    if isinstance(valor, bool):
        raise InvalidOperation
    return Decimal(str(valor).strip().replace(",", "."))


def _a_entero(valor):
    if isinstance(valor, bool):
        raise ValueError
    n = _a_decimal(valor)
    if n != n.to_integral_value():
        raise ValueError
    return int(n)


def _a_fecha(valor):
    """Fecha de la celda Vence: número de serie de Sheets, AAAA-MM-DD o DD/MM/AAAA."""
    if isinstance(valor, bool):
        raise ValueError
    if isinstance(valor, (int, float)):
        return date(1899, 12, 30) + timedelta(days=int(valor))
    texto = str(valor).strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError(texto)


def _buscar_cupon(codigo):
    """Primera fila de Cupones cuyo código normalizado es `codigo`. Devuelve (n_fila, celdas) o None."""
    from gspread.utils import ValueRenderOption

    filas = _hoja_cupones().get("A2:H", value_render_option=ValueRenderOption.unformatted)
    for i, f in enumerate(filas, start=2):
        celdas = list(f) + [""] * (8 - len(f))
        if _normalizar(celdas[0]) == codigo:
            return i, celdas
    return None


def _evaluar_cupon(codigo, producto, estricto=True):
    """Devuelve (descuento, total, n_fila, usos) o lanza CuponRechazado.

    `codigo` llega normalizado. Con estricto=False (captura de un pago ya aprobado) no se
    revisan Activo, Vence ni Tope: el descuento se le respeta a quien ya lo vio.
    """
    if not RE_CUPON.fullmatch(codigo or ""):
        raise CuponRechazado("invalido")
    hallado = _buscar_cupon(codigo)
    if hallado is None:
        raise CuponRechazado("invalido")
    n_fila, (_, _, c_desc, c_prod, c_tope, c_usos, c_vence, c_activo) = hallado

    try:
        descuento = _a_decimal(c_desc)
        tope = _a_entero(c_tope)
        usos = _a_entero(c_usos) if str(c_usos).strip() else 0
        vence = _a_fecha(c_vence) if str(c_vence).strip() else None
    except (InvalidOperation, ValueError):
        log.error("cupón %s: fila %s de Cupones con datos inválidos", codigo, n_fila)
        raise CuponRechazado("invalido")
    if descuento <= 0 or tope < 1:
        log.error("cupón %s: descuento o tope inválidos en la fila %s", codigo, n_fila)
        raise CuponRechazado("invalido")

    aplica = [_normalizar(p) for p in str(c_prod).split(",") if p.strip()]
    if "todos" not in aplica and _normalizar(PRODUCTOS[producto]["nombre"]) not in aplica:
        raise CuponRechazado("no_aplica")

    total = Decimal(_precio(producto)) - descuento
    if total < PISO_CON_CUPON:
        log.error("cupón %s: deja %s en %s, por debajo del piso", codigo, producto, total)
        raise CuponRechazado("invalido")

    if estricto:
        if _normalizar(c_activo) not in ("si", "true", "verdadero"):
            raise CuponRechazado("invalido")
        if vence is not None and datetime.now(ZONA_CUPONES).date() > vence:
            raise CuponRechazado("vencido")
        if usos >= tope:
            raise CuponRechazado("agotado")

    return descuento, total, n_fila, usos


def _monto(valor):
    return f"{valor:.2f}"


# ---------------------------------------------------------------------------
# Procesamiento de capturas
# ---------------------------------------------------------------------------

_lock_captura = threading.Lock()


def _precio(producto):
    return _cfg(PRODUCTOS[producto]["precio"])


def _monto_valido(currency, value, esperado):
    if currency != "USD":
        return False
    try:
        return Decimal(str(value)) == Decimal(str(esperado))
    except InvalidOperation:
        return False


def _procesar_con_lock(capture_id, status, value, currency, custom_id):
    """Aplica las reglas del §5. Devuelve True si hay que disparar /procesar."""
    if status != "COMPLETED":
        log.info("captura %s sin procesar: estado %s (regla 1)", capture_id, status)
        return False

    partes = (custom_id or "").split("|")
    if (
        len(partes) not in (2, 3)
        or not RE_UUID4.fullmatch(partes[0])
        or partes[1] not in PRODUCTOS
        or (len(partes) == 3 and not RE_CUPON.fullmatch(partes[2]))
    ):
        log.error("captura %s: custom_id inválido %r (regla 2)", capture_id, custom_id)
        return False
    id_pedido, producto = partes[0], partes[1]
    cupon = partes[2] if len(partes) == 3 else ""

    ws = _hoja()
    ids = ws.col_values(COL_ID_PEDIDO)
    fila = next((i + 1 for i, v in enumerate(ids) if v == id_pedido), None)
    if fila is None:
        log.error(
            "captura %s, pedido %s: fila inexistente, monto %s %s (regla 3)",
            capture_id, id_pedido, value, currency,
        )
        return False

    n, r, w, x, y, z = _leer_fila(ws, fila)
    previas = [c for c in z.split(", ") if c]

    if capture_id in previas:
        log.info("captura %s, pedido %s: captura ya registrada (regla 5)", capture_id, id_pedido)
        return False

    if previas:
        _escribir(ws, [
            (f"W{fila}", ["Duplicado"]),
            (f"Z{fila}", [z + ", " + capture_id]),
        ])
        log.warning("captura %s, pedido %s: pago duplicado (regla 6)", capture_id, id_pedido)
        return False

    esperado = Decimal(_precio(producto))
    fila_cupon = usos_cupon = None
    if cupon:
        try:
            _, esperado, fila_cupon, usos_cupon = _evaluar_cupon(cupon, producto, estricto=False)
        except Exception as e:  # sin cupón legible se espera el precio completo
            log.error(
                "captura %s, pedido %s: no se pudo leer el cupón %s: %s",
                capture_id, id_pedido, cupon, e,
            )

    estado = "Pagado" if _monto_valido(currency, value, esperado) else "Monto inválido"
    cambios = [
        (f"W{fila}:Z{fila}", [estado, PRODUCTOS[producto]["nombre"], str(value), capture_id]),
    ]
    if cupon:
        cambios.append((f"AD{fila}", [cupon]))
    _escribir(ws, cambios)

    if estado == "Pagado" and fila_cupon is not None:
        try:
            _hoja_cupones().update_cell(fila_cupon, COL_CUPON_USOS, usos_cupon + 1)
        except Exception as e:  # el pago ya está registrado: el conteo se corrige a mano
            log.error("pedido %s: no se pudo sumar el uso del cupón %s: %s", id_pedido, cupon, e)

    if estado == "Monto inválido":
        log.error(
            "captura %s, pedido %s: monto inválido %s %s (regla 8)",
            capture_id, id_pedido, value, currency,
        )
        return False

    if n != "Retenido":
        log.warning(
            "captura %s, pedido %s: fila en estado %r, no se libera (regla 9)",
            capture_id, id_pedido, n,
        )
        return False

    cambios = [(f"N{fila}", ["Pendiente"])]
    for col in PRODUCTOS[producto]["pendientes"]:
        cambios.append((f"{col}{fila}", ["Pendiente"]))
    _escribir(ws, cambios)
    log.info("captura %s, pedido %s: fila %s liberada (regla 9)", capture_id, id_pedido, fila)
    return True


def procesar_captura(capture_id, status, value, currency, custom_id):
    """Registra el pago en el Sheet y libera la fila. Corre bajo lock del proceso."""
    with _lock_captura:
        disparar = _procesar_con_lock(capture_id, status, value, currency, custom_id)
    if disparar:
        disparar_procesar()


def disparar_procesar():
    """Llama a /procesar del engine con un ID token."""
    try:
        token = id_token.fetch_id_token(Request(), _cfg("ENGINE_AUDIENCE"))
        r = requests.post(
            _cfg("ENGINE_URL_PROCESAR"),
            json={},
            headers={"Authorization": "Bearer " + token},
            timeout=5,
        )
        if r.status_code != 200:
            log.error("/procesar respondió HTTP %s: %s", r.status_code, r.text[:300])
    except requests.exceptions.ReadTimeout:
        log.info("/procesar sigue en curso (timeout de lectura esperado)")
    except Exception as e:  # la fila queda Pendiente para la próxima llamada
        log.error("no se pudo llamar a /procesar: %s", e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _json_dict():
    cuerpo = request.get_json(silent=True)
    return cuerpo if isinstance(cuerpo, dict) else {}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/crear-orden", methods=["POST", "OPTIONS"])
def crear_orden():
    if request.method == "OPTIONS":
        return "", 204
    cuerpo = _json_dict()
    id_pedido = cuerpo.get("id_pedido")
    producto = cuerpo.get("producto")
    if (
        not isinstance(id_pedido, str)
        or not RE_UUID4.fullmatch(id_pedido)
        or not isinstance(producto, str)
        or producto not in PRODUCTOS
    ):
        return jsonify({"error": "datos inválidos"}), 400

    custom_id = f"{id_pedido}|{producto}"
    descripcion = PRODUCTOS[producto]["descripcion"]
    monto = _precio(producto)
    cupon = cuerpo.get("cupon")
    if cupon not in (None, ""):
        codigo = _normalizar(cupon) if isinstance(cupon, str) else ""
        try:
            _, total, _, _ = _evaluar_cupon(codigo, producto)
        except CuponRechazado as e:
            return jsonify({"error": "cupon", "motivo": e.motivo}), 409
        except Exception as e:
            log.error("no se pudo validar el cupón del pedido %s: %s", id_pedido, e)
            return jsonify({"error": "cupon", "motivo": "no_disponible"}), 409
        custom_id += f"|{codigo}"
        descripcion += f" - descuento {codigo}"
        monto = _monto(total)

    orden = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "custom_id": custom_id,
            "description": descripcion,
            "amount": {"currency_code": "USD", "value": monto},
        }],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "brand_name": "Fluxtro",
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                }
            }
        },
    }
    try:
        r = _paypal("POST", "/v2/checkout/orders", json=orden)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
        return jsonify({"id": r.json()["id"]}), 200
    except Exception as e:
        log.error("no se pudo crear la orden del pedido %s: %s", id_pedido, e)
        return jsonify({"error": "no se pudo crear la orden"}), 502


@app.route("/validar-cupon", methods=["POST", "OPTIONS"])
def validar_cupon():
    """Dice si un cupón sirve para un producto y cuánto queda por pagar. No reserva nada."""
    if request.method == "OPTIONS":
        return "", 204
    cuerpo = _json_dict()
    producto = cuerpo.get("producto")
    cupon = cuerpo.get("cupon")
    if not isinstance(producto, str) or producto not in PRODUCTOS or not isinstance(cupon, str):
        return jsonify({"error": "datos inválidos"}), 400

    codigo = _normalizar(cupon)
    try:
        descuento, total, _, _ = _evaluar_cupon(codigo, producto)
    except CuponRechazado as e:
        return jsonify({"valido": False, "motivo": e.motivo}), 200
    except Exception as e:
        log.error("no se pudo validar el cupón: %s", e)
        return jsonify({"valido": False, "motivo": "no_disponible"}), 200
    return jsonify({
        "valido": True,
        "cupon": codigo,
        "precio": _precio(producto),
        "descuento": _monto(descuento),
        "total": _monto(total),
    }), 200


@app.route("/capturar-orden", methods=["POST", "OPTIONS"])
def capturar_orden():
    if request.method == "OPTIONS":
        return "", 204
    order_id = _json_dict().get("order_id")
    if not isinstance(order_id, str) or not RE_ORDER_ID.fullmatch(order_id):
        return jsonify({"error": "datos inválidos"}), 400

    try:
        r = _paypal(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            headers={
                "PayPal-Request-Id": f"captura-{order_id}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={},
        )
        if r.status_code == 422 and "ORDER_ALREADY_CAPTURED" in r.text:
            log.info("orden %s ya capturada, se consulta la existente", order_id)
            r = _paypal(
                "GET",
                f"/v2/checkout/orders/{order_id}",
                headers={"Prefer": "return=representation"},
            )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
        captura = r.json()["purchase_units"][0]["payments"]["captures"][0]
        estado = captura["status"]
        capture_id = captura["id"]
        value = captura["amount"]["value"]
        currency = captura["amount"]["currency_code"]
        custom_id = captura.get("custom_id") or r.json()["purchase_units"][0].get("custom_id")
    except Exception as e:
        log.error("no se pudo capturar la orden %s: %s", order_id, e)
        return jsonify({"error": "no se pudo capturar la orden"}), 502

    try:
        procesar_captura(capture_id, estado, value, currency, custom_id)
    except Exception as e:  # el comprador ya pagó: se concilia a mano
        log.error("error al registrar la captura %s en el Sheet: %s", capture_id, e)

    if estado in ("COMPLETED", "PENDING"):
        return jsonify({"status": estado}), 200
    return jsonify({"status": estado}), 402


@app.route("/webhook", methods=["POST"])
def webhook():
    webhook_id = _cfg("PAYPAL_WEBHOOK_ID")
    if not webhook_id:
        log.warning("webhook recibido sin PAYPAL_WEBHOOK_ID configurado")
        return jsonify({"error": "webhook no configurado"}), 503

    evento = request.get_json(silent=True)
    if not isinstance(evento, dict):
        return jsonify({"error": "datos inválidos"}), 400

    try:
        h = request.headers
        r = _paypal("POST", "/v1/notifications/verify-webhook-signature", json={
            "auth_algo": h.get("PAYPAL-AUTH-ALGO", ""),
            "cert_url": h.get("PAYPAL-CERT-URL", ""),
            "transmission_id": h.get("PAYPAL-TRANSMISSION-ID", ""),
            "transmission_sig": h.get("PAYPAL-TRANSMISSION-SIG", ""),
            "transmission_time": h.get("PAYPAL-TRANSMISSION-TIME", ""),
            "webhook_id": webhook_id,
            "webhook_event": evento,
        })
        verificado = r.status_code == 200 and r.json().get("verification_status") == "SUCCESS"
    except Exception as e:
        log.error("falló la verificación de firma del webhook: %s", e)
        verificado = False
    if not verificado:
        log.warning("webhook con firma no verificada")
        return jsonify({"error": "firma inválida"}), 400

    if evento.get("event_type") != "PAYMENT.CAPTURE.COMPLETED":
        return jsonify({"status": "ignorado"}), 200

    try:
        recurso = evento.get("resource") or {}
        capture_id = recurso.get("id")
        custom_id = recurso.get("custom_id")
        if not custom_id:
            rc = _paypal("GET", f"/v2/payments/captures/{capture_id}")
            rc.raise_for_status()
            custom_id = rc.json().get("custom_id")
        amount = recurso.get("amount") or {}
        procesar_captura(
            capture_id, recurso.get("status"), amount.get("value"),
            amount.get("currency_code"), custom_id,
        )
    except Exception as e:  # con firma válida siempre 200 para que PayPal no reintente
        log.error("error al procesar el webhook: %s", e)
    return jsonify({"status": "ok"}), 200
