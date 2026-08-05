"""Pruebas del extractor de texto plano y Markdown."""

import pytest

from contrato import validar_documento
from extractores import texto

PARRAFO = (
    "La red actual de sensores deja fuera el hemisferio sur, donde la cobertura "
    "es más escasa y el tiempo medio de detección supera las cuarenta y ocho horas."
)
OTRO = "El presupuesto estimado asciende a doce millones de dólares en tres años."


def escribir(tmp_path, contenido, nombre="informe.txt", encoding="utf-8"):
    destino = tmp_path / nombre
    destino.write_bytes(contenido.encode(encoding))
    return destino


# --- párrafos ------------------------------------------------------------------


def test_las_lineas_en_blanco_separan_parrafos(tmp_path):
    ruta = escribir(tmp_path, f"{PARRAFO}\n\n{OTRO}\n")
    documento = texto.extraer(ruta, fenomeno=2)

    assert [b.texto for b in documento.bloques] == [PARRAFO, OTRO]


def test_un_salto_de_linea_suelto_no_parte_el_parrafo(tmp_path):
    """El texto de un informe viene cortado a 80 columnas: partir por \\n trocearía cada frase."""
    ruta = escribir(tmp_path, "La red actual de sensores\ndeja fuera el hemisferio sur.\n")
    documento = texto.extraer(ruta, fenomeno=2)

    assert len(documento.bloques) == 1
    assert documento.bloques[0].texto == "La red actual de sensores deja fuera el hemisferio sur."


def test_la_numeracion_de_pagina_no_se_indexa(tmp_path):
    ruta = escribir(tmp_path, f"{PARRAFO}\n\n- 12 -\n\n{OTRO}\n")
    assert len(texto.extraer(ruta, fenomeno=2).bloques) == 2


def test_las_cabeceras_repetidas_entre_paginas_se_descartan(tmp_path):
    """El texto extraído de un PDF arrastra el pie repetido en medio del cuerpo."""
    pagina = "Secure World Foundation\n\n{}\n\n"
    contenido = "\x0c".join(pagina.format(f"{PARRAFO} Página {n}.") for n in range(4))
    documento = texto.extraer(escribir(tmp_path, contenido), fenomeno=2)

    assert "Secure World Foundation" not in [b.texto for b in documento.bloques]
    assert documento.bloques


def test_el_documento_sin_texto_util_lo_dice(tmp_path):
    documento = texto.extraer(escribir(tmp_path, "\n\n   \n\n"), fenomeno=2)

    assert documento.bloques == []
    assert documento.errores != []


# --- cabecera del scraper ------------------------------------------------------


def test_la_cabecera_del_scraper_va_a_meta(tmp_path):
    """SWF_full-text.txt empieza con SOURCE/SCRAPED y una regla de guiones."""
    contenido = (
        "SOURCE: https://www.swfound.org/publications/informe\n"
        "SCRAPED: 2026-05-26T20:05:44.719901Z\n"
        f"{'=' * 80}\n\n{PARRAFO}\n"
    )
    documento = texto.extraer(escribir(tmp_path, contenido), fenomeno=2)

    assert documento.meta["url"] == "https://www.swfound.org/publications/informe"
    assert documento.meta["fecha_scraping"] == "2026-05-26T20:05:44.719901Z"
    assert "SOURCE:" not in " ".join(b.texto for b in documento.bloques)


# --- Markdown -------------------------------------------------------------------


def test_los_encabezados_markdown_son_titulos(tmp_path):
    contenido = f"# Informe\n\n{PARRAFO}\n\n## Metodología\n\n{OTRO}\n"
    documento = texto.extraer(escribir(tmp_path, contenido, nombre="notas.md"), fenomeno=1)

    assert [(b.texto, b.nivel) for b in documento.bloques if b.tipo == "titulo"] == [
        ("Informe", 1),
        ("Metodología", 2),
    ]


