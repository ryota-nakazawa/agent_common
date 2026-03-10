from typing import Optional

from pydantic import BaseModel, Field


class SearchOutput(BaseModel):
    file_name: str = Field(description="Source name")
    content: str = Field(description="Relevant content")


class Plan(BaseModel):
    subtasks: list[str] = Field(..., description="Subtasks required to triage the inquiry")


class ToolResult(BaseModel):
    tool_name: str = Field(..., description="Tool name")
    args: str = Field(..., description="Tool arguments")
    results: list[SearchOutput] = Field(..., description="Search results")


class ReflectionResult(BaseModel):
    advice: str = Field(
        ...,
        description=(
            "If the current search is not sufficient, explain why and suggest a non-overlapping retry strategy."
        ),
    )
    is_completed: bool = Field(
        ...,
        description="Whether the subtask can be answered correctly from the available tool results",
    )


class Subtask(BaseModel):
    task_name: str = Field(..., description="Subtask name")
    tool_results: list[list[ToolResult]] = Field(..., description="Tool results grouped by attempt")
    reflection_results: list[ReflectionResult] = Field(..., description="Reflection results")
    is_completed: bool = Field(..., description="Whether the subtask completed successfully")
    subtask_answer: str = Field(..., description="Answer produced for the subtask")
    challenge_count: int = Field(..., description="Number of attempts")


class TriageResult(BaseModel):
    category: str = Field(..., description="Inquiry category")
    priority: str = Field(..., description="Priority level such as low, medium, or high")
    assigned_team: str = Field(..., description="Team or queue that should handle the inquiry")
    resolved_parts: list[str] = Field(
        ...,
        description="Parts of the inquiry that can already be handled or explained",
    )
    unresolved_parts: list[str] = Field(
        ...,
        description="Parts of the inquiry that remain unresolved after the current turn",
    )
    blocking_items: list[str] = Field(
        ...,
        description="Information that is required before the next meaningful step can be taken",
    )
    optional_context: list[str] = Field(
        ...,
        description="Helpful context that improves answer precision but is not required to continue",
    )
    immediate_guidance: list[str] = Field(
        ...,
        description="Guidance or steps that can be provided immediately from available documents and FAQs",
    )
    candidate_actions: list[str] = Field(
        ...,
        description="Concrete next actions the agent or user can take from the current state",
    )
    needs_follow_up: bool = Field(..., description="Whether the user should be asked follow-up questions")
    next_user_action: str = Field(
        ...,
        description="Most important next action to ask from the user or operator",
    )
    draft_reply: str = Field(..., description="Draft response to send back to the user")
    handoff_needed: bool = Field(..., description="Whether the inquiry should be handed off to a human")
    handoff_target: str = Field(
        ...,
        description="Team, queue, or contact point for handoff. Empty string if no handoff is needed",
    )
    handoff_reason: str = Field(
        ...,
        description="Reason why handoff is or is not required at the current stage",
    )
    handoff_payload: str = Field(
        ...,
        description="Short handoff summary for a human operator. Empty string if no handoff is needed",
    )
    confidence: float = Field(..., ge=0, le=1, description="Confidence score between 0 and 1")
    reasoning_summary: str = Field(..., description="Short summary of why this triage decision was made")


class DecomposedInquiry(BaseModel):
    normalized_inquiry: str = Field(..., description="Normalized version of the original inquiry")
    sub_inquiries: list[str] = Field(
        ...,
        description="Decomposed sub-inquiries when the original inquiry contains multiple concerns",
    )
    detected_intents: list[str] = Field(
        ...,
        description="Detected intents such as login issue, billing inquiry, or feature request",
    )
    assumptions: list[str] = Field(
        ...,
        description="Assumptions made while decomposing the inquiry",
    )


class TaskEvaluation(BaseModel):
    resolution_mode: str = Field(
        ...,
        description="One of answer_from_knowledge, needs_more_context, or handoff_to_human",
    )
    is_sufficient: bool = Field(
        ...,
        description="Whether the current evidence is sufficient to produce a reliable triage decision",
    )
    can_provide_general_guidance: bool = Field(
        ...,
        description="Whether the agent can already provide a useful general answer from the current knowledge",
    )
    blocking_reasons: list[str] = Field(
        ...,
        description="Information gaps that truly block the next meaningful step",
    )
    optional_context_reasons: list[str] = Field(
        ...,
        description="Helpful context that improves answer precision but does not block general guidance",
    )
    handoff_recommended: bool = Field(
        ...,
        description="Whether a human handoff is recommended at the current stage",
    )
    handoff_reason: str = Field(
        ...,
        description="Why a human handoff is or is not recommended",
    )
    issues: list[str] = Field(
        ...,
        description="Known issues, gaps, or uncertainties in the current triage process",
    )
    recommended_next_action: str = Field(
        ...,
        description="Recommended next action such as ask follow-up questions, search more documents, or escalate",
    )
    confidence: float = Field(..., ge=0, le=1, description="Confidence score between 0 and 1")


class HearingQuestion(BaseModel):
    question: str = Field(..., description="Follow-up question to ask the user")
    purpose: str = Field(..., description="Why this question is needed for triage")


