"""
generador_grafo.py
==================

CODEFEST AD ASTRA 2026 - Etapa 1 - Componente Bonus: Grafo de Conocimiento
(Seccion 7 de la Especificacion Tecnica)
Equipo CodeSlayers

Construye un grafo de conocimiento G = (E, R, T) a partir del mismo
metadata.jsonl que alimenta la base vectorial (Tabla 1 de la especificacion),
garantizando trazabilidad bidireccional entre el grafo y los fragmentos
indexados (Seccion 7.3).

Este archivo es autocontenido: incluye el lexico de dominio, el reconocimiento
de entidades, la extraccion de relaciones y la construccion del grafo. Su unica
dependencia de terceros es NetworkX.

RESTRICCIONES CUMPLIDAS
-----------------------
* Seccion 8.3 - no interviene ningun modelo generativo (decoder) en el pipeline.
* Licenciamiento - no se usa NINGUN modelo preentrenado de terceros. La
  organizacion confirmo que los componentes con licencia CC BY-NC-SA 4.0 no
  estan permitidos, lo que descarta los NER/RE multilingues habituales
  (WikiNEuRal y REBEL, ambos CC BY-NC-SA 4.0). Los modelos spaCy de espanol
  (GPL-3.0) y portugues (CC BY-SA 4.0) tampoco se ajustan a la preferencia
  Apache / MIT / CC BY del reto. El reconocimiento de entidades y la extraccion
  de relaciones son codigo original del equipo CodeSlayers.
  Unica dependencia externa: NetworkX (BSD-3-Clause).

CONTENIDO
---------
    Parte 1. Lexico de dominio multilingue (gazetteer)
    Parte 2. Reconocimiento de entidades por reglas (NER)
    Parte 3. Extraccion de relaciones por reglas (RE)
    Parte 4. Construccion, agregacion y exportacion del grafo

Pipeline
--------
    1. Carga de fragmentos (chunks) desde metadata.jsonl
    2. NER por reglas: gazetteer multilingue con formas canonicas + siglas +
       secuencias capitalizadas
    3. RE por reglas: verbo del lexicon entre dos entidades de la misma
       oracion; si no hay verbo, relacion generica `relacionado_con`
    4. Construccion de un grafo dirigido (NetworkX) con:
         - nodos   = entidades (tipo, frecuencia, fenomenos, chunk_ids)
         - aristas = relaciones tipadas y agregadas (peso, doc_ids, chunk_ids)
    5. Poda por frecuencia minima y exportacion a GraphML + estadisticas JSON

Uso
---
    pip install networkx

    python generador_grafo.py \
        --metadata base_vectorial/encoder_<nombre>/metadata.jsonl \
        --output base_vectorial/grafo/grafo.graphml \
        --min-freq-nodo 5 --min-peso-arista 2 --sin-aislados

    # Corpus grande o equipos con poca RAM: por lotes disjuntos y fusion
    python generador_grafo.py --metadata METADATA --desde 0 --hasta 34000 \
        --parcial lotes/l1.pkl
    python generador_grafo.py --fusionar lotes/l*.pkl \
        --output base_vectorial/grafo/grafo.graphml
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import networkx as nx


# =========================================================================== #
#                                                                             #
#   PARTE 1 - LEXICO DE DOMINIO                                               #
#                                                                             #
#   Diccionarios construidos integramente por el equipo CodeSlayers. Cada     #
#   entrada mapea FORMA CANONICA -> variantes de superficie (es/en/pt y       #
#   siglas). Todas las variantes de un termino colapsan en un unico nodo del  #
#   grafo, lo que da unificacion cross-lingue sin depender del encoder.       #
#                                                                             #
# =========================================================================== #

# --------------------------------------------------------------------------- #
# Países y actores estatales
# --------------------------------------------------------------------------- #

PAISES: dict[str, list[str]] = {
    "Colombia": ["Colombia", "colombiano", "colombiana", "colombianos",
                 "colombianas", "Colombian"],
    "Venezuela": ["Venezuela", "venezolano", "venezolana", "venezolanos",
                  "venezolanas", "Venezuelan"],
    "Brasil": ["Brasil", "Brazil", "brasileño", "brasileña", "brasileiro",
               "brasileira", "Brazilian"],
    "Argentina": ["Argentina", "argentino", "argentina", "Argentinian",
                  "Argentine"],
    "Chile": ["Chile", "chileno", "chilena", "Chilean"],
    "Perú": ["Perú", "Peru", "peruano", "peruana", "Peruvian"],
    "Ecuador": ["Ecuador", "ecuatoriano", "ecuatoriana", "Ecuadorian"],
    "Bolivia": ["Bolivia", "boliviano", "boliviana", "Bolivian"],
    "Paraguay": ["Paraguay", "paraguayo", "paraguaya", "Paraguayan"],
    "Uruguay": ["Uruguay", "uruguayo", "uruguaya", "Uruguayan"],
    "México": ["México", "Mexico", "mexicano", "mexicana", "Mexican"],
    "Panamá": ["Panamá", "Panama", "panameño", "panameña", "Panamanian"],
    "Costa Rica": ["Costa Rica", "costarricense", "Costa Rican"],
    "Guyana": ["Guyana", "guyanés", "guyanesa", "Guyanese"],
    "Surinam": ["Surinam", "Suriname", "Suriname"],
    "Cuba": ["Cuba", "cubano", "cubana", "Cuban"],
    "Haití": ["Haití", "Haiti", "haitiano", "haitiana", "Haitian"],
    "República Dominicana": ["República Dominicana", "Republica Dominicana",
                             "Dominican Republic"],
    "Guatemala": ["Guatemala", "guatemalteco", "guatemalteca", "Guatemalan"],
    "Honduras": ["Honduras", "hondureño", "hondureña", "Honduran"],
    "El Salvador": ["El Salvador", "salvadoreño", "salvadoreña", "Salvadoran"],
    "Nicaragua": ["Nicaragua", "nicaragüense", "Nicaraguan"],
    "Estados Unidos": ["Estados Unidos", "United States", "Estados Unidos de América",
                       "EE.UU.", "EEUU", "EE. UU.", "U.S.", "US", "USA",
                       "Norteamérica", "estadounidense", "estadounidenses",
                       "American", "Washington"],
    "China": ["China", "chino", "china", "chinos", "Chinese",
              "República Popular China", "People's Republic of China", "PRC",
              "Pekín", "Beijing"],
    "Rusia": ["Rusia", "Russia", "ruso", "rusa", "Russian", "Moscú", "Moscow",
              "Federación Rusa", "Russian Federation"],
    "India": ["India", "indio", "india", "Indian"],
    "Japón": ["Japón", "Japan", "japonés", "japonesa", "Japanese", "Tokio", "Tokyo"],
    "Corea del Sur": ["Corea del Sur", "South Korea", "surcoreano", "surcoreana",
                      "South Korean", "Seúl", "Seoul"],
    "Corea del Norte": ["Corea del Norte", "North Korea", "norcoreano",
                        "North Korean"],
    "Irán": ["Irán", "Iran", "iraní", "Iranian"],
    "Israel": ["Israel", "israelí", "israelita", "Israeli"],
    "Ucrania": ["Ucrania", "Ukraine", "ucraniano", "ucraniana", "Ukrainian"],
    "Reino Unido": ["Reino Unido", "United Kingdom", "Gran Bretaña",
                    "Great Britain", "británico", "británica", "British",
                    "Londres", "London", "UK"],
    "Francia": ["Francia", "France", "francés", "francesa", "French", "París",
                "Paris"],
    "Alemania": ["Alemania", "Germany", "alemán", "alemana", "German", "Berlín",
                 "Berlin"],
    "España": ["España", "Spain", "español", "española", "Spanish", "Madrid"],
    "Italia": ["Italia", "Italy", "italiano", "italiana", "Italian", "Roma"],
    "Países Bajos": ["Países Bajos", "Netherlands", "Holanda", "Dutch"],
    "Canadá": ["Canadá", "Canada", "canadiense", "Canadian"],
    "Australia": ["Australia", "australiano", "Australian"],
    "Turquía": ["Turquía", "Turkey", "turco", "Turkish"],
    "Arabia Saudita": ["Arabia Saudita", "Saudi Arabia", "saudí", "Saudi"],
    "Emiratos Árabes Unidos": ["Emiratos Árabes Unidos",
                               "United Arab Emirates", "UAE"],
    "Sudáfrica": ["Sudáfrica", "South Africa", "sudafricano"],
    "Nigeria": ["Nigeria", "nigeriano", "Nigerian"],
    "Egipto": ["Egipto", "Egypt", "egipcio", "Egyptian"],
    "Indonesia": ["Indonesia", "indonesio", "Indonesian"],
    "Vietnam": ["Vietnam", "vietnamita", "Vietnamese"],
    "Singapur": ["Singapur", "Singapore"],
    "Taiwán": ["Taiwán", "Taiwan", "taiwanés", "Taiwanese"],
}

# --------------------------------------------------------------------------- #
# Organismos multilaterales y de gobernanza
# --------------------------------------------------------------------------- #

ORGANISMOS: dict[str, list[str]] = {
    "Naciones Unidas": ["Naciones Unidas", "Organización de las Naciones Unidas",
                        "United Nations", "Nações Unidas", "ONU", "UN", "UNO"],
    "Consejo de Seguridad de la ONU": ["Consejo de Seguridad", "Security Council",
                                       "Conselho de Segurança", "UNSC"],
    "UNOOSA": ["UNOOSA", "Oficina de Asuntos del Espacio Ultraterrestre",
               "Office for Outer Space Affairs"],
    "COPUOS": ["COPUOS", "Comité para el Uso Pacífico del Espacio Ultraterrestre",
               "Committee on the Peaceful Uses of Outer Space"],
    "OTAN": ["OTAN", "NATO", "Organización del Tratado del Atlántico Norte",
             "North Atlantic Treaty Organization"],
    "Unión Europea": ["Unión Europea", "European Union", "União Europeia",
                      "UE", "EU"],
    "OEA": ["OEA", "OAS", "Organización de los Estados Americanos",
            "Organization of American States"],
    "CEPAL": ["CEPAL", "Comisión Económica para América Latina",
              "ECLAC", "Economic Commission for Latin America"],
    "MERCOSUR": ["MERCOSUR", "Mercosur", "Mercosul", "Mercado Común del Sur"],
    "CAN": ["Comunidad Andina", "Andean Community"],
    "UNASUR": ["UNASUR", "Unasur", "Unión de Naciones Suramericanas"],
    "CELAC": ["CELAC", "Comunidad de Estados Latinoamericanos y Caribeños"],
    "OCDE": ["OCDE", "OECD", "Organización para la Cooperación y el Desarrollo Económicos",
             "Organisation for Economic Co-operation and Development"],
    "Banco Mundial": ["Banco Mundial", "World Bank", "Banco Mundial"],
    "FMI": ["FMI", "IMF", "Fondo Monetario Internacional",
            "International Monetary Fund"],
    "BID": ["BID", "IDB", "Banco Interamericano de Desarrollo",
            "Inter-American Development Bank"],
    "OMS": ["OMS", "WHO", "Organización Mundial de la Salud",
            "World Health Organization"],
    "FAO": ["FAO", "Organización de las Naciones Unidas para la Alimentación"],
    "ACNUR": ["ACNUR", "UNHCR", "Alto Comisionado de las Naciones Unidas para los Refugiados"],
    "OIM": ["OIM", "IOM", "Organización Internacional para las Migraciones",
            "International Organization for Migration"],
    "UNESCO": ["UNESCO"],
    "UNICEF": ["UNICEF"],
    "UIT": ["UIT", "ITU", "Unión Internacional de Telecomunicaciones",
            "International Telecommunication Union"],
    "OACI": ["OACI", "ICAO", "Organización de Aviación Civil Internacional"],
    "OIEA": ["OIEA", "IAEA", "Organismo Internacional de Energía Atómica",
             "International Atomic Energy Agency"],
    "Corte Penal Internacional": ["Corte Penal Internacional",
                                  "International Criminal Court", "CPI", "ICC"],
    "Human Rights Watch": ["Human Rights Watch", "HRW"],
    "Amnistía Internacional": ["Amnistía Internacional", "Amnesty International",
                               "Anistia Internacional"],
    "Cruz Roja": ["Cruz Roja", "Red Cross", "CICR", "ICRC",
                  "Comité Internacional de la Cruz Roja"],
    "Foro Económico Mundial": ["Foro Económico Mundial", "World Economic Forum",
                               "Davos"],
    "G7": ["G7", "G-7"],
    "G20": ["G20", "G-20"],
    "BRICS": ["BRICS"],
}

# --------------------------------------------------------------------------- #
# Agencias espaciales y de defensa
# --------------------------------------------------------------------------- #

AGENCIAS: dict[str, list[str]] = {
    "NASA": ["NASA", "National Aeronautics and Space Administration"],
    "ESA": ["ESA", "Agencia Espacial Europea", "European Space Agency"],
    "Roscosmos": ["Roscosmos", "Roskosmos"],
    "CNSA": ["CNSA", "China National Space Administration",
             "Administración Espacial Nacional China"],
    "ISRO": ["ISRO", "Indian Space Research Organisation"],
    "JAXA": ["JAXA", "Japan Aerospace Exploration Agency"],
    "AEB": ["AEB", "Agência Espacial Brasileira", "Agencia Espacial Brasileña",
            "Brazilian Space Agency"],
    "CONAE": ["CONAE", "Comisión Nacional de Actividades Espaciales"],
    "AEM": ["Agencia Espacial Mexicana", "Mexican Space Agency"],
    "Fuerza Aeroespacial Colombiana": ["Fuerza Aeroespacial Colombiana",
                                       "Fuerza Aérea Colombiana", "FAC"],
    "US Space Force": ["Space Force", "Fuerza Espacial",
                       "United States Space Force", "USSF"],
    "US Space Command": ["Space Command", "SPACECOM", "USSPACECOM",
                         "Comando Espacial"],
    "Pentágono": ["Pentágono", "Pentagon", "Pentagono"],
    "Departamento de Defensa": ["Departamento de Defensa",
                                "Department of Defense", "DoD",
                                "Ministerio de Defensa", "Ministry of Defence",
                                "Ministério da Defesa"],
    "DARPA": ["DARPA", "Defense Advanced Research Projects Agency"],
    "NORAD": ["NORAD"],
    "NRO": ["National Reconnaissance Office"],
    "NGA": ["National Geospatial-Intelligence Agency"],
    "CIA": ["CIA", "Central Intelligence Agency"],
    "NSA": ["National Security Agency"],
    "FBI": ["FBI"],
    "Southern Command": ["Comando Sur", "Southern Command", "SOUTHCOM"],
    "Ejército": ["Ejército Nacional", "National Army", "Exército"],
    "Policía Nacional": ["Policía Nacional", "National Police",
                         "Polícia Nacional"],
    "INTERPOL": ["INTERPOL", "Interpol"],
    "ESA Space Debris Office": ["Space Debris Office",
                                "Oficina de Desechos Espaciales"],
}

# --------------------------------------------------------------------------- #
# Empresas y actores privados
# --------------------------------------------------------------------------- #

EMPRESAS: dict[str, list[str]] = {
    "SpaceX": ["SpaceX", "Space X"],
    "Starlink": ["Starlink"],
    "Blue Origin": ["Blue Origin"],
    "OneWeb": ["OneWeb", "One Web"],
    "Amazon Kuiper": ["Project Kuiper", "Kuiper"],
    "Boeing": ["Boeing"],
    "Airbus": ["Airbus"],
    "Lockheed Martin": ["Lockheed Martin", "Lockheed"],
    "Northrop Grumman": ["Northrop Grumman", "Northrop"],
    "Raytheon": ["Raytheon", "RTX"],
    "General Dynamics": ["General Dynamics"],
    "BAE Systems": ["BAE Systems"],
    "Thales": ["Thales"],
    "Leonardo": ["Leonardo S.p.A."],
    "Embraer": ["Embraer"],
    "Anduril": ["Anduril", "Anduril Industries"],
    "Palantir": ["Palantir", "Palantir Technologies"],
    "Rocket Lab": ["Rocket Lab", "RocketLab"],
    "Maxar": ["Maxar", "Maxar Technologies"],
    "Planet Labs": ["Planet Labs"],
    "Viasat": ["Viasat"],
    "SES": ["SES S.A."],
    "Intelsat": ["Intelsat"],
    "Iridium": ["Iridium"],
    "LeoLabs": ["LeoLabs"],
    "OpenAI": ["OpenAI", "Open AI"],
    "Anthropic": ["Anthropic"],
    "Google": ["Google", "Alphabet"],
    "DeepMind": ["DeepMind", "Google DeepMind"],
    "Microsoft": ["Microsoft"],
    "Meta": ["Meta Platforms", "Facebook"],
    "NVIDIA": ["NVIDIA", "Nvidia"],
    "Intel": ["Intel Corporation"],
    "AMD": ["Advanced Micro Devices"],
    "IBM": ["IBM"],
    "Amazon": ["Amazon Web Services", "AWS"],
    "Apple": ["Apple Inc."],
    "Huawei": ["Huawei"],
    "Baidu": ["Baidu"],
    "Alibaba": ["Alibaba"],
    "Tencent": ["Tencent"],
    "ByteDance": ["ByteDance", "TikTok"],
    "Mistral AI": ["Mistral AI"],
    "Hugging Face": ["Hugging Face", "HuggingFace"],
    "Stability AI": ["Stability AI"],
    "Clearview AI": ["Clearview AI"],
    "NSO Group": ["NSO Group", "Pegasus spyware"],
}

# --------------------------------------------------------------------------- #
# Regiones y geografía relevante para el reto
# --------------------------------------------------------------------------- #

REGIONES: dict[str, list[str]] = {
    "América Latina": ["América Latina", "Latinoamérica", "Latin America",
                       "América Latina e o Caribe", "LatAm", "latinoamericano",
                       "latinoamericana", "Latin American"],
    "El Caribe": ["el Caribe", "Caribbean", "Caribe"],
    "Amazonía": ["Amazonía", "Amazonia", "Amazon", "Amazônia",
                 "cuenca amazónica", "Amazon basin"],
    "Andes": ["los Andes", "Andean region", "región andina", "cordillera de los Andes"],
    "Cono Sur": ["Cono Sur", "Southern Cone"],
    "Triple Frontera": ["Triple Frontera", "Tri-Border Area", "Tríplice Fronteira"],
    "Tapón del Darién": ["Darién", "Darien Gap", "Tapón del Darién"],
    "Centroamérica": ["Centroamérica", "América Central", "Central America"],
    "Suramérica": ["Suramérica", "Sudamérica", "South America", "América do Sul"],
    "Norteamérica": ["North America", "América del Norte"],
    "Europa": ["Europa", "Europe", "europeo", "europea", "European"],
    "Asia-Pacífico": ["Asia-Pacífico", "Asia Pacific", "Indo-Pacífico",
                      "Indo-Pacific"],
    "Mar del Sur de China": ["Mar del Sur de China", "South China Sea",
                             "Mar Meridional de China"],
    "África": ["África", "Africa", "africano", "africana", "African"],
    "Medio Oriente": ["Medio Oriente", "Middle East", "Oriente Medio"],
    "Ártico": ["Ártico", "Arctic"],
    "Antártida": ["Antártida", "Antarctica", "Antártica"],
}

# --------------------------------------------------------------------------- #
# Marcos normativos y tratados
# --------------------------------------------------------------------------- #

NORMATIVA: dict[str, list[str]] = {
    "Tratado del Espacio Ultraterrestre": [
        "Tratado del Espacio Ultraterrestre", "Outer Space Treaty",
        "Tratado sobre el Espacio Ultraterrestre", "Tratado do Espaço Exterior",
    ],
    "Convenio sobre Responsabilidad Espacial": [
        "Convenio sobre Responsabilidad", "Liability Convention",
    ],
    "AI Act": ["AI Act", "Ley de Inteligencia Artificial",
               "Reglamento de Inteligencia Artificial", "EU AI Act"],
    "GDPR": ["GDPR", "RGPD", "Reglamento General de Protección de Datos",
             "General Data Protection Regulation"],
    "Convención de Ginebra": ["Convención de Ginebra", "Geneva Convention",
                              "Convenciones de Ginebra"],
    "Derecho Internacional Humanitario": [
        "Derecho Internacional Humanitario", "International Humanitarian Law",
        "DIH", "IHL",
    ],
    "Acuerdo de París": ["Acuerdo de París", "Paris Agreement",
                         "Acordo de Paris"],
    "Acuerdos Artemis": ["Acuerdos Artemis", "Artemis Accords"],
    "Objetivos de Desarrollo Sostenible": [
        "Objetivos de Desarrollo Sostenible", "Sustainable Development Goals",
        "ODS", "SDG", "SDGs",
    ],
}

# --------------------------------------------------------------------------- #
# Vocabulario temático del reto (los tres fenómenos)
# --------------------------------------------------------------------------- #

TEMAS: dict[str, list[str]] = {
    # --- F1: IA y capacidades estratégicas ---
    "inteligencia artificial": [
        "inteligencia artificial", "inteligência artificial",
        "artificial intelligence", "IA", "AI",
    ],
    "aprendizaje automático": [
        "aprendizaje automático", "aprendizado de máquina", "machine learning",
        "aprendizaje profundo", "deep learning", "redes neuronales",
        "neural networks", "redes neurais",
    ],
    "modelo de lenguaje": [
        "modelo de lenguaje", "modelos de lenguaje", "language model",
        "large language model", "LLM", "LLMs", "modelo fundacional",
        "foundation model", "IA generativa", "generative AI",
    ],
    "sistema de armas autónomo": [
        "sistema de armas autónomo", "sistemas de armas autónomos",
        "arma autónoma", "armas autónomas", "armas letales autónomas",
        "autonomous weapons", "autonomous weapon system", "LAWS",
        "sistemas de armas autônomos",
    ],
    "dron": [
        "dron", "drones", "drone", "vehículo aéreo no tripulado",
        "unmanned aerial vehicle", "UAV", "VANT", "veículo aéreo não tripulado",
    ],
    "ciberseguridad": [
        "ciberseguridad", "cibersegurança", "cybersecurity", "cyber security",
        "ciberdefensa", "cyber defense",
    ],
    "ciberataque": [
        "ciberataque", "ciberataques", "cyberattack", "cyberattacks",
        "ataque cibernético", "ciberataque",
    ],
    "guerra electrónica": [
        "guerra electrónica", "electronic warfare", "guerra eletrônica",
        "interferencia", "jamming", "spoofing",
    ],
    "desinformación": [
        "desinformación", "desinformação", "disinformation", "misinformation",
        "noticias falsas", "fake news",
    ],
    "vigilancia masiva": [
        "vigilancia masiva", "mass surveillance", "vigilância em massa",
        "reconocimiento facial", "facial recognition",
    ],
    "semiconductores": [
        "semiconductores", "semiconductors", "semicondutores", "chips",
        "microchips", "GPU", "GPUs",
    ],
    "computación cuántica": [
        "computación cuántica", "quantum computing", "computação quântica",
    ],
    "defensa nacional": [
        "defensa nacional", "defesa nacional", "national defense",
        "national defence", "seguridad nacional", "national security",
        "segurança nacional",
    ],
    "gobernanza de la IA": [
        "gobernanza de la inteligencia artificial", "gobernanza de la IA",
        "AI governance", "governança da IA", "regulación de la IA",
        "AI regulation", "ética de la IA", "AI ethics",
    ],
    "carrera armamentista": [
        "carrera armamentista", "arms race", "corrida armamentista",
        "gasto militar", "military spending", "gastos de defensa",
    ],

    # --- F2: Seguridad espacial y órbita baja ---
    "órbita baja terrestre": [
        "órbita baja terrestre", "orbita baja terrestre", "órbita terrestre baja",
        "low earth orbit", "órbita baixa da Terra", "LEO", "OBT",
    ],
    "órbita geoestacionaria": [
        "órbita geoestacionaria", "geostationary orbit", "GEO",
        "órbita geosíncrona",
    ],
    "satélite": [
        "satélite", "satélites", "satelite", "satelites", "satellite",
        "satellites", "megaconstelación", "megaconstelaciones",
        "mega-constellation", "constelación de satélites",
    ],
    "basura espacial": [
        "basura espacial", "desechos espaciales", "detritos espaciais",
        "space debris", "orbital debris", "chatarra espacial",
        "lixo espacial",
    ],
    "colisión orbital": [
        "colisión orbital", "colisiones orbitales", "orbital collision",
        "riesgo de colisión", "collision risk", "síndrome de Kessler",
        "Kessler syndrome",
    ],
    "gestión del tráfico espacial": [
        "gestión del tráfico espacial", "space traffic management", "STM",
        "conciencia situacional espacial", "space situational awareness", "SSA",
        "vigilancia espacial", "space surveillance",
    ],
    "arma antisatélite": [
        "arma antisatélite", "armas antisatélite", "anti-satellite weapon",
        "ASAT", "antisatelite",
    ],
    "seguridad espacial": [
        "seguridad espacial", "segurança espacial", "space security",
        "militarización del espacio", "space militarization",
        "weaponization of space",
    ],
    "lanzamiento espacial": [
        "lanzamiento espacial", "lanzamientos espaciales", "space launch",
        "vehículo de lanzamiento", "launch vehicle", "cohete", "rocket",
        "foguete",
    ],
    "observación de la Tierra": [
        "observación de la Tierra", "earth observation", "teledetección",
        "remote sensing", "sensoriamento remoto", "imágenes satelitales",
        "satellite imagery",
    ],
    "GNSS": [
        "GNSS", "GPS", "sistema de posicionamiento global",
        "global positioning system", "Galileo", "GLONASS", "BeiDou",
    ],
    "espectro radioeléctrico": [
        "espectro radioeléctrico", "radio spectrum", "espectro electromagnético",
        "asignación de frecuencias", "spectrum allocation",
    ],

    # --- F3: Dinámicas territoriales en América Latina ---
    "migración": [
        "migración", "migraciones", "migração", "migration", "migrante",
        "migrantes", "migrants", "flujo migratorio", "migration flows",
        "crisis migratoria", "migration crisis",
    ],
    "desplazamiento forzado": [
        "desplazamiento forzado", "desplazamiento interno",
        "forced displacement", "internal displacement", "deslocamento forçado",
        "desplazados", "displaced persons", "IDP", "IDPs",
    ],
    "refugiados": [
        "refugiado", "refugiados", "refugee", "refugees", "refugiado",
        "solicitantes de asilo", "asylum seekers",
    ],
    "crimen organizado": [
        "crimen organizado", "organized crime", "crime organizado",
        "grupos armados", "armed groups", "grupos armados ilegales",
        "economías ilícitas", "illicit economies",
    ],
    "narcotráfico": [
        "narcotráfico", "drug trafficking", "tráfico de drogas",
        "tráfico de estupefacientes", "cultivos ilícitos", "coca",
        "cocaína", "cocaine",
    ],
    "minería ilegal": [
        "minería ilegal", "illegal mining", "mineração ilegal",
        "garimpo", "extracción ilegal",
    ],
    "deforestación": [
        "deforestación", "deforestation", "desmatamento",
        "pérdida de bosque", "forest loss",
    ],
    "cambio climático": [
        "cambio climático", "mudança climática", "climate change",
        "crisis climática", "climate crisis", "calentamiento global",
        "global warming",
    ],
    "seguridad alimentaria": [
        "seguridad alimentaria", "food security", "segurança alimentar",
        "inseguridad alimentaria", "food insecurity",
    ],
    "recursos hídricos": [
        "recursos hídricos", "water resources", "escasez de agua",
        "water scarcity", "seguridad hídrica",
    ],
    "derechos humanos": [
        "derechos humanos", "direitos humanos", "human rights",
        "violaciones de derechos humanos", "human rights violations",
    ],
    "pueblos indígenas": [
        "pueblos indígenas", "indigenous peoples", "povos indígenas",
        "comunidades indígenas", "indigenous communities",
    ],
    "gobernanza": [
        "gobernanza", "governança", "governance", "gobernabilidad",
        "institucionalidad",
    ],
    "corrupción": [
        "corrupción", "corruption", "corrupção", "captura del Estado",
        "state capture",
    ],
    "frontera": [
        "frontera", "fronteras", "border", "borders", "fronteira",
        "zona fronteriza", "border area", "control fronterizo",
        "border control",
    ],
    "soberanía": [
        "soberanía", "sovereignty", "soberania", "integridad territorial",
        "territorial integrity",
    ],
    "conflicto armado": [
        "conflicto armado", "armed conflict", "conflito armado",
        "violencia armada", "armed violence",
    ],
    "acuerdo de paz": [
        "acuerdo de paz", "peace agreement", "acordo de paz",
        "proceso de paz", "peace process",
    ],
    "ordenamiento territorial": [
        "ordenamiento territorial", "land use planning",
        "ordenamento territorial", "planificación territorial",
    ],
    "infraestructura crítica": [
        "infraestructura crítica", "critical infrastructure",
        "infraestrutura crítica",
    ],
}


# --------------------------------------------------------------------------- #
# Consolidación
# --------------------------------------------------------------------------- #

# forma_canonica -> (tipo, variantes)
GAZETTEER: dict[str, tuple[str, list[str]]] = {}

for _dic, _tipo in (
    (PAISES, "PAIS"),
    (ORGANISMOS, "ORG"),
    (AGENCIAS, "AGENCIA"),
    (EMPRESAS, "EMPRESA"),
    (REGIONES, "REGION"),
    (NORMATIVA, "NORMA"),
    (TEMAS, "TEMA"),
):
    for _canonica, _variantes in _dic.items():
        if _canonica in GAZETTEER:
            # Si una forma canónica se repite entre categorías, se fusionan
            # las variantes conservando el primer tipo asignado.
            _tipo_previo, _prev = GAZETTEER[_canonica]
            GAZETTEER[_canonica] = (_tipo_previo, _prev + list(_variantes))
        else:
            GAZETTEER[_canonica] = (_tipo, list(_variantes))


# Siglas que deben buscarse respetando mayúsculas, para no capturar palabras
# comunes ("IA" dentro de "artificial", "US" en inglés, "a" en portugués,
# "LEO" como nombre propio, "CAN" verbo modal inglés, etc.).
SIGLAS_SENSIBLES: set[str] = {
    "IA", "AI", "LEO", "OBT", "GEO", "UAV", "VANT", "LAWS", "ASAT", "STM",
    "SSA", "GPS", "GNSS", "LLM", "LLMs", "GPU", "GPUs", "ODS", "SDG", "SDGs",
    "ONU", "UN", "UNO", "UE", "EU", "OEA", "OAS", "UK", "US", "USA", "PRC",
    "UAE", "IDP", "IDPs", "DIH", "IHL", "CPI", "ICC", "IMF", "FMI", "BID",
    "IDB", "WHO", "OMS", "FAO", "ITU", "UIT", "ICAO", "OACI", "IAEA", "OIEA",
    "NATO", "OTAN", "OECD", "OCDE", "G7", "G20", "BRICS", "FAC", "USSF",
    "SPACECOM", "USSPACECOM", "DoD", "DARPA", "NORAD", "CIA", "NSA", "FBI",
    "SOUTHCOM", "NASA", "ESA", "CNSA", "ISRO", "JAXA", "AEB", "CONAE", "AEM",
    "AWS", "IBM", "AMD", "SES", "RTX", "BAE", "HRW", "CICR", "ICRC", "GDPR",
    "RGPD", "UNSC", "UNHCR", "ACNUR", "OIM", "IOM", "COPUOS", "UNOOSA",
    "MERCOSUR", "UNASUR", "CELAC", "CEPAL", "ECLAC", "UNESCO", "UNICEF",
    "INTERPOL", "NRO", "NGA", "CAN", "EEUU",
}


def resumen() -> dict[str, int]:
    """Conteo de entradas por tipo (para documentación del entregable)."""
    conteo: dict[str, int] = {}
    n_variantes = 0
    for _canonica, (tipo, variantes) in GAZETTEER.items():
        conteo[tipo] = conteo.get(tipo, 0) + 1
        n_variantes += len(variantes)
    conteo["_total_entidades"] = len(GAZETTEER)
    conteo["_total_variantes"] = n_variantes
    return conteo

# =========================================================================== #
#                                                                             #
#   PARTE 2 y 3 - NER Y EXTRACCION DE RELACIONES POR REGLAS                   #
#                                                                             #
#   Sin modelos preentrenados. El NER combina el gazetteer de la Parte 1 con  #
#   deteccion de siglas y de secuencias capitalizadas, mas filtros de ruido   #
#   de extraccion de PDF. La RE inspecciona el texto entre dos entidades de   #
#   la misma oracion buscando un verbo del lexicon de relaciones.             #
#                                                                             #
# =========================================================================== #

# --------------------------------------------------------------------------- #
# Utilidades de normalización
# --------------------------------------------------------------------------- #

ARTICULOS_INICIALES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "o", "a", "os", "as", "um", "uma",
    "the", "an",
}

# Palabras vacías por idioma: se usan para descartar secuencias capitalizadas
# que en realidad son inicio de oración o conectores.
STOPWORDS = {
    # español
    "el", "la", "los", "las", "un", "una", "de", "del", "en", "y", "o", "que",
    "por", "para", "con", "sin", "sobre", "entre", "como", "más", "menos",
    "este", "esta", "estos", "estas", "ese", "esa", "su", "sus", "al", "lo",
    "es", "son", "fue", "ha", "han", "no", "si", "también", "pero", "porque",
    "cuando", "donde", "desde", "hasta", "durante", "según", "además", "así",
    "aunque", "mientras", "todos", "todas", "otro", "otra", "cada", "ser",
    "estados",  # se maneja vía gazetteer ("Estados Unidos")
    # inglés
    "the", "a", "an", "of", "in", "and", "or", "that", "for", "with", "without",
    "on", "at", "by", "to", "from", "as", "is", "are", "was", "were", "be",
    "this", "these", "those", "it", "its", "their", "there", "which", "while",
    "but", "because", "when", "where", "how", "all", "also", "more", "most",
    "such", "than", "then", "however", "figure", "table", "chapter", "source",
    "page", "report", "section", "appendix", "note", "notes",
    # portugués
    "os", "as", "um", "uma", "do", "da", "dos", "das", "em", "no", "na", "nos",
    "nas", "e", "ou", "que", "para", "com", "sem", "sobre", "entre", "como",
    "mais", "menos", "este", "esta", "esse", "essa", "seu", "sua", "ao", "à",
    "é", "são", "foi", "tem", "não", "se", "também", "mas", "porque", "quando",
    "onde", "desde", "até", "durante", "segundo", "além", "assim", "embora",
    "enquanto", "todos", "todas", "outro", "outra", "cada", "ser", "pelo",
    "pela", "por",
    # meses (frecuentes en encabezados de PDF)
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "janeiro", "fevereiro", "março", "maio", "junho", "julho", "setembro",
    "outubro", "novembro", "dezembro",
}

# Conectores admitidos dentro de un nombre propio multi-palabra
CONECTORES_NOMBRE = {"de", "del", "la", "las", "los", "y", "e",
                     "of", "the", "and", "for",
                     "da", "do", "das", "dos", "e"}

# Vocabulario de maquetación y estructura documental. Los PDFs del corpus
# (informes, papers, anuarios) están llenos de encabezados, pies de figura y
# secciones que el detector de secuencias capitalizadas confunde con nombres
# propios: "Chart", "Preview", "Technical Appendix", "Table of Contents"...
NOMBRES_RUIDO = {
    # inglés
    "figure", "figures", "table", "tables", "chart", "charts", "graph",
    "graphs", "preview", "source", "sources", "note", "notes", "data",
    "number", "numbers", "percentage", "percent", "total", "average",
    "appendix", "appendices", "index", "report", "reports", "chapter",
    "section", "sections", "overview", "introduction", "conclusion",
    "conclusions", "abstract", "summary", "references", "bibliography",
    "contents", "acknowledgements", "acknowledgments", "highlights",
    "methodology", "methods", "results", "discussion", "background",
    "research", "study", "studies", "analysis", "figure note", "key takeaways",
    "takeaways", "definition", "definitions", "glossary", "annex", "exhibit",
    "box", "panel", "column", "row", "page", "pages", "volume", "issue",
    "edition", "copyright", "license", "licence", "all rights reserved",
    "technical appendix", "table of contents", "executive summary",
    "figure source", "chart preview", "year", "years", "month", "months",
    "quarter", "annual", "top", "bottom", "left", "right", "figure caption",
    "and", "the", "for", "with", "from", "this", "that", "these", "those",
    "however", "moreover", "furthermore", "therefore", "meanwhile", "although",
    "while", "since", "because", "despite", "overall", "finally", "first",
    "second", "third", "fourth", "next", "then", "also", "both", "each",
    "more", "most", "many", "some", "other", "others", "such", "among",
    "between", "during", "after", "before", "above", "below", "figure number",
    # español
    "figura", "figuras", "tabla", "tablas", "gráfico", "gráficos", "grafico",
    "cuadro", "cuadros", "fuente", "fuentes", "nota", "notas", "datos",
    "número", "números", "numero", "porcentaje", "total", "promedio",
    "anexo", "anexos", "índice", "indice", "informe", "informes", "capítulo",
    "capitulo", "sección", "seccion", "secciones", "resumen", "introducción",
    "introduccion", "conclusión", "conclusiones", "referencias",
    "bibliografía", "bibliografia", "contenido", "agradecimientos",
    "metodología", "metodologia", "resultados", "discusión", "antecedentes",
    "estudio", "estudios", "análisis", "analisis", "definición", "glosario",
    "recuadro", "página", "pagina", "páginas", "volumen", "edición",
    "derechos reservados", "todos los derechos reservados", "año", "años",
    "mes", "meses", "trimestre", "anual", "sin embargo", "además", "ademas",
    "por lo tanto", "mientras", "aunque", "desde", "porque", "finalmente",
    "primero", "segundo", "tercero", "luego", "también", "tambien", "ambos",
    "cada", "más", "muchos", "algunos", "otros", "entre", "durante",
    "después", "despues", "antes", "sobre", "según", "segun",
    # portugués
    "figura", "tabela", "tabelas", "gráfico", "quadro", "fonte", "fontes",
    "nota", "notas", "dados", "número", "porcentagem", "anexo", "índice",
    "relatório", "relatorio", "capítulo", "seção", "secao", "resumo",
    "introdução", "introducao", "conclusão", "conclusao", "referências",
    "referencias", "bibliografia", "conteúdo", "agradecimentos",
    "metodologia", "resultados", "discussão", "estudo", "análise",
    "definição", "glossário", "página", "volume", "edição", "ano", "anos",
    "mês", "meses", "trimestre", "no entanto", "além disso", "portanto",
    "enquanto", "embora", "porque", "finalmente", "primeiro", "segundo",
    "também", "ambos", "cada", "mais", "muitos", "alguns", "outros",
    "entre", "durante", "depois", "antes", "sobre", "segundo",
    # etiquetas de registros bibliográficos y bases de datos estructuradas
    # (parte del corpus son exportaciones CSV/JSON: ensayos clínicos, PubMed,
    # registros legislativos), donde estas palabras son nombres de campo
    "authors", "author", "title", "citation", "pmid", "doi", "issn", "isbn",
    "journal", "publisher", "affiliation", "affiliations", "keywords",
    "status", "conditions", "condition", "interventions", "intervention",
    "sponsor", "sponsors", "phase", "enrollment", "acronym", "rank", "url",
    "identifier", "identifiers", "location", "locations", "outcome",
    "outcomes", "measures", "start date", "completion date", "first posted",
    "last update", "study results", "study type", "study designs", "funder",
    "funders", "grant", "grants", "record", "records", "entry", "entries",
    "field", "fields", "value", "values", "label", "labels", "category",
    "categories", "type", "types", "name", "names", "description",
    "autores", "autor", "título", "titulo", "cita", "revista", "editorial",
    "afiliación", "afiliacion", "palabras clave", "estado", "condiciones",
    "patrocinador", "fase", "identificador", "ubicación", "ubicacion",
    "resultado", "medidas", "fecha", "registro", "registros", "campo",
    "campos", "valor", "valores", "etiqueta", "categoría", "categoria",
    "tipo", "nombre", "descripción", "descripcion",
    "autores", "título", "revista", "editora", "afiliação", "estado",
    "condições", "patrocinador", "fase", "identificador", "localização",
    "resultado", "medidas", "data", "registro", "campo", "valor",
    "categoria", "tipo", "nome", "descrição",
    # genéricos que aparecen sueltos y no son entidades
    "space", "espacio", "espaço", "world", "mundo", "global", "international",
    "internacional", "national", "nacional", "state", "estado", "government",
    "gobierno", "governo", "country", "país", "pais", "region", "región",
}

# Núcleos estructurales: si un nombre propio candidato TERMINA en alguna de
# estas palabras, es casi siempre un elemento de maquetación y no una entidad
# ("Technical Appendix", "Figure Note", "AI Index Report", "Chart Preview").
NUCLEOS_ESTRUCTURALES = {
    "appendix", "annex", "index", "report", "chart", "table", "figure",
    "section", "chapter", "summary", "overview", "preview", "note", "notes",
    "source", "sources", "data", "contents", "references", "bibliography",
    "anexo", "índice", "indice", "informe", "cuadro", "tabla", "figura",
    "sección", "seccion", "capítulo", "capitulo", "resumen", "fuente",
    "fuentes", "datos", "referencias", "gráfico", "grafico",
    "relatório", "relatorio", "seção", "secao", "resumo", "tabela",
}

# Siglas que casi siempre son ruido de maquetación en PDFs o unidades
SIGLAS_RUIDO = {
    "PDF", "HTML", "URL", "ISBN", "DOI", "ISSN", "OK", "III", "II", "IV", "VI",
    "VII", "VIII", "IX", "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII",
    "XVIII", "XIX", "XX", "XXI", "CO2", "GDP", "PIB", "USD", "EUR", "BRL",
    "COP", "KM", "CM", "MM", "GB", "MB", "KB", "TB", "HZ", "GHZ", "MHZ",
    "AM", "PM", "ID", "TV", "PC", "OS", "IT", "OR", "AND", "THE", "FOR",
    "NOT", "ALL", "NEW", "TWO", "ONE", "SEE", "USE", "MAY", "CAN", "HAS",
    "WAS", "ARE", "BUT", "HOW", "WHY", "WHO", "ITS", "OUR", "ANY", "END",
    "TOP", "LOW", "HIGH", "FIG", "TAB", "REF", "ETC", "VOL", "NO", "PP",
    "CHAPTER", "SOURCE", "NOTE", "TABLE", "FIGURE",
}


REGEX_GLIFOS_DOBLES = re.compile(r"([A-Za-zÁÉÍÓÚÑáéíóúñ])\1")


def es_glifo_duplicado(nombre: str) -> bool:
    """Detecta el artefacto típico de extracción de texto en negrita de PDF,
    donde cada glifo aparece duplicado:
    "AArrttiiffiicciiaall IInntteelllliiggeennccee".

    Se marca cuando más de un tercio de los caracteres forma pares repetidos
    y el nombre tiene longitud suficiente para que no sea casualidad.
    """
    if len(nombre) < 8:
        return False
    dobles = len(REGEX_GLIFOS_DOBLES.findall(nombre))
    return dobles * 3 > len(nombre.replace(" ", ""))


def tiene_palabras_repetidas(nombre: str) -> bool:
    """"Index Index Report Report" -> artefacto de encabezado repetido."""
    palabras = [p.casefold() for p in nombre.split()]
    return any(a == b for a, b in zip(palabras, palabras[1:]))


def quitar_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def normalizar_entidad(nombre: str) -> str:
    """Limpia el nombre de una entidad para usarlo como id de nodo."""
    nombre = re.sub(r"\s+", " ", nombre).strip()
    nombre = nombre.strip("\"'“”‘’()[]{}.,;:!¡?¿-–—…«»|/\\ \t\n")
    partes = nombre.split(" ")
    if len(partes) > 1 and partes[0].lower() in ARTICULOS_INICIALES:
        nombre = " ".join(partes[1:])
    return nombre.strip()


def clave_entidad(nombre: str) -> str:
    """Clave de deduplicación: sin acentos, en minúsculas."""
    return quitar_acentos(nombre).casefold()


# --------------------------------------------------------------------------- #
# Compilación del gazetteer en dos autómatas (uno sensible a mayúsculas)
# --------------------------------------------------------------------------- #

def _construir_indices():
    """Devuelve (mapa_insensible, regex_insensible, mapa_sensible, regex_sensible).

    Las variantes se ordenan por longitud descendente para que la alternancia
    del regex prefiera siempre la coincidencia más larga ("Corea del Sur"
    antes que "Corea").
    """
    mapa_ins: dict[str, tuple[str, str]] = {}
    mapa_sen: dict[str, tuple[str, str]] = {}

    for canonica, (tipo, variantes) in GAZETTEER.items():
        for var in variantes:
            var = var.strip()
            if not var:
                continue
            if var in SIGLAS_SENSIBLES:
                mapa_sen.setdefault(var, (canonica, tipo))
            else:
                mapa_ins.setdefault(var.casefold(), (canonica, tipo))

    def _alternancia(claves) -> str:
        ordenadas = sorted(claves, key=len, reverse=True)
        return "|".join(re.escape(k) for k in ordenadas)

    # \b no funciona bien cuando la variante empieza/termina en puntuación
    # (p. ej. "EE.UU."), así que se usan lookarounds sobre caracteres de palabra.
    regex_ins = re.compile(
        r"(?<![\w])(?:" + _alternancia(mapa_ins.keys()) + r")(?![\w])",
        re.IGNORECASE,
    )
    regex_sen = re.compile(
        r"(?<![\w])(?:" + _alternancia(mapa_sen.keys()) + r")(?![\w])"
    )
    return mapa_ins, regex_ins, mapa_sen, regex_sen


MAPA_INS, REGEX_INS, MAPA_SEN, REGEX_SEN = _construir_indices()

# Siglas emergentes: 2 a 6 mayúsculas (permite dígitos internos: G20, F16)
REGEX_SIGLA = re.compile(r"(?<![\w])([A-ZÁÉÍÓÚÑ]{2,6}[0-9]{0,2})(?![\w])")

# Secuencias capitalizadas: hasta 4 palabras, con conectores en minúscula
_PAL_CAP = r"[A-ZÁÉÍÓÚÑÜÇ][\wÁÉÍÓÚÑáéíóúñüç'’\-]{1,}"
_CONECTOR = r"(?:de|del|la|las|los|y|e|of|the|and|for|da|do|das|dos)"
REGEX_CAPITALIZADA = re.compile(
    rf"(?<![\w])({_PAL_CAP}(?:\s+(?:{_CONECTOR}\s+)?{_PAL_CAP}){{0,3}})(?![\w])"
)

# Separador de oraciones (heurística, sin modelo)
REGEX_ORACION = re.compile(r"(?<=[.!?;])\s+(?=[\"'“«(¿¡]?[A-ZÁÉÍÓÚÑ0-9])")


# --------------------------------------------------------------------------- #
# NER
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Entidad:
    nombre: str      # forma canónica (id del nodo)
    tipo: str        # PAIS, ORG, AGENCIA, EMPRESA, REGION, NORMA, TEMA, SIGLA, NOMBRE
    inicio: int
    fin: int
    origen: str      # "gazetteer" | "sigla" | "capitalizada"


def es_etiqueta_de_campo(texto: str, fin: int) -> bool:
    """¿El candidato es el nombre de un campo y no una entidad?

    Una parte del corpus proviene de exportaciones tabulares (CSV/JSON de
    ensayos clínicos, registros bibliográficos, actos legislativos) cuyo texto
    tiene la forma `Rank: 1 | Title: ... | Status: Recruiting`. Sin este filtro
    los nombres de campo ("Authors", "Title", "PMID") se convierten en los
    nodos más conectados del grafo, por encima de cualquier entidad real.

    La regla es puramente posicional: si al candidato le sigue inmediatamente
    dos puntos, es una etiqueta. Sólo se aplica a los candidatos heurísticos,
    nunca a las entidades del gazetteer.
    """
    resto = texto[fin:fin + 2]
    return resto.startswith(":") or resto.startswith(" :")


def _solapa(inicio: int, fin: int, ocupados: list[tuple[int, int]]) -> bool:
    for a, b in ocupados:
        if inicio < b and a < fin:
            return True
    return False


def extraer_entidades(texto: str, usar_capitalizadas: bool = True) -> list[Entidad]:
    """Devuelve las entidades del texto, sin solapamientos, ordenadas por posición.

    Prioridad: gazetteer > siglas emergentes > secuencias capitalizadas.
    """
    entidades: list[Entidad] = []
    ocupados: list[tuple[int, int]] = []

    # 1. Gazetteer sensible a mayúsculas (siglas conocidas)
    for m in REGEX_SEN.finditer(texto):
        clave = m.group(0)
        if clave not in MAPA_SEN:
            continue
        canonica, tipo = MAPA_SEN[clave]
        entidades.append(Entidad(canonica, tipo, m.start(), m.end(), "gazetteer"))
        ocupados.append((m.start(), m.end()))

    # 2. Gazetteer insensible a mayúsculas
    for m in REGEX_INS.finditer(texto):
        if _solapa(m.start(), m.end(), ocupados):
            continue
        clave = m.group(0).casefold()
        if clave not in MAPA_INS:
            continue
        canonica, tipo = MAPA_INS[clave]
        entidades.append(Entidad(canonica, tipo, m.start(), m.end(), "gazetteer"))
        ocupados.append((m.start(), m.end()))

    # 3. Siglas emergentes no cubiertas por el gazetteer
    for m in REGEX_SIGLA.finditer(texto):
        if _solapa(m.start(), m.end(), ocupados):
            continue
        sigla = m.group(1)
        if sigla in SIGLAS_RUIDO or sigla.upper() in SIGLAS_RUIDO:
            continue
        if sigla.casefold() in NOMBRES_RUIDO:
            continue
        if es_etiqueta_de_campo(texto, m.end()):
            continue
        if len(sigla) < 3:          # evita "EL", "DE", "IN" en textos en mayúsculas
            continue
        entidades.append(Entidad(sigla, "SIGLA", m.start(), m.end(), "sigla"))
        ocupados.append((m.start(), m.end()))

    # 4. Secuencias capitalizadas (nombres propios emergentes)
    if usar_capitalizadas:
        for m in REGEX_CAPITALIZADA.finditer(texto):
            if _solapa(m.start(), m.end(), ocupados):
                continue
            bruto = normalizar_entidad(m.group(1))
            if not bruto:
                continue
            # Nombre de campo en un registro tabular, no una entidad
            if es_etiqueta_de_campo(texto, m.end()):
                continue
            # Artefactos de extracción de PDF
            if "_" in bruto or es_glifo_duplicado(bruto) or \
                    tiene_palabras_repetidas(bruto):
                continue
            # Vocabulario de maquetación / estructura documental
            clave_ruido = bruto.casefold()
            if clave_ruido in NOMBRES_RUIDO:
                continue
            palabras = bruto.split()
            # Nombres compuestos íntegramente por términos de maquetación
            # ("Technical Appendix", "Figure Source", "Chart Preview")
            if all(p.casefold() in NOMBRES_RUIDO or p.casefold() in CONECTORES_NOMBRE
                   for p in palabras):
                continue
            if palabras[-1].casefold() in NUCLEOS_ESTRUCTURALES:
                continue
            # Descartar si la primera palabra es vacía/conector, si toda la
            # secuencia es una sola palabra frecuente, o si va en mayúsculas
            # sostenidas (encabezados de PDF).
            if palabras[0].casefold() in STOPWORDS:
                continue
            if len(palabras) == 1 and (
                bruto.casefold() in STOPWORDS or len(bruto) < 4 or bruto.isupper()
            ):
                continue
            if all(p.isupper() for p in palabras):
                continue
            # Una secuencia de una sola palabra al inicio de la oración es
            # casi siempre un falso positivo por mayúscula inicial.
            if len(palabras) == 1 and m.start() == 0:
                continue
            entidades.append(Entidad(bruto, "NOMBRE", m.start(), m.end(),
                                     "capitalizada"))
            ocupados.append((m.start(), m.end()))

    entidades.sort(key=lambda e: e.inicio)
    return entidades


# --------------------------------------------------------------------------- #
# RE: lexicón de relaciones
# --------------------------------------------------------------------------- #

# relacion_canonica -> raíces verbales/nominales en es/en/pt.
# Se comprueban como subcadenas sobre el texto intermedio en minúsculas y sin
# acentos, por lo que basta con la raíz (cubre conjugaciones y plurales).
LEXICON_RELACIONES: dict[str, list[str]] = {
    "desarrolla": ["desarroll", "desenvolv", "develop"],
    "financia": ["financi", "fund", "invest", "invers", "investiment",
                 "subvencion", "presupuest"],
    "opera": ["opera", "oper", "gestion", "administra", "manage"],
    "lanza": ["lanz", "launch", "lanc", "despega", "puso en orbita",
              "put into orbit"],
    "monitorea": ["monitore", "monitor", "vigil", "rastre", "track",
                  "observ", "supervis", "detect"],
    "colabora_con": ["colabor", "cooper", "parceri", "partner", "alianz",
                     "allianc", "conjunt", "joint", "acuerdo con",
                     "agreement with", "trabaja con", "works with"],
    "firma": ["firm", "assin", "sign", "ratific", "suscrib", "adopt"],
    "regula": ["regul", "normativ", "legisl", "reglament", "标准", "estandariz",
               "standard"],
    "prohibe": ["prohib", "proib", "ban ", "veta", "veda", "restring",
                "restrict", "limit"],
    "amenaza": ["amenaz", "ameac", "threat", "pone en riesgo", "puts at risk",
                "compromet", "vulner"],
    "despliega": ["despleg", "deploy", "implant", "instal", "posicion"],
    "ataca": ["atac", "attack", "bombarde", "invad", "agred", "hostil"],
    "exporta": ["export", "vend", "sell", "suministr", "supply", "provee",
                "provid"],
    "adquiere": ["adquir", "compr", "purchas", "acquir", "obtien", "obtain",
                 "import"],
    "produce": ["produc", "produz", "fabric", "manufactur", "construy",
                "build", "constru"],
    "integra": ["integr", "incorpor", "incluy", "includ", "compone",
                "forma parte", "part of", "miembro de", "member of"],
    "apoya": ["apoy", "support", "respald", "suport", "asist", "assist",
              "promue", "promot", "fomenta", "foster"],
    "compite_con": ["compit", "compet", "rival", "disput", "contest"],
    "sanciona": ["sancion", "sanction", "penaliz", "multa", "embargo"],
    "afecta": ["afect", "impact", "perjudic", "damag", "harm", "deterior",
               "influy", "influenc"],
    "aumenta": ["aument", "increas", "cresc", "crec", "expand", "amplia",
                "acelera", "accelerat"],
    "reduce": ["reduc", "reduz", "decreas", "disminu", "mitig", "declin"],
    "utiliza": ["utiliz", "emplea", "uses ", "using ", "usa ", "usan ",
                "aplicac", "aplic", "appli", "adopt"],
    "investiga": ["investig", "research", "pesquis", "estudi", "study",
                  "analiz", "analyz", "analys"],
    "publica": ["public", "report", "inform", "document", "divulg",
                "anunci", "announc"],
    "alberga": ["alberg", "aloja", "host", "sede", "ubicad", "locat",
                "situad"],
}


def _construir_regex_relaciones() -> list[tuple[str, re.Pattern]]:
    """Compila el lexicón anclando cada raíz al INICIO de una palabra.

    Sin el ancla `\\b` las raíces cortas producen falsos positivos por
    subcadena: "gestion" casaría dentro de "congestión" y marcaría como
    'opera' una oración que en realidad dice 'monitorea la congestión'.
    """
    patrones = []
    for relacion, raices in LEXICON_RELACIONES.items():
        alternancia = "|".join(
            re.escape(quitar_acentos(r).casefold().strip())
            for r in sorted(raices, key=len, reverse=True)
        )
        patrones.append((relacion, re.compile(r"\b(?:" + alternancia + r")")))
    return patrones


PATRONES_RELACION = _construir_regex_relaciones()


def detectar_relacion(texto_intermedio: str) -> str | None:
    """Busca en el texto entre dos entidades una raíz del lexicón."""
    normalizado = quitar_acentos(texto_intermedio).casefold()
    for relacion, patron in PATRONES_RELACION:
        if patron.search(normalizado):
            return relacion
    return None


# --------------------------------------------------------------------------- #
# RE: extracción de tripletas
# --------------------------------------------------------------------------- #

@dataclass
class Tripleta:
    sujeto: str
    relacion: str
    objeto: str
    tipo_sujeto: str
    tipo_objeto: str


def extraer_tripletas(texto: str, entidades: list[Entidad],
                      max_palabras_intermedias: int = 15,
                      max_saltos: int = 2,
                      max_tripletas: int = 12) -> list[Tripleta]:
    """Genera tripletas entre entidades que comparten oración.

    Para cada par (A, B) con A antes que B y a lo sumo `max_saltos` entidades
    entre medias, se inspecciona el texto intermedio:

      * si contiene un verbo del lexicón -> relación tipada
      * si no, y el par está lo bastante cerca -> `relacionado_con`

    El límite de palabras intermedias evita unir entidades que sólo comparten
    un párrafo largo sin relación real.
    """
    if len(entidades) < 2:
        return []

    tripletas: list[Tripleta] = []
    pares_vistos: set[tuple[str, str]] = set()

    # Índice de fronteras de oración
    fronteras = [0]
    for m in REGEX_ORACION.finditer(texto):
        fronteras.append(m.start())
    fronteras.append(len(texto))

    def id_oracion(pos: int) -> int:
        for i in range(len(fronteras) - 1):
            if fronteras[i] <= pos < fronteras[i + 1]:
                return i
        return len(fronteras) - 2

    oracion_de = {i: id_oracion(e.inicio) for i, e in enumerate(entidades)}

    for i in range(len(entidades)):
        for j in range(i + 1, min(i + 1 + max_saltos + 1, len(entidades))):
            a, b = entidades[i], entidades[j]
            if oracion_de[i] != oracion_de[j]:
                continue
            if clave_entidad(a.nombre) == clave_entidad(b.nombre):
                continue

            intermedio = texto[a.fin:b.inicio]
            if len(intermedio.split()) > max_palabras_intermedias:
                continue

            par = (clave_entidad(a.nombre), clave_entidad(b.nombre))
            if par in pares_vistos:
                continue
            pares_vistos.add(par)

            relacion = detectar_relacion(intermedio) or "relacionado_con"
            tripletas.append(
                Tripleta(a.nombre, relacion, b.nombre, a.tipo, b.tipo)
            )
            if len(tripletas) >= max_tripletas:
                return tripletas

    return tripletas


# --------------------------------------------------------------------------- #
# API de alto nivel
# --------------------------------------------------------------------------- #

def analizar(texto: str, usar_capitalizadas: bool = True
             ) -> tuple[list[Entidad], list[Tripleta]]:
    entidades = extraer_entidades(texto, usar_capitalizadas=usar_capitalizadas)
    tripletas = extraer_tripletas(texto, entidades)
    return entidades, tripletas

# =========================================================================== #
#                                                                             #
#   PARTE 4 - CONSTRUCCION DEL GRAFO                                          #
#                                                                             #
# =========================================================================== #

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("generador_grafo")


# --------------------------------------------------------------------------- #
# 1. Carga de fragmentos
# --------------------------------------------------------------------------- #

@dataclass
class Chunk:
    doc_id: str
    chunk_id: str
    fuente: str
    fenomeno: int
    texto: str
    idioma: str = ""


def iterar_chunks(path_metadata: Path, limite: int | None = None,
                  desde: int = 0, hasta: int | None = None) -> Iterator[Chunk]:
    """Lee metadata.jsonl en streaming (un objeto JSON por línea, Tabla 1).

    Se itera en lugar de cargar todo en memoria porque el corpus del reto
    supera los 130.000 fragmentos. `desde`/`hasta` acotan el rango de líneas,
    lo que permite procesar el corpus por lotes en máquinas con poca RAM y
    fusionar después los resultados parciales.
    """
    n = 0
    with path_metadata.open("r", encoding="utf-8") as f:
        for i, linea in enumerate(f):
            if i < desde:
                continue
            if hasta is not None and i >= hasta:
                return
            linea = linea.strip()
            if not linea:
                continue
            try:
                obj = json.loads(linea)
            except json.JSONDecodeError as e:
                log.warning("Línea %d inválida: %s", i, e)
                continue
            texto = obj.get("texto", "")
            if not texto:
                continue
            yield Chunk(
                doc_id=obj.get("doc_id", ""),
                chunk_id=obj.get("chunk_id", ""),
                fuente=obj.get("fuente", ""),
                fenomeno=int(obj.get("fenomeno", 0) or 0),
                texto=texto,
                idioma=obj.get("idioma", ""),
            )
            n += 1
            if limite and n >= limite:
                return


# --------------------------------------------------------------------------- #
# 2-4. Construcción del grafo
# --------------------------------------------------------------------------- #

@dataclass
class AcumuladorNodo:
    nombre: str
    tipo: str
    frecuencia: int = 0
    n_chunks_total: int = 0   # conteo exacto; `chunks` sólo guarda ejemplos
    chunks: set = None
    docs: set = None
    fenomenos: Counter = None

    def __post_init__(self):
        self.chunks = set()
        self.docs = set()
        self.fenomenos = Counter()


def acumular(chunks: Iterator[Chunk],
             max_trazas: int = 20,
             usar_capitalizadas: bool = True,
             ) -> tuple[dict, dict, dict]:
    """Recorre los fragmentos y acumula nodos y aristas (sin montar el grafo).

    Las aristas se AGREGAN por (sujeto, relación, objeto): en un corpus de
    134.000 fragmentos una arista por ocurrencia produciría millones de
    aristas redundantes. Cada arista guarda su `peso` (número de fragmentos
    que la respaldan) y hasta `max_trazas` chunk_ids/doc_ids de ejemplo, lo
    que conserva la trazabilidad hacia la base vectorial (Sección 7.3) sin
    hacer inmanejable el archivo GraphML.

    Devuelve (nodos, aristas, meta) para poder trabajar por lotes y fusionar.
    """
    nodos: dict[str, AcumuladorNodo] = {}
    # (clave_suj, relacion, clave_obj) -> dict con peso y trazas
    aristas: dict[tuple[str, str, str], dict] = {}

    n_chunks = 0
    n_sin_entidades = 0
    n_tripletas = 0
    conteo_relaciones = Counter()
    conteo_origen = Counter()
    t0 = time.time()

    for chunk in chunks:
        n_chunks += 1
        entidades, tripletas = analizar(
            chunk.texto, usar_capitalizadas=usar_capitalizadas
        )

        if not entidades:
            n_sin_entidades += 1
        else:
            vistos_en_chunk: set[str] = set()
            for ent in entidades:
                clave = clave_entidad(ent.nombre)
                acc = nodos.get(clave)
                if acc is None:
                    acc = AcumuladorNodo(ent.nombre, ent.tipo)
                    nodos[clave] = acc
                # El gazetteer manda sobre las heurísticas
                if ent.origen == "gazetteer" and acc.tipo in ("SIGLA", "NOMBRE"):
                    acc.tipo = ent.tipo
                    acc.nombre = ent.nombre
                acc.frecuencia += 1
                # `chunks` guarda sólo una muestra de trazas para acotar la
                # memoria: en 134k fragmentos, conservar todos los chunk_id de
                # las entidades más frecuentes agotaría la RAM. El conteo
                # exacto se lleva aparte en `n_chunks_total`.
                if clave not in vistos_en_chunk:
                    acc.n_chunks_total += 1
                    if len(acc.chunks) < max_trazas:
                        acc.chunks.add(chunk.chunk_id)
                    acc.docs.add(chunk.doc_id)
                if chunk.fenomeno:
                    acc.fenomenos[chunk.fenomeno] += 1
                if clave not in vistos_en_chunk:
                    conteo_origen[ent.origen] += 1
                    vistos_en_chunk.add(clave)

        for t in tripletas:
            k_suj, k_obj = clave_entidad(t.sujeto), clave_entidad(t.objeto)
            if k_suj == k_obj:
                continue
            llave = (k_suj, t.relacion, k_obj)
            info = aristas.get(llave)
            if info is None:
                info = {"peso": 0, "chunk_ids": [], "doc_ids": set(),
                        "fenomenos": Counter()}
                aristas[llave] = info
            info["peso"] += 1
            if len(info["chunk_ids"]) < max_trazas:
                info["chunk_ids"].append(chunk.chunk_id)
            if len(info["doc_ids"]) < max_trazas:
                info["doc_ids"].add(chunk.doc_id)
            if chunk.fenomeno:
                info["fenomenos"][chunk.fenomeno] += 1
            n_tripletas += 1
            conteo_relaciones[t.relacion] += 1

        if n_chunks % 10000 == 0:
            vel = n_chunks / max(time.time() - t0, 1e-9)
            log.info("  %d chunks | %d nodos | %d aristas | %.0f chunks/s",
                     n_chunks, len(nodos), len(aristas), vel)

    log.info("Extracción terminada: %d chunks en %.1f s",
             n_chunks, time.time() - t0)

    meta = {
        "chunks_procesados": n_chunks,
        "chunks_sin_entidades": n_sin_entidades,
        "menciones_totales": n_tripletas,
        "menciones_por_origen": dict(conteo_origen),
        "relaciones": dict(conteo_relaciones),
        "segundos": round(time.time() - t0, 1),
    }
    return nodos, aristas, meta


# --------------------------------------------------------------------------- #
# Trabajo por lotes: volcado y fusión de resultados parciales
# --------------------------------------------------------------------------- #

def guardar_parcial(nodos: dict, aristas: dict, meta: dict, path: Path) -> None:
    """Serializa un lote. Los `set` se convierten en listas ordenadas."""
    payload = {
        "nodos": {
            k: {
                "nombre": a.nombre, "tipo": a.tipo, "frecuencia": a.frecuencia,
                "n_chunks": a.n_chunks_total,
                "chunks": sorted(a.chunks), "docs": sorted(a.docs),
                "fenomenos": dict(a.fenomenos),
            }
            for k, a in nodos.items()
        },
        "aristas": {
            "\t".join(k): {
                "peso": v["peso"], "chunk_ids": v["chunk_ids"],
                "doc_ids": sorted(v["doc_ids"]), "fenomenos": dict(v["fenomenos"]),
            }
            for k, v in aristas.items()
        },
        "meta": meta,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    log.info("Parcial guardado en %s (%.1f MB)", path, path.stat().st_size / 1e6)


def fusionar_parciales(rutas: list[Path], max_trazas: int = 20
                       ) -> tuple[dict, dict, dict]:
    """Fusiona lotes disjuntos.

    Los lotes cubren rangos de líneas distintos del mismo metadata.jsonl, de
    modo que ningún chunk_id aparece en dos lotes: las frecuencias y los
    conteos de chunks se suman sin riesgo de duplicación. Los doc_id sí pueden
    repetirse entre lotes, por lo que se unen como conjuntos.
    """
    nodos: dict[str, AcumuladorNodo] = {}
    aristas: dict[tuple[str, str, str], dict] = {}
    meta_total = {
        "chunks_procesados": 0, "chunks_sin_entidades": 0,
        "menciones_totales": 0, "menciones_por_origen": Counter(),
        "relaciones": Counter(), "segundos": 0.0,
    }

    for ruta in rutas:
        with ruta.open("rb") as f:
            payload = pickle.load(f)
        log.info("Fusionando %s (%d nodos, %d aristas)", ruta.name,
                 len(payload["nodos"]), len(payload["aristas"]))

        for clave, d in payload["nodos"].items():
            acc = nodos.get(clave)
            if acc is None:
                acc = AcumuladorNodo(d["nombre"], d["tipo"])
                nodos[clave] = acc
            if acc.tipo in ("SIGLA", "NOMBRE") and d["tipo"] not in ("SIGLA", "NOMBRE"):
                acc.tipo = d["tipo"]
                acc.nombre = d["nombre"]
            acc.frecuencia += d["frecuencia"]
            if len(acc.chunks) < max_trazas:
                acc.chunks.update(d["chunks"][:max_trazas])
            acc.n_chunks_total += d["n_chunks"]
            if len(acc.docs) < 200:
                acc.docs.update(d["docs"])
            for fen, c in d["fenomenos"].items():
                acc.fenomenos[int(fen)] += c

        for clave_str, v in payload["aristas"].items():
            clave = tuple(clave_str.split("\t"))
            info = aristas.get(clave)
            if info is None:
                info = {"peso": 0, "chunk_ids": [], "doc_ids": set(),
                        "fenomenos": Counter()}
                aristas[clave] = info
            info["peso"] += v["peso"]
            if len(info["chunk_ids"]) < max_trazas:
                info["chunk_ids"].extend(v["chunk_ids"][:max_trazas])
                del info["chunk_ids"][max_trazas:]
            info["doc_ids"].update(v["doc_ids"])
            for fen, c in v["fenomenos"].items():
                info["fenomenos"][int(fen)] += c

        m = payload["meta"]
        meta_total["chunks_procesados"] += m["chunks_procesados"]
        meta_total["chunks_sin_entidades"] += m["chunks_sin_entidades"]
        meta_total["menciones_totales"] += m["menciones_totales"]
        meta_total["menciones_por_origen"].update(m["menciones_por_origen"])
        meta_total["relaciones"].update(m["relaciones"])
        meta_total["segundos"] += m["segundos"]

    meta_total["menciones_por_origen"] = dict(meta_total["menciones_por_origen"])
    meta_total["relaciones"] = dict(meta_total["relaciones"])
    log.info("Fusión completa: %d nodos, %d aristas", len(nodos), len(aristas))
    return nodos, aristas, meta_total


# --------------------------------------------------------------------------- #
# Montaje del grafo
# --------------------------------------------------------------------------- #

def montar_grafo(nodos: dict, aristas: dict, meta: dict,
                 max_trazas: int = 20,
                 min_freq_nodo: int = 2,
                 min_peso_arista: int = 1,
                 ) -> tuple[nx.MultiDiGraph, dict]:
    t0 = time.time()
    n_chunks = meta["chunks_procesados"]
    n_sin_entidades = meta["chunks_sin_entidades"]
    n_tripletas = meta["menciones_totales"]
    conteo_origen = Counter(meta["menciones_por_origen"])
    conteo_relaciones = Counter(meta["relaciones"])

    # --- Poda ---
    # Las entidades del gazetteer siempre se conservan; las heurísticas
    # (siglas y nombres propios emergentes) deben superar una frecuencia
    # mínima para no llenar el grafo con ruido de maquetación de los PDFs.
    claves_validas = {
        k for k, acc in nodos.items()
        if acc.tipo not in ("SIGLA", "NOMBRE") or acc.frecuencia >= min_freq_nodo
    }
    log.info("Poda de nodos: %d -> %d (min_freq=%d para SIGLA/NOMBRE)",
             len(nodos), len(claves_validas), min_freq_nodo)

    # --- Montaje del grafo ---
    G = nx.MultiDiGraph()
    for clave in claves_validas:
        acc = nodos[clave]
        G.add_node(
            acc.nombre,
            tipo=acc.tipo,
            frecuencia=acc.frecuencia,
            n_chunks=acc.n_chunks_total,
            n_docs=len(acc.docs),
            fenomenos=",".join(str(f) for f, _ in acc.fenomenos.most_common()),
            # GraphML no admite listas: se serializan como CSV
            chunk_ids=",".join(sorted(acc.chunks)[:max_trazas]),
        )

    n_aristas_podadas = 0
    for (k_suj, relacion, k_obj), info in aristas.items():
        if k_suj not in claves_validas or k_obj not in claves_validas:
            n_aristas_podadas += 1
            continue
        if info["peso"] < min_peso_arista:
            n_aristas_podadas += 1
            continue
        G.add_edge(
            nodos[k_suj].nombre, nodos[k_obj].nombre,
            key=relacion,
            relacion=relacion,
            peso=info["peso"],
            fenomenos=",".join(str(f) for f, _ in info["fenomenos"].most_common()),
            doc_ids=",".join(sorted(info["doc_ids"])),
            chunk_ids=",".join(info["chunk_ids"]),
        )

    aislados = [n for n, g in G.degree() if g == 0]

    estadisticas = {
        "chunks_procesados": n_chunks,
        "chunks_sin_entidades": n_sin_entidades,
        "menciones_totales": n_tripletas,
        "nodos": G.number_of_nodes(),
        "aristas": G.number_of_edges(),
        "aristas_podadas": n_aristas_podadas,
        "nodos_aislados": len(aislados),
        "nodos_por_tipo": dict(Counter(
            d["tipo"] for _, d in G.nodes(data=True)
        ).most_common()),
        "menciones_por_origen": dict(conteo_origen.most_common()),
        "relaciones_mas_frecuentes": dict(conteo_relaciones.most_common(40)),
        "segundos_extraccion": meta.get("segundos", 0.0),
        "segundos_montaje": round(time.time() - t0, 1),
    }

    log.info("Grafo construido: %d nodos (%d aislados), %d aristas",
             G.number_of_nodes(), len(aislados), G.number_of_edges())
    return G, estadisticas


# --------------------------------------------------------------------------- #
# 5. Exportación
# --------------------------------------------------------------------------- #

def exportar(G: nx.MultiDiGraph, estadisticas: dict, path_salida: Path) -> None:
    path_salida.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(G, path_salida)
    log.info("Grafo exportado a %s (%.1f MB)",
             path_salida, path_salida.stat().st_size / 1e6)

    path_stats = path_salida.with_name(path_salida.stem + "_estadisticas.json")
    path_stats.write_text(
        json.dumps(estadisticas, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Estadísticas escritas en %s", path_stats)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path,
                        help="Ruta al metadata.jsonl (Tabla 1 de la spec).")
    parser.add_argument("--output", type=Path, default=Path("grafo/grafo.graphml"),
                        help="Ruta de salida del GraphML.")
    parser.add_argument("--limite", type=int, default=None,
                        help="Procesar solo los primeros N chunks (pruebas).")
    # --- trabajo por lotes (equipos con poca RAM o límites de tiempo) ---
    parser.add_argument("--desde", type=int, default=0,
                        help="Primera línea del metadata a procesar (lote).")
    parser.add_argument("--hasta", type=int, default=None,
                        help="Línea final exclusiva del lote.")
    parser.add_argument("--parcial", type=Path, default=None,
                        help="Guardar el resultado del lote en este .pkl y salir.")
    parser.add_argument("--fusionar", type=Path, nargs="+", default=None,
                        help="Fusionar estos .pkl parciales y montar el grafo.")
    parser.add_argument("--max-trazas", type=int, default=20,
                        help="Máximo de chunk_ids guardados por nodo/arista.")
    parser.add_argument("--min-freq-nodo", type=int, default=3,
                        help="Frecuencia mínima para entidades heurísticas.")
    parser.add_argument("--min-peso-arista", type=int, default=1,
                        help="Peso mínimo (nº de fragmentos) para conservar una arista.")
    parser.add_argument("--sin-capitalizadas", action="store_true",
                        help="Usar solo el gazetteer, sin nombres propios emergentes.")
    parser.add_argument("--sin-aislados", action="store_true",
                        help="Eliminar los nodos sin ninguna relación.")
    args = parser.parse_args(argv)

    # --- Modo fusión: no se vuelve a leer el corpus ---
    if args.fusionar:
        nodos, aristas, meta = fusionar_parciales(args.fusionar,
                                                  max_trazas=args.max_trazas)
    else:
        if not args.metadata or not args.metadata.exists():
            log.error("No se encontró el archivo de metadata: %s", args.metadata)
            return 1
        nodos, aristas, meta = acumular(
            iterar_chunks(args.metadata, args.limite, args.desde, args.hasta),
            max_trazas=args.max_trazas,
            usar_capitalizadas=not args.sin_capitalizadas,
        )
        # --- Modo lote: se guarda el parcial y se termina ---
        if args.parcial:
            guardar_parcial(nodos, aristas, meta, args.parcial)
            return 0

    G, stats = montar_grafo(
        nodos, aristas, meta,
        max_trazas=args.max_trazas,
        min_freq_nodo=args.min_freq_nodo,
        min_peso_arista=args.min_peso_arista,
    )

    if args.sin_aislados:
        aislados = [n for n, g in G.degree() if g == 0]
        G.remove_nodes_from(aislados)
        stats["nodos"] = G.number_of_nodes()
        stats["nodos_aislados"] = 0
        log.info("Eliminados %d nodos aislados -> %d nodos",
                 len(aislados), G.number_of_nodes())

    exportar(G, stats, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
