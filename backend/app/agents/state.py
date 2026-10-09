"""LangGraph orchestrator state definitions."""

from typing import TypedDict, Optional, List, Dict, Any


class LeadWorkflowState(TypedDict):
    operator_id: str
    lead_id: str
    service_offered: str       # Input 1: Operator's offering (e.g. "AI receptionist & auto-booking")
    target_clients: str        # Input 2: Target clientele (e.g. "Salons & Dental Clinics in Hyderabad")
    
    # Lead profile
    business_title: str
    category: str
    website: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    rating: Optional[float]
    review_count: Optional[int]
    address: Optional[str]
    raw_data: Optional[Dict[str, Any]]
    
    # Learned memory & context
    style_rules: List[str]
    few_shot_examples: List[Dict[str, Any]]
    
    # Scout & Critic steps
    proposed_ideas: List[Dict[str, str]]
    critique_notes: str
    selected_idea: Optional[Dict[str, str]]
    
    # Draft step
    draft_subject: Optional[str]
    draft_body: Optional[str]
    
    # Human-in-the-loop checkpoint
    awaiting_human_approval: bool
    human_action: Optional[str]  # "approved", "edited", "rejected"
    final_subject: Optional[str]
    final_body: Optional[str]
    feedback_tag: Optional[str]
    
    # Execution status
    current_step: str
    send_success: bool
    status_message: str
