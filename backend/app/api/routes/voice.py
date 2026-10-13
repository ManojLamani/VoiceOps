"""Voice routes: STT, TTS, a one-shot REST voice turn, and the real-time WebSocket voice session.

WebSocket protocol (/api/voice/ws/{conversation_id}):
  client -> {"type": "auth", "token": "..."}            (must be first)
  client -> {"type": "audio", "data": "<base64 WAV>"}   (one utterance, end-pointed by the client VAD)
  client -> {"type": "text", "content": "..."}          (typed fallback, or browser-side STT result)
  client -> {"type": "end"} | {"type": "ping"}
  server -> READY, TRANSCRIBING, TRANSCRIPT, NO_SPEECH, STT_FAILED, INTENT_DETECTED, RETRIEVING_KNOWLEDGE,
            KNOWLEDGE_RETRIEVED, TOOL_CALL_STARTED, TOOL_CALL_COMPLETED, CONFIRMATION_REQUIRED,
            GENERATING_RESPONSE, ESCALATED, AGENT_RESPONSE, SPEAKING (audio), TTS_UNAVAILABLE,
            COMPLETED (end of turn, carries status), CONVERSATION_ENDED, ERROR
"""
import asyncio
import base64
import binascii
import json
import time
from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.privacy import for_user
from app.core.security import get_current_user, decode_access_token
from app.db.database import get_db, async_session_factory
from app.db.models import User, Conversation
from app.schemas.schemas import MAX_MESSAGE_CHARS
from app.services.agent_service import CustomerSupportAgent, ConversationNotFound, ConversationClosed
from app.services.voice_service import get_stt_provider, get_tts_provider, synthesize_safe, VoiceError

logger = get_logger("voice_routes")
router = APIRouter(prefix="/voice", tags=["Voice"])

MAX_AUDIO_BYTES = 10 * 1024 * 1024
RECONNECT_GRACE_SECONDS = 120
_background_tasks: set = set()  # strong references so pending auto-end tasks aren't garbage-collected
_connections: Dict[str, int] = {}


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


