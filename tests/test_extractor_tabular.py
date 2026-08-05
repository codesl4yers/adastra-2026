"""Pruebas del extractor de CSV y XLSX."""

import openpyxl
import pytest

from contrato import validar_documento
from extractores import tabular

CABECERA = ["pais", "año", "lanzamientos", "observaciones"]
FILAS = [
    ["Colombia", "2024", "3", "Programa nacional en fase inicial"],
    ["Chile", "2024", "7", "Cooperación con la agencia europea"],
]


def escribir_csv(tmp_path, filas=None, cabecera=None, delimitador=",", encoding="utf-8", nombre="datos.csv"):
    lineas = [delimitador.join(cabecera if cabecera is not None else CABECERA)]
    lineas += [delimitador.join(fila) for fila in (filas if filas is not None else FILAS)]
    destino = tmp_path / nombre
    destino.write_bytes(("\n".join(lineas) + "\n").encode(encoding))
    return destino


def escribir_xlsx(tmp_path, hojas=None, nombre="datos.xlsx"):
    hojas = hojas or {"Datos": [CABECERA, *FILAS]}
    libro = openpyxl.Workbook()
    libro.remove(libro.active)
    for titulo, filas in hojas.items():
        hoja = libro.create_sheet(titulo)
        for fila in filas:
            hoja.append(fila)
    destino = tmp_path / nombre
    libro.save(destino)
    libro.close()
    return destino


# --- una fila, un bloque atómico ----------------------------------------------


def test_cada_fila_es_un_bloque_atomico(tmp_path):
    documento = tabular.extraer(escribir_csv(tmp_path), fenomeno=3)

    assert len(documento.bloques) == 2
    assert all(b.tipo == "fila" and b.atomico for b in documento.bloques)


def test_la_fila_lleva_el_nombre_de_su_columna(tmp_path):
    """Sin la columna, "Colombia | 2024 | 3" no dice qué es cada cosa."""
    documento = tabular.extraer(escribir_csv(tmp_path), fenomeno=3)

    assert documento.bloques[0].texto == (
        "pais: Colombia | año: 2024 | lanzamientos: 3 | "
        "observaciones: Programa nacional en fase inicial"
    )


def test_las_celdas_vacias_no_dejan_columnas_huerfanas(tmp_path):
    ruta = escribir_csv(tmp_path, filas=[["Perú", "", "2", ""]])
    documento = tabular.extraer(ruta, fenomeno=3)

    assert documento.bloques[0].texto == "pais: Perú | lanzamientos: 2"


def test_una_fila_entera_vacia_no_produce_bloque(tmp_path):
    ruta = escribir_csv(tmp_path, filas=[FILAS[0], ["", "", "", ""], FILAS[1]])
    assert len(tabular.extraer(ruta, fenomeno=3).bloques) == 2


def test_la_cabecera_viaja_en_meta(tmp_path):
    documento = tabular.extraer(escribir_csv(tmp_path), fenomeno=3)
    assert documento.meta["columnas"] == CABECERA


def test_el_documento_cuenta_sus_filas(tmp_path):
    assert tabular.extraer(escribir_csv(tmp_path), fenomeno=3).meta["n_filas"] == 2


# --- la trampa del CSV: delimitador y codificación ----------------------------


def test_detecta_el_punto_y_coma_de_los_csv_exportados_desde_excel(tmp_path):
    """Leerlo con coma daría una única columna con todo dentro y ningún error."""
    documento = tabular.extraer(escribir_csv(tmp_path, delimitador=";"), fenomeno=3)

    assert documento.meta["delimitador"] == ";"
    assert "pais: Colombia" in documento.bloques[0].texto


def test_detecta_el_tabulador(tmp_path):
    documento = tabular.extraer(escribir_csv(tmp_path, delimitador="\t"), fenomeno=3)
    assert "pais: Colombia" in documento.bloques[0].texto


def test_lee_un_csv_en_cp1252(tmp_path):
    ruta = escribir_csv(tmp_path, encoding="cp1252")
    documento = tabular.extraer(ruta, fenomeno=3)

    assert "Perú" in documento.bloques[0].texto or "Colombia" in documento.bloques[0].texto
    assert documento.meta["codificacion"] == "cp1252"


def test_la_codificacion_se_prueba_en_orden_fijo(tmp_path):
    """Una autodetección probabilística haría que dos corridas difieran."""
    ruta = escribir_csv(tmp_path, encoding="utf-8")
    assert tabular.extraer(ruta, fenomeno=3).meta["codificacion"] == "utf-8"


