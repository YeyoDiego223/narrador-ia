"""Saca el texto de un libro (PDF, EPUB o TXT) en capítulos -> párrafos, con sus dibujos.

Devuelve: {"titulo", "autor", "capitulos": [{"titulo", "bloques": [str | {"img": ruta}]}]}
"""
import html.parser, os, posixpath, re, zipfile
from collections import Counter
from xml.etree import ElementTree

ROMANO = r"[IVXLC]{1,7}"
TITULO_CAP = re.compile(
    rf"^(cap[ií]tulo|parte|libro)\s+(\d+|{ROMANO}|[a-záéíóú]+)\b.*$|^{ROMANO}\.?$|^\d{{1,3}}\.?$",
    re.IGNORECASE)
FIN_FRASE = tuple(".!?…:»”\")")


def es_titulo(txt):
    return len(txt) <= 60 and bool(TITULO_CAP.match(txt.strip()))


def limpiar(s):
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(\w) ([áéíóú])(?=[\s,.;:!?…]|$)", r"\1\2", s)   # "grit ó" -> "gritó"
    if s.startswith("—"):
        s = re.sub(r"^— +", "—", s)
    return s


def juntar_lineas(lineas):
    """Une las líneas de un párrafo quitando los guiones de corte de palabra."""
    out = ""
    for l in lineas:
        l = l.strip()
        if out.endswith("-") and l[:1].islower():
            out = out[:-1] + l
        else:
            out = (out + " " + l) if out else l
    return limpiar(out)


def armar_capitulos(bloques):
    """[str | img] -> capítulos, cortando en los títulos de capítulo."""
    caps = [{"titulo": "Inicio", "bloques": []}]
    for b in bloques:
        if isinstance(b, str) and es_titulo(b):
            caps.append({"titulo": b.strip().rstrip("."), "bloques": []})
        else:
            caps[-1]["bloques"].append(b)
    caps = [c for c in caps if any(isinstance(b, str) for b in c["bloques"])]
    if len(caps) <= 1:   # no se encontraron capítulos: partir en trozos de ~40 párrafos
        todo = caps[0]["bloques"] if caps else []
        caps = [{"titulo": f"Parte {i // 40 + 1}", "bloques": todo[i:i + 40]} for i in range(0, len(todo), 40)]
    return caps


# ---------------------------------------------------------------- PDF
def de_pdf(ruta, carpeta_img):
    import fitz
    doc = fitz.open(ruta)

    # líneas que se repiten en muchas páginas = encabezados / pies de página
    conteo = Counter()
    for page in doc:
        vistos = set()
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                t = "".join(s["text"] for s in l["spans"]).strip()
                if t and t not in vistos:
                    vistos.add(t); conteo[t] += 1
    repetidas = {t for t, n in conteo.items() if n >= 3 and n >= len(doc) * 0.2 and not es_titulo(t)}

    bloques = []
    for pn, page in enumerate(doc):
        imgs = {tuple(round(v) for v in i["bbox"]): i["xref"] for i in page.get_image_info(xrefs=True)}
        items = []
        for b in page.get_text("dict")["blocks"]:
            y = b["bbox"][1]
            if b["type"] == 1:
                w, h = b["bbox"][2] - b["bbox"][0], b["bbox"][3] - b["bbox"][1]
                if w < 40 or h < 40 or not imgs:
                    continue
                key = tuple(round(v) for v in b["bbox"])
                xref = imgs.get(key) or min(imgs.items(), key=lambda kv: sum(abs(a - c) for a, c in zip(kv[0], key)))[1]
                nombre = f"p{pn + 1:03d}_{xref}.png"
                destino = os.path.join(carpeta_img, nombre)
                if not os.path.exists(destino):
                    pix = fitz.Pixmap(doc, xref)
                    if pix.alpha or pix.n > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    pix.save(destino)
                items.append((y, {"img": "img/" + nombre}))
            else:
                lineas = ["".join(s["text"] for s in l["spans"]) for l in b["lines"]]
                lineas = [l for l in lineas if l.strip() and l.strip() not in repetidas
                          and not re.fullmatch(r"\s*\d+\s*", l)                     # número de página
                          and not re.fullmatch(r"\s*(https?://|www\.)\S+\s*", l)]  # URL suelta
                if not lineas:
                    continue
                # un bloque puede traer varios párrafos: cortar donde una línea empieza con raya
                pars, buf = [], []
                for l in lineas:
                    if l.lstrip().startswith("—") and buf:
                        pars.append(juntar_lineas(buf)); buf = []
                    buf.append(l)
                pars.append(juntar_lineas(buf))
                items.extend((y, p) for p in pars if p)
        primero = True
        for _, it in sorted(items, key=lambda t: t[0]):
            # párrafo partido por el cambio de página
            if primero and isinstance(it, str) and bloques and isinstance(bloques[-1], str) \
                    and not es_titulo(it) and not es_titulo(bloques[-1]) \
                    and (it[:1].islower() or not bloques[-1].endswith(FIN_FRASE)):
                bloques[-1] = bloques[-1] + " " + it
            else:
                bloques.append(it)
            if isinstance(it, str):
                primero = False
    meta = doc.metadata or {}
    return {"titulo": meta.get("title") or "", "autor": meta.get("author") or "",
            "capitulos": armar_capitulos(bloques)}


