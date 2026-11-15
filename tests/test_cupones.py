"""Pruebas de cupones: validación, creación de la orden y captura (sin red)."""

from datetime import date, datetime, timedelta

import pytest

import test_proceso as base  # fija las variables de entorno e importa main
from test_proceso import PEDIDO, HojaFalsa, fila

main = base.main

HOY = datetime.now(main.ZONA_CUPONES).date()
SERIE_BASE = date(1899, 12, 30)


def serie(d):
    """Número de serie de Sheets para una fecha (así llega con UNFORMATTED_VALUE)."""
    return (d - SERIE_BASE).days


class HojaCupones:
    """Imita lo mínimo de la pestaña Cupones: get() desde A2 y update_cell()."""

    def __init__(self, filas):
        self.filas = [list(f) for f in filas]
        self.escrituras = []
        self.lecturas = 0
        self.falla = None

    def get(self, rango, value_render_option=None):
        assert rango == "A2:H"
        assert str(getattr(value_render_option, "value", value_render_option)) == "UNFORMATTED_VALUE"
        self.lecturas += 1
        if self.falla:
            raise self.falla
        return [list(f) for f in self.filas]

    def update_cell(self, n_fila, col, valor):
        if self.falla:
            raise self.falla
        f = self.filas[n_fila - 2]
        f += [""] * (col - len(f))
        f[col - 1] = valor
        self.escrituras.append((n_fila, col, valor))


def cupon(codigo="amigosdelcolegio", grupo="Colegio", descuento=3, producto="Todos",
          tope=38, usos="", vence="", activo="Sí"):
    return [codigo, grupo, descuento, producto, tope, usos, vence, activo]


@pytest.fixture
def entorno(monkeypatch):
    disparos = []
    leads = HojaFalsa([[""] * 30, fila()])
    cupones = HojaCupones([cupon()])
    monkeypatch.setattr(main, "_hoja", lambda: leads)
    monkeypatch.setattr(main, "_hoja_cupones", lambda: cupones)
    monkeypatch.setattr(main, "disparar_procesar", lambda: disparos.append(1))
    monkeypatch.setenv("PRECIO_INFORME", "10.00")
    monkeypatch.setenv("PRECIO_COMBO", "12.00")
    return leads, cupones, disparos


def celda(leads, letra, n_fila=2):
    return leads.filas[n_fila - 1][base._col(letra) - 1]


def validar(codigo, producto="informe"):
    return main.app.test_client().post("/validar-cupon", json={"cupon": codigo, "producto": producto})


class _Resp:
    status_code = 201
    text = ""

    def json(self):
        return {"id": "ORDEN1"}


def crear(monkeypatch, cuerpo):
    enviados = []

    def paypal_falso(metodo, ruta, **kwargs):
        enviados.append(kwargs["json"])
        return _Resp()

    monkeypatch.setattr(main, "_paypal", paypal_falso)
    r = main.app.test_client().post("/crear-orden", json=cuerpo)
    return r, enviados


# --- normalización -----------------------------------------------------------

@pytest.mark.parametrize("escrito", [
    "amigosdelcolegio", "AmigosDelColegio", " Amigos del Colegio ", "AMIGOS DEL COLEGIO",
    "amigós del colegio",
])
def test_c1_el_codigo_no_distingue_mayusculas_tildes_ni_espacios(entorno, escrito):
    r = validar(escrito)
    assert r.get_json() == {
        "valido": True, "cupon": "amigosdelcolegio",
        "precio": "10.00", "descuento": "3.00", "total": "7.00",
    }


def test_c2_el_codigo_del_sheet_tambien_se_normaliza(entorno):
    _, cupones, _ = entorno
    cupones.filas = [cupon(codigo="Amigos del Colegio")]
    assert validar("amigosdelcolegio").get_json()["valido"] is True


def test_c3_con_dos_filas_iguales_gana_la_primera(entorno):
    _, cupones, _ = entorno
    cupones.filas = [cupon(descuento=3), cupon(descuento=5)]
    assert validar("amigosdelcolegio").get_json()["descuento"] == "3.00"


# --- validación --------------------------------------------------------------

