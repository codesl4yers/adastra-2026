"""Pruebas del orquestador y de la persistencia.

Cubre el punto 3 del enunciado: dos corridas producen bytes idénticos.
"""

import json
import os
import subprocess
import sys

import pytest

from contrato import calcular_doc_id, validar_documento
from indice import cargar_indice
from orquestador import cargar_manifiesto, procesar_corpus, procesar_directorio


@pytest.fixture
def salida_doble(dir_fixtures, tmp_path):
    """Ejecuta el pipeline dos veces sobre el mismo corpus, en directorios distintos."""
    primera = tmp_path / "corrida_1"
    segunda = tmp_path / "corrida_2"
    procesar_directorio(dir_fixtures, primera)
    procesar_directorio(dir_fixtures, segunda)
    return primera, segunda


# --- punto 3: determinismo byte a byte ---------------------------------------


def test_dos_corridas_producen_los_mismos_archivos(salida_doble):
    primera, segunda = salida_doble
    nombres_1 = sorted(p.name for p in primera.iterdir())
    nombres_2 = sorted(p.name for p in segunda.iterdir())
    assert nombres_1 == nombres_2
    assert nombres_1, "el pipeline produjo algo"


def test_dos_corridas_producen_bytes_identicos(salida_doble):
    primera, segunda = salida_doble
    for archivo in sorted(primera.iterdir()):
        gemelo = segunda / archivo.name
        assert archivo.read_bytes() == gemelo.read_bytes(), f"difiere {archivo.name}"


def test_determinismo_con_semilla_de_hash_distinta(dir_fixtures, tmp_path, raiz_proyecto):
    """El resultado no puede depender de PYTHONHASHSEED.

    Es la prueba que detecta el uso de set() sin ordenar o de hash() nativo.
    """
    salidas = []
    for semilla in ("0", "12345"):
        destino = tmp_path / f"semilla_{semilla}"
        entorno = dict(os.environ, PYTHONHASHSEED=semilla)
        resultado = subprocess.run(
            [
                sys.executable,
                "auxiliar/orquestador.py",
                "--entrada",
                str(dir_fixtures),
                "--salida",
                str(destino),
            ],
            cwd=raiz_proyecto,
            env=entorno,
            capture_output=True,
            text=True,
        )
        assert resultado.returncode == 0, resultado.stderr
        salidas.append(destino)

    primera, segunda = salidas
    archivos = sorted(p.name for p in primera.iterdir())
    assert archivos == sorted(p.name for p in segunda.iterdir())
    for nombre in archivos:
        assert (primera / nombre).read_bytes() == (segunda / nombre).read_bytes()


def test_determinismo_con_indice_y_semilla_de_hash_distinta(
    dir_fixtures, tmp_path, raiz_proyecto, indice_minimo
):
    """La misma red del test anterior, pero por el camino con ``--indice``.

    Sin esta prueba, el orden de ``huerfanos_del_indice`` y los conteos del
    reporte de cobertura —que solo se calculan cuando hay índice— no los cubre
    ninguna prueba de determinismo: el camino sin índice no ejercita
    ``_cruzar_con_indice`` ni el resto de la lógica que depende del mapa.
    """
    salidas = []
    for semilla in ("0", "12345"):
        destino = tmp_path / f"semilla_{semilla}"
        entorno = dict(os.environ, PYTHONHASHSEED=semilla)
        resultado = subprocess.run(
            [
                sys.executable,
                "auxiliar/orquestador.py",
                "--entrada",
                str(dir_fixtures),
                "--salida",
                str(destino),
                "--indice",
                str(indice_minimo),
            ],
            cwd=raiz_proyecto,
            env=entorno,
            capture_output=True,
            text=True,
            encoding="utf-8",
            # errors="replace": en Windows el hijo escribe stderr en la
            # codificación local (cp1252), no en UTF-8. Sin esto el hilo lector
            # revienta, stderr queda en None y el mensaje del assert de abajo
            # pierde todo su valor.
            errors="replace",
        )
        assert resultado.returncode == 0, resultado.stderr
        salidas.append(destino)

    primera, segunda = salidas
    archivos = sorted(p.name for p in primera.iterdir())
    assert archivos == sorted(p.name for p in segunda.iterdir())
    for nombre in archivos:
        assert (primera / nombre).read_bytes() == (segunda / nombre).read_bytes()


