#!/usr/bin/env python3
"""Serve a wall sign and a page to update its text over the local network.

    /sign    the sign itself; refreshes itself every few seconds
    /update  a text field and an Update button that change the sign

Pages are plain HTML with no JavaScript so that old e-reader browsers
(the Kindle experimental browser in particular) can render them.
"""

import argparse
import html
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "sign_text.txt"
DEFAULT_TEXT = "Hello"
MAX_TEXT_BYTES = 4096

# The sign text, guarded because ThreadingHTTPServer handles requests
# on multiple threads.
_lock = threading.Lock()
_text = DEFAULT_TEXT


def load_text():
    """Restore the last sign text so a restart does not blank the sign."""
    global _text
    try:
        saved = STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        return
    with _lock:
        _text = saved


def get_text():
    with _lock:
        return _text


def set_text(new_text):
    global _text
    with _lock:
        _text = new_text
    try:
        STATE_FILE.write_text(new_text, encoding="utf-8")
    except OSError:
        pass  # keep serving from memory if the file is not writable


def render(template_name, text):
    template = (BASE_DIR / template_name).read_text(encoding="utf-8")
    return template.replace("{{text}}", html.escape(text))


class SignHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SignServer/1.0"

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/sign"):
            self.send_page(render("sign.html", get_text()))
        elif path == "/update":
            self.send_page(render("update.html", get_text()))
        elif path == "/text":  # plain text, handy for scripting
            self.send_page(get_text(), content_type="text/plain; charset=utf-8")
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/update":
            self.send_error(404, "Not Found")
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_TEXT_BYTES:
            self.send_error(413, "Text too long")
            return
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        fields = parse_qs(body, keep_blank_values=True)
        set_text(fields.get("text", [""])[0].strip())

        # Redirect so a reload does not resubmit the form.
        self.send_response(303)
        self.send_header("Location", "/update")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_page(self, body, content_type="text/html; charset=utf-8"):
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        # E-reader browsers cache aggressively; the sign must not go stale.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))


def local_ip():
    """Best guess at the address other devices on the LAN should use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # no packets sent, just picks a route
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="address to bind")
    parser.add_argument("--port", type=int, default=8000, help="port to bind")
    args = parser.parse_args()

    load_text()
    server = ThreadingHTTPServer((args.host, args.port), SignHandler)
    ip = local_ip() if args.host == "0.0.0.0" else args.host
    print("Sign:   http://%s:%d/sign" % (ip, args.port))
    print("Update: http://%s:%d/update" % (ip, args.port))
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
