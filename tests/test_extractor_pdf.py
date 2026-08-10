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


# --- texto presente pero ilegible ---------------------------------------------
#
# Hay PDFs que sí tienen capa de texto y aun así no se pueden leer: si la fuente
# embebida no trae tabla ToUnicode, pdfplumber devuelve "(cid:NN)" por carácter;
# si el PDF dibuja cada letra como un objeto suelto, devuelve las letras
# separadas por espacios. La densidad de caracteres por página no los detecta
# —"(cid:47)" son nueve caracteres por letra, así que la densidad es altísima—,
# y salían al índice como fragmentos de miles de tokens sin contenido legible.
# En el corpus real: 1 783 fragmentos, el 5,3 % del coste de codificación, y un
# solo documento de 179 páginas (F3-CEOBS-030) con el 93 % de todo ello.


def linea_ilegible(texto: str) -> pdf.Linea:
    return pdf.Linea(texto=texto, tamano=10.0, top=0.0, bottom=10.0, x0=0.0)


def test_un_pdf_con_cid_sin_mapear_se_considera_ilegible():
    paginas = [[linea_ilegible("(cid:47)(cid:76)(cid:86)(cid:87)(cid:3)(cid:82)(cid:73)")]]

    assert pdf._texto_ilegible(paginas) is True


def test_un_pdf_dibujado_letra_a_letra_se_considera_ilegible():
    paginas = [[linea_ilegible("L i f e c y c l e c o s t e s t i m a t i o n r e q u i r e s")]]

    assert pdf._texto_ilegible(paginas) is True


def test_un_pdf_normal_no_se_considera_ilegible():
    paginas = [
        [linea_ilegible("El observatorio publicó su informe anual sobre capacidades.")],
        [linea_ilegible("La red de sensores cubre el arco sur del continente.")],
    ]

    assert pdf._texto_ilegible(paginas) is False


def test_unas_pocas_formulas_con_cid_no_bastan_para_ir_a_ocr():
    """Un PDF legible con una fórmula en fuente rara no se manda a OCR: el
    texto nativo es mejor que el OCR cuando el texto nativo está bien."""
    normal = "El informe analiza la evolución del gasto durante la última década. "
    paginas = [[linea_ilegible(normal * 12 + "(cid:12)(cid:34)")]]

    assert pdf._texto_ilegible(paginas) is False


def test_una_tabla_con_celdas_de_una_letra_no_va_a_ocr():
    """Las tablas de los atlas traen columnas de una letra (Y/N, X). Mientras
    quede prosa reconocible alrededor, el documento se lee bien."""
    paginas = [
        [linea_ilegible("El informe analiza la presencia de fuerzas armadas en la región.")],
        [linea_ilegible("Cada ficha recoge el despliegue declarado por el país en el periodo.")],
        [linea_ilegible("País Fuerza Y N X Argentina Y N X Brasil Y N X Chile Y N")],
    ]

    assert pdf._texto_ilegible(paginas) is False


def test_un_pdf_con_capa_de_texto_ilegible_se_manda_a_ocr(pdf_minimo, monkeypatch):
    """La densidad de caracteres por página no basta como criterio: un PDF con
    CID tiene densidad altísima —nueve caracteres por letra— y aun así no se
    puede leer."""
    # Larga a propósito: con menos de MINIMO_CARACTERES_POR_PAGINA la mandaría
    # a OCR el criterio de densidad y la prueba pasaría sin probar nada.
    cid = "(cid:47)(cid:76)(cid:86)(cid:87)" * 8
    assert len(cid) > pdf.MINIMO_CARACTERES_POR_PAGINA * 2
    monkeypatch.setattr(
        pdf, "lineas_del_documento", lambda _: [[pdf.Linea(cid, 10.0, 0.0, 10.0, 0.0)]]
    )
    monkeypatch.setattr(pdf.ocr, "hay_ocr", lambda: False)

    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    assert documento.meta["requiere_ocr"] is True


def test_un_pdf_legible_no_se_manda_a_ocr(pdf_minimo, monkeypatch):
    """El control: con texto normal, el extractor no toca el OCR."""
    monkeypatch.setattr(
        pdf.ocr, "hay_ocr", lambda: pytest.fail("no debería consultar el OCR")
    )

    assert pdf.extraer(pdf_minimo, fenomeno=1).meta.get("requiere_ocr") is None