class HearingPlan(BaseModel):
    should_ask_follow_up: bool = Field(
        ...,
        description="Whether the agent should ask the user follow-up questions before finalizing triage",
    )
    questions: list[HearingQuestion] = Field(
        ...,
        description="Follow-up questions to ask the user",
    )
    required_information: list[str] = Field(
        ...,
        description="Required information that is still missing",
    )
    reason: str = Field(..., description="Reason why follow-up is or is not needed")


class ResolvedInformationItem(BaseModel):
    key: str = Field(..., description="Structured field name")
    value: str = Field(..., description="Resolved value for the field")


class ConversationTurn(BaseModel):
    role: str = Field(..., description="Conversation role such as user or assistant")
    content: str = Field(..., description="Message content")


class ConversationState(BaseModel):
    turns: list[ConversationTurn] = Field(
        default_factory=list,
        description="Conversation history across turns",
    )
    latest_inquiry: str | None = Field(
        default=None,
        description="Latest user inquiry or follow-up answer",
    )
    conversation_summary: str = Field(
        default="",
        description="Running summary of the conversation and current issue state",
    )
    problem_summary: str = Field(
        default="",
        description="Short summary of the user's current problem",
    )
    user_goal: str = Field(
        default="",
        description="Short summary of what the user wants to achieve",
    )
    sub_issues: list[str] = Field(
        default_factory=list,
        description="Issue topics or sub-issues being tracked across turns",
    )
    confirmed_facts: list[ResolvedInformationItem] = Field(
        default_factory=list,
        description="Structured facts already confirmed from the user conversation",
    )
    blocking_items: list[str] = Field(
        default_factory=list,
        description="Information still required before the next meaningful step can be taken",
    )
    optional_context: list[str] = Field(
        default_factory=list,
        description="Helpful context that improves answer precision but is not required to continue",
    )
    immediate_guidance: list[str] = Field(
        default_factory=list,
        description="Guidance or steps that can already be provided from available information",
    )
    candidate_actions: list[str] = Field(
        default_factory=list,
        description="Current candidate actions inferred from the conversation state",
    )
    resolved_parts: list[str] = Field(
        default_factory=list,
        description="Parts of the inquiry already resolved or addressed",
    )
    unresolved_parts: list[str] = Field(
        default_factory=list,
        description="Parts of the inquiry that remain unresolved",
    )
    latest_user_update: list[str] = Field(
        default_factory=list,
        description="Facts newly provided by the user in the latest turn",
    )
    last_triage_result: TriageResult | None = Field(
        default=None,
        description="Most recent triage result",
    )
    last_task_evaluation: TaskEvaluation | None = Field(
        default=None,
        description="Most recent task evaluation",
    )
    last_hearing_plan: HearingPlan | None = Field(
        default=None,
        description="Most recent hearing plan",
    )


class ConversationStateUpdate(BaseModel):
    conversation_summary: str = Field(
        ...,
        description="Updated summary of the issue and conversation state after incorporating the latest user message",
    )
    problem_summary: str = Field(
        ...,
        description="Updated short summary of the user's current problem",
    )
    user_goal: str = Field(
        ...,
        description="Updated short summary of the user's current goal",
    )
    sub_issues: list[str] = Field(
        ...,
        description="Updated issue topics or sub-issues being tracked across turns",
    )
    confirmed_facts: list[ResolvedInformationItem] = Field(
        ...,
        description="Updated structured facts confirmed from the user conversation",
    )
    blocking_items: list[str] = Field(
        ...,
        description="Updated information still required before the next meaningful step can be taken",
    )
    optional_context: list[str] = Field(
        ...,
        description="Updated helpful context that improves answer precision but is not required to continue",
    )
    immediate_guidance: list[str] = Field(
        ...,
        description="Updated guidance or steps that can already be provided from available information",
    )
    candidate_actions: list[str] = Field(
        ...,
        description="Updated current candidate actions inferred from the conversation state",
    )
    resolved_parts: list[str] = Field(
        ...,
        description="Updated resolved parts of the inquiry",
    )
    unresolved_parts: list[str] = Field(
        ...,
        description="Updated unresolved parts of the inquiry",
    )
    latest_user_update: list[str] = Field(
        ...,
        description="Facts newly provided by the user in the latest turn",
    )


class AgentResult(BaseModel):
    inquiry: str = Field(..., description="Original user inquiry")
    processing_path: str = Field(..., description="Execution path such as fast_path or deep_path")
    conversation_state: ConversationState = Field(..., description="Updated conversation state")
    decomposed_inquiry: DecomposedInquiry = Field(..., description="Normalized and decomposed inquiry")
    plan: Plan = Field(..., description="Generated plan")
    subtasks: list[Subtask] = Field(..., description="Executed subtasks")
    task_evaluation: Optional[TaskEvaluation] = Field(
        default=None,
        description="Evaluation of whether current evidence is sufficient for triage",
    )
    hearing_plan: Optional[HearingPlan] = Field(
        default=None,
        description="Follow-up hearing plan when additional information is needed",
    )
    triage_result: TriageResult = Field(..., description="Structured triage output")
