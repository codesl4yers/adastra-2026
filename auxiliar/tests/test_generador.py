"""Pruebas del generador del índice vectorial.

El encoder real se inyecta: descargar 1,2 GB de pesos para comprobar que la
fila ``i`` del índice corresponde a la línea ``i`` de la metadata sería pagar
minutos por una propiedad que no depende del modelo. El codificador de prueba
es determinista y real —no un mock que devuelve lo que se le programa—, así que
las propiedades que verifica (orden, normalización, contenido de la metadata)
son las mismas que con granite. La prueba de integración con el checkpoint de
verdad vive en ``test_encoder_integracion.py``.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

faiss = pytest.importorskip("faiss")

from encoder import ConfigEncoder  # noqa: E402
from fragmentador import CONFIG_POR_DEFECTO as CONFIG_CHUNKER  # noqa: E402
from fragmentador import fragmentar, fragmento_a_dict  # noqa: E402
from generador import (  # noqa: E402
    NOMBRE_INDICE,
    TOP_FRAGMENTOS,
    NOMBRE_METADATA,
    NOMBRE_REPORTE,
    Candidato,
    Consulta,
    agregar_por_documento,
    cargar_consultas,
    deduplicar_por_texto,
    filtrar_por_idioma,
    generar_indice,
    mejores_fragmentos,
    responder_consultas,
)
from generador import _construir_parser  # noqa: E402

from conftest import bloque  # noqa: E402

DIMENSION_PRUEBA = 128
CONFIG_PRUEBA = ConfigEncoder(modelo="modelo-de-prueba", dimension=DIMENSION_PRUEBA)

CAMPOS_TABLA_1 = (
    "doc_id",
    "chunk_id",
    "fuente",
    "formato",
    "fenomeno",
    "posicion",
    "num_tokens",
    "texto",
)


def codificador(registro: list[list[str]] | None = None):
    """Codificador determinista: la suma de los bytes del texto por posición.

    Textos distintos dan vectores distintos y el mismo texto da siempre el
    mismo vector, que es todo lo que estas pruebas necesitan de un encoder.
    Si se le pasa ``registro``, apunta cada lote que recibe.
    """

    def codificar(textos: list[str]) -> np.ndarray:
        if registro is not None:
            registro.append(list(textos))
        filas = np.zeros((len(textos), DIMENSION_PRUEBA), dtype=np.float32)
        for fila, texto in enumerate(textos):
            for posicion, byte in enumerate(texto.encode("utf-8")):
                filas[fila, posicion % DIMENSION_PRUEBA] += byte
        return filas

    return codificar


def contar_palabras(texto: str) -> int:
    """Contador de tokens de juguete. El real es el de granite; aquí solo
    interesa que el generador use el que se le da y sepa contar truncamientos."""
    return len(texto.split())


def generar(entrada, salida, config=CONFIG_PRUEBA, codificar=None, contar_tokens=None):
    """``generar_indice`` con los dos colaboradores del encoder inyectados."""
    return generar_indice(
        entrada,
        salida,
        config,
        codificar=codificar or codificador(),
        contar_tokens=contar_tokens or contar_palabras,
    )


@pytest.fixture
def fragmentos_jsonl(tmp_path, documento_con_bloques):
    """Un ``chunks.jsonl`` real, salido del fragmentador."""
    documento = documento_con_bloques(
        bloque("Capacidades espaciales", "titulo", 1),
        bloque(
            "El observatorio publicó su informe anual. Registró catorce "
            "lanzamientos orbitales en la región. La cifra dobla la del año "
            "anterior.",
            ruta=["Capacidades espaciales"],
            pagina=1,
        ),
        bloque("Vigilancia", "titulo", 1),
        bloque(
            "La red de sensores cubre el arco sur. Los datos se publican con "
            "un retraso de tres días.",
            ruta=["Vigilancia"],
            pagina=2,
        ),
        meta={"ruta_relativa": "F2_Observatorio/informe.pdf", "observatorio": "SWF", "titulo": "Informe anual"},
    )
    fragmentos = fragmentar(documento, CONFIG_CHUNKER)
    assert len(fragmentos) >= 2, "la fixture necesita varios fragmentos"

    ruta = tmp_path / "chunks.jsonl"
    ruta.write_text(
        "".join(
            json.dumps(fragmento_a_dict(f), ensure_ascii=False, sort_keys=True) + "\n"
            for f in fragmentos
        ),
        encoding="utf-8",
        newline="\n",
    )
    return ruta


def leer_metadata(salida):
    return [
        json.loads(linea)
        for linea in (salida / NOMBRE_METADATA).read_text(encoding="utf-8").splitlines()
    ]


# --- correspondencia índice ↔ metadata -------------------------------------------


def test_hay_un_vector_por_fragmento(fragmentos_jsonl, tmp_path):
    salida = tmp_path / "indice"

    reporte = generar(fragmentos_jsonl, salida, codificar=codificador())

    indice = faiss.read_index(str(salida / NOMBRE_INDICE))
    esperados = len(fragmentos_jsonl.read_text(encoding="utf-8").splitlines())
    assert indice.ntotal == esperados
    assert reporte.n_vectores == esperados
    assert indice.d == DIMENSION_PRUEBA


def test_la_fila_del_indice_corresponde_a_la_linea_de_metadata(fragmentos_jsonl, tmp_path):
    """Sin esta correspondencia el índice devuelve el chunk_id equivocado, que
    es el peor fallo posible: la respuesta parece válida y no lo es."""
    salida = tmp_path / "indice"
    generar(fragmentos_jsonl, salida, codificar=codificador())

    origen = [json.loads(l) for l in fragmentos_jsonl.read_text(encoding="utf-8").splitlines()]
    metadata = leer_metadata(salida)

    assert [m["chunk_id"] for m in metadata] == [o["chunk_id"] for o in origen]


def test_la_metadata_lleva_los_ocho_campos_obligatorios(fragmentos_jsonl, tmp_path):
    salida = tmp_path / "indice"
    generar(fragmentos_jsonl, salida, codificar=codificador())

    for registro in leer_metadata(salida):
        assert all(campo in registro for campo in CAMPOS_TABLA_1)


def test_la_metadata_no_lleva_el_texto_enriquecido(fragmentos_jsonl, tmp_path):
    """§2.2 del spec: el prefijo de contexto entra al encoder, no a la salida
    que se reporta. Lo que el jurado ve es ``texto``, el original."""
    salida = tmp_path / "indice"
    generar(fragmentos_jsonl, salida, codificar=codificador())

    for registro in leer_metadata(salida):
        assert "texto_enriquecido" not in registro


# --- lo que se codifica ----------------------------------------------------------


def test_se_codifica_el_texto_enriquecido_y_no_el_texto(fragmentos_jsonl, tmp_path):
    """El enriquecimiento con breadcrumb y observatorio solo sirve de algo si
    llega al encoder. Aquí es donde se comprueba que llega."""
    lotes: list[list[str]] = []
    salida = tmp_path / "indice"

    generar(fragmentos_jsonl, salida, codificar=codificador(lotes))

    codificados = [texto for lote in lotes for texto in lote]
    origen = [json.loads(l) for l in fragmentos_jsonl.read_text(encoding="utf-8").splitlines()]
    assert codificados == [o["texto_enriquecido"] for o in origen]
    assert any(c != o["texto"] for c, o in zip(codificados, origen)), (
        "la fixture debería traer al menos un fragmento con prefijo de contexto"
    )


def test_el_prefijo_del_encoder_se_aplica_al_codificar(fragmentos_jsonl, tmp_path):
    """§14.1: si el modelo pide ``passage: ``, lo pone el encoder al codificar,
    no el chunker al escribir el JSONL."""
    lotes: list[list[str]] = []
    config = ConfigEncoder(
        modelo="modelo-asimetrico",
        dimension=DIMENSION_PRUEBA,
        prefijo_consulta="query: ",
        prefijo_fragmento="passage: ",
    )

    generar(fragmentos_jsonl, tmp_path / "indice", config, codificar=codificador(lotes))

    assert all(texto.startswith("passage: ") for lote in lotes for texto in lote)


def test_el_codificador_recibe_lotes_del_tamano_configurado(fragmentos_jsonl, tmp_path):
    lotes: list[list[str]] = []
    config = ConfigEncoder(modelo="m", dimension=DIMENSION_PRUEBA, lote=2)

    generar(fragmentos_jsonl, tmp_path / "indice", config, codificar=codificador(lotes))

    assert all(len(lote) <= 2 for lote in lotes)
    assert max(len(lote) for lote in lotes) == 2


# --- tamaño de lote por presupuesto de atención ----------------------------------
#
# ModernBERT materializa una máscara de atención de (lote, 1, L, L) en float32,
# con L la longitud del texto más largo del lote. El coste crece con el CUADRADO
# de L y multiplicado por el lote entero: un fragmento de 8 200 tokens en un lote
# de 32 pide 8,67 GB de una sola vez y revienta una GPU de 6 GB. Contar textos no
# acota nada; hay que acotar lote x L².


def test_un_lote_de_textos_cortos_llega_al_tope_de_textos():
    from generador import lotes_por_presupuesto

    longitudes = [100] * 10
    config = ConfigEncoder(modelo="m", lote=4, presupuesto_atencion=10_000_000)

    lotes = list(lotes_por_presupuesto(longitudes, config))

    assert [len(l) for l in lotes] == [4, 4, 2]


def test_un_texto_largo_encoge_su_lote():
    """1 000 tokens: 4 x 1000² = 4 M cabe en el presupuesto, 5 no."""
    from generador import lotes_por_presupuesto

    config = ConfigEncoder(modelo="m", lote=32, presupuesto_atencion=4_000_000)

    lotes = list(lotes_por_presupuesto([1000] * 8, config))

    assert all(len(l) <= 4 for l in lotes)


def test_un_fragmento_gigante_viaja_solo():
    """El caso de F1-AIINDEX-041-c0401: 17 803 tokens. Debe ir en su propio
    lote aunque el presupuesto no le alcance, porque no se puede partir."""
    from generador import lotes_por_presupuesto

    config = ConfigEncoder(modelo="m", lote=32, presupuesto_atencion=128_000_000)

    lotes = list(lotes_por_presupuesto([120, 17_803, 120], config))

    assert [len(l) for l in lotes] == [1, 1, 1]
    assert lotes[1] == [1]


def test_los_lotes_conservan_el_orden_y_no_pierden_nada():
    """El orden es el del archivo: la fila i del índice tiene que seguir siendo
    la línea i de la metadata."""
    from generador import lotes_por_presupuesto

    longitudes = [100, 5000, 100, 100, 9000, 100]
    config = ConfigEncoder(modelo="m", lote=8, presupuesto_atencion=50_000_000)

    indices = [i for lote in lotes_por_presupuesto(longitudes, config) for i in lote]

    assert indices == list(range(len(longitudes)))


def test_el_presupuesto_por_defecto_admite_un_fragmento_de_la_ventana_completa():
    """Con la configuración de fábrica, ningún lote debe superar la memoria de
    una GPU de 6 GB: dos máscaras de lote x L² x 4 bytes."""
    from encoder import CONFIG_POR_DEFECTO
    from generador import lotes_por_presupuesto

    longitudes = [450] * 100 + [17_803] + [450] * 100
    peor = 0
    for lote in lotes_por_presupuesto(longitudes, CONFIG_POR_DEFECTO):
        largo = max(longitudes[i] for i in lote)
        peor = max(peor, 2 * len(lote) * largo * largo * 4)

    assert peor < 3 * 1024**3  # 3 GB, con el modelo y las activaciones aparte


# --- propiedades del índice ------------------------------------------------------


def test_los_vectores_del_indice_estan_normalizados(fragmentos_jsonl, tmp_path):
    """§5.2 del enunciado: IndexFlatIP con vectores normalizados es lo que hace
    que el producto interno sea el coseno."""
    salida = tmp_path / "indice"
    generar(fragmentos_jsonl, salida, codificar=codificador())

    indice = faiss.read_index(str(salida / NOMBRE_INDICE))
    vectores = indice.reconstruct_n(0, indice.ntotal)

    assert np.allclose(np.linalg.norm(vectores, axis=1), 1.0, atol=1e-5)


def test_el_indice_es_plano_de_producto_interno(fragmentos_jsonl, tmp_path):
    salida = tmp_path / "indice"
    generar(fragmentos_jsonl, salida, codificar=codificador())

    indice = faiss.read_index(str(salida / NOMBRE_INDICE))

    assert indice.metric_type == faiss.METRIC_INNER_PRODUCT
    assert isinstance(indice, faiss.IndexFlat)


def test_un_fragmento_se_recupera_a_si_mismo(fragmentos_jsonl, tmp_path):
    """Prueba de humo de punta a punta: consultar con el vector de un fragmento
    devuelve ese fragmento en primer lugar, con similitud 1."""
    salida = tmp_path / "indice"
    generar(fragmentos_jsonl, salida, codificar=codificador())

    indice = faiss.read_index(str(salida / NOMBRE_INDICE))
    consulta = indice.reconstruct_n(1, 1)
    similitudes, posiciones = indice.search(consulta, 1)

    assert posiciones[0][0] == 1
    assert similitudes[0][0] == pytest.approx(1.0, abs=1e-5)


def test_dos_corridas_producen_el_mismo_indice(fragmentos_jsonl, tmp_path):
    """§1.4 del enunciado: el jurado tiene que poder reproducir el índice."""
    primera, segunda = tmp_path / "a", tmp_path / "b"

    generar(fragmentos_jsonl, primera, codificar=codificador())
    generar(fragmentos_jsonl, segunda, codificar=codificador())

    assert (primera / NOMBRE_INDICE).read_bytes() == (segunda / NOMBRE_INDICE).read_bytes()
    assert (primera / NOMBRE_METADATA).read_bytes() == (segunda / NOMBRE_METADATA).read_bytes()


# --- reporte y truncamiento (§14.3) ----------------------------------------------


def test_el_reporte_registra_la_identidad_del_encoder(fragmentos_jsonl, tmp_path):
    """El informe técnico tiene que poder citar con qué se construyó el índice."""
    salida = tmp_path / "indice"

    reporte = generar(fragmentos_jsonl, salida, codificar=codificador())

    assert reporte.modelo == "modelo-de-prueba"
    assert reporte.dimension == DIMENSION_PRUEBA
    assert reporte.pooling == "cls"
    escrito = json.loads((salida / NOMBRE_REPORTE).read_text(encoding="utf-8"))
    assert escrito["modelo"] == "modelo-de-prueba"


def test_sin_textos_largos_no_hay_truncamientos(fragmentos_jsonl, tmp_path):
    reporte = generar(fragmentos_jsonl, tmp_path / "indice")

    assert reporte.n_truncados == 0


def test_un_fragmento_mas_largo_que_la_ventana_se_cuenta_como_truncado(
    fragmentos_jsonl, tmp_path
):
    """Truncar es el fallo silencioso de §14.3: el vector sale igual y el
    fragmento queda indexado a medias. Si no se cuenta, nadie se entera."""
    config = ConfigEncoder(modelo="m", dimension=DIMENSION_PRUEBA)

    reporte = generar(
        fragmentos_jsonl,
        tmp_path / "indice",
        config,
        contar_tokens=lambda texto: config.ventana_modelo + 1,
    )

    assert reporte.n_truncados == reporte.n_vectores


def test_el_reporte_verifica_la_norma_unitaria(fragmentos_jsonl, tmp_path):
    """La comprobación de §14.2 tiene que quedar por escrito en el reporte:
    es la evidencia de que el producto interno del índice es el coseno."""
    reporte = generar(fragmentos_jsonl, tmp_path / "indice")

    assert reporte.norma_min == pytest.approx(1.0, abs=1e-5)
    assert reporte.norma_max == pytest.approx(1.0, abs=1e-5)


def test_el_reporte_desglosa_por_fenomeno_y_formato(fragmentos_jsonl, tmp_path):
    reporte = generar(fragmentos_jsonl, tmp_path / "indice")

    assert sum(reporte.vectores_por_fenomeno.values()) == reporte.n_vectores
    assert sum(reporte.vectores_por_formato.values()) == reporte.n_vectores


# --- avance por stderr -----------------------------------------------------------


def test_el_avisador_no_habla_en_cada_lote(capsys):
    """Con lotes de 32 y 140k fragmentos serían 4 400 líneas de ruido en las
    que el avance real se pierde."""
    from generador import PROGRESO_CADA, avisador

    avisar = avisador()
    for procesados in range(32, PROGRESO_CADA, 32):
        avisar(procesados, 140_686)

    assert capsys.readouterr().err == ""


def test_el_avisador_habla_cada_tantos_fragmentos(capsys):
    from generador import PROGRESO_CADA, avisador

    avisar = avisador()
    for procesados in range(32, 3 * PROGRESO_CADA, 32):
        avisar(procesados, 140_686)

    assert len(capsys.readouterr().err.splitlines()) == 2


def test_el_avisador_siempre_anuncia_el_final(capsys):
    from generador import avisador

    avisar = avisador()
    avisar(10, 10)

    assert "10/10" in capsys.readouterr().err


# --- entradas rotas --------------------------------------------------------------


def test_un_jsonl_vacio_es_un_error(tmp_path):
    vacio = tmp_path / "chunks.jsonl"
    vacio.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="sin fragmentos"):
        generar(vacio, tmp_path / "indice")


def test_acepta_el_directorio_de_fragmentos(fragmentos_jsonl, tmp_path):
    """El fragmentador escribe a un directorio; poder pasarle ese directorio
    evita tener que recordar el nombre del archivo."""
    salida = tmp_path / "indice"

    reporte = generar(fragmentos_jsonl.parent, salida)

    assert reporte.n_vectores > 0


def test_una_linea_sin_texto_enriquecido_es_un_error(tmp_path):
    roto = tmp_path / "chunks.jsonl"
    roto.write_text(json.dumps({"chunk_id": "X-c0000"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="texto_enriquecido"):
        generar(roto, tmp_path / "indice")


# --- duplicados exactos ---------------------------------------------------------


def jsonl_con_textos(ruta, textos):
    """Un chunks.jsonl mínimo con los textos dados, uno por línea."""
    ruta.write_text(
        "".join(
            json.dumps(
                {
                    "doc_id": f"F1-DOC-{n:03d}",
                    "chunk_id": f"F1-DOC-{n:03d}-c0000",
                    "fuente": f"archivo_{n}.csv",
                    "formato": "csv",
                    "fenomeno": 1,
                    "posicion": 0,
                    "num_tokens": len(texto.split()),
                    "texto": texto,
                    "texto_enriquecido": texto,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
            for n, texto in enumerate(textos)
        ),
        encoding="utf-8",
        newline="\n",
    )
    return ruta


def test_un_texto_repetido_se_codifica_una_sola_vez(tmp_path):
    """lit-covid viene en CSV y en XLSX: mismas filas, dos fuente distintos."""
    entrada = jsonl_con_textos(tmp_path / "chunks.jsonl", ["igual", "otro", "igual"])
    lotes = []

    generar(entrada, tmp_path / "indice", codificar=codificador(lotes))

    codificados = [texto for lote in lotes for texto in lote]
    assert codificados.count("igual") == 1


def test_el_duplicado_conserva_su_vector_y_su_fila(tmp_path):
    """Ahorrar el pase del encoder no puede costar una fila del índice: sin
    vector propio, ese archivo jamás aparecería en el top-3 (§10.2.1)."""
    entrada = jsonl_con_textos(tmp_path / "chunks.jsonl", ["igual", "otro", "igual"])
    salida = tmp_path / "indice"

    reporte = generar(entrada, salida, codificar=codificador())

    indice = faiss.read_index(str(salida / NOMBRE_INDICE))
    assert reporte.n_vectores == 3
    vectores = indice.reconstruct_n(0, 3)
    assert np.allclose(vectores[0], vectores[2])
    assert not np.allclose(vectores[0], vectores[1])

    lineas = (salida / NOMBRE_METADATA).read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["fuente"] for l in lineas] == [
        "archivo_0.csv",
        "archivo_1.csv",
        "archivo_2.csv",
    ]


def test_el_reporte_cuenta_los_pases_de_encoder_ahorrados(tmp_path):
    """Sin el número, el informe técnico no puede justificar el ahorro."""
    entrada = jsonl_con_textos(tmp_path / "chunks.jsonl", ["igual", "otro", "igual"])

    reporte = generar(entrada, tmp_path / "indice", codificar=codificador())

    assert reporte.n_vectores == 3
    assert reporte.n_reutilizados == 1


# --- consultas: el entregable ----------------------------------------------------


def responder(indice, consultas, salida, config=CONFIG_PRUEBA, codificar=None, **extra):
    """``responder_consultas`` con el encoder de prueba inyectado."""
    return responder_consultas(
        indice, consultas, salida, config, codificar=codificar or codificador(), **extra
    )


def leer_resultados(ruta):
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def indice_de_cuatro(tmp_path):
    """Cuatro documentos de un fragmento cada uno; el primero y el último, iguales."""
    entrada = jsonl_con_textos(
        tmp_path / "chunks.jsonl", ["alfa", "beta", "gamma", "alfa"]
    )
    salida = tmp_path / "indice"
    generar(entrada, salida, codificar=codificador())
    return salida


def test_hay_una_linea_por_consulta(indice_de_cuatro, tmp_path):
    destino = tmp_path / "resultados.jsonl"

    reporte = responder(
        indice_de_cuatro,
        [Consulta("q001", "alfa"), Consulta("q002", "beta")],
        destino,
    )

    assert reporte.n_consultas == 2
    assert [r["query_id"] for r in leer_resultados(destino)] == ["q001", "q002"]


def test_la_consulta_recupera_el_documento_de_su_propio_texto(indice_de_cuatro, tmp_path):
    """La comprobación de piso: si el texto exacto no se recupera a sí mismo,
    lo que falla es la correspondencia fila ↔ metadata, no el ranking."""
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "beta")], destino)

    primero = leer_resultados(destino)[0]["documentos"][0]
    assert primero["doc_id"] == "F1-DOC-001"
    assert primero["texto"] == "beta"
    assert primero["score"] == pytest.approx(1.0, abs=1e-5)
    assert primero["puesto"] == 1


def test_el_entregable_no_da_mas_documentos_de_los_pedidos(indice_de_cuatro, tmp_path):
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "alfa")], destino, top=2)

    assert len(leer_resultados(destino)[0]["documentos"]) == 2


def test_el_entregable_lleva_el_top_de_fragmentos(indice_de_cuatro, tmp_path):
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "alfa")], destino, top_fragmentos=3)

    fragmentos = leer_resultados(destino)[0]["fragmentos"]
    assert [f["puesto"] for f in fragmentos] == [1, 2, 3]
    assert fragmentos[0]["texto"] == "alfa"


def test_cada_fragmento_lleva_su_chunk_id(indice_de_cuatro, tmp_path):
    """Es la clave con la que el ground truth empareja: sin ella no hay NDCG."""
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "beta")], destino, top_fragmentos=2)

    primero = leer_resultados(destino)[0]["fragmentos"][0]
    assert primero["chunk_id"] == "F1-DOC-001-c0000"
    assert primero["doc_id"] == "F1-DOC-001"
    assert primero["score"] == pytest.approx(1.0, abs=1e-5)


def test_los_fragmentos_salen_ordenados_por_score(indice_de_cuatro, tmp_path):
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "alfa")], destino, top_fragmentos=4)

    scores = [f["score"] for f in leer_resultados(destino)[0]["fragmentos"]]
    assert scores == sorted(scores, reverse=True)


def test_sin_top_de_fragmentos_el_entregable_sale_como_antes(indice_de_cuatro, tmp_path):
    """El entregable de la corrida anterior sigue siendo válido palabra por palabra."""
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "alfa")], destino, top_fragmentos=0)

    registro = leer_resultados(destino)[0]
    assert "fragmentos" not in registro
    assert len(registro["documentos"]) == 3


def test_el_top_de_documentos_no_cambia_por_emitir_fragmentos(indice_de_cuatro, tmp_path):
    """Las dos vistas salen del mismo top-k: emitir una no puede alterar la otra."""
    con = tmp_path / "con.jsonl"
    sin = tmp_path / "sin.jsonl"
    consultas = [Consulta("q001", "alfa"), Consulta("q002", "beta")]

    responder(indice_de_cuatro, consultas, con, top_fragmentos=10)
    responder(indice_de_cuatro, consultas, sin, top_fragmentos=0)

    assert [r["documentos"] for r in leer_resultados(con)] == [
        r["documentos"] for r in leer_resultados(sin)
    ]


def test_el_reporte_registra_cuantos_fragmentos_se_pidieron(indice_de_cuatro, tmp_path):
    reporte = responder(
        indice_de_cuatro,
        [Consulta("q001", "alfa")],
        tmp_path / "r.jsonl",
        top_fragmentos=7,
    )

    assert reporte.top_fragmentos == 7


def test_una_consulta_que_no_llena_el_top_de_fragmentos_se_nombra(
    indice_de_cuatro, tmp_path
):
    """El índice tiene cuatro vectores y uno se cae por duplicado: pedir diez
    no puede dar diez, y callarlo esconde un NDCG@10 mermado por construcción."""
    reporte = responder(
        indice_de_cuatro,
        [Consulta("q001", "alfa")],
        tmp_path / "r.jsonl",
        top_fragmentos=10,
    )

    assert reporte.consultas_sin_fragmentos_completos == ["q001"]


def test_la_cli_acepta_el_tope_de_fragmentos():
    parser = _construir_parser()

    assert parser.parse_args(["--top-fragmentos", "5"]).top_fragmentos == 5
    assert parser.parse_args([]).top_fragmentos == TOP_FRAGMENTOS


def test_un_documento_no_ocupa_dos_puestos(fragmentos_jsonl, tmp_path):
    """Todos los fragmentos de la fixture son del mismo documento: el top-3 de
    §8.6 es de documentos, así que el entregable tiene que traer uno solo."""
    salida = tmp_path / "indice"
    generar(fragmentos_jsonl, salida)
    destino = tmp_path / "resultados.jsonl"

    reporte = responder(salida, [Consulta("q001", "sensores")], destino)

    documentos = leer_resultados(destino)[0]["documentos"]
    assert len({d["doc_id"] for d in documentos}) == len(documentos) == 1
    assert reporte.consultas_sin_top_completo == ["q001"]


def test_dos_documentos_con_el_mismo_texto_llegan_los_dos_al_top(
    indice_de_cuatro, tmp_path
):
    """El primero y el último de la fixture tienen el mismo texto y distinto
    ``fuente``, como lit-covid en CSV y en XLSX. El jurado empareja por
    ``fuente``: si uno se descarta, ese archivo no puede acertar nunca."""
    destino = tmp_path / "resultados.jsonl"

    reporte = responder(indice_de_cuatro, [Consulta("q001", "alfa")], destino)

    documentos = leer_resultados(destino)[0]["documentos"]
    assert [d["texto"] for d in documentos].count("alfa") == 2
    assert len({d["fuente"] for d in documentos}) == len(documentos)
    assert reporte.n_duplicados_descartados == 0


def test_la_consulta_lleva_el_prefijo_del_encoder(indice_de_cuatro, tmp_path):
    """El prefijo de consulta es asimétrico con el de fragmento: omitirlo con un
    modelo que lo pide degrada la recuperación sin que nada falle."""
    config = ConfigEncoder(
        modelo="modelo-de-prueba", dimension=DIMENSION_PRUEBA, prefijo_consulta="query: "
    )
    lotes = []

    responder(
        indice_de_cuatro,
        [Consulta("q001", "alfa")],
        tmp_path / "resultados.jsonl",
        config=config,
        codificar=codificador(lotes),
    )

    assert [texto for lote in lotes for texto in lote] == ["query: alfa"]


def test_dos_corridas_dan_el_mismo_entregable(indice_de_cuatro, tmp_path):
    """§1.4: mismo índice y misma consulta, mismo resultados.jsonl."""
    consultas = [Consulta("q001", "alfa"), Consulta("q002", "gamma")]
    primera, segunda = tmp_path / "a.jsonl", tmp_path / "b.jsonl"

    responder(indice_de_cuatro, consultas, primera)
    responder(indice_de_cuatro, consultas, segunda)

    assert primera.read_bytes() == segunda.read_bytes()


def test_el_indice_desalineado_de_la_metadata_es_un_error(indice_de_cuatro, tmp_path):
    """Con una línea de menos, cada fila devuelve la metadata de otro fragmento
    y el entregable sale entero, creíble y equivocado."""
    metadata = indice_de_cuatro / NOMBRE_METADATA
    lineas = metadata.read_text(encoding="utf-8").splitlines()
    metadata.write_text("\n".join(lineas[:-1]) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match="no se corresponden"):
        responder(indice_de_cuatro, [Consulta("q001", "alfa")], tmp_path / "r.jsonl")


def test_consultar_con_otra_dimension_es_un_error(indice_de_cuatro, tmp_path):
    config = ConfigEncoder(modelo="modelo-de-prueba", dimension=DIMENSION_PRUEBA * 2)

    with pytest.raises(ValueError, match="dimensiones"):
        responder(
            indice_de_cuatro, [Consulta("q001", "alfa")], tmp_path / "r.jsonl", config=config
        )


def test_un_indice_que_no_existe_es_un_error(tmp_path):
    with pytest.raises(ValueError, match="no existe"):
        responder(tmp_path / "sin_indice", [Consulta("q001", "alfa")], tmp_path / "r.jsonl")


# --- consultas: agregación y post-filtros ----------------------------------------


def candidato(fila, score, doc_id, texto="texto", idioma="es"):
    return Candidato(
        fila=fila,
        score=score,
        metadata={"doc_id": doc_id, "texto": texto, "idioma": idioma},
    )


def test_el_documento_puntua_con_su_mejor_fragmento():
    """Máximo y no suma: sumar corona al documento largo por ser largo."""
    documentos = agregar_por_documento(
        [
            candidato(0, 0.9, "A", "uno"),
            candidato(1, 0.5, "B", "dos"),
            candidato(2, 0.4, "B", "tres"),
            candidato(3, 0.3, "B", "cuatro"),
        ]
    )

    assert [d.doc_id for d in documentos] == ["A", "B"]
    assert documentos[1].score == pytest.approx(0.5)
    assert documentos[1].n_fragmentos == 3


def test_el_empate_se_desempata_por_doc_id():
    """Sin desempate estable, dos corridas del mismo índice pueden ordenar
    distinto el top-3 y la corrida deja de ser reproducible."""
    documentos = agregar_por_documento(
        [candidato(0, 0.5, "Z", "uno"), candidato(1, 0.5, "A", "dos")]
    )

    assert [d.doc_id for d in documentos] == ["A", "Z"]


def test_el_post_filtro_por_idioma_descarta_lo_que_no_coincide():
    candidatos = [candidato(0, 0.9, "A", idioma="en"), candidato(1, 0.8, "B", idioma="es")]

    assert [c.metadata["doc_id"] for c in filtrar_por_idioma(candidatos, "es")] == ["B"]


def test_sin_idioma_el_post_filtro_no_toca_nada():
    """Apagado por defecto: las consultas son en español y el corpus, sobre todo
    en inglés. Filtrar a ``es`` no afina la respuesta, la vacía."""
    candidatos = [candidato(0, 0.9, "A", idioma="en"), candidato(1, 0.8, "B", idioma="es")]

    assert filtrar_por_idioma(candidatos, None) == candidatos


def test_el_duplicado_que_sobrevive_es_el_de_mejor_score():
    """Con el mismo ``doc_id``, se queda el primero: viene ya ordenado."""
    candidatos = [
        candidato(0, 0.9, "A", "El mismo texto"),
        candidato(1, 0.6, "A", "el   mismo  texto"),
        candidato(2, 0.7, "C", "otro"),
    ]

    unicos, descartados = deduplicar_por_texto(candidatos)

    assert [(c.fila, c.score) for c in unicos] == [(0, 0.9), (2, 0.7)]
    assert descartados == 1


def test_el_top_de_fragmentos_corta_donde_se_le_pide():
    candidatos = [candidato(n, 0.9 - n / 10, f"D{n}") for n in range(5)]

    assert [c.fila for c in mejores_fragmentos(candidatos, 3)] == [0, 1, 2]


def test_el_top_de_fragmentos_respeta_el_orden_de_llegada():
    """Vienen ordenados por score desde FAISS: cortar no puede reordenar."""
    candidatos = [candidato(0, 0.9, "A"), candidato(1, 0.8, "B"), candidato(2, 0.7, "C")]

    assert [c.score for c in mejores_fragmentos(candidatos, 3)] == [0.9, 0.8, 0.7]


def test_con_menos_candidatos_que_el_tope_salen_todos():
    candidatos = [candidato(0, 0.9, "A"), candidato(1, 0.8, "B")]

    assert len(mejores_fragmentos(candidatos, 10)) == 2


def test_un_tope_de_cero_apaga_los_fragmentos():
    """Con --top-fragmentos 0 el entregable sale como antes de esta pieza."""
    candidatos = [candidato(0, 0.9, "A")]

    assert mejores_fragmentos(candidatos, 0) == []


# --- consultas: lectura del archivo de ADL ---------------------------------------


def test_una_consulta_partida_en_varias_lineas_se_reensambla(tmp_path):
    """Así vienen en el PDF de ADL: el corte lo marca el identificador
    siguiente, no el salto de línea."""
    ruta = tmp_path / "preguntas.txt"
    ruta.write_text(
        "q001 ¿Cómo está transformando la inteligencia artificial la capacidad\n"
        "de los Estados para contrarrestar amenazas NBQR?\n"
        "q002 ¿Qué lecciones dejan los conflictos recientes?\n",
        encoding="utf-8",
    )

    consultas = cargar_consultas(ruta)

    assert [c.id for c in consultas] == ["q001", "q002"]
    assert consultas[0].texto == (
        "¿Cómo está transformando la inteligencia artificial la capacidad de "
        "los Estados para contrarrestar amenazas NBQR?"
    )


def test_un_identificador_repetido_es_un_error(tmp_path):
    """Dos líneas con el mismo query_id en el entregable y no hay forma de
    saber cuál de las dos se evalúa."""
    ruta = tmp_path / "preguntas.txt"
    ruta.write_text("q001 primera\nq001 segunda\n", encoding="utf-8")

    with pytest.raises(ValueError, match="q001"):
        cargar_consultas(ruta)


def test_sin_marcas_cada_linea_es_una_consulta(tmp_path):
    ruta = tmp_path / "preguntas.txt"
    ruta.write_text("primera pregunta\n\nsegunda pregunta\n", encoding="utf-8")

    consultas = cargar_consultas(ruta)

    assert [(c.id, c.texto) for c in consultas] == [
        ("q001", "primera pregunta"),
        ("q002", "segunda pregunta"),
    ]


def test_las_consultas_pueden_venir_en_jsonl(tmp_path):
    ruta = tmp_path / "preguntas.jsonl"
    ruta.write_text(
        json.dumps({"query_id": "q001", "consulta": "primera"}, ensure_ascii=False) + "\n"
        + json.dumps({"id": "q002", "pregunta": "segunda"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    consultas = cargar_consultas(ruta)

    assert [(c.id, c.texto) for c in consultas] == [("q001", "primera"), ("q002", "segunda")]


def test_un_archivo_de_consultas_que_no_existe_es_un_error(tmp_path):
    with pytest.raises(ValueError, match="no existe"):
        cargar_consultas(tmp_path / "no_esta.pdf")


def test_dos_documentos_con_el_mismo_texto_compiten_por_separado():
    """lit-covid está en CSV (041) y en XLSX (042): son dos ``fuente`` distintos
    y el jurado empareja por ``fuente`` (§10.2.1). Descartar uno le quita el
    único vector con el que podría aparecer en el top-3, y el acierto con él."""
    candidatos = [
        candidato(0, 0.9, "F1-AIINDEX-041", "El mismo texto"),
        candidato(1, 0.9, "F1-AIINDEX-042", "El mismo texto"),
        candidato(2, 0.7, "C", "otro"),
    ]

    unicos, descartados = deduplicar_por_texto(candidatos)

    assert [c.metadata["doc_id"] for c in unicos] == [
        "F1-AIINDEX-041",
        "F1-AIINDEX-042",
        "C",
    ]
    assert descartados == 0


def test_el_texto_repetido_dentro_de_un_documento_si_se_descarta():
    """Ahí sí es ruido: el documento ya compite con su mejor fragmento, y el
    duplicado solo ocupa un puesto del top-k sin aportar nada nuevo."""
    candidatos = [
        candidato(0, 0.9, "A", "El mismo texto"),
        candidato(1, 0.8, "A", "el   mismo  texto"),
        candidato(2, 0.7, "C", "otro"),
    ]

    unicos, descartados = deduplicar_por_texto(candidatos)

    assert [c.fila for c in unicos] == [0, 2]
    assert descartados == 1
