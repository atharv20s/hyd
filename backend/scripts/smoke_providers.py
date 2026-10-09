"""Small live checks without sending emails or contacting any lead."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from app.agents.scout_agent import scout_propose_ideas, critic_filter_ideas
from app.agents.comms_agent import draft_outreach_email
from app.core.config import get_settings
from app.services.elevenlabs_service import ElevenLabsService


async def main():
    settings = get_settings()
    state = {
        "business_title": "HYD PS2 Test Salon (synthetic)", "category": "Hair salon",
        "service_offered": "AI receptionist and appointment request intake; bookings require staff confirmation",
        "website": "", "rating": 4.3, "review_count": 60, "address": "Banjara Hills, Hyderabad",
        "raw_data": {}, "style_rules": ["Warm English, no invented statistics, no confirmed booking promises"],
        "few_shot_examples": [], "proposed_ideas": []
    }
    state.update(await scout_propose_ideas(state))
    state.update(await critic_filter_ideas(state))
    state.update(await draft_outreach_email(state))
    print(json.dumps({"reasoning_pipeline": "passed", "ideas": len(state["proposed_ideas"]),
        "selected_service": state["selected_idea"]["hook_name"], "draft_ready": bool(state["draft_body"]),
        "awaiting_human_approval": state["awaiting_human_approval"]}))
    voice = ElevenLabsService()
    for role, agent_id in (("operator", settings.elevenlabs_operator_agent_id), ("client", settings.elevenlabs_agent_id)):
        agent = await voice.get_agent(agent_id)
        token = await voice.get_conversation_token(agent_id)
        print(json.dumps({"voice_role": role, "auth_enabled": agent["platform_settings"]["auth"]["enable_auth"],
            "session_token_ready": bool(token.get("token")), "tools": len(agent["conversation_config"]["agent"]["prompt"]["tool_ids"])}))
    async with httpx.AsyncClient(timeout=15) as client:
        result = await client.get("https://api.resend.com/domains", headers={"Authorization": f"Bearer {settings.resend_api_key}"})
        result.raise_for_status()
        domains = result.json().get("data", [])
        print(json.dumps({"resend_key": "valid", "verified_sending_domains": sum(domain.get("status") == "verified" for domain in domains),
                          "outreach_enabled": settings.email_enabled, "emails_sent_during_test": 0}))


if __name__ == "__main__":
    asyncio.run(main())
