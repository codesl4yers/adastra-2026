"""Pruebas del lector del índice maestro de ADL."""

import openpyxl
import pytest

from indice import EntradaIndice, cargar_indice


def escribir_xlsx(destino, filas, hoja="Inventario de Archivos"):
    """Construye un xlsx con la cabecera de ADL y las filas dadas."""
    cabecera = [
        "Fenómeno",
        "Observatorio",
        "Código Observatorio",
        "DOC_ID",
        "Nombre estandarizado",
        "Carpeta",
        "Tipo",
    ]
    libro = openpyxl.Workbook()
    ws = libro.active
    ws.title = hoja
    ws.append(cabecera)
    for fila in filas:
        ws.append(list(fila))
    libro.save(destino)
    libro.close()
    return destino


# --- lectura correcta ---------------------------------------------------------


def test_carga_las_cuatro_entradas_del_fixture(indice_minimo):
    entradas = cargar_indice(indice_minimo)
    assert len(entradas) == 4


def test_la_clave_es_la_ruta_relativa_no_el_nombre(indice_minimo):
    entradas = cargar_indice(indice_minimo)
    assert "colisiones/a/informe.html" in entradas
    assert "colisiones/b/informe.html" in entradas
    assert "informe.html" not in entradas


def test_mapea_el_fenomeno_a_entero(indice_minimo):
    entradas = cargar_indice(indice_minimo)
    assert entradas["bien_formado.html"].fenomeno == 1
    assert entradas["colisiones/a/informe.html"].fenomeno == 2
    assert entradas["anidado.html"].fenomeno == 3


def test_la_entrada_lleva_todos_los_campos(indice_minimo):
    entrada = cargar_indice(indice_minimo)["colisiones/a/informe.html"]
    assert entrada == EntradaIndice(
        doc_id="F2-SWF-001",
        fuente="informe.html",
        ruta_relativa="colisiones/a/informe.html",
        fenomeno=2,
        observatorio="Secure_World",
        codigo_observatorio="SWF",
        tipo_declarado="HTML",
    )


def test_una_carpeta_vacia_deja_la_ruta_igual_al_nombre(indice_minimo):
    entrada = cargar_indice(indice_minimo)["bien_formado.html"]
    assert entrada.ruta_relativa == "bien_formado.html"


def test_dos_homonimos_conservan_la_misma_fuente(indice_minimo):
    entradas = cargar_indice(indice_minimo)
    a = entradas["colisiones/a/informe.html"]
    b = entradas["colisiones/b/informe.html"]
    assert a.fuente == b.fuente == "informe.html"
    assert a.doc_id != b.doc_id


# --- normalización de separadores --------------------------------------------


def test_normaliza_separadores_de_windows(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "F1_Carpeta\\sub", "PDF")],
    )
    entradas = cargar_indice(ruta)
    assert "F1_Carpeta/sub/a.pdf" in entradas


def test_ignora_barras_sobrantes_en_la_carpeta(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "/F1_Carpeta/sub/", "PDF")],
    )
    assert "F1_Carpeta/sub/a.pdf" in cargar_indice(ruta)


# --- determinismo -------------------------------------------------------------


def test_el_orden_del_mapa_es_el_del_archivo(tmp_path):
    filas = [
        ("F1", "Obs", "OBS", f"F1-OBS-{n:03d}", f"z{9 - n}.pdf", "carpeta", "PDF")
        for n in range(1, 5)
    ]
    ruta = escribir_xlsx(tmp_path / "i.xlsx", filas)
    esperado = [f"carpeta/z{9 - n}.pdf" for n in range(1, 5)]
    assert list(cargar_indice(ruta)) == esperado


# --- índices inconsistentes: ValueError ---------------------------------------


def test_doc_id_repetido_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [
            ("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "uno", "PDF"),
            ("F1", "Obs", "OBS", "F1-OBS-001", "b.pdf", "dos", "PDF"),
        ],
    )
    with pytest.raises(ValueError, match="DOC_ID duplicado"):
        cargar_indice(ruta)


def test_ruta_repetida_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [
            ("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "uno", "PDF"),
            ("F1", "Obs", "OBS", "F1-OBS-002", "a.pdf", "uno", "PDF"),
        ],
    )
    with pytest.raises(ValueError, match="ruta duplicada"):
        cargar_indice(ruta)


def test_fenomeno_invalido_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F9", "Obs", "OBS", "F9-OBS-001", "a.pdf", "uno", "PDF")],
    )
    with pytest.raises(ValueError, match="fenómeno"):
        cargar_indice(ruta)


def test_celda_obligatoria_vacia_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", None, "a.pdf", "uno", "PDF")],
    )
    with pytest.raises(ValueError, match="DOC_ID"):
        cargar_indice(ruta)


def test_doc_id_con_separador_de_ruta_lanza_value_error(tmp_path):
    """Reproduce el caso real: una barra de más (typo o autocorrección de
    Excel) convierte el DOC_ID en una ruta y revienta la escritura en
    orquestador.py a mitad de la corrida. Debe detectarse aquí, no ahí."""
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", "F1-SUB/OBS-002", "a.pdf", "uno", "PDF")],
    )
    with pytest.raises(ValueError, match="DOC_ID"):
        cargar_indice(ruta)


@pytest.mark.parametrize("caracter", list('/\\:*?"<>|'))
def test_doc_id_con_caracter_prohibido_en_windows_lanza_value_error(tmp_path, caracter):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", f"F1-OBS{caracter}001", "a.pdf", "uno", "PDF")],
    )
    with pytest.raises(ValueError, match="DOC_ID"):
        cargar_indice(ruta)


def test_doc_id_con_mas_de_tres_digitos_no_es_demasiado_estricto(tmp_path):
    """La forma es F<n>-<CODIGO>-<nnn>, pero el número de dígitos no está
    fijado a tres: el día que un observatorio pase de 999 documentos, un
    DOC_ID de cuatro dígitos debe seguir siendo válido."""
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", "F1-OBS-1000", "a.pdf", "uno", "PDF")],
    )
    entradas = cargar_indice(ruta)
    assert entradas["uno/a.pdf"].doc_id == "F1-OBS-1000"


def test_hoja_ausente_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "uno", "PDF")],
        hoja="Otra Hoja",
    )
    with pytest.raises(ValueError, match="Inventario de Archivos"):
        cargar_indice(ruta)


def test_columna_ausente_lanza_value_error(tmp_path):
    libro = openpyxl.Workbook()
    ws = libro.active
    ws.title = "Inventario de Archivos"
    ws.append(["Fenómeno", "DOC_ID"])
    ws.append(["F1", "F1-OBS-001"])
    destino = tmp_path / "i.xlsx"
    libro.save(destino)
    libro.close()

    with pytest.raises(ValueError, match="columnas"):
        cargar_indice(destino)


def test_archivo_inexistente_lanza_value_error(tmp_path):
    with pytest.raises(ValueError, match="no existe"):
        cargar_indice(tmp_path / "no_existe.xlsx")


# --- filas en blanco ----------------------------------------------------------


def test_ignora_las_filas_completamente_vacias(tmp_path):
    destino = tmp_path / "i.xlsx"
    escribir_xlsx(
        destino,
        [
            ("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "uno", "PDF"),
            (None, None, None, None, None, None, None),
            ("F1", "Obs", "OBS", "F1-OBS-002", "b.pdf", "uno", "PDF"),
        ],
    )
    assert len(cargar_indice(destino)) == 2