def test_un_logotipo_multilingue_no_manda_el_documento_a_ocr():
    """Caso real `F2-SWF-035`: la portada repite el nombre de la fundación en
    cinco alfabetos, y los caracteres CJK y árabes cuentan como palabras de una
    letra. El documento se lee perfectamente; mandarlo a OCR lo empeoraría."""
    paginas = [
        [linea_ilegible("FUNDACIÓN 安 全 世 界 基 金 会 FOUNDATION م SECURE ФОНД")],
        [linea_ilegible("El informe examina la seguridad del entorno espacial en 2025.")],
    ]

    assert pdf._texto_ilegible(paginas) is False


# --- OCR página a página -------------------------------------------------------
#
# Decidir por documento arreglaba una parte y estropeaba otra: `F2-CSIS-113` y
# `F3-SIPRI-007` tienen la portada destrozada y el cuerpo perfectamente legible,
# y el OCR pierde acentos ("análisis" sale "nalisis"). La decisión es por página:
# texto nativo donde se puede leer, OCR solo donde no.


def test_los_avisos_de_fuentes_de_pdfminer_no_ensucian_la_salida():
    """`pdfminer` avisa por cada fuente cuyo descriptor no trae un FontBBox
    parseable, y en una corrida del corpus eso son decenas de líneas en stderr
    entre las que se pierden los errores de verdad. No afecta a la extracción:
    pdfminer sigue adelante con un recuadro (0,0,0,0)."""
    import logging

    assert logging.getLogger("pdfminer").level >= logging.ERROR


def paginas_mixtas():
    """Una página legible y otra con la capa de texto rota."""
    return [
        [linea_ilegible("El observatorio publicó su informe anual sobre capacidades espaciales.")],
        [linea_ilegible("(cid:47)(cid:76)(cid:86)(cid:87)(cid:3)(cid:82)(cid:73)" * 8)],
    ]


def ocr_de_prueba(monkeypatch, texto="Texto recuperado por el reconocimiento optico", confianza=93.0):
    """Sustituye el reconocimiento real; devuelve la lista de páginas pedidas."""
    pedidas = []

    def texto_de_imagen(imagen):
        pedidas.append(imagen)
        return texto, confianza

    monkeypatch.setattr(pdf.ocr, "hay_ocr", lambda: True)
    monkeypatch.setattr(pdf.ocr, "version", lambda: "5.5.3")
    monkeypatch.setattr(pdf.ocr, "texto_de_imagen", texto_de_imagen)
    return pedidas


def test_solo_las_paginas_ilegibles_pasan_por_el_ocr(pdf_minimo, monkeypatch):
    """El corazón del cambio: una página rota no condena al documento entero."""
    monkeypatch.setattr(pdf, "lineas_del_documento", lambda _: paginas_mixtas())
    pedidas = ocr_de_prueba(monkeypatch)

    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    assert len(pedidas) == 1, "solo la página ilegible debería rasterizarse"
    assert documento.meta["paginas_ocr"] == [2]


def test_el_texto_nativo_legible_sobrevive_intacto(pdf_minimo, monkeypatch):
    """Lo que se lee bien no se toca: el OCR degradaría los acentos."""
    monkeypatch.setattr(pdf, "lineas_del_documento", lambda _: paginas_mixtas())
    ocr_de_prueba(monkeypatch)

    textos = [b.texto for b in pdf.extraer(pdf_minimo, fenomeno=1).bloques if b]

    assert any("informe anual sobre capacidades" in t for t in textos)
    assert not any("(cid:" in t for t in textos)


def test_el_texto_reconocido_reemplaza_al_ilegible(pdf_minimo, monkeypatch):
    monkeypatch.setattr(pdf, "lineas_del_documento", lambda _: paginas_mixtas())
    ocr_de_prueba(monkeypatch)

    textos = [b.texto for b in pdf.extraer(pdf_minimo, fenomeno=1).bloques if b]

    assert any("recuperado por el reconocimiento" in t for t in textos)


def test_los_bloques_reconocidos_se_marcan_como_ocr(pdf_minimo, monkeypatch):
    """El contrato tiene un tipo para esto; usarlo mantiene la trazabilidad."""
    monkeypatch.setattr(pdf, "lineas_del_documento", lambda _: paginas_mixtas())
    ocr_de_prueba(monkeypatch)

    bloques = [b for b in pdf.extraer(pdf_minimo, fenomeno=1).bloques if b]
    reconocidos = [b for b in bloques if "recuperado" in b.texto]

    assert reconocidos and all(b.tipo == "ocr" for b in reconocidos)
    assert all(b.pagina == 2 for b in reconocidos)


def test_un_documento_legible_no_rasteriza_ninguna_pagina(pdf_minimo, monkeypatch):
    """Rasterizar cuesta ~1 s por página: no se hace sin necesidad."""
    pedidas = ocr_de_prueba(monkeypatch)

    pdf.extraer(pdf_minimo, fenomeno=1)

    assert pedidas == []


