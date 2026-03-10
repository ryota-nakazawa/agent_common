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
    missing_information: list[str] = Field(
        default_factory=list,
        description="Missing information that should be collected before resolution",
    )
    needs_follow_up: bool = Field(..., description="Whether the user should be asked follow-up questions")
    draft_reply: str = Field(..., description="Draft response to send back to the user")
    confidence: float = Field(..., ge=0, le=1, description="Confidence score between 0 and 1")
    reasoning_summary: str = Field(..., description="Short summary of why this triage decision was made")


class DecomposedInquiry(BaseModel):
    normalized_inquiry: str = Field(..., description="Normalized version of the original inquiry")
    sub_inquiries: list[str] = Field(
        default_factory=list,
        description="Decomposed sub-inquiries when the original inquiry contains multiple concerns",
    )
    detected_intents: list[str] = Field(
        default_factory=list,
        description="Detected intents such as login issue, billing inquiry, or feature request",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions made while decomposing the inquiry",
    )


class TaskEvaluation(BaseModel):
    is_sufficient: bool = Field(
        ...,
        description="Whether the current evidence is sufficient to produce a reliable triage decision",
    )
    issues: list[str] = Field(
        default_factory=list,
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
        default_factory=list,
        description="Follow-up questions to ask the user",
    )
    required_information: list[str] = Field(
        default_factory=list,
        description="Required information that is still missing",
    )
    reason: str = Field(..., description="Reason why follow-up is or is not needed")


class AgentResult(BaseModel):
    inquiry: str = Field(..., description="Original user inquiry")
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
