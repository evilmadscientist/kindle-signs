from flask import Flask, Response, render_template, request, redirect, url_for
from pathlib import Path
import argparse
import socket
import threading

# Templates live next to this file rather than in templates/.
app = Flask(__name__, template_folder=".")

STATE_FILE = Path(__file__).resolve().parent / "sign_text.txt"
DEFAULT_TEXT = "Hello"
KEEPALIVE_SECONDS = 15  # nudge idle SSE connections so proxies keep them open

# The sign text. The condition wakes every open stream the moment it changes,
# and _version lets a stream tell "changed" from "woke up for a keepalive".
_cond = threading.Condition()
_text = DEFAULT_TEXT
_version = 0


def load_text():
    """Restore the last sign text so a restart does not blank the sign."""
    global _text
    try:
        _text = STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        pass


def get_text():
    with _cond:
        return _text


def set_text(new_text):
    global _text, _version
    with _cond:
        _text = new_text
        _version += 1
        _cond.notify_all()
    try:
        STATE_FILE.write_text(new_text, encoding="utf-8")
    except OSError:
        pass  # keep serving from memory if the file is not writable


@app.route("/")
@app.route("/sign")
def sign():
    # Rendered with the current text so the sign is right on load, even
    # before (or without) the EventSource connecting.
    return render_template("sign.html", text=get_text())


@app.route("/update", methods=["GET", "POST"])
def update():
    if request.method == "POST":
        set_text(request.form.get("text", "").strip())
        # Redirect so a reload does not resubmit the form.
        return redirect(url_for("update"))
    return render_template("update.html", text=get_text())


@app.route("/stream_text")
def stream_text():
    def event_stream():
        last_seen = -1
        while True:
            with _cond:
                if _version == last_seen:
                    _cond.wait(timeout=KEEPALIVE_SECONDS)
                text, version = _text, _version
            if version == last_seen:
                yield ": keepalive\n\n"
            else:
                last_seen = version
                # A data: field cannot span lines; the sign is one line anyway.
                yield "data: %s\n\n" % text.replace("\r", " ").replace("\n", " ")

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/text")
def text():
    """Current text as plain text, handy for scripting."""
    return Response(get_text(), mimetype="text/plain")


@app.after_request
def no_cache(response):
    # E-reader browsers cache aggressively; the sign must not go stale.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve a wall sign on the LAN.")
    parser.add_argument("--host", default="0.0.0.0", help="address to bind")
    parser.add_argument("--port", type=int, default=5000, help="port to bind")
    args = parser.parse_args()

    load_text()
    ip = local_ip() if args.host == "0.0.0.0" else args.host
    print("Sign:   http://%s:%d/sign" % (ip, args.port))
    print("Update: http://%s:%d/update" % (ip, args.port))
    # debug=False: this listens on the LAN, and the debugger is a remote shell.
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