def test_el_ocr_no_altera_la_jerarquia_de_titulos_del_texto_nativo(pdf_minimo, monkeypatch):
    """Los niveles salen del tamaño de fuente. Las líneas del OCR no tienen
    tamaño real, así que no pueden entrar en ese cálculo: si entraran,
    desplazarían el tamaño del cuerpo y los títulos del documento cambiarían."""
    cuerpo = [
        pdf.Linea(f"Línea {i} del informe sobre el gasto regional del periodo.", 10.0, i * 12.0, i * 12.0 + 10, 50.0)
        for i in range(6)
    ]
    # Dos páginas: las que tiene el PDF de la fixture. Simular más haría que
    # `documento_pdf.pages[n]` se saliera del rango y el documento saldría
    # fallido, con la prueba fallando por un motivo que no es el suyo.
    nativas = [
        [pdf.Linea("Capacidades estratégicas", 22.0, 0.0, 22.0, 50.0), *cuerpo],
        [linea_ilegible("(cid:47)(cid:76)(cid:86)(cid:87)(cid:3)(cid:82)(cid:73)" * 8)],
    ]
    monkeypatch.setattr(pdf, "lineas_del_documento", lambda _: nativas)
    # Muchas líneas reconocidas: si entraran en el cálculo, su tamaño ficticio
    # sería la moda y pasaría a ser "el cuerpo", convirtiendo en título todo lo
    # demás. Con menos líneas la prueba pasaría sin comprobar nada.
    ocr_de_prueba(monkeypatch, texto="\n".join(f"renglón reconocido {i}" for i in range(10)))

    bloques = [b for b in pdf.extraer(pdf_minimo, fenomeno=1).bloques if b]
    titulos = [b for b in bloques if b.tipo == "titulo"]

    assert [t.texto for t in titulos] == ["Capacidades estratégicas"]


def test_una_pagina_sin_texto_de_un_documento_legible_no_se_rasteriza(pdf_minimo, monkeypatch):
    """Portadas, separadores de capítulo y páginas de figuras aparecen casi
    vacías en cualquier informe. Tratarlas como rotas mandaba a OCR 298 de los
    759 PDFs del corpus —1 764 páginas, media hora— para recuperar, en el mejor
    caso, el título de una portada. La falta de texto solo es señal de que hay
    algo que reconocer cuando lo es del documento entero."""
    monkeypatch.setattr(
        pdf,
        "lineas_del_documento",
        lambda _: [
            # Bastante texto para que la media del documento supere el mínimo
            # por página: si no, el documento entero parecería escaneado y la
            # prueba pasaría por el motivo equivocado.
            [
                linea_ilegible(
                    "El observatorio publicó su informe anual sobre capacidades "
                    "espaciales de la región durante el año pasado, con atención "
                    "a los lanzamientos orbitales y a la vigilancia del entorno."
                )
            ],
            [],  # página en blanco: un separador de capítulo
        ],
    )
    pedidas = ocr_de_prueba(monkeypatch)

    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    assert pedidas == []
    assert documento.meta.get("requiere_ocr") is None


def test_un_documento_entero_sin_capa_de_texto_si_va_a_ocr(pdf_minimo, monkeypatch):
    """El caso clásico del escaneado: ninguna página tiene texto, y ahí la
    ausencia sí significa que hay que reconocer."""
    monkeypatch.setattr(pdf, "lineas_del_documento", lambda _: [[], []])
    pedidas = ocr_de_prueba(monkeypatch)

    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    assert len(pedidas) == 2
    assert documento.meta["paginas_ocr"] == [1, 2]


def test_una_pagina_rota_se_reconoce_aunque_el_documento_se_lea_bien(pdf_minimo, monkeypatch):
    """El otro lado del ajuste: el texto roto sí se juzga página a página."""
    monkeypatch.setattr(pdf, "lineas_del_documento", lambda _: paginas_mixtas())
    pedidas = ocr_de_prueba(monkeypatch)

    pdf.extraer(pdf_minimo, fenomeno=1)

    assert len(pedidas) == 1


