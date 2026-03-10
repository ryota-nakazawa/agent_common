from pathlib import Path

import streamlit as st

from src.agent import SupportAgent
from src.configs import Settings
from src.knowledge_base import LocalKnowledgeBase
from src.models import AgentResult, HearingPlan, TaskEvaluation, TriageResult
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
        st.rerun()


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
            else:
                st.markdown(message["content"])


def render_triage_result(result: TriageResult) -> None:
    follow_up = "Yes" if result.needs_follow_up else "No"
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Category", result.category)
    col2.metric("Priority", result.priority)
    col3.metric("Assigned Team", result.assigned_team)
    col4.metric("Confidence", f"{result.confidence:.2f}")

    st.markdown("**Follow-up Required**")
    st.write(follow_up)

    st.markdown("**Draft Reply**")
    st.info(result.draft_reply)

    st.markdown("**Missing Information**")
    if result.missing_information:
        for item in result.missing_information:
            st.write(f"- {item}")
    else:
        st.write("- なし")

    with st.expander("Reasoning Summary", expanded=True):
        st.write(result.reasoning_summary)

    with st.expander("Raw JSON"):
        st.json(result.model_dump())


def render_task_evaluation(task_evaluation: TaskEvaluation | None) -> None:
    if task_evaluation is None:
        return

    sufficiency = "Sufficient" if task_evaluation.is_sufficient else "Insufficient"
    st.markdown("**Task Evaluation**")
    col1, col2 = st.columns(2)
    col1.metric("Evidence Sufficiency", sufficiency)
    col2.metric("Evaluation Confidence", f"{task_evaluation.confidence:.2f}")

    st.markdown("**Recommended Next Action**")
    st.write(task_evaluation.recommended_next_action)

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


def run_triage(prompt: str) -> None:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        agent = get_agent()
        with st.chat_message("assistant"):
            with st.spinner("前さばき結果を生成中です..."):
                result: AgentResult = agent.run_agent(prompt)
            render_triage_result(result.triage_result)
            render_task_evaluation(result.task_evaluation)
            render_hearing_plan(result.hearing_plan)
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
        }
    )


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon=":speech_balloon:", layout="wide")
    st.title(APP_TITLE)

    try:
        settings = Settings()
    except Exception:
        settings = None

    if settings is not None:
        st.caption(f"{settings.domain_name} 向けの汎用前さばきテンプレート")
    else:
        st.caption("汎用前さばきテンプレート")

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

    st.markdown(
        """
        この UI は最終回答ではなく、カテゴリ、優先度、担当先、不足情報、返信案を返します。
        まずは右のサンプル問い合わせか、下の入力欄から試してください。
        """
    )

    render_messages()

    prompt = st.chat_input("問い合わせ内容を入力してください")
    active_prompt = st.session_state.pending_prompt or prompt
    st.session_state.pending_prompt = None
    if active_prompt is None:
        return

    run_triage(active_prompt)


if __name__ == "__main__":
    main()
