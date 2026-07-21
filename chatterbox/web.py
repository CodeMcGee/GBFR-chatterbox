"""Local HTTP server and the single-page UI: reads the user's own game
install; hosts nothing."""
import argparse
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser

from chatterbox.app import App
from chatterbox.banks import PKG_DIR
from chatterbox.game import HERE, find_game

UI = (PKG_DIR / "ui.html").read_bytes() if (PKG_DIR / "ui.html").exists() \
    else b"<h1>ui.html is missing from this folder.</h1>"

# Any web page the user has open can POST to a localhost server. These endpoints
# write into the game install, so writes must prove they came from our own page.
TOKEN = secrets.token_urlsafe(24)


def make_handler(app):
    class H(http.server.BaseHTTPRequestHandler):
        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if "html" in ctype:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            # Reads leak the CSRF token and the game install path, so they need the
            # same Host check as writes. Without it a rebound DNS name can read them.
            if not self.local_host():
                return self.send(403, {"error": "this address is not served here; open "
                                               f"http://127.0.0.1:{self.server.server_address[1]}/"})
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    page = UI.replace(b"__CHATTERBOX_TOKEN__", TOKEN.encode())
                    return self.send(200, page, "text/html; charset=utf-8")
                if u.path == "/api/characters":
                    return self.send(200, app.characters())
                if u.path == "/api/pck-status":
                    return self.send(200, app.pck_status())
                if u.path == "/api/lines":
                    return self.send(200, app.lines(q["pl"][0]))
                if u.path == "/api/wav":
                    return self.send(200, app.wav(q["pl"][0], q["id"][0]), "audio/wav")
                self.send(404, {"error": "not found"})
            except Exception as e:
                self.send(500, {"error": str(e)})

        def local_host(self):
            """Host must name this loopback server, which is what blocks rebinding."""
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").strip()
            return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

        def local_request(self):
            """Reject anything not originating from this server's own page."""
            if not self.local_host():
                return False
            port = self.server.server_address[1]
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return False
            sent = self.headers.get("X-Chatterbox-Token") or ""
            return secrets.compare_digest(sent, TOKEN)

        def do_POST(self):
            if not self.local_request():
                return self.send(403, {"error": "request did not come from the Chatterbox page"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 4 * 1024 * 1024:
                    return self.send(413, {"error": "request too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError) as e:
                return self.send(400, {"error": f"bad request body: {e}"})
            try:
                if self.path == "/api/apply":
                    return self.send(200, app.apply(body["pl"], body.get("mutes", []),
                                                    body.get("swaps", {}),
                                                    body.get("unmutes", [])))
                if self.path == "/api/mute-all":
                    if body.get("all"):
                        return self.send(200, app.mute_all())
                    return self.send(200, app.mute_character(body["pl"]))
                if self.path == "/api/flag":
                    return self.send(200, app.set_flag(body["wem_id"], body.get("wrong", True),
                                                       body.get("correct")))
                if self.path == "/api/extract-pcks":
                    return self.send(200, app.extract_pcks())
                if self.path == "/api/revert":
                    if body.get("all"):
                        return self.send(200, app.revert_all())
                    return self.send(200, app.revert(body["pl"]))
                self.send(404, {"error": "not found"})
            except Exception as e:
                self.send(500, {"error": str(e)})
    return H


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--game")
    p.add_argument("--port", type=int, default=8777)
    default_atlas = HERE / "atlas"
    if not default_atlas.is_dir():          # source checkout: use the published dataset
        default_atlas = HERE / "data" / "per-character"
    p.add_argument("--atlas", default=str(default_atlas))
    p.add_argument("--profile")
    p.add_argument("--reapply", action="store_true",
                   help="re-apply saved profile to the current game files, then exit")
    p.add_argument("--forget", metavar="plXXXX",
                   help="forget the recorded original for one character, after "
                        "restoring it through Steam. Then exit.")
    a = p.parse_args()

    voice = find_game(a.game)
    app = App(voice, a.atlas, a.profile)
    if a.forget:
        app.forget(a.forget)
        return
    if a.reapply:
        r = app.reapply()
        print(f"reapplied: {r['reapplied'] or 'nothing stored'}")
        return
    chars = app.characters()
    if not chars:
        sys.exit(f"No atlas data found in {a.atlas}")

    # Try a few ports: someone who double-clicked run.bat cannot pass --port.
    for port in range(a.port, a.port + 10):
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
            break
        except OSError:
            continue
    else:
        sys.exit(f"Ports {a.port} to {a.port + 9} are all in use.\n"
                 f"Close any other Chatterbox console windows and try again.")
    if port != a.port:
        print(f"Port {a.port} was busy, using {port} instead.")
    url = f"http://127.0.0.1:{port}/"
    print(f"GBFR-chatterbox - {len(chars)} characters\nGame: {voice}\nOpen: {url}  (Ctrl-C to quit)")
    print("If your browser does not open by itself, type that address into it.")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