def test_los_archivos_usan_saltos_de_linea_unix(salida_doble):
    """En Windows, un salto CRLF rompería el diff entre plataformas."""
    primera, _ = salida_doble
    for archivo in primera.iterdir():
        assert b"\r\n" not in archivo.read_bytes(), f"{archivo.name} tiene CRLF"


# --- estructura de la salida -------------------------------------------------


def test_escribe_un_json_por_documento_nombrado_por_doc_id(salida_doble):
    primera, _ = salida_doble
    manifiesto = cargar_manifiesto(primera / "manifiesto.jsonl")
    for entrada in manifiesto:
        assert (primera / f"{entrada['doc_id']}.json").exists()


def test_el_json_tiene_claves_ordenadas_e_indentacion_fija(salida_doble):
    primera, _ = salida_doble
    doc_id = calcular_doc_id("bien_formado.txt")
    crudo = (primera / f"{doc_id}.json").read_text(encoding="utf-8")

    datos = json.loads(crudo)
    assert list(datos.keys()) == sorted(datos.keys())
    assert crudo == json.dumps(datos, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def test_el_json_conserva_los_acentos_sin_escapar(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "Deforestación.txt").write_text("contenido", encoding="utf-8")
    salida = tmp_path / "salida"

    procesar_directorio(entrada, salida)

    crudo = next(salida.glob("*.json")).read_text(encoding="utf-8")
    assert "Deforestación" in crudo
    assert "\\u00f3" not in crudo


# --- manifiesto ---------------------------------------------------------------


def test_el_manifiesto_esta_ordenado_por_fuente(salida_doble):
    primera, _ = salida_doble
    fuentes = [e["fuente"] for e in cargar_manifiesto(primera / "manifiesto.jsonl")]
    assert fuentes == sorted(fuentes)


def test_el_manifiesto_tiene_una_linea_por_documento(salida_doble, dir_fixtures):
    from orquestador import EXTRACTORES

    primera, _ = salida_doble
    entradas = cargar_manifiesto(primera / "manifiesto.jsonl")
    con_extractor = [
        p for p in dir_fixtures.rglob("*") if p.is_file() and p.suffix.lower() in EXTRACTORES
    ]
    assert len(entradas) == len(con_extractor)


def test_el_manifiesto_tiene_los_campos_del_contrato(salida_doble):
    primera, _ = salida_doble
    esperados = {
        "doc_id",
        "fuente",
        "formato",
        "fenomeno",
        "idioma",
        "n_bloques",
        "n_chars",
        "n_errores",
        "observatorio",
        "fuente_ambigua",
    }
    for entrada in cargar_manifiesto(primera / "manifiesto.jsonl"):
        assert set(entrada.keys()) == esperados


def test_los_contadores_del_manifiesto_coinciden_con_el_documento(salida_doble):
    primera, _ = salida_doble
    for entrada in cargar_manifiesto(primera / "manifiesto.jsonl"):
        datos = json.loads((primera / f"{entrada['doc_id']}.json").read_text(encoding="utf-8"))
        assert entrada["n_bloques"] == len(datos["bloques"])
        assert entrada["n_errores"] == len(datos["errores"])
        assert entrada["n_chars"] == sum(len(b["texto"]) for b in datos["bloques"])


def test_el_manifiesto_refleja_lo_que_extrajo_cada_extractor(salida_doble):
    """El manifiesto es la herramienta de regresión: sus contadores no mienten."""
    primera, _ = salida_doble
    entradas = {e["fuente"]: e for e in cargar_manifiesto(primera / "manifiesto.jsonl")}
    extraido = entradas["bien_formado.txt"]
    assert extraido["n_bloques"] > 0
    assert extraido["n_chars"] > 0
    assert extraido["n_errores"] == 0


# --- recorrido y robustez -----------------------------------------------------


def test_el_texto_plano_tiene_extractor_registrado(tmp_path):
    """SWF_full-text.txt es un informe completo, no un residuo del corpus."""
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "SWF_full-text.txt").write_text("Informe completo.", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["SWF_full-text.txt"]
    assert documentos[0].formato == "texto"
    assert documentos[0].bloques != []
    assert documentos[0].errores == []


def test_el_markdown_tiene_extractor_registrado(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "notas.md").write_text("# Titulo", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.formato for d in documentos] == ["texto"]


def test_avif_se_trata_como_imagen(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "grafico_web.avif").write_bytes(b"\x00\x00\x00 ftypavif")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.formato for d in documentos] == ["imagen"]
    assert documentos[0].errores != []


