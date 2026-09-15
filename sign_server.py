from flask import Flask, Response, render_template, request, redirect, url_for
from pathlib import Path
import argparse
import json
import socket
import threading

# Templates live next to this file rather than in templates/.
app = Flask(__name__, template_folder=".")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "sign_text.json"
LEGACY_STATE_FILE = BASE_DIR / "sign_text.txt"  # single-line state, pre-h1/h2/h3

FIELDS = ("h1", "h2", "h3")  # big, medium, small
DEFAULTS = {"h1": "Hello", "h2": "", "h3": ""}
KEEPALIVE_SECONDS = 15  # nudge idle SSE connections so proxies keep them open

# The sign text. The condition wakes every open stream the moment it changes,
# and _version lets a stream tell "changed" from "woke up for a keepalive".
_cond = threading.Condition()
_state = dict(DEFAULTS)
_version = 0


def load_state():
    """Restore the last sign text so a restart does not blank the sign."""
    global _state
    try:
        saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Fall back to the old single-line file, which became the big line.
        try:
            _state["h1"] = LEGACY_STATE_FILE.read_text(encoding="utf-8")
        except OSError:
            pass
        return
    _state = {name: str(saved.get(name, DEFAULTS[name])) for name in FIELDS}


def get_state():
    with _cond:
        return dict(_state)


def set_state(new_state):
    global _version
    with _cond:
        _state.update(new_state)
        _version += 1
        _cond.notify_all()
        snapshot = dict(_state)
    try:
        STATE_FILE.write_text(json.dumps(snapshot), encoding="utf-8")
    except OSError:
        pass  # keep serving from memory if the file is not writable


@app.route("/")
@app.route("/sign")
def sign():
    # Rendered with the current text so the sign is right on load, even
    # before (or without) the EventSource connecting.
    return render_template("sign.html", **get_state())


@app.route("/update", methods=["GET", "POST"])
def update():
    if request.method == "POST":
        # All three lines change together, from one click.
        set_state({name: request.form.get(name, "").strip() for name in FIELDS})
        # Redirect so a reload does not resubmit the form.
        return redirect(url_for("update"))
    return render_template("update.html", **get_state())


@app.route("/stream_text")
def stream_text():
    def event_stream():
        last_seen = -1
        while True:
            with _cond:
                if _version == last_seen:
                    _cond.wait(timeout=KEEPALIVE_SECONDS)
                state, version = dict(_state), _version
            if version == last_seen:
                yield ": keepalive\n\n"
            else:
                last_seen = version
                # JSON keeps the three lines in one event, and escapes any
                # newline that would otherwise break the data: field.
                yield "data: %s\n\n" % json.dumps(state)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/text")
def text():
    """Current lines as plain text, one per line, handy for scripting."""
    state = get_state()
    return Response("\n".join(state[name] for name in FIELDS), mimetype="text/plain")


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

    load_state()
    ip = local_ip() if args.host == "0.0.0.0" else args.host
    print("Sign:   http://%s:%d/sign" % (ip, args.port))
    print("Update: http://%s:%d/update" % (ip, args.port))
    # debug=False: this listens on the LAN, and the debugger is a remote shell.
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
