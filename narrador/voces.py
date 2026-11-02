"""Reparte voces de Microsoft Edge (edge-tts) entre los personajes según su género y edad."""
import asyncio

import edge_tts

COLORES = ["#c98a12", "#d6455d", "#2e7d32", "#7a5c12", "#8a4f9e", "#00796b", "#e07b00", "#c2185b",
           "#455a64", "#9e9d24", "#6a1b9a", "#d35400", "#5d6d7e", "#ad7fa8", "#1565c0", "#e57373"]

# Ajustes de tono y velocidad por edad (sobre la voz base)
POR_EDAD = {"niño": ("+0%", "+30Hz"), "joven": ("+5%", "+8Hz"), "adulto": ("+0%", "+0Hz"),
            "anciano": ("-10%", "-12Hz")}
# Variaciones para que dos personajes con la misma voz base no suenen igual
VARIANTES = [("+0%", 0), ("-8%", -12), ("+10%", 10), ("-4%", 18), ("+6%", -20)]


def voces_disponibles(idioma):
    lista = asyncio.run(edge_tts.list_voices())
    return [v for v in lista if v["Locale"].lower().startswith(idioma.lower())]


def asignar(personajes, cuantas_lineas, idioma="es", narrador="es-MX-JorgeNeural"):
    """personajes: {id: {nombre, genero, edad}} -> {id: {nombre, voz, rate, pitch, color}}"""
    disp = voces_disponibles(idioma)
    if not any(v["ShortName"] == narrador for v in disp):
        narrador = next(v["ShortName"] for v in disp if v["Gender"] == "Male") if disp else narrador
    # las voces del mismo país que el narrador primero
    pais = narrador.split("-")[1] if "-" in narrador else ""
    disp.sort(key=lambda v: (v["Locale"].split("-")[-1] != pais, v["ShortName"]))
    por_genero = {
        "masculino": [v["ShortName"] for v in disp if v["Gender"] == "Male" and v["ShortName"] != narrador],
        "femenino": [v["ShortName"] for v in disp if v["Gender"] == "Female"],
    }
    por_genero["neutro"] = por_genero["femenino"] + por_genero["masculino"]

    voces = {"narrador": {"nombre": "Narrador", "voz": narrador, "rate": "-5%", "pitch": "+0Hz",
                          "color": "#3b6ea5"}}
    usadas = {}
    # los personajes que más hablan escogen voz primero
    for k, pid in enumerate(sorted(personajes, key=lambda p: -cuantas_lineas.get(p, 0))):
        info = personajes[pid]
        edad = info.get("edad", "adulto")
        genero = info.get("genero", "neutro")
        if edad == "niño":   # en edge-tts no hay voces de niño: voz de mujer más aguda
            genero = "femenino"
        lista = por_genero.get(genero) or por_genero["neutro"] or [narrador]
        base = min(lista, key=lambda v: (usadas.get(v, 0), lista.index(v)))
        vez = usadas.get(base, 0)
        usadas[base] = vez + 1
        rate_e, pitch_e = POR_EDAD.get(edad, POR_EDAD["adulto"])
        rate_v, pitch_v = VARIANTES[vez % len(VARIANTES)]
        rate = int(rate_e.rstrip("%")) + int(rate_v.rstrip("%"))
        pitch = int(pitch_e.rstrip("Hz")) + pitch_v
        voces[pid] = {"nombre": info.get("nombre", pid), "voz": base,
                      "rate": f"{rate:+d}%", "pitch": f"{pitch:+d}Hz",
                      "color": COLORES[k % len(COLORES)]}
    return voces
