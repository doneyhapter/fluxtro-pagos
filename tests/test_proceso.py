"""Pruebas de procesar_captura() con una hoja falsa en memoria (sin red)."""

import os
import re
import sys

import pytest

# Variables mínimas para poder importar main (valores de prueba, no reales)
for _k, _v in {
    "PAYPAL_ENV": "sandbox",
    "PAYPAL_CLIENT_ID": "id-de-prueba",
    "PAYPAL_CLIENT_SECRET": "secreto-de-prueba",
    "SPREADSHEET_ID": "hoja-de-prueba",
    "PRECIO_INFORME": "3.00",
    "PRECIO_COMBO": "4.00",
    "ALLOWED_ORIGINS": "https://ejemplo.test",
    "ENGINE_URL_PROCESAR": "https://engine.test/procesar",
    "ENGINE_AUDIENCE": "https://engine.test/audiencia",
}.items():
    os.environ[_k] = _v

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: E402

PEDIDO = "3f2b1c9a-8d4e-4a6b-9c1d-2e3f4a5b6c7d"
OTRO = "11111111-2222-4333-8444-555555555555"


def _col(letra):
    n = 0
    for c in letra:
        n = n * 26 + ord(c) - 64
    return n


class HojaFalsa:
    """Imita lo mínimo de gspread.Worksheet que usa main.py."""

    def __init__(self, filas):
        self.filas = [list(f) + [""] * (26 - len(f)) for f in filas]
        self.escrituras = []  # (fila, columna) de cada celda escrita

    def col_values(self, col):
        return [f[col - 1] for f in self.filas]

    def _ref(self, a1):
        m = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", a1)
        c1, f1, c2, f2 = m.groups()
        return _col(c1), int(f1), _col(c2 or c1), int(f2 or f1)

    def batch_get(self, rangos):
        salida = []
        for a1 in rangos:
            c1, f1, c2, _ = self._ref(a1)
            vals = self.filas[f1 - 1][c1 - 1:c2]
            while vals and vals[-1] == "":
                vals.pop()
            salida.append([vals] if vals else [])
        return salida

    def batch_update(self, data, value_input_option=None):
        for item in data:
            c1, f1, _, _ = self._ref(item["range"])
            for i, v in enumerate(item["values"][0]):
                self.filas[f1 - 1][c1 - 1 + i] = v
                self.escrituras.append((f1, c1 + i))


def fila(id_pedido=PEDIDO, n="Retenido", r="", w="", x="", y="", z=""):
    f = [""] * 26
    f[13], f[17], f[21], f[22], f[23], f[24], f[25] = n, r, id_pedido, w, x, y, z
    return f


@pytest.fixture
def entorno(monkeypatch):
    disparos = []
    hoja = HojaFalsa([[""] * 26, fila()])  # fila 1 = encabezados, fila 2 = pedido
    monkeypatch.setattr(main, "_hoja", lambda: hoja)
    monkeypatch.setattr(main, "disparar_procesar", lambda: disparos.append(1))
    return hoja, disparos


def valores(hoja, fila_n=2):
    f = hoja.filas[fila_n - 1]
    return {
        "N": f[13], "R": f[17], "W": f[22], "X": f[23], "Y": f[24], "Z": f[25],
    }


def test_1_informe_completo_libera_la_fila(entorno):
    hoja, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "3.00", "USD", f"{PEDIDO}|informe")
    v = valores(hoja)
    assert (v["W"], v["X"], v["Y"], v["Z"]) == ("Pagado", "Informe", "3.00", "CAP1")
    assert v["N"] == "Pendiente"
    assert v["R"] == ""
    assert len(disparos) == 1


def test_2_combo_libera_tambien_el_audio(entorno):
    hoja, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "4.00", "USD", f"{PEDIDO}|combo")
    v = valores(hoja)
    assert (v["W"], v["X"], v["Y"], v["Z"]) == ("Pagado", "Combo", "4.00", "CAP1")
    assert v["N"] == "Pendiente"
    assert v["R"] == "Pendiente"
    assert len(disparos) == 1


def test_3_misma_captura_dos_veces_no_cambia_nada(entorno):
    hoja, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "3.00", "USD", f"{PEDIDO}|informe")
    antes = [list(f) for f in hoja.filas]
    escrituras = len(hoja.escrituras)
    main.procesar_captura("CAP1", "COMPLETED", "3.00", "USD", f"{PEDIDO}|informe")
    assert hoja.filas == antes
    assert len(hoja.escrituras) == escrituras
    assert len(disparos) == 1


def test_4_segunda_captura_distinta_es_duplicado(entorno):
    hoja, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "3.00", "USD", f"{PEDIDO}|informe")
    main.procesar_captura("CAP2", "COMPLETED", "3.00", "USD", f"{PEDIDO}|informe")
    v = valores(hoja)
    assert v["W"] == "Duplicado"
    assert v["Z"] == "CAP1, CAP2"
    assert len(disparos) == 1  # solo el de la primera captura


def test_5_monto_distinto_es_monto_invalido(entorno):
    hoja, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "1.00", "USD", f"{PEDIDO}|informe")
    v = valores(hoja)
    assert v["W"] == "Monto inválido"
    assert v["Z"] == "CAP1"
    assert v["N"] == "Retenido"
    assert disparos == []


def test_6_fila_ya_liberada_no_se_toca(entorno):
    hoja, disparos = entorno
    hoja.filas[1][13] = "Procesado"
    hoja.filas[1][17] = "Listo"
    main.procesar_captura("CAP1", "COMPLETED", "4.00", "USD", f"{PEDIDO}|combo")
    v = valores(hoja)
    assert (v["W"], v["X"], v["Y"], v["Z"]) == ("Pagado", "Combo", "4.00", "CAP1")
    assert v["N"] == "Procesado"
    assert v["R"] == "Listo"
    assert disparos == []


def test_7_id_pedido_inexistente_no_escribe(entorno):
    hoja, disparos = entorno
    main.procesar_captura("CAP1", "COMPLETED", "3.00", "USD", f"{OTRO}|informe")
    assert hoja.escrituras == []
    assert disparos == []


def test_8_estado_pending_no_escribe(entorno):
    hoja, disparos = entorno
    main.procesar_captura("CAP1", "PENDING", "3.00", "USD", f"{PEDIDO}|informe")
    assert hoja.escrituras == []
    assert disparos == []

