"""Exercise the /api/download SSRF guard (review finding B2). No network.

Every resolver used here is a stub, and the reject cases use one that fails
the test if it is ever called — proving those inputs are refused before any
DNS lookup, let alone a fetch.

    venv/bin/python scripts/check_ssrf_guard.py

Covers: downloader.normalize_youtube_url (allowlist + canonicalisation +
post-resolution IP check), the downloader sinks (incl. _download_via_cobalt),
the real Flask endpoints /api/download and /api/prefetch returning 400 without
creating a job, and /api/admin/diag-audio (network edges stubbed) sending only
the canonical URL to yt-dlp and Cobalt.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import downloader  # noqa: E402
from downloader import (UnsafeSourceURLError, looks_like_url,  # noqa: E402
                        normalize_youtube_url)

fails = []
VID = "dQw4w9WgXcQ"
CANON = f"https://www.youtube.com/watch?v={VID}"


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def resolver_to(*addrs):
    def _r(host, port, proto=0):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM,
                 proto, "", (a, port)) for a in addrs]
    return _r


def resolver_never(host, port, proto=0):
    raise AssertionError(f"resolver called for {host} — input should have been rejected first")


def resolver_fails(host, port, proto=0):
    raise socket.gaierror("stub: no such host")


PUBLIC = resolver_to("142.250.72.14")


def rejected(value, resolver=resolver_never):
    try:
        out = normalize_youtube_url(value, resolver=resolver)
        return False, f"accepted -> {out}"
    except UnsafeSourceURLError as e:
        return True, str(e)
    except AssertionError as e:
        return False, str(e)


def main():
    print("[1] rejected before any DNS lookup")
    for url in [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://localhost/",
        "http://localhost:5001/api/admin/cookie-status",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://169.254.169.254/?youtube.com",                 # passed the old substring test
        f"http://169.254.169.254/watch?v={VID}&x=youtube.com",
        f"https://youtube.com@169.254.169.254/watch?v={VID}",  # userinfo trick
        f"https://www.youtube.com.evil.example/watch?v={VID}",
        f"https://www.youtube.com:8080/watch?v={VID}",
        "https://www.youtube.com/redirect?q=http://169.254.169.254/",
        "https://www.youtube.com/watch?v=tooshort",
        f"https://www.youtube.com/watch?v={VID}%0A",           # "$" let a trailing \n through
        f"https://www.youtube.com/watch?v={VID}%0D%0A",
        f"https://www.youtube.com/watch?v={VID}x",             # 12 chars
        "https://www.youtube.com/",
        "file:///etc/passwd",
        f"ftp://www.youtube.com/watch?v={VID}",
        "gopher://127.0.0.1:6379/_INFO",
        "",
    ]:
        ok, detail = rejected(url)
        check(f"reject {url or '<empty>'}", ok, detail)

    print("\n[2] allowed host, hostile resolution (rebinding / poisoned DNS)")
    for label, res in [
        ("-> 169.254.169.254", resolver_to("169.254.169.254")),
        ("-> 127.0.0.1", resolver_to("127.0.0.1")),
        ("-> 10.1.2.3", resolver_to("10.1.2.3")),
        ("-> ::1", resolver_to("::1")),
        ("-> ::ffff:169.254.169.254 (IPv4-mapped)", resolver_to("::ffff:169.254.169.254")),
        ("-> fd00::1 (ULA)", resolver_to("fd00::1")),
        ("-> 224.0.0.1 (multicast)", resolver_to("224.0.0.1")),
        ("-> public + 10.0.0.1 (any bad address)", resolver_to("142.250.72.14", "10.0.0.1")),
        ("-> resolution fails", resolver_fails),
    ]:
        ok, detail = rejected(CANON, resolver=res)
        check(f"reject www.youtube.com {label}", ok, detail)

    print("\n[3] accepted, rebuilt as the canonical URL")
    for url in [
        f"https://www.youtube.com/watch?v={VID}",
        f"http://youtube.com/watch?feature=share&v={VID}&t=42",
        f"https://m.youtube.com/watch?v={VID}",
        f"https://music.youtube.com/watch?v={VID}&list=RDAMVM",
        f"https://youtu.be/{VID}?si=abc",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://WWW.YOUTUBE.COM./watch?v={VID}",
        f"  https://www.youtube.com/watch?v={VID}  ",
    ]:
        try:
            out = normalize_youtube_url(url, resolver=PUBLIC)
            check(f"accept {url.strip()}", out == CANON, out)
        except UnsafeSourceURLError as e:
            check(f"accept {url.strip()}", False, str(e))

    print("\n[4] search queries are not URLs; any scheme is")
    for q, want in [("Eagles - Take It Easy official audio", False),
                    ("AC/DC - Back In Black official audio", False),
                    ("Sixpence None the Richer - Kiss Me official audio", False),
                    ("http://x", True), ("HTTP://x", True), ("file:///etc/passwd", True),
                    (" gopher://x", True)]:
        check(f"looks_like_url({q!r}) == {want}", looks_like_url(q) == want)

    print("\n[5] downloader sinks refuse before fetching")
    for fn, arg in [("resolve_audio", {"query": "http://169.254.169.254/latest/meta-data/"}),
                    ("download_audio_from_youtube", "http://localhost:5001/"),
                    ("_download_via_cobalt", "http://169.254.169.254/?youtube.com")]:
        try:
            if fn == "resolve_audio":
                downloader.resolve_audio(arg, "ssrfcheck")
            elif fn == "_download_via_cobalt":   # direct caller: admin diag-audio
                downloader._download_via_cobalt(arg, "ssrfcheck")
            else:
                downloader.download_audio_from_youtube(arg, "ssrfcheck")
            check(f"{fn} rejects", False, "returned without raising")
        except UnsafeSourceURLError as e:
            check(f"{fn} rejects", True, str(e))
        except Exception as e:
            check(f"{fn} rejects", False, f"{type(e).__name__}: {e}")

    print("\n[6] real endpoints return 400 and create no job")
    check_endpoints()

    print("\n[7] admin diag-audio: normalize-then-sink, like the public endpoints")
    check_diag()

    print(f"\n{'FAIL' if fails else 'PASS'}: {len(fails)} failure(s)")
    for f in fails:
        print(f"  - {f}")
    return 1 if fails else 0


# Runs in a subprocess with a throwaway cwd: importing app creates riffd.db,
# uploads/ and outputs/ relative to cwd and prunes old job dirs.
_ENDPOINT_PROBE = r'''
import json, sys
sys.path.insert(0, REPO)
import app
c = app.app.test_client()
cases = [
    ("/api/download", {"url": "http://169.254.169.254/latest/meta-data/"}),
    ("/api/download", {"url": "http://169.254.169.254/?youtube.com"}),
    ("/api/download", {"url": "http://localhost:5001/api/admin/cookie-status"}),
    ("/api/download", {"query": "http://127.0.0.1/"}),
    ("/api/download", {"query": "file:///etc/passwd"}),
    ("/api/prefetch", {"yt_query": "http://169.254.169.254/latest/meta-data/"}),
    ("/api/prefetch", {"yt_query": "http://localhost/"}),
]
out = []
for path, body in cases:
    before = len(app.jobs)
    r = c.post(path, json=body)
    out.append({"path": path, "body": body, "status": r.status_code,
                "error": (r.get_json() or {}).get("error"), "new_jobs": len(app.jobs) - before})
print("SSRF_RESULT " + json.dumps(out))
'''


def check_endpoints():
    env = dict(os.environ, SENTRY_DSN="", SITE_PASSWORD="")
    env.setdefault("FLASK_SECRET_KEY", "riffd-ssrf-check-only")
    code = f"REPO = {str(REPO)!r}\n" + _ENDPOINT_PROBE
    with tempfile.TemporaryDirectory(prefix="riffd_ssrf_") as cwd:
        proc = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("SSRF_RESULT ")), None)
    if line is None:
        check("import app", False, f"exit {proc.returncode}: {proc.stderr[-800:]}")
        return
    for r in json.loads(line[len("SSRF_RESULT "):]):
        field, val = next(iter(r["body"].items()))
        check(f"POST {r['path']} {field}={val}",
              r["status"] == 400 and r["new_jobs"] == 0,
              f"HTTP {r['status']}, new jobs {r['new_jobs']}, error={r['error']!r}")


# The diag route calls the downloader sinks directly. Stub every network edge
# (DNS, yt-dlp, Cobalt, Piped) and record what reaches each, so the check
# proves which URL each sink received and which hosts were DNS-checked.
_DIAG_PROBE = r'''
import json, socket, sys
sys.path.insert(0, REPO)
import app, downloader

resolved, ytdlp_sources, cobalt_urls = [], [], []
def fake_getaddrinfo(host, port, *a, **k):
    resolved.append(host)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", port))]
def fake_run_ytdlp(source, job_id, **k):
    ytdlp_sources.append(source); raise RuntimeError("stub: yt-dlp not run")
class _Req:
    @staticmethod
    def post(url, json=None, **k):
        cobalt_urls.append((json or {}).get("url")); raise RuntimeError("stub: cobalt not called")
    get = staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stub: no GET")))
socket.getaddrinfo = fake_getaddrinfo
downloader._run_ytdlp = fake_run_ytdlp
downloader.requests = _Req
downloader._get_cobalt_instances = lambda: ["https://cobalt.invalid"]
downloader._get_piped_instances = lambda: []
downloader._resolve_youtube_id = lambda q: None

c = app.app.test_client()
out = []
for q in ("http://169.254.169.254/latest/meta-data/",
          "http://169.254.169.254/?youtube.com",
          "http://localhost:5001/",
          "https://youtu.be/" + VID + "?si=abc",
          "Eagles - Take It Easy official audio"):
    del resolved[:], ytdlp_sources[:], cobalt_urls[:]
    r = c.get("/api/admin/diag-audio", query_string={"secret": "diag-secret", "q": q})
    out.append({"q": q, "status": r.status_code, "resolved": list(resolved),
                "ytdlp": list(ytdlp_sources), "cobalt": list(cobalt_urls)})
print("DIAG_RESULT " + json.dumps(out))
'''


def check_diag():
    env = dict(os.environ, SENTRY_DSN="", SITE_PASSWORD="", ADMIN_SECRET="diag-secret")
    env.setdefault("FLASK_SECRET_KEY", "riffd-ssrf-check-only")
    code = f"REPO = {str(REPO)!r}\nVID = {VID!r}\n" + _DIAG_PROBE
    with tempfile.TemporaryDirectory(prefix="riffd_ssrf_") as cwd:
        proc = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("DIAG_RESULT ")), None)
    if line is None:
        check("diag probe", False, f"exit {proc.returncode}: {proc.stderr[-800:]}")
        return
    res = {r["q"]: r for r in json.loads(line[len("DIAG_RESULT "):])}
    for q in ("http://169.254.169.254/latest/meta-data/",
              "http://169.254.169.254/?youtube.com", "http://localhost:5001/"):
        r = res[q]
        check(f"diag rejects {q}", r["status"] == 400 and not r["ytdlp"] and not r["cobalt"],
              f"HTTP {r['status']}, yt-dlp got {r['ytdlp']}, cobalt got {r['cobalt']}")
    r = res[f"https://youtu.be/{VID}?si=abc"]
    check("diag youtu.be link: 200", r["status"] == 200, f"HTTP {r['status']}")
    check("diag: yt-dlp receives only the canonical URL", r["ytdlp"] == [CANON], str(r["ytdlp"]))
    check("diag: Cobalt receives only the canonical URL", r["cobalt"] == [CANON], str(r["cobalt"]))
    check("diag: DNS check covers the host yt-dlp fetches",
          "youtu.be" in r["resolved"] and "www.youtube.com" in r["resolved"],
          f"resolved {r['resolved']}")
    r = res["Eagles - Take It Easy official audio"]
    # The sink retries a failed search once with a simplified query, so there
    # may be two sources; both must be searches, the first the query verbatim.
    check("diag search query still a search", r["status"] == 200 and
          r["ytdlp"][:1] == ["ytsearch1:Eagles - Take It Easy official audio"] and
          all(src.startswith("ytsearch1:") for src in r["ytdlp"]),
          f"HTTP {r['status']}, yt-dlp got {r['ytdlp']}")


if __name__ == "__main__":
    sys.exit(main())
