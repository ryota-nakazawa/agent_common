import json
import operator
import re
from typing import Annotated, Literal, Sequence, TypedDict

from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.constants import Send
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from src.configs import Settings
from src.custom_logger import setup_logger
from src.models import (
    AgentResult,
    ConversationState,
    ConversationStateUpdate,
    ConversationTurn,
    DecomposedInquiry,
    HearingPlan,
    Plan,
    ReflectionResult,
    SearchOutput,
    Subtask,
    TaskEvaluation,
    TriageResult,
    ToolResult,
)
from src.prompts import SupportAgentPrompts

logger = setup_logger(__file__)


class AgentState(TypedDict):
    inquiry: str
    conversation_state: ConversationState
    decomposed_inquiry: DecomposedInquiry
    plan: list[str]
    current_step: int
    subtask_results: Annotated[Sequence[Subtask], operator.add]
    task_evaluation: TaskEvaluation
    hearing_plan: HearingPlan
    triage_result: TriageResult


class AgentSubGraphState(TypedDict):
    inquiry: str
    conversation_state: ConversationState
    plan: list[str]
    subtask: str
    is_completed: bool
    messages: list[ChatCompletionMessageParam]
    challenge_count: int
    tool_results: Annotated[Sequence[Sequence[ToolResult]], operator.add]
    reflection_results: Annotated[Sequence[ReflectionResult], operator.add]
    subtask_answer: str