@pytest.mark.parametrize("producto, total", [
    ("informe", "7.00"), ("combo", "9.00"), ("tu2027", "7.00"), ("carta_tu2027", "14.00"),
])
def test_c4_total_por_producto(entorno, producto, total):
    assert validar("amigosdelcolegio", producto).get_json()["total"] == total


@pytest.mark.parametrize("cambios, motivo", [
    ({"codigo": "otro"}, "invalido"),
    ({"activo": "No"}, "invalido"),
    ({"activo": ""}, "invalido"),
    ({"descuento": ""}, "invalido"),
    ({"descuento": 0}, "invalido"),
    ({"descuento": "tres"}, "invalido"),
    ({"tope": ""}, "invalido"),
    ({"tope": 0}, "invalido"),
    ({"vence": "pronto"}, "invalido"),
    ({"descuento": 8}, "invalido"),          # 10 - 8 = 2, por debajo del piso de 3
    ({"vence": serie(HOY - timedelta(days=1))}, "vencido"),
    ({"tope": 1, "usos": 1}, "agotado"),
    ({"tope": 38, "usos": 38}, "agotado"),
    ({"producto": "Combo"}, "no_aplica"),
    ({"producto": ""}, "no_aplica"),
])
def test_c5_motivos_de_rechazo(entorno, cambios, motivo):
    _, cupones, _ = entorno
    cupones.filas = [cupon(**cambios)]
    assert validar("amigosdelcolegio").get_json() == {"valido": False, "motivo": motivo}


@pytest.mark.parametrize("vence", [
    serie(HOY), serie(HOY + timedelta(days=10)), HOY.isoformat(), HOY.strftime("%d/%m/%Y"), "",
])
def test_c6_vale_hasta_el_final_del_dia_de_vencimiento(entorno, vence):
    _, cupones, _ = entorno
    cupones.filas = [cupon(vence=vence)]
    assert validar("amigosdelcolegio").get_json()["valido"] is True


@pytest.mark.parametrize("producto_sheet, producto, valido", [
    ("Tu 2027", "tu2027", True),
    ("tu 2027", "tu2027", True),
    ("Informe, Tu 2027", "tu2027", True),
    ("Carta + Tu 2027", "carta_tu2027", True),
    ("Tu 2027", "carta_tu2027", False),
    ("Informe", "combo", False),
    ("TODOS", "combo", True),
])
def test_c7_columna_producto(entorno, producto_sheet, producto, valido):
    _, cupones, _ = entorno
    cupones.filas = [cupon(producto=producto_sheet)]
    assert validar("amigosdelcolegio", producto).get_json()["valido"] is valido


def test_c8_activo_acepta_casilla_y_variantes(entorno):
    _, cupones, _ = entorno
    for activo in ("Sí", "si", "SI", True):
        cupones.filas = [cupon(activo=activo)]
        assert validar("amigosdelcolegio").get_json()["valido"] is True
    cupones.filas = [cupon(activo=False)]
    assert validar("amigosdelcolegio").get_json()["valido"] is False


def test_c9_descuento_con_coma_decimal(entorno):
    _, cupones, _ = entorno
    cupones.filas = [cupon(descuento="2,50")]
    assert validar("amigosdelcolegio").get_json()["total"] == "7.50"


def test_c10_si_el_sheet_falla_el_cupon_no_esta_disponible(entorno):
    _, cupones, _ = entorno
    cupones.falla = RuntimeError("sheets caído")
    assert validar("amigosdelcolegio").get_json() == {"valido": False, "motivo": "no_disponible"}


def test_c11_validar_no_escribe_ni_cuenta_usos(entorno):
    leads, cupones, _ = entorno
    validar("amigosdelcolegio")
    assert cupones.escrituras == [] and leads.escrituras == []


@pytest.mark.parametrize("cuerpo", [
    {"cupon": "amigosdelcolegio"},
    {"cupon": "amigosdelcolegio", "producto": "otro"},
    {"producto": "informe"},
    {"cupon": 5, "producto": "informe"},
])
def test_c12_validar_con_datos_invalidos_responde_400(entorno, cuerpo):
    assert main.app.test_client().post("/validar-cupon", json=cuerpo).status_code == 400


