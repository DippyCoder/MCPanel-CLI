"""HTTP helpers — stdlib equivalents of the fetchJSON/fetchText/downloadFile
functions in main.js. No third-party dependencies."""

import json
import urllib.request

USER_AGENT = "MCPanel/2.1 (https://github.com/DippyCoder/MCPanel; planewriter255@gmail.com)"


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=30)


def post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = res.read().decode("utf-8", "replace")
    try:
        return json.loads(data)
    except Exception:
        raise RuntimeError("Parse error: " + data[:100])


def fetch_json(url):
    with _open(url) as res:
        data = res.read().decode("utf-8", "replace")
    try:
        return json.loads(data)
    except Exception:
        raise RuntimeError("Parse error: " + data[:100])


def fetch_text(url):
    with _open(url) as res:
        return res.read().decode("utf-8", "replace")


def download_file(url, dest, on_progress=None):
    """Stream `url` to `dest`, following redirects (urllib does this for us),
    reporting integer percent via on_progress(percent)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as res:
        total = int(res.headers.get("content-length") or 0)
        downloaded = 0
        last = -1
        with open(dest, "wb") as f:
            while True:
                chunk = res.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total and on_progress:
                    pct = round(downloaded / total * 100)
                    if pct != last:
                        last = pct
                        on_progress(pct)
    if total and on_progress and last != 100:
        on_progress(100)
