"""Speech-to-text and text-to-speech providers.

STT: ``openai`` (any OpenAI-compatible Whisper endpoint: OpenAI, Groq) or ``local``
     (Whisper running on CPU via Hugging Face transformers; expects WAV audio).
TTS: ``openai`` (OpenAI-compatible speech endpoint) or ``edge`` (Microsoft Edge neural
     voices via edge-tts, no API key).
``browser`` for either means the server has no engine and the web client uses the
browser's Web Speech API instead.
"""
import asyncio
import io
import re
import wave
import zlib
from typing import Optional, Dict, Any

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("voice_service")


class VoiceError(Exception):
    pass


# ── Audio helpers ──

def decode_wav(data: bytes, target_rate: int = 16000) -> np.ndarray:
    """Decode PCM WAV bytes to mono float32 at ``target_rate``."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise VoiceError(f"Unsupported audio: expected PCM WAV ({e})") from e
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise VoiceError(f"Unsupported WAV sample width: {width}")
    audio = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    if width == 1:
        audio = (audio - 128) / 128.0
    else:
        audio /= float(2 ** (8 * width - 1))
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != target_rate and len(audio):
        duration = len(audio) / rate
        new_len = int(duration * target_rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, new_len), np.arange(len(audio)), audio).astype(np.float32)
    return audio


def is_wav(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def clean_for_speech(text: str) -> str:
    text = re.sub(r"[*_#`>]+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\b(?:order\s+)?ORD(\d+)", lambda m: "order " + " ".join(m.group(1)), text, flags=re.I)
    text = re.sub(r"\b(?:ticket\s+)?TKT(\d+)", lambda m: "ticket " + (m.group(1).lstrip("0") or "0"), text,
                  flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


# ── STT ──

# Biases Whisper toward the identifiers customers spell out ("O R D zero zero one" -> "ORD001").
DOMAIN_PROMPT = ("VoiceOps customer support call. Order numbers like ORD001, ORD003. "
                 "Customer IDs like CUST002. Ticket TKT0005.")


def audio_is_speech(samples: np.ndarray, rate: int = 16000) -> bool:
    """Cheap pre-check so tones, beeps and near-silence never reach Whisper (which invents text for them).

    Speech must have enough active frames, and its energy is spread over many harmonics; a tone or beep
    concentrates most of its energy in one narrow band.
    """
    frame = int(rate * 0.032)
    if len(samples) < frame * 8:
        return False
    frames = samples[: len(samples) // frame * frame].reshape(-1, frame)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    active = frames[rms > max(0.01, float(np.percentile(rms, 20)) * 2)]
    if len(active) < 6:  # less than ~0.2 s of signal
        return False
    spectra = np.abs(np.fft.rfft(active * np.hanning(frame), axis=1)) ** 2
    total = spectra.sum(axis=1) + 1e-12
    peak = spectra.argmax(axis=1)
    band = np.array([spectra[i, max(0, p - 2): p + 3].sum() for i, p in enumerate(peak)])
    return float(np.median(band / total)) < 0.6


def transcript_is_hallucination(text: str, duration_s: Optional[float] = None) -> bool:
    """Reject the classic Whisper failure modes: loops, prompt echo, stock phrases, impossible speech rates."""
    t = text.strip()
    if not t:
        return False
    if "VoiceOps customer support call" in t or "Order numbers like" in t:
        return True
    words = re.findall(r"[\w']+", t.lower())
    if not words:
        return True
    # The same phrase looping: a single word 4+ times in a row ("no, no, no" is real speech), 2-3 words 3+ times.
    for n, reps in ((1, 4), (2, 3), (3, 3)):
        for i in range(len(words) - reps * n + 1):
            if all(words[i:i + n] == words[i + k * n:i + (k + 1) * n] for k in range(1, reps)):
                return True
    if len(t) > 40 and len(t) / max(1, len(zlib.compress(t.encode()))) > 2.4:  # Whisper's compression check
        return True
    if duration_s is not None:
        if len(words) > max(4.0, duration_s * 5.5):  # far faster than anyone talks
            return True
        if duration_s < 1.5 and re.fullmatch(r"\W*(thanks for watching|you)\W*", t, re.I):
            return True
    return False

class OpenAISTT:
    name = "openai-whisper"

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(base_url=base_url or None, api_key=api_key, timeout=60, max_retries=1)
        self.model = model
        self.name = f"whisper-api:{model}"

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> Dict[str, Any]:
        f = io.BytesIO(audio)
        f.name = filename
        try:
            resp = await self.client.audio.transcriptions.create(model=self.model, file=f, language="en",
                                                                 prompt=DOMAIN_PROMPT)
        except Exception as e:
            raise VoiceError(f"Transcription failed: {e}") from e
        text = (resp.text or "").strip()
        duration = len(decode_wav(audio)) / 16000 if is_wav(audio) else None
        return {"text": "" if transcript_is_hallucination(text, duration) else text}


class LocalWhisperSTT:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.name = f"local-whisper:{model_name.split('/')[-1]}"
        self._pipe = None
        self._prompt_ids = None
        self._lock = asyncio.Lock()

    def _load(self):
        if self._pipe is None:
            from transformers import pipeline
            self._pipe = pipeline("automatic-speech-recognition", model=self.model_name, device="cpu")
            self._prompt_ids = self._pipe.tokenizer.get_prompt_ids(DOMAIN_PROMPT, return_tensors="pt")
            logger.info("local_whisper_loaded", model=self.model_name)
        return self._pipe

    def _run(self, audio: np.ndarray) -> str:
        pipe = self._load()
        kwargs = {"prompt_ids": self._prompt_ids}
        if not self.model_name.endswith(".en"):
            kwargs["language"] = "en"
        return (pipe({"raw": audio, "sampling_rate": 16000}, generate_kwargs=kwargs).get("text") or "").strip()

    async def warmup(self):
        async with self._lock:
            await asyncio.to_thread(self._load)

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> Dict[str, Any]:
        if not is_wav(audio):
            raise VoiceError("Local speech recognition needs WAV audio. The web client sends WAV automatically.")
        samples = decode_wav(audio)
        duration = len(samples) / 16000
        if duration < 0.3 or not audio_is_speech(samples):
            return {"text": "", "duration_s": round(duration, 2)}
        async with self._lock:
            try:
                text = await asyncio.to_thread(self._run, samples)
            except Exception as e:
                raise VoiceError(f"Local transcription failed: {e}") from e
        if transcript_is_hallucination(text, duration):
            logger.info("stt_hallucination_dropped", text=text[:80])
            text = ""
        return {"text": text, "duration_s": round(duration, 2)}


# ── TTS ──

class OpenAITTS:
    def __init__(self, base_url: str, api_key: str, model: str, voice: str):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(base_url=base_url or None, api_key=api_key, timeout=30, max_retries=1)
        self.model, self.voice = model, voice
        self.name = f"openai-tts:{voice}"

    async def synthesize(self, text: str) -> Dict[str, Any]:
        try:
            resp = await self.client.audio.speech.create(model=self.model, voice=self.voice,
                                                         input=clean_for_speech(text), response_format="mp3")
        except Exception as e:
            raise VoiceError(f"Speech synthesis failed: {e}") from e
        return {"audio": resp.content, "mime": "audio/mpeg"}


class EdgeTTS:
    def __init__(self, voice: str):
        self.voice = voice
        self.name = f"edge-tts:{voice}"

    async def synthesize(self, text: str) -> Dict[str, Any]:
        import edge_tts
        buf = bytearray()
        try:
            communicate = edge_tts.Communicate(clean_for_speech(text), self.voice, rate="+5%")

            async def collect():
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        buf.extend(chunk["data"])
            await asyncio.wait_for(collect(), timeout=20)
        except Exception as e:
            raise VoiceError(f"Speech synthesis failed: {e}") from e
        if not buf:
            raise VoiceError("Speech synthesis returned no audio")
        return {"audio": bytes(buf), "mime": "audio/mpeg"}


# ── Factories ──

_stt = _tts = None
_stt_resolved = _tts_resolved = False


def get_stt_provider():
    """Configured STT engine, or None when transcription happens in the browser."""
    global _stt, _stt_resolved
    if _stt_resolved:
        return _stt
    s = get_settings()
    mode = s.stt_provider.lower()
    openai_key = s.stt_api_key or (s.llm_api_key if not s.llm_base_url else "")
    if mode == "openai" or (mode == "auto" and openai_key):
        _stt = OpenAISTT(s.stt_base_url, openai_key, s.stt_model)
    elif mode in ("local", "auto"):
        try:
            import transformers  # noqa: F401
            _stt = LocalWhisperSTT(s.stt_local_model)
        except ImportError:
            logger.warning("local_stt_unavailable")
    _stt_resolved = True
    logger.info("stt_provider", provider=_stt.name if _stt else "browser")
    return _stt


def get_tts_provider():
    """Configured TTS engine, or None when speech is synthesized in the browser."""
    global _tts, _tts_resolved
    if _tts_resolved:
        return _tts
    s = get_settings()
    mode = s.tts_provider.lower()
    openai_key = s.tts_api_key or (s.llm_api_key if not s.llm_base_url else "")
    if mode == "openai" or (mode == "auto" and openai_key):
        _tts = OpenAITTS(s.tts_base_url, openai_key, s.tts_model, s.tts_voice)
    elif mode in ("edge", "auto"):
        try:
            import edge_tts  # noqa: F401
            _tts = EdgeTTS(s.tts_edge_voice)
        except ImportError:
            logger.warning("edge_tts_unavailable")
    _tts_resolved = True
    logger.info("tts_provider", provider=_tts.name if _tts else "browser")
    return _tts


def set_voice_providers(stt=None, tts=None):
    """Testing hook."""
    global _stt, _tts, _stt_resolved, _tts_resolved
    _stt, _tts, _stt_resolved, _tts_resolved = stt, tts, True, True


async def synthesize_safe(text: str) -> Optional[Dict[str, Any]]:
    """TTS that never raises: returns None so the client falls back to text/browser speech."""
    tts = get_tts_provider()
    if not tts or not text:
        return None
    try:
        return await tts.synthesize(text)
    except VoiceError as e:
        logger.warning("tts_failed", error=str(e))
        return None