def test_c13_codigo_con_caracteres_raros_es_invalido_sin_leer_el_sheet(entorno):
    _, cupones, _ = entorno
    assert validar("a|b").get_json() == {"valido": False, "motivo": "invalido"}
    assert validar("ab").get_json()["motivo"] == "invalido"
    assert cupones.lecturas == 0


def test_c14_cors_en_validar_cupon(entorno):
    r = main.app.test_client().post(
        "/validar-cupon", json={"cupon": "x", "producto": "informe"},
        headers={"Origin": "https://ejemplo.test"})
    assert r.headers.get("Access-Control-Allow-Origin") == "https://ejemplo.test"


# --- /crear-orden ------------------------------------------------------------

def test_c15_orden_con_cupon_lleva_el_total_y_el_codigo(entorno, monkeypatch):
    r, enviados = crear(monkeypatch, {
        "id_pedido": PEDIDO, "producto": "tu2027", "cupon": " Amigos del Colegio "})
    assert r.status_code == 200
    unidad = enviados[0]["purchase_units"][0]
    assert unidad["amount"] == {"currency_code": "USD", "value": "7.00"}
    assert unidad["custom_id"] == f"{PEDIDO}|tu2027|amigosdelcolegio"
    assert unidad["description"] == "Tu 2027 - descuento amigosdelcolegio"
    assert "breakdown" not in unidad["amount"]
    assert len(unidad["custom_id"]) <= 127 and unidad["description"].isascii()


@pytest.mark.parametrize("cupon_enviado", [None, ""])
def test_c16_orden_sin_cupon_queda_como_antes(entorno, monkeypatch, cupon_enviado):
    _, cupones, _ = entorno
    cuerpo = {"id_pedido": PEDIDO, "producto": "informe"}
    if cupon_enviado is not None:
        cuerpo["cupon"] = cupon_enviado
    r, enviados = crear(monkeypatch, cuerpo)
    assert r.status_code == 200
    unidad = enviados[0]["purchase_units"][0]
    assert unidad["amount"]["value"] == "10.00"
    assert unidad["custom_id"] == f"{PEDIDO}|informe"
    assert unidad["description"] == "Informe Fluxtro"
    assert cupones.lecturas == 0


@pytest.mark.parametrize("cambios, motivo", [
    ({"activo": "No"}, "invalido"),
    ({"vence": serie(HOY - timedelta(days=1))}, "vencido"),
    ({"tope": 1, "usos": 1}, "agotado"),
    ({"producto": "Combo"}, "no_aplica"),
])
def test_c17_orden_con_cupon_rechazado_no_llega_a_paypal(entorno, monkeypatch, cambios, motivo):
    _, cupones, _ = entorno
    cupones.filas = [cupon(**cambios)]
    r, enviados = crear(monkeypatch, {
        "id_pedido": PEDIDO, "producto": "informe", "cupon": "amigosdelcolegio"})
    assert r.status_code == 409
    assert r.get_json() == {"error": "cupon", "motivo": motivo}
    assert enviados == []


def test_c18_orden_con_cupon_y_sheet_caido_no_cobra(entorno, monkeypatch):
    _, cupones, _ = entorno
    cupones.falla = RuntimeError("sheets caído")
    r, enviados = crear(monkeypatch, {
        "id_pedido": PEDIDO, "producto": "informe", "cupon": "amigosdelcolegio"})
    assert r.status_code == 409 and r.get_json()["motivo"] == "no_disponible"
    assert enviados == []


def test_c19_cupon_que_no_es_texto_se_rechaza(entorno, monkeypatch):
    r, enviados = crear(monkeypatch, {"id_pedido": PEDIDO, "producto": "informe", "cupon": 123})
    assert r.status_code == 409 and enviados == []


# --- captura -----------------------------------------------------------------

def test_c20_captura_con_cupon_libera_registra_y_cuenta(entorno):
    leads, cupones, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|tu2027|amigosdelcolegio")
    assert celda(leads, "W") == "Pagado"
    assert celda(leads, "X") == "Tu 2027"
    assert celda(leads, "Y") == "7.00"
    assert celda(leads, "AD") == "amigosdelcolegio"
    assert celda(leads, "N") == "Pendiente" and celda(leads, "AA") == "Pendiente"
    assert cupones.escrituras == [(2, 6, 1)]
    assert len(disparos) == 1