# ---------------------------------------------------------------- EPUB
class _Xhtml(html.parser.HTMLParser):
    BLOQUE = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div"}

    def __init__(self):
        super().__init__()
        self.bloques, self.buf, self.titulo, self.en_titulo, self.saltar = [], [], None, False, 0

    def _cerrar(self):
        t = limpiar("".join(self.buf))
        if t:
            self.bloques.append(t)
            if self.en_titulo and not self.titulo:
                self.titulo = t
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.saltar += 1
        elif tag in self.BLOQUE:
            self._cerrar()
            self.en_titulo = tag in ("h1", "h2", "h3")
        elif tag == "br":
            self.buf.append(" ")
        elif tag in ("img", "image"):
            a = dict(attrs)
            src = a.get("src") or a.get("xlink:href") or a.get("href")
            if src:
                self._cerrar()
                self.bloques.append({"img": src})

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.saltar -= 1
        elif tag in self.BLOQUE:
            self._cerrar()
            self.en_titulo = False

    def handle_data(self, data):
        if not self.saltar:
            self.buf.append(data)


def de_epub(ruta, carpeta_img):
    z = zipfile.ZipFile(ruta)
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container",
          "o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
    opf_ruta = ElementTree.fromstring(z.read("META-INF/container.xml")).find(".//c:rootfile", ns).get("full-path")
    opf = ElementTree.fromstring(z.read(opf_ruta))
    base = posixpath.dirname(opf_ruta)
    manifest = {i.get("id"): i.get("href") for i in opf.find("o:manifest", ns)}
    titulo = (opf.findtext(".//dc:title", "", ns) or "").strip()
    autor = (opf.findtext(".//dc:creator", "", ns) or "").strip()

    caps = []
    for ref in opf.find("o:spine", ns):
        href = manifest.get(ref.get("idref"))
        if not href:
            continue
        doc = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
        p = _Xhtml()
        p.feed(z.read(doc).decode("utf-8", "replace"))
        p._cerrar()
        bloques = []
        for b in p.bloques:
            if isinstance(b, dict):
                img = posixpath.normpath(posixpath.join(posixpath.dirname(doc), b["img"]))
                if img in z.namelist():
                    nombre = re.sub(r"[^\w.-]", "_", img)
                    with open(os.path.join(carpeta_img, nombre), "wb") as f:
                        f.write(z.read(img))
                    bloques.append({"img": "img/" + nombre})
            elif b != p.titulo:
                bloques.append(b)
        if sum(len(b) for b in bloques if isinstance(b, str)) < 200:
            continue   # portadillas, índices y páginas casi vacías
        caps.append({"titulo": (p.titulo or f"Parte {len(caps) + 1}")[:60], "bloques": bloques})
    return {"titulo": titulo, "autor": autor, "capitulos": caps}


# ---------------------------------------------------------------- TXT
def de_txt(ruta, carpeta_img):
    crudo = open(ruta, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    if "\n\n" in crudo:
        pars = [juntar_lineas(p.split("\n")) for p in re.split(r"\n\s*\n", crudo)]
    else:
        pars = [limpiar(l) for l in crudo.split("\n")]
    return {"titulo": "", "autor": "", "capitulos": armar_capitulos([p for p in pars if p])}


def extraer(ruta, carpeta_img):
    os.makedirs(carpeta_img, exist_ok=True)
    ext = os.path.splitext(ruta)[1].lower()
    lector = {".pdf": de_pdf, ".epub": de_epub, ".txt": de_txt}.get(ext)
    if not lector:
        raise SystemExit(f"Formato no soportado: {ext} (usa PDF, EPUB o TXT)")
    libro = lector(ruta, carpeta_img)
    libro["titulo"] = libro["titulo"] or os.path.splitext(os.path.basename(ruta))[0]
    return libro