class SupportAgent:
    def __init__(
        self,
        settings: Settings,
        tools: list,
        prompts: SupportAgentPrompts | None = None,
    ) -> None:
        self.settings = settings
        self.tools = tools
        self.tool_map = {tool.name: tool for tool in tools}
        self.prompts = prompts or SupportAgentPrompts(settings=settings)
        self.client = OpenAI(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_api_base,
        )

    def _log_messages(self, label: str, messages: list[ChatCompletionMessageParam]) -> None:
        summary: list[dict] = []
        for idx, message in enumerate(messages):
            entry = {
                "idx": idx,
                "role": message["role"],
                "has_tool_calls": "tool_calls" in message,
                "tool_call_id": message.get("tool_call_id"),
            }
            if "tool_calls" in message:
                entry["tool_call_ids"] = [tool_call["id"] for tool_call in message["tool_calls"]]
            content = message.get("content")
            if isinstance(content, str):
                entry["content_preview"] = content[:120]
            summary.append(entry)
        logger.info("%s messages=%s", label, summary)

    def _format_tool_results(self, tool_results: list[ToolResult]) -> str:
        lines: list[str] = []
        for result in tool_results:
            lines.append(f"Tool: {result.tool_name}")
            lines.append(f"Arguments: {result.args}")
            if not result.results:
                lines.append("Results: none")
            else:
                for idx, item in enumerate(result.results, start=1):
                    lines.append(f"Result {idx} Source: {item.file_name}")
                    lines.append(f"Result {idx} Content: {item.content}")
            lines.append("")
        return "\n".join(lines).strip()

    def _dedupe_preserve_order(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _is_guidance_first_inquiry(self, inquiry: str) -> bool:
        informational_patterns = (
            "方法",
            "手順",
            "設定",
            "確認場所",
            "どこ",
            "使い方",
            "見方",
            "止めたい",
            "変更したい",
            "教えて",
            "確認したい",
            "知りたい",
        )
        high_context_patterns = (
            "ログインできない",
            "入れない",
            "エラー",
            "障害",
            "請求",
            "返金",
            "権限",
            "ロック",
            "契約",
            "削除",
        )
        text = inquiry.strip()
        return any(pattern in text for pattern in informational_patterns) and not any(
            pattern in text for pattern in high_context_patterns
        )

    def _is_high_risk_inquiry(self, inquiry: str) -> bool:
        high_risk_patterns = (
            "請求",
            "返金",
            "契約",
            "解約",
            "削除",
            "権限",
            "障害",
            "エラー",
            "ログインできない",
            "入れない",
            "ロック",
            "セキュリティ",
            "個人情報",
        )
        text = inquiry.strip()
        return any(pattern in text for pattern in high_risk_patterns)

    def _looks_multi_issue(self, inquiry: str) -> bool:
        separators = ("し、", "かつ", "それに", "あと", "と、", "、あと", "。また")
        text = inquiry.strip()
        return any(separator in text for separator in separators)

    def _user_requested_human_handoff(self, inquiry: str, conversation_state: ConversationState) -> bool:
        patterns = (
            "人に",
            "担当者",
            "オペレーター",
            "サポートにつない",
            "電話",
            "チャットで",
            "有人",
            "直接対応",
        )
        recent_text = " ".join(turn.content for turn in conversation_state.turns[-4:] if turn.role == "user")
        recent_text = f"{recent_text} {inquiry}"
        return any(pattern in recent_text for pattern in patterns)

    def _normalize_plan(self, inquiry: str, plan: list[str]) -> list[str]:
        deduped_plan = self._dedupe_preserve_order(plan)
        if not self._is_guidance_first_inquiry(inquiry):
            return deduped_plan[:5]

        normalized_plan = [
            "FAQやドキュメントから共通手順や確認場所を特定する",
            "共通案内だけで進められるか、追加情報が本当に必要かを判断する",
            "問い合わせの分類、優先度、担当先を判断する",
        ]
        return normalized_plan

    def _search_fast_path_knowledge(self, inquiry: str) -> list[ToolResult]:
        tool_results: list[ToolResult] = []
        query_variants = self._build_fast_path_queries(inquiry)

        for query in query_variants:
            faq_args = json.dumps({"query": query}, ensure_ascii=False)
            doc_args = json.dumps({"keywords": query}, ensure_ascii=False)
            faq_results: list[SearchOutput] = self.tool_map["search_faq_answers"].invoke(faq_args)
            doc_results: list[SearchOutput] = self.tool_map["search_knowledge_documents"].invoke(doc_args)

            if faq_results:
                tool_results.append(
                    ToolResult(
                        tool_name="search_faq_answers",
                        args=faq_args,
                        results=faq_results,
                    )
                )
            if doc_results:
                tool_results.append(
                    ToolResult(
                        tool_name="search_knowledge_documents",
                        args=doc_args,
                        results=doc_results,
                    )
                )

        deduped_results: list[ToolResult] = []
        seen_sources: set[tuple[str, str, str]] = set()
        for tool_result in tool_results:
            unique_items: list[SearchOutput] = []
            for item in tool_result.results:
                source_key = (tool_result.tool_name, item.file_name, item.content)
                if source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
                unique_items.append(item)
            if unique_items:
                deduped_results.append(
                    ToolResult(
                        tool_name=tool_result.tool_name,
                        args=tool_result.args,
                        results=unique_items[:3],
                    )
                )
        return deduped_results

    def _build_fast_path_queries(self, inquiry: str) -> list[str]:
        base = inquiry.strip()
        simplified = base
        for phrase in (
            "教えてください",
            "教えて",
            "知りたいです",
            "知りたい",
            "確認したいです",
            "確認したい",
            "設定方法",
            "方法",
            "手順",
            "どこですか",
            "どこ",
            "確認場所",
            "したいです",
            "したい",
            "です",
            "ます",
            "。", 
            "、",
            "だけ",
        ):
            simplified = simplified.replace(phrase, " ")
        simplified = re.sub(r"\s+", " ", simplified).strip()

        queries = [base]
        if simplified:
            queries.append(simplified)

        if "通知" in base and ("止" in base or "停止" in base) and "プロジェクト" in base:
            queries.extend(
                [
                    "特定のプロジェクト 通知 停止 設定方法",
                    "通知設定 特定プロジェクト",
                ]
            )
        if "リリース" in base and ("確認" in base or "どこ" in base):
            queries.extend(["リリースノート 確認方法", "最新リリース 確認場所"])

        return self._dedupe_preserve_order(queries)

    def _build_fast_path_decomposed_inquiry(self, inquiry: str) -> DecomposedInquiry:
        normalized = inquiry.strip()
        intents: list[str] = []
        if "通知" in inquiry:
            intents.append("通知設定")
        elif "リリース" in inquiry:
            intents.append("情報確認")
        else:
            intents.append("一般案内")
        return DecomposedInquiry(
            normalized_inquiry=normalized,
            sub_inquiries=[normalized],
            detected_intents=intents,
            assumptions=[],
        )

    def _build_fast_path_triage_result(
        self,
        inquiry: str,
        tool_results: list[ToolResult],
        handoff_needed: bool,
    ) -> TriageResult:
        guidance: list[str] = []
        resolved_parts: list[str] = []
        for tool_result in tool_results:
            for item in tool_result.results[:2]:
                guidance.append(item.content)
                resolved_parts.append(f"{item.file_name} を根拠に案内可能")

        immediate_guidance = self._dedupe_preserve_order(guidance)[:2]
        resolved_parts = self._dedupe_preserve_order(resolved_parts)

        if "通知" in inquiry:
            category = "通知設定方法"
        elif "リリース" in inquiry:
            category = "確認方法"
        else:
            category = "一般案内"

        if handoff_needed:
            return TriageResult(
                category=category,
                priority="medium",
                assigned_team="カスタマーサポート",
                resolved_parts=resolved_parts,
                unresolved_parts=["FAQやドキュメントだけでは十分な案内を特定できませんでした。"],
                blocking_items=[],
                optional_context=[],
                immediate_guidance=immediate_guidance,
                candidate_actions=["担当者へ引き継いで詳細確認する"],
                needs_follow_up=False,
                next_user_action="担当者からの案内をお待ちください。",
                draft_reply=(
                    "FAQやドキュメントだけでは十分な案内を特定できなかったため、担当者に引き継ぎます。"
                    "必要に応じて詳細確認のご連絡を差し上げます。"
                ),
                handoff_needed=True,
                handoff_target="カスタマーサポート",
                handoff_reason="ナレッジに明確な一致がなく、一般案内だけでは解決が見込めないためです。",
                handoff_payload=f"問い合わせ: {inquiry}",
                confidence=0.65,
                reasoning_summary="FAQやドキュメントに明確な一致が見つからなかったため、人による確認が適切と判断しました。",
            )

        guidance_lines = "\n".join(f"- {item}" for item in immediate_guidance) if immediate_guidance else "- 該当手順を確認中です。"
        return TriageResult(
            category=category,
            priority="medium",
            assigned_team="カスタマーサポート",
            resolved_parts=resolved_parts or ["FAQやドキュメントから共通案内を提示可能"],
            unresolved_parts=[],
            blocking_items=[],
            optional_context=[],
            immediate_guidance=immediate_guidance,
            candidate_actions=[
                "まずは案内した共通手順を試す",
                "うまくいかなければ画面名や状況を共有する",
            ],
            needs_follow_up=False,
            next_user_action="まずは上記の案内内容をお試しください。",
            draft_reply=(
                "FAQやドキュメントで確認できた共通手順をご案内します。\n"
                f"{guidance_lines}\n"
                "まずは上記をお試しください。うまくいかない場合は、表示された画面や状況を教えてください。"
            ),
            handoff_needed=False,
            handoff_target="",
            handoff_reason="FAQやドキュメントの共通案内でまず対応可能です。",
            handoff_payload="",
            confidence=0.88,
            reasoning_summary="FAQやドキュメントに一致する共通案内が見つかったため、まずはその内容を案内する方針としました。",
        )
        return tool_results

    def _build_fast_path_subtask(self, inquiry: str, tool_results: list[ToolResult]) -> Subtask:
        snippets: list[str] = []
        for tool_result in tool_results:
            for item in tool_result.results[:2]:
                snippets.append(f"{item.file_name}: {item.content}")
        if snippets:
            summary = (
                "FAQまたはドキュメントでは、"
                + " / ".join(snippets[:3])
                + " と案内されています。まずはこの共通案内を返してください。"
            )
        else:
            summary = (
                "FAQまたはドキュメントに明確な一致が見つからなかったため、"
                "一般案内ではなく担当者への引き継ぎ判断を優先してください。"
            )
        return Subtask(
            task_name="ナレッジから共通案内を作成する",
            tool_results=[tool_results],
            reflection_results=[
                ReflectionResult(
                    advice="共通案内を優先して返し、必要なら追加文脈は optional として扱う。",
                    is_completed=True,
                )
            ],
            is_completed=True,
            subtask_answer=summary,
            challenge_count=1,
        )

    def _run_fast_path(
        self,
        inquiry: str,
        conversation_state: ConversationState,
    ) -> AgentResult | None:
        if (
            self._user_requested_human_handoff(inquiry, conversation_state)
            or self._is_high_risk_inquiry(inquiry)
            or self._looks_multi_issue(inquiry)
        ):
            return None

        updated_state = self.update_conversation_state(
            {"inquiry": inquiry, "conversation_state": conversation_state}
        )["conversation_state"]
        tool_results = self._search_fast_path_knowledge(inquiry)
        if not tool_results and not self._is_guidance_first_inquiry(inquiry):
            return None

        logger.info("Running fast path")
        decomposed_inquiry = self._build_fast_path_decomposed_inquiry(inquiry)
        plan = Plan(subtasks=["ナレッジから共通案内を作成する"])
        subtask = self._build_fast_path_subtask(inquiry, tool_results)
        handoff_needed = not bool(tool_results)
        task_evaluation = TaskEvaluation(
            resolution_mode="handoff_to_human" if handoff_needed else "answer_from_knowledge",
            is_sufficient=not handoff_needed,
            can_provide_general_guidance=not handoff_needed,
            blocking_reasons=[],
            optional_context_reasons=[],
            handoff_recommended=handoff_needed,
            handoff_reason=(
                "FAQやドキュメントの共通案内で対応可能です。"
                if not handoff_needed
                else "FAQやドキュメントに明確な一致がなく、人による確認が必要です。"
            ),
            issues=[] if not handoff_needed else ["FAQやドキュメントに明確な一致がありませんでした。"],
            recommended_next_action=(
                "FAQやドキュメントにある共通手順を案内する"
                if not handoff_needed
                else "担当者へ引き継ぐ"
            ),
            confidence=0.88 if not handoff_needed else 0.65,
        )
        hearing_plan = HearingPlan(
            should_ask_follow_up=False,
            questions=[],
            required_information=[],
            reason=(
                "共通案内が可能なため、追加確認は不要です。"
                if not handoff_needed
                else "ナレッジに一致がなく、人への引き継ぎを優先するため追加確認は行いません。"
            ),
        )
        triage_result = self._build_fast_path_triage_result(
            inquiry=inquiry,
            tool_results=tool_results,
            handoff_needed=handoff_needed,
        )
        final_conversation_state = ConversationState(
            turns=[
                *updated_state.turns,
                ConversationTurn(role="assistant", content=triage_result.draft_reply),
            ],
            latest_inquiry=inquiry,
            conversation_summary=updated_state.conversation_summary,
            problem_summary=updated_state.problem_summary,
            user_goal=updated_state.user_goal,
            sub_issues=updated_state.sub_issues,
            confirmed_facts=updated_state.confirmed_facts,
            blocking_items=triage_result.blocking_items,
            optional_context=triage_result.optional_context,
            immediate_guidance=triage_result.immediate_guidance,
            candidate_actions=triage_result.candidate_actions,
            resolved_parts=triage_result.resolved_parts,
            unresolved_parts=triage_result.unresolved_parts,
            latest_user_update=updated_state.latest_user_update,
            last_triage_result=triage_result,
            last_task_evaluation=task_evaluation,
            last_hearing_plan=hearing_plan,
        )
        return AgentResult(
            inquiry=inquiry,
            processing_path="fast_path",
            conversation_state=final_conversation_state,
            decomposed_inquiry=decomposed_inquiry,
            plan=plan,
            subtasks=[subtask],
            task_evaluation=task_evaluation,
            hearing_plan=hearing_plan,
            triage_result=triage_result,
        )

    def _format_decomposed_inquiry(self, decomposed_inquiry: DecomposedInquiry) -> str:
        return "\n".join(
            [
                f"normalized_inquiry: {decomposed_inquiry.normalized_inquiry}",
                f"sub_inquiries: {decomposed_inquiry.sub_inquiries}",
                f"detected_intents: {decomposed_inquiry.detected_intents}",
                f"assumptions: {decomposed_inquiry.assumptions}",
            ]
        )

    def _format_conversation_state(self, conversation_state: ConversationState | None) -> str:
        if conversation_state is None:
            return "conversation_state: not available"

        turn_lines = [f"{turn.role}: {turn.content}" for turn in conversation_state.turns[-6:]]
        return "\n".join(
            [
                f"latest_inquiry: {conversation_state.latest_inquiry}",
                f"conversation_summary: {conversation_state.conversation_summary}",
                f"problem_summary: {conversation_state.problem_summary}",
                f"user_goal: {conversation_state.user_goal}",
                f"sub_issues: {conversation_state.sub_issues}",
                f"confirmed_facts: {conversation_state.confirmed_facts}",
                f"blocking_items: {conversation_state.blocking_items}",
                f"optional_context: {conversation_state.optional_context}",
                f"immediate_guidance: {conversation_state.immediate_guidance}",
                f"candidate_actions: {conversation_state.candidate_actions}",
                f"resolved_parts: {conversation_state.resolved_parts}",
                f"unresolved_parts: {conversation_state.unresolved_parts}",
                f"latest_user_update: {conversation_state.latest_user_update}",
                f"last_triage_result: {conversation_state.last_triage_result.model_dump() if conversation_state.last_triage_result else None}",
                f"last_task_evaluation: {conversation_state.last_task_evaluation.model_dump() if conversation_state.last_task_evaluation else None}",
                f"last_hearing_plan: {conversation_state.last_hearing_plan.model_dump() if conversation_state.last_hearing_plan else None}",
                f"recent_turns: {turn_lines}",
            ]
        )

    def update_conversation_state(self, state: AgentState) -> dict:
        logger.info("Updating conversation state")
        current_state = state["conversation_state"]
        messages = [
            {"role": "system", "content": self.prompts.conversation_state_system_prompt},
            {
                "role": "user",
                "content": self.prompts.conversation_state_user_prompt.format(
                    conversation_state=self._format_conversation_state(current_state),
                    inquiry=state["inquiry"],
                ),
            },
        ]
        self._log_messages("update_conversation_state.request", messages)

        response = self.client.beta.chat.completions.parse(
            model=self.settings.openai_model,
            messages=messages,
            response_format=ConversationStateUpdate,
            temperature=0,
            seed=0,
        )
        state_update = response.choices[0].message.parsed
        if state_update is None:
            raise ValueError("Conversation state update is None")

        updated_state = ConversationState(
            turns=[*current_state.turns, ConversationTurn(role="user", content=state["inquiry"])],
            latest_inquiry=state["inquiry"],
            conversation_summary=state_update.conversation_summary,
            problem_summary=state_update.problem_summary,
            user_goal=state_update.user_goal,
            sub_issues=state_update.sub_issues,
            confirmed_facts=state_update.confirmed_facts,
            blocking_items=state_update.blocking_items,
            optional_context=state_update.optional_context,
            immediate_guidance=state_update.immediate_guidance,
            candidate_actions=state_update.candidate_actions,
            resolved_parts=state_update.resolved_parts,
            unresolved_parts=state_update.unresolved_parts,
            latest_user_update=state_update.latest_user_update,
            last_triage_result=current_state.last_triage_result,
            last_task_evaluation=current_state.last_task_evaluation,
            last_hearing_plan=current_state.last_hearing_plan,
        )
        return {"conversation_state": updated_state}

    def _format_task_evaluation(self, task_evaluation: TaskEvaluation | None) -> str:
        if task_evaluation is None:
            return "task_evaluation: not available"
        return "\n".join(
            [
                f"resolution_mode: {task_evaluation.resolution_mode}",
                f"is_sufficient: {task_evaluation.is_sufficient}",
                f"can_provide_general_guidance: {task_evaluation.can_provide_general_guidance}",
                f"blocking_reasons: {task_evaluation.blocking_reasons}",
                f"optional_context_reasons: {task_evaluation.optional_context_reasons}",
                f"handoff_recommended: {task_evaluation.handoff_recommended}",
                f"handoff_reason: {task_evaluation.handoff_reason}",
                f"issues: {task_evaluation.issues}",
                f"recommended_next_action: {task_evaluation.recommended_next_action}",
                f"confidence: {task_evaluation.confidence}",
            ]
        )

    def _normalize_triage_result(self, triage_result: TriageResult, task_evaluation: TaskEvaluation) -> TriageResult:
        triage_result.immediate_guidance = self._dedupe_preserve_order(triage_result.immediate_guidance)
        triage_result.resolved_parts = self._dedupe_preserve_order(triage_result.resolved_parts)
        triage_result.unresolved_parts = self._dedupe_preserve_order(triage_result.unresolved_parts)
        triage_result.candidate_actions = self._dedupe_preserve_order(triage_result.candidate_actions)
        triage_result.handoff_needed = task_evaluation.handoff_recommended
        triage_result.handoff_reason = task_evaluation.handoff_reason

        if task_evaluation.handoff_recommended or task_evaluation.resolution_mode == "handoff_to_human":
            triage_result.blocking_items = []
            triage_result.needs_follow_up = False
            if not triage_result.handoff_target:
                triage_result.handoff_target = triage_result.assigned_team
            if not triage_result.handoff_payload:
                triage_result.handoff_payload = (
                    f"カテゴリ: {triage_result.category}; "
                    f"未解決: {' / '.join(triage_result.unresolved_parts) or '要確認'}; "
                    f"ユーザー要望: {triage_result.next_user_action}"
                )
        elif task_evaluation.can_provide_general_guidance and not task_evaluation.blocking_reasons:
            triage_result.blocking_items = []
            triage_result.optional_context = self._dedupe_preserve_order(
                triage_result.optional_context + task_evaluation.optional_context_reasons
            )
            triage_result.needs_follow_up = False
            if triage_result.immediate_guidance:
                triage_result.next_user_action = (
                    "まずは上記の共通手順をお試しください。必要であれば追加情報をお知らせください。"
                )
        else:
            triage_result.blocking_items = self._dedupe_preserve_order(task_evaluation.blocking_reasons)
            triage_result.optional_context = self._dedupe_preserve_order(
                triage_result.optional_context + task_evaluation.optional_context_reasons
            )
            triage_result.needs_follow_up = bool(triage_result.blocking_items)

        return triage_result

    def _format_hearing_plan(self, hearing_plan: HearingPlan | None) -> str:
        if hearing_plan is None:
            return "hearing_plan: not available"
        return "\n".join(
            [
                f"should_ask_follow_up: {hearing_plan.should_ask_follow_up}",
                f"required_information: {hearing_plan.required_information}",
                f"questions: {[question.model_dump() for question in hearing_plan.questions]}",
                f"reason: {hearing_plan.reason}",
            ]
        )

    def decompose_inquiry(self, state: AgentState) -> dict:
        logger.info("Starting query decomposition")
        messages = [
            {"role": "system", "content": self.prompts.query_decomposition_system_prompt},
            {
                "role": "user",
                "content": self.prompts.query_decomposition_user_prompt.format(
                    inquiry=state["inquiry"],
                    conversation_state=self._format_conversation_state(state["conversation_state"]),
                ),
            },
        ]
        self._log_messages("decompose_inquiry.request", messages)

        response = self.client.beta.chat.completions.parse(
            model=self.settings.openai_model,
            messages=messages,
            response_format=DecomposedInquiry,
            temperature=0,
            seed=0,
        )
        decomposed_inquiry = response.choices[0].message.parsed
        if decomposed_inquiry is None:
            raise ValueError("Decomposed inquiry is None")
        return {"decomposed_inquiry": decomposed_inquiry}

    def create_plan(self, state: AgentState) -> dict:
        logger.info("Starting plan generation")
        messages = [
            {"role": "system", "content": self.prompts.planner_system_prompt},
            {
                "role": "user",
                "content": self.prompts.planner_user_prompt.format(
                    inquiry=state["inquiry"],
                    conversation_state=self._format_conversation_state(state["conversation_state"]),
                    decomposed_inquiry=self._format_decomposed_inquiry(state["decomposed_inquiry"]),
                ),
            },
        ]
        self._log_messages("create_plan.request", messages)

        response = self.client.beta.chat.completions.parse(
            model=self.settings.openai_model,
            messages=messages,
            response_format=Plan,
            temperature=0,
            seed=0,
        )
        plan = response.choices[0].message.parsed
        return {"plan": self._normalize_plan(state["inquiry"], plan.subtasks)}

    def select_tools(self, state: AgentSubGraphState) -> dict:
        logger.info("Selecting tools")
        openai_tools = [convert_to_openai_tool(tool) for tool in self.tools]

        if state["challenge_count"] == 0:
            messages: list[ChatCompletionMessageParam] = [
                {"role": "system", "content": self.prompts.subtask_system_prompt},
                {
                    "role": "user",
                    "content": self.prompts.subtask_tool_selection_user_prompt.format(
                        inquiry=state["inquiry"],
                        conversation_state=self._format_conversation_state(state["conversation_state"]),
                        plan=state["plan"],
                        subtask=state["subtask"],
                    ),
                },
            ]
        else:
            retry_advice = state["reflection_results"][-1].advice
            messages = [
                {"role": "system", "content": self.prompts.subtask_system_prompt},
                {
                    "role": "user",
                    "content": (
                        self.prompts.subtask_tool_selection_user_prompt.format(
                            inquiry=state["inquiry"],
                            conversation_state=self._format_conversation_state(state["conversation_state"]),
                            plan=state["plan"],
                            subtask=state["subtask"],
                        )
                        + "\n\n"
                        + self.prompts.subtask_retry_answer_user_prompt
                        + f"\n\n前回の反省内容:\n{retry_advice}"
                    ),
                }
            ]

        self._log_messages("select_tools.request", messages)
        response = self.client.chat.completions.create(
            model=self.settings.openai_model,
            messages=messages,
            tools=openai_tools,  # type: ignore[arg-type]
            tool_choice="required",
            temperature=0,
            seed=0,
        )

        if response.choices[0].message.tool_calls is None:
            raise ValueError("Tool calls are None")

        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    tool_call.model_dump() for tool_call in response.choices[0].message.tool_calls
                ],
            }
        )
        return {"messages": messages}

    def execute_tools(self, state: AgentSubGraphState) -> dict:
        logger.info("Executing tools")
        messages = state["messages"]
        tool_calls = messages[-1]["tool_calls"]
        if tool_calls is None:
            raise ValueError("Tool calls are None")

        tool_results: list[ToolResult] = []

        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            tool_args = tool_call["function"]["arguments"]
            tool = self.tool_map[tool_name]
            tool_result: list[SearchOutput] = tool.invoke(tool_args)

            tool_results.append(
                ToolResult(
                    tool_name=tool_name,
                    args=tool_args,
                    results=tool_result,
                )
            )
            messages.append(
                {
                    "role": "tool",
                    "content": str(tool_result),
                    "tool_call_id": tool_call["id"],
                }
            )

        clean_messages = [
            message for message in messages if message["role"] in {"system", "user"}
        ]
        clean_messages.append(
            {
                "role": "user",
                "content": (
                    "ツール実行結果を共有します。以下を根拠としてサブタスク回答を作成してください。\n\n"
                    f"{self._format_tool_results(tool_results)}"
                ),
            }
        )
        self._log_messages("execute_tools.next_messages", clean_messages)

        return {"messages": clean_messages, "tool_results": [tool_results]}

    def create_subtask_answer(self, state: AgentSubGraphState) -> dict:
        logger.info("Creating subtask answer")
        messages = state["messages"]
        self._log_messages("create_subtask_answer.request", messages)
        response = self.client.chat.completions.create(
            model=self.settings.openai_model,
            messages=messages,
            temperature=0,
            seed=0,
        )
        subtask_answer = response.choices[0].message.content
        messages.append({"role": "assistant", "content": subtask_answer})
        return {"messages": messages, "subtask_answer": subtask_answer}

    def reflect_subtask(self, state: AgentSubGraphState) -> dict:
        logger.info("Reflecting on subtask answer")
        messages = state["messages"]
        messages.append({"role": "user", "content": self.prompts.subtask_reflection_user_prompt})
        self._log_messages("reflect_subtask.request", messages)

        response = self.client.beta.chat.completions.parse(
            model=self.settings.openai_model,
            messages=messages,
            response_format=ReflectionResult,
            temperature=0,
            seed=0,
        )

        reflection_result = response.choices[0].message.parsed
        if reflection_result is None:
            raise ValueError("Reflection result is None")

        messages.append(
            {
                "role": "assistant",
                "content": reflection_result.model_dump_json(),
            }
        )

        update_state = {
            "messages": messages,
            "reflection_results": [reflection_result],
            "challenge_count": state["challenge_count"] + 1,
            "is_completed": reflection_result.is_completed,
        }

        if (
            update_state["challenge_count"] >= self.settings.max_challenge_count
            and not reflection_result.is_completed
        ):
            update_state["subtask_answer"] = f"{state['subtask']}について十分な情報を確認できませんでした。"

        return update_state

    def create_triage_result(self, state: AgentState) -> dict:
        logger.info("Creating triage result")
        subtask_results = [(result.task_name, result.subtask_answer) for result in state["subtask_results"]]
        messages = [
            {"role": "system", "content": self.prompts.create_last_answer_system_prompt},
            {
                "role": "user",
                "content": self.prompts.create_last_answer_user_prompt.format(
                    inquiry=state["inquiry"],
                    conversation_state=self._format_conversation_state(state["conversation_state"]),
                    decomposed_inquiry=self._format_decomposed_inquiry(state["decomposed_inquiry"]),
                    task_evaluation=self._format_task_evaluation(state["task_evaluation"]),
                    hearing_plan=self._format_hearing_plan(state["hearing_plan"]),
                    subtask_results=str(subtask_results),
                ),
            },
        ]
        self._log_messages("create_triage_result.request", messages)

        response = self.client.beta.chat.completions.parse(
            model=self.settings.openai_model,
            messages=messages,
            response_format=TriageResult,
            temperature=0,
            seed=0,
        )
        triage_result = response.choices[0].message.parsed
        if triage_result is None:
            raise ValueError("Triage result is None")
        return {"triage_result": self._normalize_triage_result(triage_result, state["task_evaluation"])}

    def create_task_evaluation(self, state: AgentState) -> dict:
        logger.info("Creating task evaluation")
        subtask_results = [(result.task_name, result.subtask_answer) for result in state["subtask_results"]]
        messages = [
            {"role": "system", "content": self.prompts.task_evaluation_system_prompt},
            {
                "role": "user",
                "content": self.prompts.task_evaluation_user_prompt.format(
                    inquiry=state["inquiry"],
                    conversation_state=self._format_conversation_state(state["conversation_state"]),
                    decomposed_inquiry=self._format_decomposed_inquiry(state["decomposed_inquiry"]),
                    subtask_results=str(subtask_results),
                ),
            },
        ]
        self._log_messages("create_task_evaluation.request", messages)

        response = self.client.beta.chat.completions.parse(
            model=self.settings.openai_model,
            messages=messages,
            response_format=TaskEvaluation,
            temperature=0,
            seed=0,
        )
        task_evaluation = response.choices[0].message.parsed
        if task_evaluation is None:
            raise ValueError("Task evaluation is None")
        if self._user_requested_human_handoff(state["inquiry"], state["conversation_state"]):
            task_evaluation.resolution_mode = "handoff_to_human"
            task_evaluation.handoff_recommended = True
            task_evaluation.handoff_reason = "ユーザーが人との対応を希望しているためです。"
            task_evaluation.blocking_reasons = []
        return {"task_evaluation": task_evaluation}

    def create_hearing_plan(self, state: AgentState) -> dict:
        logger.info("Creating hearing plan")
        if (
            state["task_evaluation"].is_sufficient
            or not state["task_evaluation"].blocking_reasons
            or state["task_evaluation"].handoff_recommended
        ):
            return {
                "hearing_plan": HearingPlan(
                    should_ask_follow_up=False,
                    questions=[],
                    required_information=[],
                    reason="共通案内で進められるか、人への引き継ぎを優先するため、追加確認は不要です。",
                )
            }

        subtask_results = [(result.task_name, result.subtask_answer) for result in state["subtask_results"]]
        messages = [
            {"role": "system", "content": self.prompts.hearing_system_prompt},
            {
                "role": "user",
                "content": self.prompts.hearing_user_prompt.format(
                    inquiry=state["inquiry"],
                    conversation_state=self._format_conversation_state(state["conversation_state"]),
                    decomposed_inquiry=self._format_decomposed_inquiry(state["decomposed_inquiry"]),
                    task_evaluation=self._format_task_evaluation(state["task_evaluation"]),
                    subtask_results=str(subtask_results),
                ),
            },
        ]
        self._log_messages("create_hearing_plan.request", messages)

        response = self.client.beta.chat.completions.parse(
            model=self.settings.openai_model,
            messages=messages,
            response_format=HearingPlan,
            temperature=0,
            seed=0,
        )
        hearing_plan = response.choices[0].message.parsed
        if hearing_plan is None:
            raise ValueError("Hearing plan is None")
        hearing_plan.required_information = self._dedupe_preserve_order(
            state["task_evaluation"].blocking_reasons
        )
        hearing_plan.should_ask_follow_up = bool(hearing_plan.required_information)
        return {"hearing_plan": hearing_plan}

    def _execute_subgraph(self, state: AgentState) -> dict:
        subgraph = self._create_subgraph()
        result = subgraph.invoke(
            {
                "inquiry": state["inquiry"],
                "conversation_state": state["conversation_state"],
                "plan": state["plan"],
                "subtask": state["plan"][state["current_step"]],
                "is_completed": False,
                "challenge_count": 0,
            }
        )

        return {
            "subtask_results": [
                Subtask(
                    task_name=result["subtask"],
                    tool_results=result["tool_results"],
                    reflection_results=result["reflection_results"],
                    is_completed=result["is_completed"],
                    subtask_answer=result["subtask_answer"],
                    challenge_count=result["challenge_count"],
                )
            ]
        }

    def _should_continue_exec_subtasks(self, state: AgentState) -> list:
        return [
            Send(
                "execute_subtasks",
                {
                    "inquiry": state["inquiry"],
                    "conversation_state": state["conversation_state"],
                    "plan": state["plan"],
                    "current_step": idx,
                },
            )
            for idx, _ in enumerate(state["plan"])
        ]

    def _should_continue_exec_subtask_flow(self, state: AgentSubGraphState) -> Literal["end", "continue"]:
        if state["is_completed"] or state["challenge_count"] >= self.settings.max_challenge_count:
            return "end"
        return "continue"

    def _create_subgraph(self) -> Pregel:
        workflow = StateGraph(AgentSubGraphState)
        workflow.add_node("select_tools", self.select_tools)
        workflow.add_node("execute_tools", self.execute_tools)
        workflow.add_node("create_subtask_answer", self.create_subtask_answer)
        workflow.add_node("reflect_subtask", self.reflect_subtask)

        workflow.add_edge(START, "select_tools")
        workflow.add_edge("select_tools", "execute_tools")
        workflow.add_edge("execute_tools", "create_subtask_answer")
        workflow.add_edge("create_subtask_answer", "reflect_subtask")
        workflow.add_conditional_edges(
            "reflect_subtask",
            self._should_continue_exec_subtask_flow,
            {"continue": "select_tools", "end": END},
        )
        return workflow.compile()

    def create_graph(self) -> Pregel:
        workflow = StateGraph(AgentState)
        workflow.add_node("update_conversation_state", self.update_conversation_state)
        workflow.add_node("decompose_inquiry", self.decompose_inquiry)
        workflow.add_node("create_plan", self.create_plan)
        workflow.add_node("execute_subtasks", self._execute_subgraph)
        workflow.add_node("create_task_evaluation", self.create_task_evaluation)
        workflow.add_node("create_hearing_plan", self.create_hearing_plan)
        workflow.add_node("create_triage_result", self.create_triage_result)

        workflow.add_edge(START, "update_conversation_state")
        workflow.add_edge("update_conversation_state", "decompose_inquiry")
        workflow.add_edge("decompose_inquiry", "create_plan")
        workflow.add_conditional_edges("create_plan", self._should_continue_exec_subtasks)
        workflow.add_edge("execute_subtasks", "create_task_evaluation")
        workflow.add_edge("create_task_evaluation", "create_hearing_plan")
        workflow.add_edge("create_hearing_plan", "create_triage_result")
        workflow.set_finish_point("create_triage_result")
        return workflow.compile()

    def run_agent(self, inquiry: str, conversation_state: ConversationState | None = None) -> AgentResult:
        current_state = conversation_state or ConversationState()
        fast_path_result = self._run_fast_path(inquiry, current_state)
        if fast_path_result is not None:
            return fast_path_result

        app = self.create_graph()
        result = app.invoke(
            {
                "inquiry": inquiry,
                "conversation_state": current_state,
                "current_step": 0,
            }
        )
        updated_blocking_items = (
            result["hearing_plan"].required_information
            if result["hearing_plan"].should_ask_follow_up
            else result["triage_result"].blocking_items
        )
        updated_conversation_state = ConversationState(
            turns=[
                *result["conversation_state"].turns,
                ConversationTurn(role="assistant", content=result["triage_result"].draft_reply),
            ],
            latest_inquiry=inquiry,
            conversation_summary=result["conversation_state"].conversation_summary,
            problem_summary=result["conversation_state"].problem_summary,
            user_goal=result["conversation_state"].user_goal,
            sub_issues=result["conversation_state"].sub_issues,
            confirmed_facts=result["conversation_state"].confirmed_facts,
            blocking_items=updated_blocking_items,
            optional_context=result["triage_result"].optional_context,
            immediate_guidance=result["triage_result"].immediate_guidance,
            candidate_actions=result["triage_result"].candidate_actions,
            resolved_parts=result["triage_result"].resolved_parts,
            unresolved_parts=result["triage_result"].unresolved_parts,
            latest_user_update=result["conversation_state"].latest_user_update,
            last_triage_result=result["triage_result"],
            last_task_evaluation=result["task_evaluation"],
            last_hearing_plan=result["hearing_plan"],
        )
        return AgentResult(
            inquiry=inquiry,
            processing_path="deep_path",
            conversation_state=updated_conversation_state,
            decomposed_inquiry=result["decomposed_inquiry"],
            plan=Plan(subtasks=result["plan"]),
            subtasks=result["subtask_results"],
            task_evaluation=result["task_evaluation"],
            hearing_plan=result["hearing_plan"],
            triage_result=result["triage_result"],
        )