def test_c21_el_uso_se_suma_sobre_el_valor_existente(entorno):
    _, cupones, _ = entorno
    cupones.filas = [cupon(usos=4)]
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    assert cupones.escrituras == [(2, 6, 5)]


def test_c22_misma_captura_dos_veces_cuenta_un_solo_uso(entorno):
    _, cupones, disparos = entorno
    for _ in range(2):
        main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    assert cupones.escrituras == [(2, 6, 1)]
    assert len(disparos) == 1


def test_c23_pago_duplicado_no_cuenta_otro_uso(entorno):
    leads, cupones, _ = entorno
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    main.procesar_captura("CAP2", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    assert celda(leads, "W") == "Duplicado"
    assert cupones.escrituras == [(2, 6, 1)]


@pytest.mark.parametrize("cambios", [
    {"activo": "No"},
    {"vence": serie(HOY - timedelta(days=1))},
    {"tope": 1, "usos": 1},
])
def test_c24_al_capturar_se_respeta_el_descuento_ya_visto(entorno, cambios):
    leads, cupones, disparos = entorno
    cupones.filas = [cupon(**cambios)]
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    assert celda(leads, "W") == "Pagado"
    assert celda(leads, "N") == "Pendiente"
    assert len(cupones.escrituras) == 1 and len(disparos) == 1


def test_c25_monto_que_no_coincide_con_el_cupon_es_monto_invalido(entorno):
    leads, cupones, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "5.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    assert celda(leads, "W") == "Monto inválido"
    assert celda(leads, "AD") == "amigosdelcolegio"
    assert celda(leads, "N") == "Retenido"
    assert cupones.escrituras == [] and disparos == []


def test_c26_cupon_inexistente_al_capturar_es_monto_invalido(entorno):
    leads, cupones, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|borrado")
    assert celda(leads, "W") == "Monto inválido"
    assert celda(leads, "AD") == "borrado"
    assert cupones.escrituras == [] and disparos == []


def test_c27_sheet_de_cupones_caido_al_capturar_es_monto_invalido(entorno):
    leads, cupones, disparos = entorno
    cupones.falla = RuntimeError("sheets caído")
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    assert celda(leads, "W") == "Monto inválido"
    assert celda(leads, "N") == "Retenido" and disparos == []


def test_c28_si_falla_el_conteo_la_fila_igual_se_libera(entorno):
    leads, cupones, disparos = entorno

    def falla(*a):
        raise RuntimeError("sin permiso")

    cupones.update_cell = falla
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    assert celda(leads, "W") == "Pagado" and celda(leads, "N") == "Pendiente"
    assert len(disparos) == 1


def test_c29_captura_sin_cupon_no_lee_cupones_ni_escribe_ad(entorno):
    leads, cupones, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "10.00", "USD", f"{PEDIDO}|informe")
    assert celda(leads, "W") == "Pagado" and celda(leads, "AD") == ""
    assert (2, 30) not in leads.escrituras
    assert cupones.lecturas == 0 and len(disparos) == 1


@pytest.mark.parametrize("custom_id", [
    f"{PEDIDO}|informe|", f"{PEDIDO}|informe|a b", f"{PEDIDO}|informe|x|y", f"{PEDIDO}|informe|AB",
])
def test_c30_custom_id_con_cupon_mal_formado_no_escribe(entorno, custom_id):
    leads, cupones, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", custom_id)
    assert leads.escrituras == [] and cupones.escrituras == [] and disparos == []


def test_c31_fila_ya_liberada_registra_el_pago_y_cuenta_el_uso(entorno):
    leads, cupones, disparos = entorno
    leads.filas[1][13] = "Informe enviado"
    main.procesar_captura("CAP1", "COMPLETED", "7.00", "USD", f"{PEDIDO}|informe|amigosdelcolegio")
    assert celda(leads, "W") == "Pagado" and celda(leads, "N") == "Informe enviado"
    assert cupones.escrituras == [(2, 6, 1)] and disparos == []
