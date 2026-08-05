"""Pruebas del extractor de PDF.

Las funciones de análisis se prueban con las mismas estructuras que devuelve
``pdfplumber.extract_words`` —diccionarios con ``text``, ``x0``, ``x1``,
``top``, ``bottom`` y ``size``— para no depender de generar un PDF por caso.
El pipeline completo se prueba contra un PDF real (fixture binario) y contra
una muestra del corpus.
"""

import pytest

from contrato import validar_documento
from extractores import pdf


def palabra(texto, x0, top, size=10.0, ancho=None, alto=None):
    """Una palabra tal y como la entrega pdfplumber."""
    return {
        "text": texto,
        "x0": x0,
        "x1": x0 + (ancho if ancho is not None else len(texto) * size * 0.5),
        "top": top,
        "bottom": top + (alto if alto is not None else size),
        "size": size,
        "fontname": "Test-Regular",
    }


def linea_de(textos, top, x0=50.0, size=10.0):
    palabras = []
    x = x0
    for texto in textos:
        palabras.append(palabra(texto, x, top, size))
        x = palabras[-1]["x1"] + 3
    return palabras


# --- agrupar palabras en líneas -----------------------------------------------


def test_las_palabras_a_la_misma_altura_forman_una_linea():
    palabras = linea_de(["El", "informe", "anual"], top=100)
    lineas = pdf.agrupar_en_lineas(palabras)

    assert len(lineas) == 1
    assert lineas[0].texto == "El informe anual"


def test_una_variacion_minima_de_altura_no_parte_la_linea():
    """Los subíndices y las tildes desplazan el 'top' unas décimas."""
    palabras = [palabra("El", 50, 100.0), palabra("año", 70, 100.4)]
    assert len(pdf.agrupar_en_lineas(palabras)) == 1


def test_dos_alturas_distintas_son_dos_lineas():
    palabras = linea_de(["Primera"], top=100) + linea_de(["Segunda"], top=115)
    lineas = pdf.agrupar_en_lineas(palabras)

    assert [linea.texto for linea in lineas] == ["Primera", "Segunda"]


def test_las_palabras_de_una_linea_se_ordenan_por_posicion():
    """El PDF no garantiza el orden de dibujo: hay que ordenar por x."""
    palabras = [palabra("mundo", 100, 50), palabra("Hola", 50, 50)]
    assert pdf.agrupar_en_lineas(palabras)[0].texto == "Hola mundo"


def test_la_linea_conserva_el_tamano_dominante():
    palabras = linea_de(["Título", "grande"], top=50, size=20.0)
    assert pdf.agrupar_en_lineas(palabras)[0].tamano == 20.0


# --- dos columnas: la trampa principal ----------------------------------------


def test_detecta_el_corredor_vertical_de_una_pagina_a_dos_columnas():
    izquierda = [w for t in range(100, 300, 12) for w in linea_de(["texto", "de", "izquierda"], top=t, x0=50)]
    derecha = [w for t in range(100, 300, 12) for w in linea_de(["texto", "de", "derecha"], top=t, x0=330)]

    corte = pdf.detectar_corte_de_columnas(izquierda + derecha, ancho=612)

    assert corte is not None
    assert all(w["x1"] < corte for w in izquierda), "el corte deja toda la izquierda a su izquierda"
    assert all(w["x0"] > corte for w in derecha), "y toda la derecha a su derecha"


def test_una_pagina_de_una_columna_no_tiene_corredor():
    palabras = [w for t in range(100, 300, 12) for w in linea_de(["una", "sola", "columna", "muy", "ancha"], top=t, x0=50, size=14)]
    assert pdf.detectar_corte_de_columnas(palabras, ancho=612) is None


def test_una_pagina_casi_vacia_no_inventa_columnas():
    """Con cuatro palabras cualquier hueco parece un corredor."""
    assert pdf.detectar_corte_de_columnas(linea_de(["Solo", "esto"], top=100), ancho=612) is None


def test_las_columnas_se_leen_una_despues_de_otra():
    """Leer por altura intercalaría las dos columnas línea a línea."""
    alturas = range(100, 500, 12)  # suficientes palabras para que la detección se active
    izquierda = [w for t in alturas for w in linea_de(["izq"], top=t, x0=50)]
    derecha = [w for t in alturas for w in linea_de(["der"], top=t, x0=330)]

    lineas = pdf.ordenar_por_columnas(izquierda + derecha, ancho=612)
    textos = [linea.texto for linea in lineas]

    assert textos == ["izq"] * len(alturas) + ["der"] * len(alturas)


