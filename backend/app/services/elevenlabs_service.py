"""ElevenLabs Conversational AI voice agent integration."""

import logging
import httpx
from typing import Dict, Any, Optional
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ElevenLabsService:
    def __init__(self):
        self.api_key = settings.elevenlabs_api_key
        self.agent_id = settings.elevenlabs_agent_id
        self.base_url = "https://api.elevenlabs.io/v1"

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not configured")
        return {"xi-api-key": self.api_key, "Content-Type": "application/json"}

    async def create_agent(self, name: str = "HYD-PS2 Receptionist") -> str:
        """Create the default ElevenLabs conversational agent and return its ID."""
        payload = {
            "name": name,
            "tags": ["hyd-ps2", "receptionist"],
            "conversation_config": {
                "agent": {
                    "language": "en",
                    "first_message": "Hello! Thanks for calling. How can I help you today?",
                    "prompt": {
                        "prompt": (
                            "You are a warm, concise front-desk receptionist for a local business. "
                            "Qualify the caller, answer only from supplied context, collect their name, "
                            "phone number and request, and offer a human callback when uncertain. "
                            "Never invent prices, availability, or policies."
                        )
                    },
                }
            },
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/convai/agents/create",
                headers=self._headers(),
                json=payload,
                timeout=30.0,
            )
        if response.status_code != 200:
            raise RuntimeError(f"ElevenLabs agent creation failed ({response.status_code}): {response.text[:500]}")
        return response.json()["agent_id"]

    async def get_agent(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        resolved_id = agent_id or self.agent_id
        if not resolved_id:
            raise RuntimeError("ELEVENLABS_AGENT_ID is not configured")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/convai/agents/{resolved_id}",
                headers=self._headers(),
                timeout=20.0,
            )
        if response.status_code != 200:
            raise RuntimeError(f"ElevenLabs agent lookup failed ({response.status_code})")
        return response.json()

    async def get_conversation_token(self, agent_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}/convai/conversation/token", params={"agent_id": agent_id}, headers=self._headers())
        if response.status_code != 200:
            raise RuntimeError(f"Voice session failed ({response.status_code}).")
        return response.json()

    async def get_demo_widget_config(self, business_name: str, service_pitch: str) -> Dict[str, Any]:
        """Returns parameters to embed the ElevenLabs web conversational widget with dynamic prompt overrides."""
        prompt_override = (
            f"You are the virtual receptionist and assistant for {business_name}. "
            f"You are polite, professional, and knowledgeable. Answer caller questions about appointments, "
            f"services, and opening hours. If asked about digital solutions or improvements, mention: {service_pitch}."
        )
        return {
            "agent_id": self.agent_id,
            "business_name": business_name,
            "has_api_key": bool(self.api_key and self.api_key != "elevenlabs_test_key_here"),
            "dynamic_prompt": prompt_override,
            "sample_greeting": f"Hello! Welcome to {business_name}. How can I assist you today?",
        }

    async def generate_speech_preview(self, text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> Optional[bytes]:
        """Synthesize a quick TTS sample preview for demonstration."""
        if not self.api_key or self.api_key == "elevenlabs_test_key_here":
            logger.info("ElevenLabs key is mock; skipping live audio fetch")
            return None

        url = f"{self.base_url}/text-to-speech/{voice_id}"
        headers = {**self._headers(), "Accept": "audio/mpeg"}
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload, timeout=15.0)
            if resp.status_code == 200:
                return resp.content
            logger.error("ElevenLabs TTS error: %s", resp.text)
            return None
