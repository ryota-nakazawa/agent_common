from pathlib import Path

import streamlit as st

from src.agent import SupportAgent
from src.configs import Settings
from src.inquiry_store import append_inquiry_record
from src.knowledge_base import LocalKnowledgeBase
from src.models import AgentResult, ConversationState, HearingPlan, TaskEvaluation, TriageResult
from src.tools import build_tools

APP_TITLE = "Generic Inquiry Triage Agent"
DATA_DIR = Path(__file__).resolve().parent / "data"
SAMPLE_INQUIRIES = [
    "ログインに5回失敗してアカウントがロックされました。今日中に作業したいです。",
    "特定のプロジェクトだけ通知を止めたいです。設定方法を教えてください。",
    "最新リリースの確認場所が分かりません。",
]


@st.cache_resource
def get_knowledge_base() -> LocalKnowledgeBase:
    return LocalKnowledgeBase.from_paths(
        documents_path=DATA_DIR / "knowledge_documents.json",
        faq_path=DATA_DIR / "faq_items.json",
    )


@st.cache_resource
def get_agent() -> SupportAgent:
    settings = Settings()
    knowledge_base = get_knowledge_base()
    tools = build_tools(knowledge_base=knowledge_base)
    return SupportAgent(settings=settings, tools=tools)