def test_el_margen_de_una_pagina_estrecha_no_se_lee_como_corredor():
    """El ancho es el de la página física, no el del texto que haya en ella.

    Una página que solo llena media caja tiene un blanco enorme a la derecha.
    Como el corredor se busca únicamente dentro de la zona ocupada, ese blanco
    no cuenta y la página no se parte en columnas inexistentes.
    """
    palabras = [w for t in range(100, 500, 12) for w in linea_de(["texto", "estrecho"], top=t, x0=50)]

    assert pdf.detectar_corte_de_columnas(palabras, ancho=612) is None


# --- niveles de título por tamaño de fuente -----------------------------------


def test_el_tamano_mas_frecuente_es_el_cuerpo():
    assert pdf.tamano_del_cuerpo([10.0] * 50 + [20.0] * 3) == 10.0


def test_los_tamanos_mayores_que_el_cuerpo_son_titulos_escalonados():
    niveles = pdf.niveles_por_tamano([10.0] * 50 + [20.0] * 3 + [14.0] * 6, cuerpo=10.0)

    assert niveles[20.0] == 1
    assert niveles[14.0] == 2
    assert 10.0 not in niveles


def test_el_cuerpo_no_es_titulo_por_mucho_que_se_repita():
    assert pdf.niveles_por_tamano([10.0] * 50, cuerpo=10.0) == {}


def test_los_niveles_se_acotan_al_maximo_del_contrato():
    tamanos = [10.0] * 50 + [float(t) for t in range(11, 25)]
    niveles = pdf.niveles_por_tamano(tamanos, cuerpo=10.0)

    assert all(1 <= nivel <= 6 for nivel in niveles.values())


def test_una_diferencia_ridicula_de_tamano_no_crea_un_titulo():
    """10.0 y 10.2 son la misma fuente con otro redondeo, no una jerarquía."""
    assert pdf.niveles_por_tamano([10.0] * 50 + [10.2] * 5, cuerpo=10.0) == {}


# --- párrafos ------------------------------------------------------------------


def test_las_lineas_seguidas_forman_un_parrafo():
    lineas = pdf.agrupar_en_lineas(
        linea_de(["primera", "línea"], top=100) + linea_de(["segunda", "línea"], top=112)
    )
    parrafos = pdf.agrupar_en_parrafos(lineas)

    assert len(parrafos) == 1
    assert parrafos[0].texto == "primera línea segunda línea"


def test_un_hueco_vertical_grande_abre_parrafo():
    lineas = pdf.agrupar_en_lineas(
        linea_de(["uno"], top=100) + linea_de(["dos"], top=112) + linea_de(["tres"], top=160)
    )
    assert len(pdf.agrupar_en_parrafos(lineas)) == 2


def test_un_titular_a_varias_lineas_no_se_parte_en_varios_titulos():
    """Un título de 40 pt salta 45 pt entre líneas; el cuerpo del documento, 12.

    Con un umbral basado solo en el interlineado dominante, cada línea del
    titular de portada salía como un título distinto, y cada uno cerraba al
    anterior: el breadcrumb del documento entero acababa colgando de la última
    palabra de la portada.
    """
    lineas = pdf.agrupar_en_lineas(
        linea_de(["Walking", "the", "Walk"], top=100, size=40)
        + linea_de(["of", "AI", "Ethics"], top=145, size=40)
        + [w for t in range(300, 400, 12) for w in linea_de(["cuerpo", "del", "informe"], top=t)]
    )

    titulares = [p for p in pdf.agrupar_en_parrafos(lineas) if p.tamano == 40.0]

    assert len(titulares) == 1
    assert titulares[0].texto == "Walking the Walk of AI Ethics"


def test_una_linea_larga_no_es_un_titulo_aunque_tenga_letra_grande():
    """Un pie de autores en cursiva grande no es un encabezado de sección."""
    largo = " ".join(f"palabra{n}" for n in range(30))
    assert pdf.parece_titulo(largo) is False
    assert pdf.parece_titulo("Metodología y fuentes") is True