def test_el_cuerpo_markdown_cuelga_de_su_encabezado(tmp_path):
    contenido = f"# Informe\n\n## Metodología\n\n{OTRO}\n"
    documento = texto.extraer(escribir(tmp_path, contenido, nombre="notas.md"), fenomeno=1)

    assert documento.bloques[-1].ruta == ["Informe", "Metodología"]


def test_una_almohadilla_dentro_de_un_bloque_de_codigo_no_es_un_titulo(tmp_path):
    contenido = f"# Informe\n\n```python\n# esto es un comentario\nx = 1\n```\n\n{OTRO}\n"
    documento = texto.extraer(escribir(tmp_path, contenido, nombre="notas.md"), fenomeno=1)

    titulos = [b.texto for b in documento.bloques if b.tipo == "titulo"]
    assert titulos == ["Informe"]


def test_en_un_txt_las_almohadillas_no_son_titulos(tmp_path):
    """El .txt no tiene marcado: todo es párrafo."""
    documento = texto.extraer(escribir(tmp_path, "# no es un titulo\n"), fenomeno=1)
    assert all(b.tipo == "parrafo" for b in documento.bloques)


def test_los_elementos_de_lista_markdown_se_marcan_como_lista(tmp_path):
    contenido = "# T\n\n- primer elemento de la lista\n- segundo elemento de la lista\n"
    documento = texto.extraer(escribir(tmp_path, contenido, nombre="notas.md"), fenomeno=1)

    assert [b.tipo for b in documento.bloques if b.tipo == "lista"] == ["lista", "lista"]


# --- codificación y robustez -----------------------------------------------------


def test_lee_utf8_por_defecto(tmp_path):
    documento = texto.extraer(escribir(tmp_path, "Año de canción en español."), fenomeno=1)

    assert documento.bloques[0].texto == "Año de canción en español."
    assert documento.meta["codificacion"] == "utf-8"


def test_recurre_a_cp1252_y_lo_registra(tmp_path):
    """Un cambio de codificación cambia el texto: sin registrarlo no hay forma de saberlo."""
    ruta = escribir(tmp_path, "Año de canción en español.", encoding="cp1252")
    documento = texto.extraer(ruta, fenomeno=1)

    assert documento.meta["codificacion"] == "cp1252"
    assert "Año" in documento.bloques[0].texto


def test_un_archivo_con_bytes_nul_se_rechaza_entero(tmp_path):
    """Un NUL significa corrupción, y un documento truncado que parece válido es peor."""
    ruta = tmp_path / "corrupto.txt"
    ruta.write_bytes(b"Texto valido\x00\x00 y basura")

    documento = texto.extraer(ruta, fenomeno=1)

    assert documento.bloques == []
    assert any("NUL" in e for e in documento.errores)


def test_la_pagina_es_siempre_nula(tmp_path):
    """El texto plano no tiene páginas."""
    documento = texto.extraer(escribir(tmp_path, PARRAFO), fenomeno=1)
    assert all(b.pagina is None for b in documento.bloques)


def test_la_salida_cumple_el_contrato(tmp_path):
    contenido = f"# Informe\n\n{PARRAFO}\n\n## Detalle\n\n- un elemento\n\n{OTRO}\n"
    documento = texto.extraer(escribir(tmp_path, contenido, nombre="notas.md"), fenomeno=1)
    assert validar_documento(documento) == []


def test_dos_extracciones_son_identicas(tmp_path):
    ruta = escribir(tmp_path, f"{PARRAFO}\n\n{OTRO}\n")
    assert texto.extraer(ruta, fenomeno=1) == texto.extraer(ruta, fenomeno=1)


def test_el_txt_real_del_corpus_se_extrae(dir_corpus):
    """SWF_full-text.txt (F2-SWF-113) es un informe completo, no un residuo."""
    reales = list(dir_corpus.rglob("*.txt"))
    if not reales:
        pytest.skip("el corpus real no está disponible")

    documento = texto.extraer(reales[0], fenomeno=2)

    assert documento.bloques
    assert validar_documento(documento) == []