async def _transcribe(audio: bytes, filename: str = "audio.wav") -> str:
    stt = get_stt_provider()
    if stt is None:
        raise HTTPException(status_code=503, detail="No server speech recognition configured; use browser STT")
    try:
        return (await stt.transcribe(audio, filename))["text"]
    except VoiceError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="No audio received")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large")
    started = time.perf_counter()
    text = await _transcribe(data, audio.filename or "audio.wav")
    return {"text": text, "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "provider": get_stt_provider().name}


@router.post("/synthesize")
async def synthesize_speech(data: SynthesizeRequest, current_user: dict = Depends(get_current_user)):
    tts = get_tts_provider()
    if tts is None:
        raise HTTPException(status_code=503, detail="No server TTS configured; use browser speech synthesis")
    try:
        out = await tts.synthesize(data.text)
    except VoiceError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return Response(content=out["audio"], media_type=out["mime"])


@router.post("/respond")
async def voice_respond(conversation_id: UUID = Form(...),
                        text: Optional[str] = Form(None, max_length=MAX_MESSAGE_CHARS),
                        audio: Optional[UploadFile] = File(None), current_user: dict = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """One complete voice turn over REST: audio -> STT -> agent -> TTS."""
    started = time.perf_counter()
    transcript = (text or "").strip()
    stt_ms = None
    if not transcript and audio is not None:
        data = await audio.read()
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio too large")
        t = time.perf_counter()
        transcript = await _transcribe(data, audio.filename or "audio.wav")
        stt_ms = round((time.perf_counter() - t) * 1000, 1)
        if not transcript:
            return {"status": "no_speech", "transcript": "", "content": None}
    if not transcript:
        raise HTTPException(status_code=400, detail="Provide text or audio")

    result = await CustomerSupportAgent(db).process_message(conversation_id, transcript)
    t = time.perf_counter()
    speech = await synthesize_safe(result["content"])
    result.update({
        "transcript": transcript,
        "audio_base64": base64.b64encode(speech["audio"]).decode() if speech else None,
        "audio_mime": speech["mime"] if speech else None,
        "timings": {"stt_ms": stt_ms, "agent_ms": result["processing_time_ms"],
                    "tts_ms": round((time.perf_counter() - t) * 1000, 1) if speech else None,
                    "total_ms": round((time.perf_counter() - started) * 1000, 1)},
    })
    return for_user(current_user, result)


# ── WebSocket voice session ──

async def _authenticate(ws: WebSocket) -> Optional[dict]:
    try:
        msg = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        if msg.get("type") != "auth":
            return None
        payload = decode_access_token(msg.get("token", ""))
        async with async_session_factory() as session:
            user = await session.get(User, UUID(payload["sub"]))
        return {"user_id": str(user.id), "email": user.email, "role": user.role} \
            if user and user.is_active else None
    except Exception:
        return None


async def _auto_end(conversation_id: UUID):
    """End a conversation whose client disconnected and never came back."""
    await asyncio.sleep(RECONNECT_GRACE_SECONDS)
    if _connections.get(str(conversation_id), 0) > 0:
        return
    try:
        async with async_session_factory() as session:
            conv = await session.get(Conversation, conversation_id)
            if conv and conv.ended_at is None:
                await CustomerSupportAgent(session).end_conversation(conversation_id)
                await session.commit()
                logger.info("conversation_auto_ended", conversation_id=str(conversation_id))
    except Exception as e:
        logger.warning("auto_end_failed", error=str(e))


@router.websocket("/ws/{conversation_id}")
async def voice_websocket(ws: WebSocket, conversation_id: str):
    await ws.accept()
    try:
        conv_id = UUID(conversation_id)
    except ValueError:
        await ws.send_json({"type": "ERROR", "data": {"error": "Invalid conversation id", "fatal": True}})
        await ws.close(code=4400)
        return
    user = await _authenticate(ws)
    if not user:
        await ws.send_json({"type": "AUTH_FAILED", "data": {"error": "Invalid or expired token"}})
        await ws.close(code=4401)
        return
    async with async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
    if not conv or conv.ended_at is not None:
        await ws.send_json({"type": "ERROR", "data": {"error": "Conversation not found or already ended",
                                                      "fatal": True}})
        await ws.close(code=4404)
        return

    key = str(conv_id)
    _connections[key] = _connections.get(key, 0) + 1
    stt, tts = get_stt_provider(), get_tts_provider()

    async def send(event: str, data: dict = None):
        payload = data or {}
        if event != "SPEAKING":  # every event carrying agent/tool data is masked for viewers
            payload = for_user(user, payload)
        await ws.send_json({"type": event, "data": payload})

    await send("READY", {"stt": stt.name if stt else "browser", "tts": tts.name if tts else "browser"})
    ended = False

    async def run_turn(user_text: str, stt_ms: Optional[float] = None):
        started = time.perf_counter()
        async with async_session_factory() as session:
            agent = CustomerSupportAgent(session)
            try:
                result = await agent.process_message(conv_id, user_text, event_callback=send)
                await session.commit()
            except ConversationClosed:
                await send("ERROR", {"error": "This conversation has ended.", "fatal": True})
                return
            except Exception as e:
                await session.rollback()
                logger.error("ws_turn_failed", error=str(e))
                await send("ERROR", {"error": "Something went wrong while handling that. Please try again.",
                                     "retry": True})
                return
        await send("AGENT_RESPONSE", result)
        t = time.perf_counter()
        speech = await synthesize_safe(result["content"])
        if speech:
            await send("SPEAKING", {"audio": base64.b64encode(speech["audio"]).decode(), "mime": speech["mime"],
                                    "message_id": result["message_id"]})
        else:
            await send("TTS_UNAVAILABLE", {"text": result["content"], "fallback": "browser"})
        await send("COMPLETED", {
            "status": result["status"],
            "timings": {"stt_ms": stt_ms, "agent_ms": result["processing_time_ms"],
                        "tts_ms": round((time.perf_counter() - t) * 1000, 1) if speech else None,
                        "total_ms": round((time.perf_counter() - started) * 1000 + (stt_ms or 0), 1)}})

    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                await send("ERROR", {"error": "Malformed message"})
                continue
            kind = msg.get("type")

            if kind == "ping":
                await send("PONG")
            elif kind == "text":
                content = (msg.get("content") or "").strip()
                if len(content) > MAX_MESSAGE_CHARS:
                    await send("ERROR", {"error": f"Message too long (max {MAX_MESSAGE_CHARS} characters).",
                                         "retry": True})
                    continue
                if content:
                    await send("TRANSCRIPT", {"text": content, "source": msg.get("source", "typed")})
                    await run_turn(content)
            elif kind == "audio":
                try:
                    audio = base64.b64decode(msg.get("data") or "", validate=True)
                except (binascii.Error, ValueError):
                    await send("STT_FAILED", {"error": "Audio payload was not valid base64.", "retry": True})
                    continue
                if not audio or len(audio) > MAX_AUDIO_BYTES:
                    await send("STT_FAILED", {"error": "Audio was empty or too large.", "retry": True})
                    continue
                if stt is None:
                    await send("STT_FAILED", {"error": "Server speech recognition is not configured.",
                                              "fallback": "browser"})
                    continue
                await send("TRANSCRIBING")
                t = time.perf_counter()
                try:
                    text = (await stt.transcribe(audio, "audio.wav"))["text"]
                except VoiceError as e:
                    logger.warning("ws_stt_failed", error=str(e))
                    await send("STT_FAILED", {"error": "I couldn't process that audio. Please try again.",
                                              "retry": True})
                    continue
                stt_ms = round((time.perf_counter() - t) * 1000, 1)
                if not text:
                    await send("NO_SPEECH", {"stt_ms": stt_ms})
                    continue
                await send("TRANSCRIPT", {"text": text, "source": "stt", "stt_ms": stt_ms})
                await run_turn(text, stt_ms)
            elif kind == "end":
                async with async_session_factory() as session:
                    try:
                        summary = await CustomerSupportAgent(session).end_conversation(conv_id)
                        await session.commit()
                    except ConversationNotFound:
                        summary = {}
                await send("CONVERSATION_ENDED", summary)
                ended = True
                await ws.close()
                break
            else:
                await send("ERROR", {"error": f"Unknown message type '{kind}'"})
    except WebSocketDisconnect:
        logger.info("ws_disconnected", conversation_id=key)
    finally:
        _connections[key] = max(0, _connections.get(key, 1) - 1)
        if not ended:
            task = asyncio.create_task(_auto_end(conv_id))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
