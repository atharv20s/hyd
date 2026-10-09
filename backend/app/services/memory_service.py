"""MongoDB memory layer for human-in-the-loop decisions, chat history, and operator preferences."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
import uuid
import json
import time
from app.core.database import get_redis
from app.core.config import get_settings


class MemoryService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.decisions = db["human_decisions"]
        self.transcripts = db["chat_transcripts"]
        self.operator_rules = db["operator_rules"]
        self.enrichment_cache = db["enrichment_cache"]

    # ── Human Decisions (Learning Layer) ──

    async def record_decision(
        self,
        operator_id: str,
        lead_id: str,
        checkpoint_type: str,  # "idea_approval", "message_draft", "reply_escalation", "deal_terms"
        agent_proposal: Dict[str, Any],
        human_action: str,  # "approved", "edited", "rejected"
        final_content: Dict[str, Any],
        feedback_tag: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> str:
        """Stores a human checkpoint decision to be used for in-context learning."""
        decision_id = str(uuid.uuid4())
        doc = {
            "decision_id": decision_id,
            "operator_id": operator_id,
            "lead_id": lead_id,
            "checkpoint_type": checkpoint_type,
            "agent_proposal": agent_proposal,
            "human_action": human_action,
            "final_content": final_content,
            "feedback_tag": feedback_tag,
            "notes": notes,
            "created_at": datetime.now(timezone.utc),
        }
        await self.decisions.insert_one(doc)

        # If user repeatedly edited or gave feedback, synthesize or update style rule
        if human_action == "edited" and feedback_tag:
            await self._update_operator_rule(operator_id, feedback_tag)

        return decision_id

    async def get_recent_decisions(
        self,
        operator_id: str,
        checkpoint_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieves past decisions for few-shot prompt injection."""
        query: Dict[str, Any] = {"operator_id": operator_id}
        if checkpoint_type:
            query["checkpoint_type"] = checkpoint_type

        cursor = self.decisions.find(query).sort("created_at", -1).limit(limit)
        results = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            results.append(doc)
        return results

    async def get_recent_outcomes(self, operator_id: str, limit: int = 5):
        """Real response outcomes supplement operator examples; no synthetic success rate."""
        rows = []
        async for row in self.db["idea_outcomes"].find({"operator_id": operator_id}).sort("created_at", -1).limit(limit):
            row["_id"] = str(row["_id"])
            rows.append(row)
        return rows

    async def _update_operator_rule(self, operator_id: str, rule: str) -> None:
        """Upsert operator style preferences based on repeated human corrections."""
        await self.operator_rules.update_one(
            {"operator_id": operator_id, "rule": rule},
            {"$inc": {"occurrence_count": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def get_operator_rules(self, operator_id: str) -> List[str]:
        """Fetch accumulated style rules for prompt system messages."""
        cursor = self.operator_rules.find({"operator_id": operator_id}).sort("occurrence_count", -1)
        rules = []
        async for doc in cursor:
            rules.append(doc["rule"])
        return rules

    # ── Chat Transcripts & Memory ──

    async def save_chat_message(
        self,
        session_id: str,
        sender_type: str,  # "operator", "client", "agent"
        role: str,  # "user", "assistant", "system"
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        doc = {
            "session_id": session_id,
            "sender_type": sender_type,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc),
            "recorded_at_ns": time.time_ns(),
        }
        await self.transcripts.insert_one(doc)
        cache = await get_redis()
        if cache is not None:
            try:
                await cache.delete(f"hydps2:chat:{session_id}")
            except Exception:
                pass

    async def get_chat_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        cache = await get_redis()
        cache_key = f"hydps2:chat:{session_id}:{limit}"
        # Cache keys include the durable transcript count so writes by other replicas
        # invalidate old snapshots without relying on process-local state.
        count = await self.transcripts.count_documents({"session_id": session_id})
        cache_key += f":{count}"
        if cache is not None:
            try:
                cached = await cache.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass
        cursor = self.transcripts.find({"session_id": session_id}).sort([("timestamp", -1), ("recorded_at_ns", -1), ("_id", -1)]).limit(limit)
        messages = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            messages.append(doc)
        messages.reverse()
        if cache is not None:
            try:
                await cache.set(cache_key, json.dumps(messages, default=str), ex=get_settings().cache_ttl_seconds)
            except Exception:
                pass
        return messages
