"""Communication Agent: Drafts hyper-personalized outreach emails incorporating operator style rules and few-shot memory."""

import logging
from typing import Dict, Any, List
from app.agents.state import LeadWorkflowState
from app.agents.scout_agent import call_llm, ProviderError

logger = logging.getLogger(__name__)


async def draft_outreach_email(state: LeadWorkflowState) -> Dict[str, Any]:
    """Drafts outreach email applying past approved examples and human-taught style rules."""
    business = state["business_title"]
    category = state.get("category") or "business"
    selected_idea = state.get("selected_idea") or {}
    pitch = selected_idea.get("pitch", f"automate customer inquiries for {business}")
    style_rules = state.get("style_rules") or []
    few_shots = state.get("few_shot_examples") or []

    system_prompt = (
        "You are an outreach specialist writing cold emails to local business owners. "
        "Rules to follow strictly:\n"
        "- Friendly, casual, under 100 words.\n"
        "- Never sound like a generic marketing bot.\n"
        "- Explicitly mention their business name.\n"
        "- Explain the chosen service using only the operator's actual offer; never assume it is a voice receptionist.\n"
        "- Disclose that this is an AI-assisted outreach draft. No invented claims about their business or results.\n"
        "- Mention that they can opt out by replying STOP or not interested.\n"
        + ("\nOperator style rules:\n" + "\n".join(f"- {r}" for r in style_rules) if style_rules else "")
    )

    examples_block = ""
    if few_shots:
        examples_block = "\nPast decisions (imitate approved edits; avoid rejected proposals):\n"
        for ex in few_shots[:2]:
            final = ex.get("final_content", {})
            examples_block += f"Decision: {ex.get('human_action')} Feedback: {ex.get('notes') or ex.get('feedback_tag') or ''}\nSubject: {final.get('subject', '')}\nBody: {final.get('body', '')}\n---\n"

    user_prompt = (
        f"Target Business: {business}\n"
        f"Category: {category}\n"
        f"Key Angle: {pitch}\n"
        f"Operator's actual service: {state.get('service_offered', '')}\n"
        f"{examples_block}\n"
        f"Draft a subject line and email body. Output format:\n"
        f"SUBJECT: <subject line>\n"
        f"BODY:\n<email body>"
    )

    llm_output = await call_llm(system_prompt, user_prompt, strict=True)
    subject = f"Quick question regarding {business}"
    body = (
        f"Hi team at {business},\n\n"
        f"I came across {business} in Hyderabad and noticed how active you are with local clients. "
        f"{pitch}\n\n"
        f"We put together a quick 30-second interactive AI voice assistant demo customized specifically with {business}'s name. "
        f"Would you be open to hearing how it sounds?\n\n"
        f"Best regards,\nOutreach Team"
    )

    if llm_output and "SUBJECT:" in llm_output and "BODY:" in llm_output:
        try:
            parts = llm_output.split("BODY:", 1)
            subject = parts[0].replace("SUBJECT:", "").strip()
            body = parts[1].strip()
        except Exception:
            raise ProviderError("The outreach draft could not be parsed. Retry; no message was sent.")
    else:
        raise ProviderError("The outreach draft could not be parsed. Retry; no message was sent.")
    if not subject or not body or len(subject) > 500 or len(body) > 10000:
        raise ProviderError("The outreach draft was empty or too long. Retry; no message was sent.")

    return {
        "draft_subject": subject,
        "draft_body": body,
        "awaiting_human_approval": True,
        "current_step": "awaiting_approval",
    }
