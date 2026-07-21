#!/usr/bin/env python3
"""Boot the server for real and exercise every endpoint.

Import-only checks miss undefined module globals, which is exactly how a
NameError once shipped. This actually starts the thing and reads the page.

    python3 test_smoke.py
"""
import http.client, json, pathlib, shutil, struct, subprocess, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).resolve().parent.parent   # project root
sys.path.insert(0, str(HERE))


def fake_game(root: pathlib.Path, bank_src: pathlib.Path):
    voice = root / "data/sound/English(US)"
    voice.mkdir(parents=True)
    shutil.copy(bank_src, voice / bank_src.name)
    return root


def main():
    banks = sorted(HERE.glob("samples/**/*_m.bnk"))
    if not banks:
        sys.exit("need a sample bank under samples/ to smoke test")
    bank = banks[0]
    pl = bank.name.split("_")[1]
    atlas = HERE / "data/per-character"
    if not (atlas / f"{pl}.json").exists():
        sys.exit(f"no atlas entry for {pl}")

    import serve
    assert serve.NAMES, "NAMES is empty - characters.json not loaded"
    assert b"<title>" in serve.UI, "UI is empty - ui.html not loaded"

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        game = fake_game(td / "game", bank)
        profile = td / "profile.json"
        app = serve.App(serve.find_game(str(game)), atlas, profile)
        import http.server as hs
        srv = hs.ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

        def get(path, host=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            c.request("GET", path, headers={"Host": host} if host else {})
            r = c.getresponse()
            return r.status, r.read()

        def post(path, body, token=True, host=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            h = {"Content-Type": "application/json",
                 "Host": host or f"127.0.0.1:{port}"}
            if token:
                h["X-Chatterbox-Token"] = serve.TOKEN
            c.request("POST", path, json.dumps(body), h)
            r = c.getresponse()
            return r.status, json.loads(r.read())

        checks = []

        st, body = get("/")
        assert st == 200 and b"<title>" in body, f"GET / -> {st}"
        checks.append("page loads")

        st, body = get("/api/characters")
        chars = json.loads(body)
        assert st == 200 and chars and chars[0]["name"] != chars[0]["pl"], \
            "character names not resolved (NAMES not loaded?)"
        checks.append(f"characters resolve ({chars[0]['name']})")

        st, body = get(f"/api/lines?pl={pl}")
        data = json.loads(body)
        assert st == 200 and data["lines"], "no lines"
        checks.append(f"{len(data['lines'])} lines")

        st, body = get("/api/pck-status")
        assert st == 200, "pck-status failed"
        checks.append("pck status")

        wid = next(l["wem_id"] for l in data["lines"] if l["duration"])
        st, wav = get(f"/api/wav?pl={pl}&id={wid}")
        assert st == 200 and wav[:4] == b"RIFF", f"preview not a wav: {wav[:16]}"
        checks.append("audio preview decodes")

        st, r = post("/api/apply", {"pl": pl, "mutes": [wid]})
        assert st == 200 and r.get("ok"), f"apply failed: {r}"
        assert pathlib.Path(r["backup_path"]).exists(), "no backup written"
        checks.append("apply + backup")

        st, body = get(f"/api/lines?pl={pl}")
        muted = [l for l in json.loads(body)["lines"] if l["muted"]]
        assert any(l["wem_id"] == wid for l in muted), "mute did not persist"
        checks.append("mute persists")

        prof = json.loads(profile.read_text())          # keyed by bank filename now
        assert any(e["mutes"] == [wid] for e in prof.values()), "profile not saved"
        checks.append("profile saved")

        st, r = post("/api/revert", {"pl": pl})
        assert st == 200 and r.get("ok"), f"revert failed: {r}"
        st, body = get(f"/api/lines?pl={pl}")
        assert not [l for l in json.loads(body)["lines"] if l["muted"]], "revert left mutes"
        checks.append("revert")

        # a page on another site must not be able to touch the game files
        st, r = post("/api/apply", {"pl": pl, "mutes": [wid]}, token=False)
        assert st == 403, f"unauthenticated write was not rejected: {st}"
        st, r = post("/api/apply", {"pl": pl, "mutes": [wid]}, host="evil.example")
        assert st == 403, f"rebinding host was not rejected: {st}"
        checks.append("writes reject cross-origin")

        st, r = post("/api/apply", {"pl": "../../etc/passwd", "mutes": []})
        assert st != 200, "path traversal in pl was accepted"
        checks.append("path traversal blocked")

        # reads leak the CSRF token and the install path, so they need the Host check too
        st, _ = get("/", host="evil.example")
        assert st == 403, f"rebound GET / was not rejected: {st}"
        st, _ = get(f"/api/lines?pl={pl}", host="evil.example")
        assert st == 403, f"rebound GET /api/lines was not rejected: {st}"
        checks.append("reads reject rebinding")

        # the panic button: undo every character at once
        st, r = post("/api/apply", {"pl": pl, "mutes": [wid]})
        assert st == 200, f"apply before revert-all failed: {r}"
        st, r = post("/api/revert", {"all": True})
        assert st == 200 and r.get("ok") and pl in r["reverted"], f"revert all failed: {r}"
        st, body = get(f"/api/lines?pl={pl}")
        assert not [l for l in json.loads(body)["lines"] if l["muted"]], "revert all left mutes"
        checks.append("revert all")

        st, body = get("/api/nope")
        assert st == 404, "missing route should 404"
        st, body = get("/api/lines")
        assert st != 200, "missing query param should not 200"
        checks.append("errors handled")

        srv.shutdown()

    for c in checks:
        print(f"  ok  {c}")
    print(f"\n{len(checks)} checks passed")


def test_all():
    main()


if __name__ == "__main__":
    main()