def render_sidebar(settings: Settings | None) -> None:
    st.sidebar.header("Configuration")
    st.sidebar.write("1. `.env` を設定")
    st.sidebar.write("2. `data/*.json` を差し替え")
    st.sidebar.write("3. `make run.ui`")

    if settings is not None:
        knowledge_base = get_knowledge_base()
        st.sidebar.divider()
        st.sidebar.subheader("Current Setup")
        st.sidebar.write(f"Domain: `{settings.domain_name}`")
        st.sidebar.write(f"Model: `{settings.openai_model}`")
        st.sidebar.write(f"Documents: `{len(knowledge_base.documents)}`")
        st.sidebar.write(f"FAQ Items: `{len(knowledge_base.faq_items)}`")

    st.sidebar.divider()
    st.sidebar.subheader("Sample Inquiries")
    for sample in SAMPLE_INQUIRIES:
        if st.sidebar.button(sample, use_container_width=True):
            st.session_state.pending_prompt = sample

    if st.sidebar.button("Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pending_prompt = None
        st.session_state.conversation_state = ConversationState()
        st.rerun()

    st.sidebar.divider()
    st.sidebar.link_button("管理画面を開く", "http://localhost:8501", use_container_width=True)


def inject_page_style() -> None:
    st.markdown(
        """
        <style>
        .agent-hero {
            background:
                radial-gradient(circle at top right, rgba(15,118,110,0.14), transparent 24%),
                linear-gradient(180deg, #fbfaf7 0%, #f3f0e8 100%);
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 24px;
            padding: 1.3rem 1.4rem 1.15rem 1.4rem;
            margin-bottom: 1rem;
        }
        .agent-eyebrow {
            color: #0f766e;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-weight: 700;
        }
        .agent-title {
            color: #111827;
            font-size: 1.9rem;
            font-weight: 800;
            margin-top: 0.35rem;
            margin-bottom: 0.4rem;
        }
        .agent-subtitle {
            color: #4b5563;
            font-size: 0.98rem;
            max-width: 60rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_hero(settings: Settings | None) -> None:
    domain_text = settings.domain_name if settings is not None else "サポート対象サービス"
    st.markdown(
        f"""
        <div class="agent-hero">
            <div class="agent-eyebrow">Inquiry Desk</div>
            <div class="agent-title">{domain_text} の前さばき画面</div>
            <div class="agent-subtitle">
                まずは FAQ / ドキュメントでそのまま案内できるかを優先し、
                難しい問い合わせだけを深い前さばきへ回します。
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_messages() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if "triage_result" in message:
                render_triage_result(TriageResult.model_validate(message["triage_result"]))
                task_evaluation = message.get("task_evaluation")
                if task_evaluation is not None:
                    render_task_evaluation(TaskEvaluation.model_validate(task_evaluation))
                hearing_plan = message.get("hearing_plan")
                if hearing_plan is not None:
                    render_hearing_plan(HearingPlan.model_validate(hearing_plan))
                conversation_state = message.get("conversation_state")
                if conversation_state is not None:
                    render_conversation_state(ConversationState.model_validate(conversation_state))
            else:
                st.markdown(message["content"])


def render_triage_result(result: TriageResult) -> None:
    st.markdown("**ご案内**")
    st.info(result.draft_reply)
    if result.handoff_needed:
        st.warning(f"引き継ぎ先: {result.handoff_target or result.assigned_team}")
        st.caption(result.handoff_reason)
    elif result.needs_follow_up:
        st.markdown("**確認したいこと**")
        st.write(result.next_user_action)
    elif result.next_user_action:
        st.markdown("**次の一手**")
        st.write(result.next_user_action)

    with st.expander("詳細を見る", expanded=False):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Category", result.category)
        col2.metric("Priority", result.priority)
        col3.metric("Assigned Team", result.assigned_team)
        col4.metric("Confidence", f"{result.confidence:.2f}")

        st.markdown("**Immediate Guidance**")
        if result.immediate_guidance:
            for item in result.immediate_guidance:
                st.write(f"- {item}")
        else:
            st.write("- なし")

        st.markdown("**Resolved Parts**")
        if result.resolved_parts:
            for item in result.resolved_parts:
                st.write(f"- {item}")
        else:
            st.write("- なし")

        st.markdown("**Unresolved Parts**")
        if result.unresolved_parts:
            for item in result.unresolved_parts:
                st.write(f"- {item}")
        else:
            st.write("- なし")

        st.markdown("**Blocking Items**")
        if result.blocking_items:
            for item in result.blocking_items:
                st.write(f"- {item}")
        else:
            st.write("- なし")

        st.markdown("**Candidate Actions**")
        if result.candidate_actions:
            for item in result.candidate_actions:
                st.write(f"- {item}")
        else:
            st.write("- なし")

        st.markdown("**Reasoning Summary**")
        st.write(result.reasoning_summary)

        if result.handoff_needed:
            st.markdown("**Handoff Summary**")
            st.write(f"Target: {result.handoff_target or 'なし'}")
            st.write(f"Payload: {result.handoff_payload or 'なし'}")


def render_task_evaluation(task_evaluation: TaskEvaluation | None) -> None:
    if task_evaluation is None:
        return

    sufficiency = "Sufficient" if task_evaluation.is_sufficient else "Insufficient"
    general_guidance = "Available" if task_evaluation.can_provide_general_guidance else "Not Available"
    handoff = "Recommended" if task_evaluation.handoff_recommended else "Not Needed"
    st.markdown("**Task Evaluation**")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Evidence Sufficiency", sufficiency)
    col2.metric("Evaluation Confidence", f"{task_evaluation.confidence:.2f}")
    col3.metric("General Guidance", general_guidance)
    col4.metric("Handoff", handoff)

    st.markdown("**Resolution Mode**")
    st.write(task_evaluation.resolution_mode)

    st.markdown("**Recommended Next Action**")
    st.write(task_evaluation.recommended_next_action)

    with st.expander("Blocking Reasons"):
        if task_evaluation.blocking_reasons:
            for item in task_evaluation.blocking_reasons:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Optional Context Reasons"):
        if task_evaluation.optional_context_reasons:
            for item in task_evaluation.optional_context_reasons:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Handoff Reason"):
        st.write(task_evaluation.handoff_reason)

    with st.expander("Evaluation Issues"):
        if task_evaluation.issues:
            for item in task_evaluation.issues:
                st.write(f"- {item}")
        else:
            st.write("- なし")


def render_hearing_plan(hearing_plan: HearingPlan | None) -> None:
    if hearing_plan is None:
        return

    follow_up = "Required" if hearing_plan.should_ask_follow_up else "Not Required"
    st.markdown("**Hearing Plan**")
    st.write(follow_up)

    st.markdown("**Required Information**")
    if hearing_plan.required_information:
        for item in hearing_plan.required_information:
            st.write(f"- {item}")
    else:
        st.write("- なし")

    with st.expander("Follow-up Questions"):
        if hearing_plan.questions:
            for question in hearing_plan.questions:
                st.write(f"- {question.question}")
                st.caption(question.purpose)
        else:
            st.write("- なし")

    with st.expander("Hearing Reason"):
        st.write(hearing_plan.reason)


def render_conversation_state(conversation_state: ConversationState | None) -> None:
    if conversation_state is None:
        return

    st.markdown("**Conversation State**")
    st.markdown("**Summary**")
    st.write(conversation_state.conversation_summary or "- なし")
    st.markdown("**Problem Summary**")
    st.write(conversation_state.problem_summary or "- なし")
    st.markdown("**User Goal**")
    st.write(conversation_state.user_goal or "- なし")

    with st.expander("Sub Issues"):
        if conversation_state.sub_issues:
            for item in conversation_state.sub_issues:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Confirmed Facts"):
        if conversation_state.confirmed_facts:
            for item in conversation_state.confirmed_facts:
                st.write(f"- {item.key}: {item.value}")
        else:
            st.write("- なし")

    with st.expander("Resolved Parts"):
        if conversation_state.resolved_parts:
            for item in conversation_state.resolved_parts:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Immediate Guidance"):
        if conversation_state.immediate_guidance:
            for item in conversation_state.immediate_guidance:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Unresolved Parts"):
        if conversation_state.unresolved_parts:
            for item in conversation_state.unresolved_parts:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Blocking Items"):
        if conversation_state.blocking_items:
            for item in conversation_state.blocking_items:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Optional Context"):
        if conversation_state.optional_context:
            for item in conversation_state.optional_context:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Candidate Actions"):
        if conversation_state.candidate_actions:
            for item in conversation_state.candidate_actions:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with st.expander("Latest User Update"):
        if conversation_state.latest_user_update:
            for item in conversation_state.latest_user_update:
                st.write(f"- {item}")
        else:
            st.write("- なし")


def run_triage(prompt: str) -> None:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        agent = get_agent()
        with st.chat_message("assistant"):
            with st.status("前さばきを開始しています...", expanded=True) as status:
                status.write("問い合わせを整理しています")
                status.write("ナレッジとFAQを確認しています")
                result: AgentResult = agent.run_agent(prompt, st.session_state.conversation_state)
                status.write("回答をまとめています")
                status.update(label="前さばきが完了しました", state="complete", expanded=False)
            st.session_state.conversation_state = result.conversation_state
            append_inquiry_record(result)
            render_triage_result(result.triage_result)
            with st.expander("詳細情報", expanded=False):
                render_task_evaluation(result.task_evaluation)
                render_hearing_plan(result.hearing_plan)
                render_conversation_state(result.conversation_state)
    except Exception as exc:
        error_message = (
            "前さばき結果の生成に失敗しました。`.env` と `data/*.json` の内容を確認してください。\n\n"
            f"詳細: `{exc}`"
        )
        with st.chat_message("assistant"):
            st.error(error_message)
        st.session_state.messages.append({"role": "assistant", "content": error_message})
        return

    st.session_state.messages.append(
        {
            "role": "assistant",
            "triage_result": result.triage_result.model_dump(),
            "task_evaluation": result.task_evaluation.model_dump() if result.task_evaluation is not None else None,
            "hearing_plan": result.hearing_plan.model_dump() if result.hearing_plan is not None else None,
            "conversation_state": result.conversation_state.model_dump(),
        }
    )


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon=":speech_balloon:", layout="wide")
    inject_page_style()

    try:
        settings = Settings()
    except Exception:
        settings = None

    render_hero(settings)

    render_sidebar(settings)

    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "問い合わせを入力してください。ローカルのナレッジとFAQを参照して前さばき結果を返します。",
            }
        ]
    if "pending_prompt" not in st.session_state:
        st.session_state.pending_prompt = None
    if "conversation_state" not in st.session_state:
        st.session_state.conversation_state = ConversationState()

    st.caption("右のサンプル問い合わせ、または下の入力欄から試してください。")

    render_messages()

    prompt = st.chat_input("問い合わせ内容を入力してください")
    active_prompt = st.session_state.pending_prompt or prompt
    st.session_state.pending_prompt = None
    if active_prompt is None:
        return

    run_triage(active_prompt)


if __name__ == "__main__":
    main()
