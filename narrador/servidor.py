"""Servidor para leer el audiolibro desde el teléfono en la red local.

El http.server de Python no acepta pedidos por rangos (Range), y sin eso el navegador
del teléfono no puede saltar a otra parte del audio: siempre vuelve al inicio.
"""
import functools, http.server, os, re, socket


class ConRangos(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        rango = self.headers.get("Range")
        ruta = self.translate_path(self.path)
        if not rango or not os.path.isfile(ruta):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rango)
        tam = os.path.getsize(ruta)
        if not m or (not m.group(1) and not m.group(2)):
            return super().send_head()
        if m.group(1):
            ini = int(m.group(1))
            fin = int(m.group(2)) if m.group(2) else tam - 1
        else:   # "bytes=-500": los últimos 500
            ini, fin = max(0, tam - int(m.group(2))), tam - 1
        fin = min(fin, tam - 1)
        if ini >= tam or ini > fin:
            self.send_error(416, "Rango fuera del archivo")
            return None
        f = open(ruta, "rb")
        f.seek(ini)
        self._quedan = fin - ini + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(ruta))
        self.send_header("Content-Range", f"bytes {ini}-{fin}/{tam}")
        self.send_header("Content-Length", str(self._quedan))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return f

    def end_headers(self):
        if not self.headers.get("Range"):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def copyfile(self, origen, destino):
        quedan = getattr(self, "_quedan", None)
        if quedan is None:
            return super().copyfile(origen, destino)
        while quedan > 0:
            trozo = origen.read(min(64 * 1024, quedan))
            if not trozo:
                break
            destino.write(trozo)
            quedan -= len(trozo)


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _Servidor(http.server.ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind pregunta el nombre de la PC (getfqdn); con VPNs como
        # Hamachi o Radmin esa consulta se cuelga y el servidor nunca arranca.
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


def servir(carpeta, puerto=8080):
    manejador = functools.partial(ConRangos, directory=carpeta)
    with _Servidor(("0.0.0.0", puerto), manejador) as srv:
        print(f"En el teléfono (mismo WiFi) abre:  http://{ip_local()}:{puerto}/lector.html")
        print("Ctrl+C para apagarlo")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
