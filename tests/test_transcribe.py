"""Model-free tests for the transcribe package: context building, hotword
assembly, corrections overlay, and corpus scoring against small fixtures."""
import json

from transcribe import corrections, evaluate
from transcribe.asr import hotwords
from transcribe.context import build_ctx, decode_label


def test_decode_label():
    assert decode_label("PL2200_vo_ATK_default_ss_7").startswith("attack: default ss 7")
    lt = decode_label("PL2900_vo_SP_burst_reaction_B_PL0300")
    assert lt.startswith("skill or co-op link: burst reaction b")
    assert decode_label("") == ""


def test_build_ctx_speaker_and_type():
    ctx = build_ctx("pl2200", "PL2200_vo_ATK_default_ss_7")
    assert "spoken by Seofon" in ctx
    assert "Line type: attack" in ctx
    assert "If wordless" not in ctx          # grunt tail must be stripped


def test_build_ctx_addressee():
    ctx = build_ctx("pl2200", "PL2200_vo_SP_charge_speak_call_PL0300")
    assert "directed at their ally Rackam" in ctx


def test_race_hint_by_name():
    # Fediel names allies by race+gender; races.json is keyed by character name
    ctx = build_ctx("pl2900", "PL2900_vo_SP_charge_speak_call_PL2700")
    assert "male Erune" in ctx               # Eustace
    assert "directed at" not in ctx          # she never uses the name


def test_hotwords():
    full = hotwords("pl2200")
    assert "Cien Mil Espadas" in full
    assert "En garde" in full                # truth-verified catchphrase
    assert "Katalina" in full                # ally names for call lines
    lean = hotwords("pl2200", short=True)
    assert "Katalina" not in lean            # lean list drops allies (bark rescue)


def test_corrections_apply(tmp_path):
    wid, text = next(iter(corrections.load().items()))
    doc = {"lines": {wid: {"transcript": "totally wrong", "source_model": "qwen3-omni"}}}
    (tmp_path / "pl9999.json").write_text(json.dumps(doc))
    corrections.apply(tmp_path)
    fixed = json.loads((tmp_path / "pl9999.json").read_text())["lines"][wid]
    assert fixed["transcript"] == text
    assert fixed["source_model"] == "human"


def test_evaluate_score(tmp_path):
    truth = evaluate.truth()
    wid, t = next(iter(truth["verified"].items()))
    good = {"lines": {wid: {"transcript": t["text"], "confidence": -0.1}}}
    (tmp_path / "pl9999.json").write_text(json.dumps(good))
    assert evaluate.score(str(tmp_path)) == 0    # correct source: no failures
    bad = {"lines": {wid: {"transcript": "nonsense", "confidence": -0.9}}}
    (tmp_path / "pl9999.json").write_text(json.dumps(bad))
    assert evaluate.score(str(tmp_path)) == 1    # one wrong verified line
