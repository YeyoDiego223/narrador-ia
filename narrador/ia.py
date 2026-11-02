"""Gemini decide quién dice cada réplica y escribe el glosario de cada capítulo."""
import json, os, time, urllib.error, urllib.request

MODELO = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

ESQUEMA = {
    "type": "OBJECT",
    "properties": {
        "personajes_nuevos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"},
            "nombre": {"type": "STRING"},
            "genero": {"type": "STRING", "enum": ["masculino", "femenino", "neutro"]},
            "edad": {"type": "STRING", "enum": ["niño", "joven", "adulto", "anciano"]},
        }, "required": ["id", "nombre", "genero", "edad"]}},
        "replicas": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "n": {"type": "INTEGER"},
            "id": {"type": "STRING"},
        }, "required": ["n", "id"]}},
        "glosario": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "forma": {"type": "STRING"},
            "palabra": {"type": "STRING"},
            "definicion": {"type": "STRING"},
        }, "required": ["forma", "palabra", "definicion"]}},
    },
    "required": ["personajes_nuevos", "replicas", "glosario"],
}

INSTRUCCIONES = """Eres editor de audiolibros. Te paso un capítulo de un libro. Las réplicas de diálogo
están marcadas con [#n] al inicio.

1. Para CADA réplica [#n] di qué personaje la dice (campo "replicas", una entrada por cada n).
   - Si la dice quien narra la historia (narrador en primera persona), usa el id "narrador".
   - Usa el id de un personaje conocido si es el mismo personaje.
   - Si es un personaje nuevo, invéntale un id corto en minúsculas sin espacios ni acentos
     (por ejemplo "zorro", "rey", "maria") y agrégalo a "personajes_nuevos" con su nombre,
     género y edad aproximada.
   - Personajes que solo dicen una línea y no tienen nombre pueden compartir un id genérico
     como "hombre", "mujer" o "nino".
2. En "glosario" elige hasta {max_glosario} palabras o expresiones de este capítulo que un lector
   joven podría no entender. "forma" = la palabra EXACTAMENTE como aparece en el texto (una sola
   palabra). "palabra" = su forma de diccionario (o la expresión completa). "definicion" = qué
   significa AHÍ, en ese momento de la historia, en una o dos frases sencillas.

Personajes conocidos hasta ahora (id: nombre):
{conocidos}

CAPÍTULO "{titulo}":
{texto}"""


def clave_api(carpetas):
    clave = os.environ.get("GEMINI_API_KEY")
    if clave:
        return clave
    for c in carpetas:
        ruta = os.path.join(c, ".env")
        if os.path.exists(ruta):
            for linea in open(ruta, encoding="utf-8"):
                if linea.strip().startswith("GEMINI_API_KEY="):
                    return linea.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("Falta la clave de Gemini: crea un archivo .env con GEMINI_API_KEY=tu_clave "
                     "(sácala gratis en https://aistudio.google.com/apikey) o usa --sin-ia")


def preguntar(clave, prompt):
    cuerpo = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": ESQUEMA,
                             "temperature": 0.2},
    }).encode()
    for intento in range(6):
        req = urllib.request.Request(URL.format(MODELO), data=cuerpo, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": clave})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                datos = json.load(r)
            return json.loads(datos["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 503) and intento < 5:
                espera = 20 * (intento + 1)
                print(f"    Gemini ocupado ({e.code}), espero {espera}s…")
                time.sleep(espera)
                continue
            raise SystemExit(f"Gemini respondió {e.code}: {detalle}")
        except (KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as e:
            if intento < 5:
                print(f"    respuesta rara de Gemini ({e}), reintento…")
                time.sleep(5)
                continue
            raise


def texto_marcado(segs):
    """El capítulo como texto, con las réplicas a decidir marcadas [#n]."""
    partes, n = [], 0
    for s in segs:
        if "t" not in s:
            continue
        if s["p"] == "?":
            n += 1
            partes.append(f"\n[#{n}] {s['t']}")
        elif s.get("mismo"):
            partes.append(" " + s["t"])
        else:
            partes.append("\n" + s["t"])
    return "".join(partes).strip(), n


def analizar_capitulo(clave, cap, personajes, max_glosario=15):
    """Llena los "?" de cap["segmentos"]; agrega personajes nuevos; devuelve el glosario."""
    texto, total = texto_marcado(cap["segmentos"])
    if total == 0 and max_glosario == 0:
        return []
    conocidos = "\n".join(f"- {k}: {v['nombre']}" for k, v in personajes.items()) or "(ninguno)"
    r = preguntar(clave, INSTRUCCIONES.format(max_glosario=max_glosario, conocidos=conocidos,
                                              titulo=cap["titulo"], texto=texto))
    for p in r.get("personajes_nuevos", []):
        if p["id"] not in personajes and p["id"] != "narrador":
            personajes[p["id"]] = {"nombre": p["nombre"], "genero": p["genero"], "edad": p["edad"]}
    quien = {x["n"]: x["id"] for x in r.get("replicas", [])}
    n = 0
    for s in cap["segmentos"]:
        if s.get("p") == "?":
            n += 1
            pid = quien.get(n, "narrador")
            if pid != "narrador" and pid not in personajes:
                personajes[pid] = {"nombre": pid.capitalize(), "genero": "neutro", "edad": "adulto"}
            s["p"] = pid
    return r.get("glosario", [])
