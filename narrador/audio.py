"""Genera el audio de cada segmento con edge-tts (con el tiempo de cada palabra) y lo une.

Cada fragmento se guarda en cache/ con un nombre sacado de su texto y su voz:
si cambias una línea o una voz, solo se vuelve a generar eso.
"""
import asyncio, hashlib, json, os, re, shutil, subprocess, wave

import edge_tts

PAUSA_MISMO = 0.25    # segundos entre segmentos del mismo párrafo
PAUSA_PARRAFO = 0.6
PAUSA_CAPITULO = 1.5
RATE = 24000          # Hz del wav de trabajo
EN_PARALELO = 6


def _clave(texto, v):
    return hashlib.sha1(f"{texto}|{v['voz']}|{v['rate']}|{v['pitch']}".encode()).hexdigest()[:16]


async def _sintetizar(texto, v, base, sem):
    if os.path.exists(base + ".json"):
        return
    async with sem:
        for intento in range(4):
            try:
                com = edge_tts.Communicate(texto, v["voz"], rate=v["rate"], pitch=v["pitch"],
                                           boundary="WordBoundary")
                audio, palabras = bytearray(), []
                async for ch in com.stream():
                    if ch["type"] == "audio":
                        audio += ch["data"]
                    elif ch["type"] == "WordBoundary":
                        ini = ch["offset"] / 1e7
                        palabras.append((ch["text"], ini, ini + ch["duration"] / 1e7))
                break
            except Exception as e:
                if intento == 3:
                    raise
                print("    reintento:", e)
                await asyncio.sleep(2 + intento * 3)
    with open(base + ".mp3", "wb") as f:
        f.write(audio)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", base + ".mp3",
                    "-ac", "1", "-ar", str(RATE), base + ".wav"], check=True)
    json.dump(palabras, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False)


def _duracion(wav):
    with wave.open(wav) as w:
        return w.getnframes() / RATE


def _alinear(texto, palabras, t0):
    """Ubica cada palabra hablada dentro del texto original (con su puntuación)."""
    trozos, cursor = [], 0
    for pal, ini, fin in palabras:
        m = re.search(re.escape(pal), texto[cursor:], re.IGNORECASE)
        if not m:
            continue
        a, b = cursor + m.start(), cursor + m.end()
        if a > cursor:
            trozos.append({"x": texto[cursor:a]})
        trozos.append({"x": texto[a:b], "i": round(t0 + ini, 3), "f": round(t0 + fin, 3)})
        cursor = b
    if cursor < len(texto):
        trozos.append({"x": texto[cursor:]})
    return trozos


async def _sintetizar_todo(proyecto, cache):
    sem = asyncio.Semaphore(EN_PARALELO)
    tareas = []
    for cap in proyecto["capitulos"]:
        for seg in cap["segmentos"]:
            if "t" in seg:
                v = proyecto["voces"][seg["p"]]
                seg["_base"] = os.path.join(cache, _clave(seg["t"], v))
                tareas.append(_sintetizar(seg["t"], v, seg["_base"], sem))
    faltan = sum(not os.path.exists(s["_base"] + ".json")
                 for c in proyecto["capitulos"] for s in c["segmentos"] if "t" in s)
    print(f"  {len(tareas)} fragmentos de voz, faltan {faltan}")
    for k, t in enumerate(asyncio.as_completed(tareas), 1):
        await t
        if k % 100 == 0:
            print(f"    {k}/{len(tareas)}")


def generar(proyecto, carpeta):
    cache = os.path.join(carpeta, "cache")
    os.makedirs(cache, exist_ok=True)
    asyncio.run(_sintetizar_todo(proyecto, cache))

    lista, t, salida = [], 0.0, []

    def silencio(seg):
        nonlocal t
        nombre = os.path.join(cache, f"sil_{int(seg * 1000)}.wav")
        if not os.path.exists(nombre):
            with wave.open(nombre, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
                w.writeframes(b"\0\0" * int(seg * RATE))
        lista.append(nombre)
        t += seg

    for c, cap in enumerate(proyecto["capitulos"]):
        if c:
            silencio(PAUSA_CAPITULO)
        cap_out = {"titulo": cap["titulo"], "parrafos": []}
        hubo_audio = False
        for seg in cap["segmentos"]:
            if "img" in seg:   # dibujo: va en su propio "párrafo", sin audio
                cap_out["parrafos"].append({"img": seg["img"], "alt": seg.get("alt", "Ilustración")})
                continue
            if hubo_audio:
                silencio(PAUSA_MISMO if seg.get("mismo") else PAUSA_PARRAFO)
            if not cap_out["parrafos"] or not seg.get("mismo") or isinstance(cap_out["parrafos"][-1], dict):
                cap_out["parrafos"].append([])
            palabras = json.load(open(seg["_base"] + ".json", encoding="utf-8"))
            cap_out["parrafos"][-1].append({"p": seg["p"], "inicio": round(t, 3),
                                            "trozos": _alinear(seg["t"], palabras, t)})
            lista.append(seg["_base"] + ".wav")
            t += _duracion(seg["_base"] + ".wav")
            hubo_audio = True
        salida.append(cap_out)

    with open(os.path.join(cache, "lista.txt"), "w", encoding="utf-8") as f:
        f.writelines(f"file '{p.replace(os.sep, '/')}'\n" for p in lista)
    print("  Uniendo el audio…")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", os.path.join(cache, "lista.txt"), "-b:a", "96k",
                    os.path.join(carpeta, "audio.mp3")], check=True)

    datos = {"titulo": proyecto["titulo"], "autor": proyecto.get("autor", ""),
             "portada": proyecto.get("portada"), "voces": proyecto["voces"],
             "glosario": proyecto.get("glosario", {}), "capitulos": salida, "duracion": round(t, 3)}
    with open(os.path.join(carpeta, "datos.js"), "w", encoding="utf-8") as f:
        f.write("const LIBRO = " + json.dumps(datos, ensure_ascii=False) + ";\n")
    shutil.copy(os.path.join(os.path.dirname(__file__), "lector.html"), os.path.join(carpeta, "lector.html"))
    return t
