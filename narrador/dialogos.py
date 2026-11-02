"""Corta los párrafos en segmentos de narración y de diálogo (réplicas con raya —)."""
import re


def partir(par):
    """Corta un párrafo en trozos (tipo, texto, nueva_replica).

    '—¡Hola —dijo él—, qué tal! —Bien.' ->
      habla '—¡Hola' | narra '—dijo él—,' | habla 'qué tal!' | habla (réplica nueva) '—Bien.'
    """
    out, cursor = [], 0
    habla = par.startswith("—")
    nueva = True
    for m in re.finditer(r"—", par):
        i = m.start()
        if i == 0:
            continue
        antes = par[:i].rstrip()[-1:] or ""
        despues = par[i + 1:].lstrip()[:1]
        pegado = not par[i - 1].isspace()   # "rey—te ordeno": raya pegada a la palabra anterior
        if not despues or despues in ",.;:" or (not habla and pegado):
            # raya que cierra un inciso: vuelve a hablar el mismo personaje
            if habla:
                continue
            out.append(("narra", par[cursor:i + 1].strip(), nueva)); nueva = False
            cursor, habla = i + 1, True
        elif despues.islower():
            # raya que abre un inciso del narrador
            if not habla:
                continue
            out.append(("habla", par[cursor:i].strip(), nueva)); nueva = False
            cursor, habla = i, False
        elif antes in ".?!…:»”\"":
            # raya que abre una réplica nueva
            out.append(("habla" if habla else "narra", par[cursor:i].strip(), nueva))
            cursor, habla, nueva = i, True, True
    out.append(("habla" if habla else "narra", par[cursor:].strip(), nueva))
    limpio = []
    for t, x, n in out:
        if not x:
            continue
        # puntuación al principio ("ella—. Mi papá…"): se pega al trozo anterior
        signos = (re.fullmatch(r"[\s,.;:—]+", x) or re.match(r"\s*[,.;:]+\s*", x)) if limpio else None
        if signos:
            limpio[-1] = (limpio[-1][0], limpio[-1][1] + signos.group().strip(), limpio[-1][2])
            x = x[signos.end():]
        if x:
            limpio.append((t, x, n))
    return limpio


def segmentar(bloques):
    """Párrafos -> segmentos {p, t, mismo?} o {img}.

    p = "narrador", "?" (réplica nueva, falta saber quién la dice)
        o "=" (sigue la réplica anterior después de un inciso).
    """
    segs = []
    for b in bloques:
        if isinstance(b, dict):
            segs.append(b)
            continue
        for k, (t, x, nueva) in enumerate(partir(b)):
            s = {"p": "narrador" if t == "narra" else "?", "t": x}
            if s["p"] == "?":
                s["_replica"] = True
            if k and not nueva:
                s["mismo"] = True
                if t == "habla":
                    # en párrafos de narración ("Es raro —pensó—, y ...") lo sigue leyendo el narrador
                    s["p"] = "=" if b.startswith("—") else "narrador"
            segs.append(s)
    return segs