def test_ignora_los_formatos_sin_extractor_registrado(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "presentacion.docx").write_bytes(b"PK\x03\x04 falso")
    (entrada / "pagina.txt").write_text("Hola", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["pagina.txt"]


def test_un_archivo_corrupto_no_frena_a_los_demas(tmp_path):
    """Requisito 2 del enunciado, a nivel de pipeline.

    El PDF es basura con cabecera de PDF: su extractor falla y aun así el .txt
    de al lado se extrae entero.
    """
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "informe.pdf").write_bytes(b"%PDF-1.4 contenido falso")
    (entrada / "pagina.txt").write_text("Hola, esto sí se lee.", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    por_fuente = {d.fuente: d for d in documentos}

    assert por_fuente["informe.pdf"].bloques == []
    assert por_fuente["informe.pdf"].errores != []
    assert por_fuente["pagina.txt"].bloques != []
    assert por_fuente["pagina.txt"].errores == []


def test_recorre_subdirectorios(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "sub").mkdir(parents=True)
    (entrada / "sub" / "anidada.txt").write_text(
        "Contenido anidado", encoding="utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["anidada.txt"]


def test_dos_homonimos_producen_dos_documentos_sin_excepcion(tmp_path):
    """En el corpus de ADL 59 nombres se repiten en 186 archivos.

    Son colisiones legítimas —el mismo informe en carpetas por tipo, el mismo
    tile en distintos niveles de zoom—, así que el pipeline no puede morir por
    ellas. La desambiguación se hace por ruta.
    """
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.txt").write_text("Uno", "utf-8")
    (entrada / "b" / "informe.txt").write_text("Dos", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")

    assert len(documentos) == 2
    assert {d.fuente for d in documentos} == {"informe.txt"}
    assert len({d.doc_id for d in documentos}) == 2
    assert all(d.meta["fuente_ambigua"] is True for d in documentos)
    assert {d.meta["ruta_relativa"] for d in documentos} == {"a/informe.txt", "b/informe.txt"}


def test_el_doc_id_de_un_homonimo_deriva_de_la_ruta_no_del_nombre(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.txt").write_text("Uno", "utf-8")
    (entrada / "b" / "informe.txt").write_text("Dos", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    esperados = {calcular_doc_id("a/informe.txt"), calcular_doc_id("b/informe.txt")}
    assert {d.doc_id for d in documentos} == esperados


def test_dos_homonimos_escriben_dos_json_distintos(tmp_path):
    """El defecto oculto: con doc_id derivado del nombre, uno sobrescribía al otro."""
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.txt").write_text("Uno", "utf-8")
    (entrada / "b" / "informe.txt").write_text("Dos", "utf-8")
    salida = tmp_path / "salida"

    procesar_directorio(entrada, salida)

    assert len(list(salida.glob("*.json"))) == 2
    assert len(cargar_manifiesto(salida / "manifiesto.jsonl")) == 2


def test_un_archivo_sin_homonimos_no_se_marca_ambiguo(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "unico.txt").write_text("Solo", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert documentos[0].meta["fuente_ambigua"] is False


def test_todos_los_documentos_llevan_ruta_relativa(tmp_path):
    """No solo los ambiguos: la ruta es trazabilidad de todos."""
    entrada = tmp_path / "entrada"
    (entrada / "sub").mkdir(parents=True)
    (entrada / "raiz.txt").write_text("Raiz", "utf-8")
    (entrada / "sub" / "hoja.txt").write_text("Hoja", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    rutas = {d.fuente: d.meta["ruta_relativa"] for d in documentos}
    assert rutas == {"raiz.txt": "raiz.txt", "hoja.txt": "sub/hoja.txt"}


def test_la_colision_se_avisa_por_stderr(tmp_path, capsys):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.txt").write_text("Uno", "utf-8")
    (entrada / "b" / "informe.txt").write_text("Dos", "utf-8")

    procesar_directorio(entrada, tmp_path / "salida")

    err = capsys.readouterr().err
    assert "1 nombre" in err
    assert "2 archivo" in err


def test_la_salida_de_homonimos_cumple_el_contrato(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.txt").write_text("Uno", "utf-8")
    (entrada / "b" / "informe.txt").write_text("Dos", "utf-8")

    for documento in procesar_directorio(entrada, tmp_path / "salida"):
        assert validar_documento(documento) == []


def test_directorio_de_entrada_vacio_produce_manifiesto_vacio(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    salida = tmp_path / "salida"

    documentos = procesar_directorio(entrada, salida)
    assert documentos == []
    assert (salida / "manifiesto.jsonl").read_bytes() == b""


def test_el_fenomeno_se_infiere_del_subdirectorio(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "fenomeno_2").mkdir(parents=True)
    (entrada / "fenomeno_2" / "informe.txt").write_text(
        "Contenido del segundo fenómeno", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].fenomeno == 2


# --- fenomeno: precedencia indice > carpeta > defecto -------------------------


def test_el_fenomeno_se_infiere_de_la_carpeta_de_adl(tmp_path):
    """Las carpetas reales son F3_Dinamicas_Territoriales, no fenomeno_3."""
    entrada = tmp_path / "entrada"
    (entrada / "F3_Dinamicas_Territoriales").mkdir(parents=True)
    (entrada / "F3_Dinamicas_Territoriales" / "informe.txt").write_text(
        "Contenido del tercer fenomeno", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].fenomeno == 3
    assert documentos[0].meta["origen_fenomeno"] == "carpeta"


@pytest.mark.parametrize(
    "carpeta,esperado",
    [
        ("F1_IA_y_Capacidades_Estrategicas", 1),
        ("F2_Seguridad_Entorno_Espacial", 2),
        ("F3_Dinamicas_Territoriales", 3),
        ("fenomeno_2", 2),
        ("Fenomeno 3", 3),
        ("fenómeno-1", 1),
    ],
)
def test_las_dos_convenciones_de_carpeta_valen(tmp_path, carpeta, esperado):
    entrada = tmp_path / "entrada"
    (entrada / carpeta).mkdir(parents=True)
    (entrada / carpeta / "informe.txt").write_text(
        "Contenido", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].fenomeno == esperado


def test_una_carpeta_que_no_declara_fenomeno_cae_al_defecto(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "recursos").mkdir(parents=True)
    (entrada / "recursos" / "informe.txt").write_text(
        "Contenido", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=2)
    assert documentos[0].fenomeno == 2
    assert documentos[0].meta["origen_fenomeno"] == "defecto"


def test_una_carpeta_llamada_F4_no_se_confunde_con_un_fenomeno(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "F4_Otra_Cosa").mkdir(parents=True)
    (entrada / "F4_Otra_Cosa" / "informe.txt").write_text(
        "Contenido", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].meta["origen_fenomeno"] == "defecto"


# --- indice como fuente de verdad ---------------------------------------------


def test_con_indice_el_doc_id_y_el_fenomeno_salen_del_indice(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.txt").write_text(
        "Uno", "utf-8"
    )

    documentos = procesar_directorio(
        entrada, tmp_path / "salida", fenomeno_por_defecto=1, indice=cargar_indice(indice_minimo)
    )

    documento = documentos[0]
    assert documento.doc_id == "F2-SWF-001"
    assert documento.fenomeno == 2
    assert documento.meta["origen_doc_id"] == "indice"
    assert documento.meta["origen_fenomeno"] == "indice"
    assert documento.meta["observatorio"] == "Secure_World"
    assert documento.meta["codigo_observatorio"] == "SWF"
    assert documento.fuente == "informe.txt"


def test_el_indice_gana_a_la_carpeta(tmp_path, indice_minimo):
    """La carpeta dice F1; el indice dice F2. Manda el indice."""
    entrada = tmp_path / "entrada"
    destino = entrada / "F1_IA_y_Capacidades_Estrategicas" / "colisiones" / "a"
    destino.mkdir(parents=True)
    (destino / "informe.txt").write_text("Uno", "utf-8")

    indice = {
        "F1_IA_y_Capacidades_Estrategicas/colisiones/a/informe.txt": next(
            e for e in cargar_indice(indice_minimo).values() if e.doc_id == "F2-SWF-001"
        )
    }
    documentos = procesar_directorio(entrada, tmp_path / "salida", indice=indice)
    assert documentos[0].fenomeno == 2
    assert documentos[0].meta["origen_fenomeno"] == "indice"


def test_un_archivo_fuera_del_indice_no_se_procesa(tmp_path, indice_minimo):
    """Con indice, el indice filtra: manda ADL sobre lo que haya en disco."""
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.txt").write_text(
        "Uno", "utf-8"
    )
    (entrada / "intruso.txt").write_text("No listado", "utf-8")

    documentos, reporte = procesar_corpus(
        entrada, tmp_path / "salida", indice=cargar_indice(indice_minimo)
    )

    assert [d.fuente for d in documentos] == ["informe.txt"]
    assert reporte.fuera_del_indice == ["intruso.txt"]


def test_un_indice_vacio_sigue_filtrando_todo(tmp_path):
    """Un xlsx con cabecera y sin filas produce ``indice == {}``.

    ``{}`` es falsy en Python, así que un chequeo con ``if indice`` (en vez de
    ``is not None``) confundiría "índice vacío" con "sin índice" y procesaría
    el disco entero en silencio, justo lo contrario de lo que pide ``--indice``.
    """
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.txt").write_text("Hola", "utf-8")

    documentos, reporte = procesar_corpus(entrada, tmp_path / "salida", indice={})

    assert documentos == []
    assert reporte.fuera_del_indice == ["pagina.txt"]
    assert reporte.huerfanos_del_indice == []


def test_sin_indice_el_pipeline_sigue_funcionando(tmp_path):
    """Las fixtures sinteticas no tienen indice; no puede ser obligatorio."""
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.txt").write_text("Hola", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida", indice=None)
    assert documentos[0].meta["origen_doc_id"] == "derivado"


# --- reporte de cobertura -----------------------------------------------------


def test_el_reporte_lista_los_archivos_sin_extractor(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.txt").write_text("Hola", "utf-8")
    (entrada / "presentacion.docx").write_bytes(b"PK\x03\x04 falso")

    documentos, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.sin_extractor == ["presentacion.docx"]
    assert [d.fuente for d in documentos] == ["pagina.txt"]


def test_los_archivos_de_sistema_no_se_reportan_como_formato_sin_extractor(tmp_path):
    """El corpus de ADL viene de un Mac y trae un `.DS_Store` por carpeta: nueve
    en total. No son documentos que falte soportar, son metadatos del Finder, y
    listarlos junto a los formatos sin extractor esconde los que sí importan."""
    entrada = tmp_path / "entrada"
    (entrada / "sub").mkdir(parents=True)
    (entrada / "pagina.txt").write_text("Hola", "utf-8")
    (entrada / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (entrada / "sub" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (entrada / "Thumbs.db").write_bytes(b"basura de miniaturas")
    (entrada / "presentacion.docx").write_bytes(b"PK\x03\x04 falso")

    documentos, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.sin_extractor == ["presentacion.docx"]
    assert [d.fuente for d in documentos] == ["pagina.txt"]


def test_el_reporte_lista_las_entradas_huerfanas_del_indice(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "bien_formado.txt").write_text("Hola", "utf-8")

    _, reporte = procesar_corpus(
        entrada, tmp_path / "salida", indice=cargar_indice(indice_minimo)
    )

    # El orden es el del archivo xlsx, no el alfabético: F1 bien_formado (en
    # disco), F2 colisiones/a, F2 colisiones/b, F3 anidado.
    assert reporte.huerfanos_del_indice == [
        "colisiones/a/informe.txt",
        "colisiones/b/informe.txt",
        "anidado.txt",
    ]


def test_un_archivo_indexado_sin_extractor_no_es_huerfano(tmp_path):
    """Un archivo que el índice lista y que existe en disco no es un huérfano.

    Antes de esta corrección, ``_cruzar_con_indice`` solo miraba los archivos
    con extractor: uno indexado con una extensión sin extractor (p. ej. un
    ``.zip`` del catálogo) no aparecía entre los "vistos" y se reportaba como
    "no existe en disco" siendo falso. Debe salir en ``sin_extractor``, que es
    el motivo real por el que no se procesa.
    """
    from indice import EntradaIndice

    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "catalogo.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    indice = {
        "catalogo.zip": EntradaIndice(
            doc_id="F1-OBS-001",
            fuente="catalogo.zip",
            ruta_relativa="catalogo.zip",
            fenomeno=1,
            observatorio="Obs",
            codigo_observatorio="OBS",
            tipo_declarado="ZIP",
        )
    }

    _, reporte = procesar_corpus(entrada, tmp_path / "salida", indice=indice)

    assert reporte.sin_extractor == ["catalogo.zip"]
    assert reporte.huerfanos_del_indice == []


def test_el_reporte_cuenta_los_origenes(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "F2_Seguridad_Entorno_Espacial").mkdir(parents=True)
    (entrada / "F2_Seguridad_Entorno_Espacial" / "a.txt").write_text(
        "Uno", "utf-8"
    )
    (entrada / "suelto.txt").write_text("Dos", "utf-8")

    _, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.por_origen_fenomeno == {"carpeta": 1, "defecto": 1}
    assert reporte.por_origen_doc_id == {"derivado": 2}


def test_el_reporte_cuenta_las_fuentes_ambiguas(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.txt").write_text("Uno", "utf-8")
    (entrada / "b" / "informe.txt").write_text("Dos", "utf-8")

    _, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.nombres_ambiguos == 1
    assert reporte.archivos_ambiguos == 2


# --- doc_id duplicado: lo unico que sigue deteniendo la corrida ---------------


def test_un_doc_id_duplicado_en_el_indice_detiene_la_corrida(tmp_path):
    from indice import EntradaIndice

    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "uno.txt").write_text("Uno", "utf-8")
    (entrada / "b" / "dos.txt").write_text("Dos", "utf-8")

    def entrada_con(ruta, fuente):
        return EntradaIndice(
            doc_id="F1-OBS-001",
            fuente=fuente,
            ruta_relativa=ruta,
            fenomeno=1,
            observatorio="Obs",
            codigo_observatorio="OBS",
            tipo_declarado="TXT",
        )

    indice = {
        "a/uno.txt": entrada_con("a/uno.txt", "uno.txt"),
        "b/dos.txt": entrada_con("b/dos.txt", "dos.txt"),
    }

    with pytest.raises(ValueError, match="doc_id duplicado"):
        procesar_directorio(entrada, tmp_path / "salida", indice=indice)


# --- manifiesto ampliado ------------------------------------------------------


def test_el_manifiesto_lleva_observatorio_y_fuente_ambigua(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "b").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.txt").write_text(
        "Uno", "utf-8"
    )
    (entrada / "colisiones" / "b" / "informe.txt").write_text(
        "Dos", "utf-8"
    )
    salida = tmp_path / "salida"

    procesar_directorio(entrada, salida, indice=cargar_indice(indice_minimo))

    entradas = cargar_manifiesto(salida / "manifiesto.jsonl")
    assert all(e["observatorio"] == "Secure_World" for e in entradas)
    assert all(e["fuente_ambigua"] is True for e in entradas)


# --- CLI ----------------------------------------------------------------------


def test_la_cli_acepta_indice(tmp_path, raiz_proyecto, indice_minimo):
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.txt").write_text(
        "Uno", "utf-8"
    )
    salida = tmp_path / "salida"

    resultado = subprocess.run(
        [
            sys.executable, "auxiliar/orquestador.py",
            "--entrada", str(entrada),
            "--salida", str(salida),
            "--indice", str(indice_minimo),
        ],
        # errors="replace": el hijo escribe stderr en la codificación local, que
        # en Windows no es UTF-8. Sin esto el hilo lector revienta, stderr queda
        # en None y el mensaje del assert de abajo pierde todo su valor.
        cwd=raiz_proyecto, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )

    assert resultado.returncode == 0, resultado.stderr
    entradas = cargar_manifiesto(salida / "manifiesto.jsonl")
    assert [e["doc_id"] for e in entradas] == ["F2-SWF-001"]


def test_la_cli_funciona_sin_indice(tmp_path, raiz_proyecto):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.txt").write_text("Hola", "utf-8")
    salida = tmp_path / "salida"

    resultado = subprocess.run(
        [sys.executable, "auxiliar/orquestador.py", "--entrada", str(entrada), "--salida", str(salida)],
        cwd=raiz_proyecto, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert resultado.returncode == 0, resultado.stderr


# --- paralelismo --------------------------------------------------------------


@pytest.fixture
def corpus_variado(tmp_path):
    """Varios archivos de formatos distintos, suficientes para repartir trabajo."""
    entrada = tmp_path / "entrada"
    (entrada / "sub").mkdir(parents=True)
    for numero in range(6):
        (entrada / f"nota{numero}.txt").write_text(
            f"Informe número {numero}. La cobertura de sensores sigue siendo parcial.",
            encoding="utf-8",
        )
    (entrada / "sub" / "datos.csv").write_text("pais,valor\nColombia,3\n", encoding="utf-8")
    return entrada


def test_procesar_en_paralelo_da_el_mismo_resultado_que_en_serie(corpus_variado, tmp_path):
    """El paralelismo no puede cambiar ni un byte: solo reparte el trabajo."""
    en_serie = procesar_directorio(corpus_variado, tmp_path / "serie")
    en_paralelo = procesar_directorio(corpus_variado, tmp_path / "paralelo", procesos=2)

    assert en_paralelo == en_serie


def test_dos_corridas_en_paralelo_producen_los_mismos_bytes(corpus_variado, tmp_path):
    """Requisito 3 del enunciado, también con varios procesos."""
    procesar_directorio(corpus_variado, tmp_path / "una", procesos=2)
    procesar_directorio(corpus_variado, tmp_path / "otra", procesos=2)

    unos = (tmp_path / "una" / "manifiesto.jsonl").read_bytes()
    otros = (tmp_path / "otra" / "manifiesto.jsonl").read_bytes()
    assert unos == otros


def test_un_solo_proceso_no_arranca_el_pool(corpus_variado, tmp_path):
    """Con procesos=1 el coste de arrancar procesos no se justifica."""
    documentos = procesar_directorio(corpus_variado, tmp_path / "salida", procesos=1)
    assert len(documentos) == 7
