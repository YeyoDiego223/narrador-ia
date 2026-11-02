"""narrador-ia: convierte un libro (PDF, EPUB o TXT) en audiolibro con una voz por personaje
y un lector web que resalta cada palabra mientras se escucha.

  python narrador.py crear mi_libro.pdf
  python narrador.py generar salida/mi_libro      (después de editar proyecto.json)
"""
import argparse, json, os, re, sys, unicodedata

from narrador import audio, dialogos, extraer, ia, servidor, voces

AQUI = os.path.dirname(os.path.abspath(__file__))


def slug(texto):
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "_", t).strip("_").lower()[:50] or "libro"


def guardar(proyecto, carpeta):
    limpio = {**proyecto, "capitulos": [
        {**c, "segmentos": [{k: v for k, v in s.items() if not k.startswith("_")} for s in c["segmentos"]]}
        for c in proyecto["capitulos"]]}
    with open(os.path.join(carpeta, "proyecto.json"), "w", encoding="utf-8") as f:
        json.dump(limpio, f, ensure_ascii=False, indent=1)


def resolver_continuaciones(cap):
    """Las réplicas que siguen tras un inciso ("=") son del mismo que habló antes."""
    ultimo = "narrador"
    for s in cap["segmentos"]:
        if s.get("p") == "=":
            s["p"] = ultimo
        elif s.get("p") not in (None, "narrador") or (s.get("_replica")):
            ultimo = s["p"]


def crear(args):
    libro_ruta = os.path.abspath(args.libro)
    carpeta = os.path.abspath(args.salida or os.path.join("salida", slug(os.path.splitext(os.path.basename(libro_ruta))[0])))
    os.makedirs(carpeta, exist_ok=True)
    ruta_proyecto = os.path.join(carpeta, "proyecto.json")

    if os.path.exists(ruta_proyecto) and not args.desde_cero:
        print(f"Retomando {ruta_proyecto}")
        proyecto = json.load(open(ruta_proyecto, encoding="utf-8"))
    else:
        print("1/4 Leyendo el libro…")
        libro = extraer.extraer(libro_ruta, os.path.join(carpeta, "img"))
        caps = libro["capitulos"][:args.capitulos] if args.capitulos else libro["capitulos"]
        proyecto = {
            "titulo": args.titulo or libro["titulo"], "autor": args.autor or libro["autor"],
            "personajes": {}, "glosario": {}, "voces": {},
            "capitulos": [{"titulo": c["titulo"], "segmentos": dialogos.segmentar(c["bloques"])} for c in caps],
        }
        replicas = sum(s.get("p") == "?" for c in proyecto["capitulos"] for s in c["segmentos"])
        print(f"    {len(caps)} capítulos, {replicas} réplicas de diálogo")
        guardar(proyecto, carpeta)

    # 2) quién habla + glosario
    pendientes = [c for c in proyecto["capitulos"] if not c.get("analizado")]
    if pendientes and args.sin_ia:
        print("2/4 Sin IA: todas las réplicas con una sola voz de diálogo")
        proyecto["personajes"].setdefault("dialogo", {"nombre": "Diálogos", "genero": "neutro", "edad": "adulto"})
        for c in pendientes:
            for s in c["segmentos"]:
                if s.get("p") == "?":
                    s["p"] = "dialogo"
            resolver_continuaciones(c)
            c["analizado"] = True
        guardar(proyecto, carpeta)
    elif pendientes:
        clave = ia.clave_api([os.getcwd(), AQUI])
        print(f"2/4 Gemini ({ia.MODELO}) lee {len(pendientes)} capítulos para saber quién habla…")
        for k, c in enumerate(pendientes, 1):
            print(f"    [{k}/{len(pendientes)}] {c['titulo']}")
            glos = ia.analizar_capitulo(clave, c, proyecto["personajes"], args.glosario)
            for g in glos:
                forma = g["forma"].strip().lower()
                if forma and " " not in forma:
                    proyecto["glosario"][f"{forma}@{c['titulo']}"] = {"palabra": g["palabra"], "def": g["definicion"]}
            resolver_continuaciones(c)
            c["analizado"] = True
            guardar(proyecto, carpeta)   # se guarda cada capítulo: si se corta, se retoma aquí

    # 3) voces
    if not proyecto["voces"] or set(proyecto["personajes"]) - set(proyecto["voces"]):
        print("3/4 Repartiendo voces…")
        cuenta = {}
        for c in proyecto["capitulos"]:
            for s in c["segmentos"]:
                if "t" in s:
                    cuenta[s["p"]] = cuenta.get(s["p"], 0) + 1
        nuevas = voces.asignar(proyecto["personajes"], cuenta, args.idioma, args.narrador)
        proyecto["voces"] = {**nuevas, **proyecto["voces"]}   # respeta las que ya editaste
        guardar(proyecto, carpeta)
        for pid, v in proyecto["voces"].items():
            print(f"    {v['nombre']:<28} {v['voz']} {v['rate']} {v['pitch']}  ({cuenta.get(pid, 0)} líneas)")

    generar_en(carpeta, proyecto)


def generar_en(carpeta, proyecto=None):
    proyecto = proyecto or json.load(open(os.path.join(carpeta, "proyecto.json"), encoding="utf-8"))
    print("4/4 Generando el audio…")
    total = audio.generar(proyecto, carpeta)
    print(f"\nListo: {total / 60:.1f} min de audio")
    print(f"Abre {os.path.join(carpeta, 'lector.html')}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Libro -> audiolibro con voces por personaje y lector web")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crear", help="crea el audiolibro a partir de un PDF, EPUB o TXT")
    c.add_argument("libro")
    c.add_argument("--salida", help="carpeta de salida (por defecto salida/<nombre del libro>)")
    c.add_argument("--titulo"); c.add_argument("--autor")
    c.add_argument("--sin-ia", action="store_true", help="no usar Gemini: una sola voz para los diálogos")
    c.add_argument("--capitulos", type=int, help="solo los primeros N capítulos (para probar)")
    c.add_argument("--glosario", type=int, default=15, help="palabras de glosario por capítulo (0 = ninguna)")
    c.add_argument("--idioma", default="es", help="idioma de las voces (es, en, fr…)")
    c.add_argument("--narrador", default="es-MX-JorgeNeural", help="voz del narrador")
    c.add_argument("--desde-cero", action="store_true", help="ignora el proyecto.json que ya exista")
    g = sub.add_parser("generar", help="vuelve a generar audio y lector desde proyecto.json")
    g.add_argument("carpeta")
    s = sub.add_parser("servir", help="comparte el lector en tu red local para leerlo en el teléfono")
    s.add_argument("carpeta")
    s.add_argument("--puerto", type=int, default=8080)
    args = ap.parse_args()
    if args.cmd == "crear":
        crear(args)
    elif args.cmd == "generar":
        generar_en(os.path.abspath(args.carpeta))
    else:
        servidor.servir(os.path.abspath(args.carpeta), args.puerto)


if __name__ == "__main__":
    main()