def test_no_se_reemplaza_texto_nativo_por_un_ocr_mas_pobre(pdf_minimo, monkeypatch):
    """Caso real, página 64 de `F1-AIINDEX-014`: texto chino perfectamente
    legible conviviendo con las etiquetas rotadas de un gráfico, que salen
    letra a letra y disparan el criterio de ilegibilidad. El diagnóstico es
    correcto —parte de esa página no se puede leer— pero el OCR solo devuelve
    los porcentajes del gráfico: 605 caracteres de contenido se convertirían en
    85 de ruido. Detectar que una página está rota no basta; hay que comprobar
    que el reconocimiento mejora lo que había."""
    chino = "第一章：研究与开发 1.3标志性人工智能模型 重点: 模型训练会面临数据枯竭 事实准确性 资料来源"
    etiquetas = " ".join("C S C S T A T A t F I T M F F R S T P")
    monkeypatch.setattr(
        pdf,
        "lineas_del_documento",
        lambda _: [
            [linea_ilegible(f"{chino} {etiquetas}")],
            [linea_ilegible("Página legible con texto corriente del informe anual publicado.")],
        ],
    )
    ocr_de_prueba(monkeypatch, texto="100% 89.50% 80% 75.40%")

    documento = pdf.extraer(pdf_minimo, fenomeno=1)
    textos = [b.texto for b in documento.bloques if b]

    assert any("第一章" in t for t in textos), "el texto chino no puede perderse"
    assert not any("89.50%" in t for t in textos)
    assert documento.meta.get("paginas_ocr") is None


def test_el_ocr_si_reemplaza_una_pagina_que_solo_tiene_cid(pdf_minimo, monkeypatch):
    """El contrapunto: el CID abulta mucho en caracteres —nueve por letra— pero
    no es contenido. Comparar longitudes en bruto dejaría sin reconocer justo
    las páginas que más lo necesitan."""
    monkeypatch.setattr(
        pdf,
        "lineas_del_documento",
        lambda _: [
            [linea_ilegible("(cid:47)(cid:76)(cid:86)(cid:87)(cid:3)(cid:82)(cid:73)" * 20)],
            [linea_ilegible("Página legible con texto corriente del informe anual publicado.")],
        ],
    )
    ocr_de_prueba(monkeypatch, texto="List of Images and Tables")

    documento = pdf.extraer(pdf_minimo, fenomeno=1)
    textos = [b.texto for b in documento.bloques if b]

    assert any("List of Images" in t for t in textos)
    assert not any("(cid:" in t for t in textos)


def test_si_el_ocr_esta_pero_no_mejora_no_se_reporta_que_falta(pdf_minimo, monkeypatch):
    """Que no se reconociera ninguna página tiene dos causas muy distintas: que
    falte Tesseract, o que el OCR no mejorara lo que ya había. Confundirlas
    llena el reporte de corrida con 40 documentos mandando a instalar algo que
    ya está instalado, y esconde el estado real del corpus."""
    chino = "第一章：研究与开发 1.3标志性人工智能模型 重点: 模型训练会面临数据枯竭 事实准确性 资料来源"
    etiquetas = " ".join("C S C S T A T A t F I T M F F R S T P")
    monkeypatch.setattr(
        pdf,
        "lineas_del_documento",
        lambda _: [
            [linea_ilegible(f"{chino} {etiquetas}")],
            [linea_ilegible("Página legible con texto corriente del informe anual publicado.")],
        ],
    )
    ocr_de_prueba(monkeypatch, texto="100%")  # menos contenido que el nativo

    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    assert not any("sin OCR disponible" in e for e in documento.errores)
    assert documento.meta["paginas_sin_recuperar"] == [1]


def test_sin_tesseract_las_paginas_ilegibles_se_registran(pdf_minimo, monkeypatch):
    """Sin OCR no se puede arreglar, pero sí dejar constancia para una corrida
    posterior en vez de indexar la basura en silencio."""
    monkeypatch.setattr(pdf, "lineas_del_documento", lambda _: paginas_mixtas())
    monkeypatch.setattr(pdf.ocr, "hay_ocr", lambda: False)

    documento = pdf.extraer(pdf_minimo, fenomeno=1)

    assert documento.meta["requiere_ocr"] is True
    assert any("ocr" in e.lower() for e in documento.errores)


def test_un_documento_sin_lineas_no_es_ilegible_sino_escaneado():
    """Sin texto no hay nada que juzgar: de eso se ocupa _parece_escaneado."""
    assert pdf._texto_ilegible([[], []]) is False


def test_las_palabras_de_una_pagina_no_sobreviven_a_la_pagina(pdf_minimo):
    """Materializar todas las páginas antes de procesar agota la memoria.

    Un informe de 250 páginas son millones de diccionarios de palabra vivos a
    la vez; con varios procesos en paralelo, el proceso muere con MemoryError.
    Solo se conservan las líneas, que son dos órdenes de magnitud menos.
    """
    lineas = pdf.lineas_del_documento(pdf_minimo)

    assert len(lineas) == 2  # una lista de líneas por página
    assert all(isinstance(linea, pdf.Linea) for pagina in lineas for linea in pagina)
