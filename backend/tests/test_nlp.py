"""Text normalization, intent classification, RAG pipeline and audio helpers."""
import io
import wave

import numpy as np
import pytest

from app.services.nlu import get_intent_classifier
from app.services.rag_service import TextChunker, get_rag_service, clean_markdown
from app.services.text_utils import (
    extract_order_id, extract_customer_id, extract_email, classify_confirmation, normalize_identifier,
)
from app.services.voice_service import decode_wav, clean_for_speech


@pytest.mark.parametrize("text,expected", [
    ("What's the status of ORD001?", "ORD001"),
    ("my order number is ord 3", "ORD003"),
    ("It's O R D zero zero five", "ORD005"),
    ("order number one two", "ORD012"),
    ("order #14 please", "ORD014"),
    ("I ordered headphones last week", None),
])
def test_extract_order_id(text, expected):
    assert extract_order_id(text) == expected


def test_extract_customer_and_email():
    assert extract_customer_id("customer ID is cust zero zero two") == "CUST002"
    assert extract_email("it's emily dot chen at email dot com") == "emily.chen@email.com"
    assert normalize_identifier("tkt-5", "TKT", 4) == "TKT0005"


@pytest.mark.parametrize("text,expected", [
    ("Yes, please go ahead", True), ("yeah do it", True), ("sure", True), ("That's correct.", True),
    ("No, don't cancel it", False), ("never mind", False), ("wait", False),
    ("how long will the refund take?", None),
])
def test_confirmation_classifier(text, expected):
    assert classify_confirmation(text) is expected


@pytest.mark.parametrize("text,intent", [
    ("Where's my package? It was supposed to come yesterday", "delivery_issue"),
    ("has my order shipped yet", "order_status"),
    ("I don't want the watch anymore, please stop the order", "order_cancellation"),
    ("I'd like to send back the earbuds", "return_request"),
    ("when do I get my money back", "refund_request"),
    ("my card got billed two times", "payment_issue"),
    ("I forgot my password", "account_issue"),
    ("let me talk to an actual person", "escalation"),
    ("what's your policy on international shipping", "policy_question"),
])
def test_semantic_intents(text, intent):
    assert get_intent_classifier().classify(text)[0] == intent


def test_multi_intent_utterance():
    intent, _, all_intents = get_intent_classifier().classify_detailed(
        "I was charged twice for my headphones and I want to speak to a real person")
    assert intent == "escalation" and "payment_issue" in all_intents


def test_chunker_is_heading_aware():
    doc = "# Return Policy - Store\n\n## Return Window\n- Items may be returned within **30 days**.\n\n" \
          "## Exchanges\n- We do not offer exchanges."
    chunks = TextChunker(600, 80).chunk("Return Policy", doc)
    assert [c["section"] for c in chunks] == ["Return Window", "Exchanges"]
    assert chunks[0]["content"].startswith("Return Policy > Return Window\n")
    assert "**" not in clean_markdown(doc)


def test_chunker_splits_long_sections_with_overlap():
    body = " ".join(f"Sentence number {i} explains a policy detail." for i in range(60))
    chunks = TextChunker(300, 60).chunk("Doc", f"## Long\n{body}")
    assert len(chunks) > 3 and all(len(c["content"]) < 400 for c in chunks)


def test_retrieval_is_grounded_and_thresholded(client):
    rag = get_rag_service()
    results = rag.retrieve("How long do refunds take?")
    assert results and results[0]["document_title"] in ("Return Policy", "Refund Policy", "Payment FAQ")
    assert all(r["score"] >= rag.settings.rag_min_score for r in results)
    assert rag.retrieve("What's the weather like on Mars today?") == []
    assert any("5-7 business days" in s for s in rag.best_sentences("How long do refunds take?", results))


def test_knowledge_search_endpoint(client, admin_headers):
    r = client.get("/api/knowledge/search", params={"query": "do you ship internationally"}, headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["results"][0]["document_title"] == "Shipping Policy"


def test_decode_wav_resamples_to_16k():
    rate = 44100
    tone = (np.sin(np.linspace(0, 2 * np.pi * 440, rate)) * 12000).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(tone.tobytes())
    audio = decode_wav(buf.getvalue())
    assert abs(len(audio) - 16000) <= 1 and audio.dtype == np.float32 and audio.max() <= 1.0


def test_clean_for_speech():
    assert clean_for_speech("**Order ORD003** is *cancelled*") == "order 0 0 3 is cancelled"
    assert clean_for_speech("Your reference is ticket TKT0005.") == "Your reference is ticket 5."


# ── STT hallucination guards ──

from app.services.voice_service import audio_is_speech, transcript_is_hallucination  # noqa: E402


def _speechlike(seconds=2.0, rate=16000):
    """Harmonic-rich voiced signal with syllable-rate amplitude modulation (stands in for speech)."""
    t = np.arange(int(seconds * rate)) / rate
    f0 = 140 + 20 * np.sin(2 * np.pi * 0.7 * t)
    voice = sum(np.sin(2 * np.pi * k * np.cumsum(f0) / rate) / np.sqrt(k) for k in range(1, 12))
    envelope = np.clip(np.sin(2 * np.pi * 3.5 * t), 0, None)
    return (0.15 * voice * envelope).astype(np.float32)


def test_audio_is_speech_rejects_tones_noise_and_silence():
    rate = 16000
    t = np.arange(rate * 3) / rate
    beep = np.where((t % 1) < 0.2, 0.3 * np.sin(2 * np.pi * 1000 * t), 0).astype(np.float32)
    assert not audio_is_speech(beep)
    assert not audio_is_speech((0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32))
    assert not audio_is_speech((0.05 * np.random.randn(len(t))).astype(np.float32))
    assert not audio_is_speech(np.zeros(rate * 2, dtype=np.float32))
    assert audio_is_speech(_speechlike())


@pytest.mark.parametrize("text,duration,expected", [
    ("TKT0005. " * 20, 5.0, True),                       # loop seen in the audit (fake-mic beep)
    ("VoiceOps customer support call. Order numbers like ORD001", 3.0, True),  # prompt echo
    ("one two three four five six seven eight nine ten eleven twelve", 1.0, True),  # impossible rate
    ("What is the status of my order? ORD001.", 4.0, False),
    ("No, no, no, don't cancel it", 2.0, False),
    ("Thank you.", 0.8, False),
    ("ORD003", 1.0, False),
])
def test_transcript_hallucination_guard(text, duration, expected):
    assert transcript_is_hallucination(text, duration) is expected


def test_local_stt_skips_non_speech_without_running_whisper(run):
    from app.services.voice_service import LocalWhisperSTT

    class Exploding(LocalWhisperSTT):
        def _run(self, audio):
            raise AssertionError("Whisper must not run on non-speech audio")

    rate = 16000
    t = np.arange(rate * 2) / rate
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((np.sin(2 * np.pi * 1000 * t) * 9000).astype(np.int16).tobytes())
    assert run(Exploding("unused").transcribe, buf.getvalue())["text"] == ""