def test_las_lineas_de_distinto_tamano_no_se_mezclan():
    """Un título pegado a su primer párrafo no puede salir como un solo bloque."""
    lineas = pdf.agrupar_en_lineas(
        linea_de(["TÍTULO"], top=100, size=18) + linea_de(["cuerpo", "del", "texto"], top=112)
    )
    assert len(pdf.agrupar_en_parrafos(lineas)) == 2


# --- pipeline completo sobre un PDF real --------------------------------------


@pytest.fixture(scope="module")
def pdf_minimo(dir_fixtures):
    ruta = dir_fixtures / "minimo.pdf"
    if not ruta.is_file():
        pytest.skip("falta fixtures/minimo.pdf: regenerar con fixtures/generar_binarios.py")
    return ruta


def test_extrae_el_texto_del_pdf(pdf_minimo):
    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    texto = " ".join(b.texto for b in documento.bloques)
    assert "El informe anual describe" in texto


def test_el_titulo_grande_se_reconoce_como_titulo(pdf_minimo):
    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    titulos = [b for b in documento.bloques if b.tipo == "titulo"]
    assert titulos
    assert titulos[0].texto == "Informe de prueba"
    assert titulos[0].nivel == 1


def test_el_cuerpo_cuelga_del_titulo(pdf_minimo):
    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    cuerpo = next(b for b in documento.bloques if b.tipo == "parrafo")
    assert cuerpo.ruta == ["Informe de prueba"]


def test_la_pagina_empieza_en_uno(pdf_minimo):
    documento = pdf.extraer(pdf_minimo, fenomeno=1)
    assert documento.bloques[0].pagina == 1


def test_la_salida_cumple_el_contrato(pdf_minimo):
    assert validar_documento(pdf.extraer(pdf_minimo, fenomeno=1)) == []


def test_dos_extracciones_son_identicas(pdf_minimo):
    assert pdf.extraer(pdf_minimo, fenomeno=1) == pdf.extraer(pdf_minimo, fenomeno=1)


def test_el_documento_registra_su_numero_de_paginas(pdf_minimo):
    assert pdf.extraer(pdf_minimo, fenomeno=1).meta["n_paginas"] >= 1


# --- robustez ------------------------------------------------------------------


def test_un_pdf_ilegible_no_lanza(tmp_path):
    ruta = tmp_path / "roto.pdf"
    ruta.write_bytes(b"%PDF-1.4 esto no es un pdf")

    documento = pdf.extraer(ruta, fenomeno=1)

    assert documento.bloques == []
    assert documento.errores != []


def test_un_pdf_sin_capa_de_texto_lo_dice(pdf_minimo, monkeypatch):
    """El 3 % del corpus está escaneado: devuelve cero caracteres sin fallar.

    Se simula haciendo que las páginas no den ninguna palabra, porque un PDF
    escaneado de verdad son megabytes de imagen y no cabe como fixture.
    """
    monkeypatch.setattr(pdf, "_palabras_de_pagina", lambda _: [])
    monkeypatch.setattr(pdf.ocr, "hay_ocr", lambda: False)

    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    assert documento.bloques == []
    assert any("sin capa de texto" in e for e in documento.errores)


def test_un_pdf_escaneado_se_marca_para_ocr_en_meta(pdf_minimo, monkeypatch):
    monkeypatch.setattr(pdf, "_palabras_de_pagina", lambda _: [])
    monkeypatch.setattr(pdf.ocr, "hay_ocr", lambda: False)

    assert pdf.extraer(pdf_minimo, fenomeno=1).meta["requiere_ocr"] is True


def test_las_palabras_de_una_pagina_no_sobreviven_a_la_pagina(pdf_minimo):
    """Materializar todas las páginas antes de procesar agota la memoria.

    Un informe de 250 páginas son millones de diccionarios de palabra vivos a
    la vez; con varios procesos en paralelo, el proceso muere con MemoryError.
    Solo se conservan las líneas, que son dos órdenes de magnitud menos.
    """
    lineas = pdf.lineas_del_documento(pdf_minimo)

    assert len(lineas) == 2  # una lista de líneas por página
    assert all(isinstance(linea, pdf.Linea) for pagina in lineas for linea in pagina)
