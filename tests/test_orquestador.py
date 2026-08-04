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
                "orquestador.py",
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
                "orquestador.py",
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
    doc_id = calcular_doc_id("bien_formado.html")
    crudo = (primera / f"{doc_id}.json").read_text(encoding="utf-8")

    datos = json.loads(crudo)
    assert list(datos.keys()) == sorted(datos.keys())
    assert crudo == json.dumps(datos, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def test_el_json_conserva_los_acentos_sin_escapar(salida_doble):
    primera, _ = salida_doble
    doc_id = calcular_doc_id("bien_formado.html")
    crudo = (primera / f"{doc_id}.json").read_text(encoding="utf-8")
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


def test_el_manifiesto_registra_el_documento_corrupto(salida_doble):
    primera, _ = salida_doble
    entradas = {e["fuente"]: e for e in cargar_manifiesto(primera / "manifiesto.jsonl")}
    corrupto = entradas["malformado.html"]
    assert corrupto["n_bloques"] == 0
    assert corrupto["n_errores"] > 0


# --- recorrido y robustez -----------------------------------------------------


def test_el_texto_plano_tiene_extractor_registrado(tmp_path):
    """SWF_full-text.txt es un informe completo, no un residuo del corpus."""
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "SWF_full-text.txt").write_text("Informe completo.", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["SWF_full-text.txt"]
    assert documentos[0].formato == "texto"
    assert documentos[0].bloques == []
    assert documentos[0].errores != []


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
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["pagina.html"]


def test_un_formato_aun_no_implementado_no_tumba_el_pipeline(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "informe.pdf").write_bytes(b"%PDF-1.4 contenido falso")
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    por_fuente = {d.fuente: d for d in documentos}
    assert por_fuente["informe.pdf"].bloques == []
    assert por_fuente["informe.pdf"].errores != []
    assert por_fuente["pagina.html"].bloques != []


def test_recorre_subdirectorios(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "sub").mkdir(parents=True)
    (entrada / "sub" / "anidada.html").write_text(
        "<html><body><p>Contenido anidado</p></body></html>", encoding="utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["anidada.html"]


def test_dos_homonimos_producen_dos_documentos_sin_excepcion(tmp_path):
    """En el corpus de ADL 59 nombres se repiten en 186 archivos.

    Son colisiones legítimas —el mismo informe en carpetas por tipo, el mismo
    tile en distintos niveles de zoom—, así que el pipeline no puede morir por
    ellas. La desambiguación se hace por ruta.
    """
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")

    assert len(documentos) == 2
    assert {d.fuente for d in documentos} == {"informe.html"}
    assert len({d.doc_id for d in documentos}) == 2
    assert all(d.meta["fuente_ambigua"] is True for d in documentos)
    assert {d.meta["ruta_relativa"] for d in documentos} == {"a/informe.html", "b/informe.html"}


def test_el_doc_id_de_un_homonimo_deriva_de_la_ruta_no_del_nombre(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    esperados = {calcular_doc_id("a/informe.html"), calcular_doc_id("b/informe.html")}
    assert {d.doc_id for d in documentos} == esperados


def test_dos_homonimos_escriben_dos_json_distintos(tmp_path):
    """El defecto oculto: con doc_id derivado del nombre, uno sobrescribía al otro."""
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")
    salida = tmp_path / "salida"

    documentos = procesar_directorio(entrada, salida)

    assert len(list(salida.glob("*.json"))) == 2
    assert len(cargar_manifiesto(salida / "manifiesto.jsonl")) == 2


def test_un_archivo_sin_homonimos_no_se_marca_ambiguo(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "unico.html").write_text("<html><body><p>Solo</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert documentos[0].meta["fuente_ambigua"] is False


def test_todos_los_documentos_llevan_ruta_relativa(tmp_path):
    """No solo los ambiguos: la ruta es trazabilidad de todos."""
    entrada = tmp_path / "entrada"
    (entrada / "sub").mkdir(parents=True)
    (entrada / "raiz.html").write_text("<html><body><p>Raiz</p></body></html>", "utf-8")
    (entrada / "sub" / "hoja.html").write_text("<html><body><p>Hoja</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    rutas = {d.fuente: d.meta["ruta_relativa"] for d in documentos}
    assert rutas == {"raiz.html": "raiz.html", "hoja.html": "sub/hoja.html"}


def test_la_colision_se_avisa_por_stderr(tmp_path, capsys):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    procesar_directorio(entrada, tmp_path / "salida")

    err = capsys.readouterr().err
    assert "1 nombre" in err
    assert "2 archivo" in err


def test_la_salida_de_homonimos_cumple_el_contrato(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

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
    (entrada / "fenomeno_2" / "informe.html").write_text(
        "<html><body><p>Contenido del segundo fenómeno</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].fenomeno == 2


# --- fenomeno: precedencia indice > carpeta > defecto -------------------------


def test_el_fenomeno_se_infiere_de_la_carpeta_de_adl(tmp_path):
    """Las carpetas reales son F3_Dinamicas_Territoriales, no fenomeno_3."""
    entrada = tmp_path / "entrada"
    (entrada / "F3_Dinamicas_Territoriales").mkdir(parents=True)
    (entrada / "F3_Dinamicas_Territoriales" / "informe.html").write_text(
        "<html><body><p>Contenido del tercer fenomeno</p></body></html>", "utf-8"
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
    (entrada / carpeta / "informe.html").write_text(
        "<html><body><p>Contenido</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].fenomeno == esperado


def test_una_carpeta_que_no_declara_fenomeno_cae_al_defecto(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "recursos").mkdir(parents=True)
    (entrada / "recursos" / "informe.html").write_text(
        "<html><body><p>Contenido</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=2)
    assert documentos[0].fenomeno == 2
    assert documentos[0].meta["origen_fenomeno"] == "defecto"


def test_una_carpeta_llamada_F4_no_se_confunde_con_un_fenomeno(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "F4_Otra_Cosa").mkdir(parents=True)
    (entrada / "F4_Otra_Cosa" / "informe.html").write_text(
        "<html><body><p>Contenido</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].meta["origen_fenomeno"] == "defecto"


# --- indice como fuente de verdad ---------------------------------------------


def test_con_indice_el_doc_id_y_el_fenomeno_salen_del_indice(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
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
    assert documento.fuente == "informe.html"


def test_el_indice_gana_a_la_carpeta(tmp_path, indice_minimo):
    """La carpeta dice F1; el indice dice F2. Manda el indice."""
    entrada = tmp_path / "entrada"
    destino = entrada / "F1_IA_y_Capacidades_Estrategicas" / "colisiones" / "a"
    destino.mkdir(parents=True)
    (destino / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")

    indice = {
        "F1_IA_y_Capacidades_Estrategicas/colisiones/a/informe.html": next(
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
    (entrada / "colisiones" / "a" / "informe.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )
    (entrada / "intruso.html").write_text("<html><body><p>No listado</p></body></html>", "utf-8")

    documentos, reporte = procesar_corpus(
        entrada, tmp_path / "salida", indice=cargar_indice(indice_minimo)
    )

    assert [d.fuente for d in documentos] == ["informe.html"]
    assert reporte.fuera_del_indice == ["intruso.html"]


def test_un_indice_vacio_sigue_filtrando_todo(tmp_path):
    """Un xlsx con cabecera y sin filas produce ``indice == {}``.

    ``{}`` es falsy en Python, así que un chequeo con ``if indice`` (en vez de
    ``is not None``) confundiría "índice vacío" con "sin índice" y procesaría
    el disco entero en silencio, justo lo contrario de lo que pide ``--indice``.
    """
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")

    documentos, reporte = procesar_corpus(entrada, tmp_path / "salida", indice={})

    assert documentos == []
    assert reporte.fuera_del_indice == ["pagina.html"]
    assert reporte.huerfanos_del_indice == []


def test_sin_indice_el_pipeline_sigue_funcionando(tmp_path):
    """Las fixtures sinteticas no tienen indice; no puede ser obligatorio."""
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida", indice=None)
    assert documentos[0].meta["origen_doc_id"] == "derivado"


# --- reporte de cobertura -----------------------------------------------------


def test_el_reporte_lista_los_archivos_sin_extractor(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")
    (entrada / "presentacion.docx").write_bytes(b"PK\x03\x04 falso")

    documentos, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.sin_extractor == ["presentacion.docx"]
    assert [d.fuente for d in documentos] == ["pagina.html"]


def test_el_reporte_lista_las_entradas_huerfanas_del_indice(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "bien_formado.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")

    _, reporte = procesar_corpus(
        entrada, tmp_path / "salida", indice=cargar_indice(indice_minimo)
    )

    # El orden es el del archivo xlsx, no el alfabético: F1 bien_formado (en
    # disco), F2 colisiones/a, F2 colisiones/b, F3 anidado.
    assert reporte.huerfanos_del_indice == [
        "colisiones/a/informe.html",
        "colisiones/b/informe.html",
        "anidado.html",
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
    (entrada / "F2_Seguridad_Entorno_Espacial" / "a.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )
    (entrada / "suelto.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    _, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.por_origen_fenomeno == {"carpeta": 1, "defecto": 1}
    assert reporte.por_origen_doc_id == {"derivado": 2}


def test_el_reporte_cuenta_las_fuentes_ambiguas(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    _, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.nombres_ambiguos == 1
    assert reporte.archivos_ambiguos == 2


# --- doc_id duplicado: lo unico que sigue deteniendo la corrida ---------------


def test_un_doc_id_duplicado_en_el_indice_detiene_la_corrida(tmp_path):
    from indice import EntradaIndice

    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "uno.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "dos.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    def entrada_con(ruta, fuente):
        return EntradaIndice(
            doc_id="F1-OBS-001",
            fuente=fuente,
            ruta_relativa=ruta,
            fenomeno=1,
            observatorio="Obs",
            codigo_observatorio="OBS",
            tipo_declarado="HTML",
        )

    indice = {
        "a/uno.html": entrada_con("a/uno.html", "uno.html"),
        "b/dos.html": entrada_con("b/dos.html", "dos.html"),
    }

    with pytest.raises(ValueError, match="doc_id duplicado"):
        procesar_directorio(entrada, tmp_path / "salida", indice=indice)


# --- manifiesto ampliado ------------------------------------------------------


def test_el_manifiesto_lleva_observatorio_y_fuente_ambigua(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "b").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )
    (entrada / "colisiones" / "b" / "informe.html").write_text(
        "<html><body><p>Dos</p></body></html>", "utf-8"
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
    (entrada / "colisiones" / "a" / "informe.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )
    salida = tmp_path / "salida"

    resultado = subprocess.run(
        [
            sys.executable, "orquestador.py",
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
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")
    salida = tmp_path / "salida"

    resultado = subprocess.run(
        [sys.executable, "orquestador.py", "--entrada", str(entrada), "--salida", str(salida)],
        cwd=raiz_proyecto, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert resultado.returncode == 0, resultado.stderr