def test_un_csv_con_bom_no_ensucia_la_primera_columna(tmp_path):
    ruta = escribir_csv(tmp_path, encoding="utf-8-sig")
    documento = tabular.extraer(ruta, fenomeno=3)

    assert documento.meta["columnas"][0] == "pais"


# --- XLSX ----------------------------------------------------------------------


def test_cada_hoja_del_libro_es_una_seccion(tmp_path):
    ruta = escribir_xlsx(tmp_path, {"Resumen": [CABECERA, FILAS[0]], "Detalle": [CABECERA, FILAS[1]]})
    documento = tabular.extraer(ruta, fenomeno=1)

    titulos = [(b.texto, b.tipo) for b in documento.bloques if b.tipo == "titulo"]
    assert titulos == [("Resumen", "titulo"), ("Detalle", "titulo")]


def test_las_filas_cuelgan_de_su_hoja(tmp_path):
    ruta = escribir_xlsx(tmp_path, {"Resumen": [CABECERA, FILAS[0]]})
    documento = tabular.extraer(ruta, fenomeno=1)

    fila = next(b for b in documento.bloques if b.tipo == "fila")
    assert fila.ruta == ["Resumen"]


def test_el_formato_sale_de_la_extension(tmp_path):
    assert tabular.extraer(escribir_csv(tmp_path), fenomeno=1).formato == "csv"
    assert tabular.extraer(escribir_xlsx(tmp_path), fenomeno=1).formato == "xlsx"


def test_las_columnas_se_registran_por_hoja(tmp_path):
    ruta = escribir_xlsx(tmp_path, {"Resumen": [CABECERA, FILAS[0]]})
    assert tabular.extraer(ruta, fenomeno=1).meta["columnas"] == {"Resumen": CABECERA}


def test_una_hoja_vacia_no_deja_titulo_huerfano(tmp_path):
    ruta = escribir_xlsx(tmp_path, {"Vacia": [], "Datos": [CABECERA, FILAS[0]]})
    documento = tabular.extraer(ruta, fenomeno=1)

    assert "Vacia" not in [b.texto for b in documento.bloques]


def test_una_celda_con_fecha_no_sale_como_numero_serial(tmp_path):
    """Excel guarda las fechas como enteros; sin data_only saldría 45000."""
    import datetime

    ruta = escribir_xlsx(tmp_path, {"D": [["fecha", "nota"], [datetime.date(2024, 3, 12), "Informe"]]})
    documento = tabular.extraer(ruta, fenomeno=1)

    assert "2024-03-12" in documento.bloques[-1].texto


# --- robustez y contrato --------------------------------------------------------


def test_un_csv_ilegible_no_lanza(tmp_path):
    ruta = tmp_path / "roto.csv"
    ruta.write_bytes(b"\x00\x01\x02\xff\xfe binario")

    documento = tabular.extraer(ruta, fenomeno=1)

    assert documento.bloques == []
    assert documento.errores != []


def test_un_xlsx_que_no_es_un_libro_no_lanza(tmp_path):
    ruta = tmp_path / "falso.xlsx"
    ruta.write_bytes(b"PK\x03\x04 esto no es un xlsx")

    documento = tabular.extraer(ruta, fenomeno=1)

    assert documento.bloques == []
    assert documento.errores != []


def test_un_csv_sin_filas_lo_dice(tmp_path):
    ruta = escribir_csv(tmp_path, filas=[])
    documento = tabular.extraer(ruta, fenomeno=1)

    assert documento.bloques == []
    assert documento.errores != []


@pytest.mark.parametrize("constructor", ["csv", "xlsx"])
def test_toda_salida_cumple_el_contrato(tmp_path, constructor):
    ruta = escribir_csv(tmp_path) if constructor == "csv" else escribir_xlsx(tmp_path)
    assert validar_documento(tabular.extraer(ruta, fenomeno=1)) == []


def test_dos_extracciones_son_identicas(tmp_path):
    ruta = escribir_csv(tmp_path)
    assert tabular.extraer(ruta, fenomeno=1) == tabular.extraer(ruta, fenomeno=1)


def test_un_dataset_enorme_se_trunca_y_lo_registra(tmp_path):
    """Un CSV de 60.000 filas produciría 60.000 vectores casi idénticos."""
    filas = [[f"pais{n}", "2024", str(n), "nota"] for n in range(tabular.MAXIMO_FILAS + 50)]
    documento = tabular.extraer(escribir_csv(tmp_path, filas=filas), fenomeno=1)

    assert len(documento.bloques) == tabular.MAXIMO_FILAS
    assert documento.meta["filas_truncadas"] == 50
    assert documento.errores != []
