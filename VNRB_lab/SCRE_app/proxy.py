import re
import requests
from flask import Flask, request, Response, render_template

BACKEND_URL = "http://127.0.0.1:5000"

proxy = Flask("reverse_proxy")

SQLI_PATTERNS = [
    r"(\bor\b|\band\b)\s+\d+=\d+",
    r"--",
    r";",
    r"'",
    r"\"",
    r"union\s+select",
    r"sleep\(",
]

def looks_like_sqli(value: str) -> bool:
    value = value.lower()
    return any(re.search(p, value) for p in SQLI_PATTERNS)

@proxy.before_request
def block_sqli():
    for k, v in request.args.items():
        if looks_like_sqli(v):
            return render_template("blocked.html"), 403

    for k, v in request.form.items():
        if looks_like_sqli(v):
            return render_template("blocked.html"), 403


# Proxy logic
@proxy.route("/", defaults={"path": ""}, methods=["GET", "POST"])
@proxy.route("/<path:path>", methods=["GET", "POST"])
def proxy_request(path):
    target = f"{BACKEND_URL}/{path}"

    if request.method == "GET":
        upstream = requests.get(target, params=request.args)
    else:
        upstream = requests.post(target, data=request.form)

    return Response(
        upstream.content,
        status=upstream.status_code,
        headers=dict(upstream.headers),
    )

def start_proxy():
    proxy.run(host="127.0.0.1", port=8080)
