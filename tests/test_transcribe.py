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


def test_flag_states(tmp_path):
    from chatterbox.store import Store
    store = Store(tmp_path / "profile.json")
    store.set_flag("111", wrong=True, correct="fixed words")
    assert store.flags()["111"] == {"wrong": True, "correct": "fixed words"}
    store.set_flag("111", verified=True)          # verified clears wrong
    assert store.flags()["111"] == {"verified": True}
    store.set_flag("111", wrong=True)             # wrong clears verified
    assert store.flags()["111"] == {"wrong": True}
    store.set_flag("111", wrong=False)            # clearing the last state drops the entry
    assert "111" not in store.flags()


def test_ally_name_decodes_duo_tail():
    """The duo-rescue label class whose partner once leaked as raw digits."""
    from transcribe.context import ally_name
    assert ally_name("PL0000_vo_DUO_rescue1_A_PL2700") == "Eustace"
    assert ally_name("PL0400_vo_SP_link_multi_talk1_B_PL0600_3") == "Rosetta"
    assert ally_name("PL2200_vo_ATK_default_ss_7") is None


def test_no_engine_codes_in_subtitles_csv():
    """Human-facing columns of the built JP table hold names, never PL codes."""
    import csv, pathlib, re

    import pytest
    path = pathlib.Path(__file__).parent.parent / "build" / "subtitles-jp.csv"
    if not path.exists():
        pytest.skip("subtitles-jp.csv not built")
    code = re.compile(r"^(pl|np)?\d{4}$", re.IGNORECASE)
    for row in csv.DictReader(open(path)):
        for col in ("character", "partner"):
            assert not code.match(row[col]), f"{col}={row[col]!r} in {row['label']}"
