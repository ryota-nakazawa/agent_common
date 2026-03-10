import asyncio
from contextlib import suppress
from pathlib import Path

import chainlit as cl

from src.agent import SupportAgent
from src.configs import Settings
from src.inquiry_store import append_inquiry_record
from src.knowledge_base import LocalKnowledgeBase
from src.models import AgentResult, ConversationState, HearingPlan, TaskEvaluation, TriageResult
from src.tools import build_tools

PROGRESS_MESSAGES = [
    "[1/4] 問い合わせを整理しています",
    "[2/4] ナレッジとFAQを確認しています",
    "[3/4] 回答方針をまとめています",
    "[4/4] 返信内容を整えています",
]


def get_agent() -> SupportAgent:
    settings = Settings()
    knowledge_base = LocalKnowledgeBase.from_paths(
        documents_path=cl.user_session.get("documents_path"),
        faq_path=cl.user_session.get("faq_path"),
    )
    tools = build_tools(knowledge_base=knowledge_base)
    return SupportAgent(settings=settings, tools=tools)


def ensure_session_state() -> None:
    if cl.user_session.get("documents_path") is None:
        app_dir = Path(__file__).resolve().parent
        cl.user_session.set("documents_path", app_dir / "data" / "knowledge_documents.json")
        cl.user_session.set("faq_path", app_dir / "data" / "faq_items.json")

    if cl.user_session.get("conversation_state") is None:
        cl.user_session.set("conversation_state", ConversationState())


def format_triage_result(result: TriageResult) -> str:
    path_label = "Human Handoff" if result.handoff_needed else ("Follow Up" if result.needs_follow_up else "Knowledge Answer")
    summary_lines = [
        "## ご案内",
        "",
        f"`{result.category}` | `{result.priority}` | `{path_label}`",
        "",
        result.draft_reply,
    ]

    if result.handoff_needed:
        summary_lines.extend(
            [
                "",
                "### 引き継ぎ",
                f"- 引き継ぎ先: {result.handoff_target or result.assigned_team}",
                f"- 理由: {result.handoff_reason}",
            ]
        )
    elif result.needs_follow_up:
        summary_lines.extend(
            [
                "",
                "### 確認したいこと",
                f"- {result.next_user_action}",
            ]
        )
    elif result.next_user_action:
        summary_lines.extend(
            [
                "",
                "### 次の一手",
                f"- {result.next_user_action}",
            ]
        )

    return "\n".join(summary_lines)


def format_details(
    result: TriageResult,
    task_evaluation: TaskEvaluation | None,
    hearing_plan: HearingPlan | None,
    conversation_state: ConversationState | None,
) -> str:
    resolved_parts = "\n".join(f"- {item}" for item in result.resolved_parts) or "- なし"
    immediate_guidance = "\n".join(f"- {item}" for item in result.immediate_guidance) or "- なし"
    unresolved_parts = "\n".join(f"- {item}" for item in result.unresolved_parts) or "- なし"
    blocking_items = "\n".join(f"- {item}" for item in result.blocking_items) or "- なし"

    lines = [
        "### 詳細",
        f"- カテゴリ: {result.category}",
        f"- 優先度: {result.priority}",
        f"- 担当先: {result.assigned_team}",
        f"- 信頼度: {result.confidence:.2f}",
        "",
        "#### 今すぐ案内できる内容",
        immediate_guidance,
        "",
        "#### 解消できた部分",
        resolved_parts,
        "",
        "#### 未解決の部分",
        unresolved_parts,
        "",
        "#### 進行に必須の確認事項",
        blocking_items,
    ]

    if task_evaluation is not None:
        lines.extend(
            [
                "",
                "#### 評価",
                f"- 解決モード: {task_evaluation.resolution_mode}",
                f"- 共通案内の可否: {'可能' if task_evaluation.can_provide_general_guidance else '不可'}",
                f"- 人への引き継ぎ: {'必要' if task_evaluation.handoff_recommended else '不要'}",
            ]
        )

    if hearing_plan is not None and hearing_plan.should_ask_follow_up:
        questions = "\n".join(f"- {item.question}" for item in hearing_plan.questions) or "- なし"
        lines.extend(
            [
                "",
                "#### 確認候補",
                questions,
            ]
        )

    if conversation_state is not None and conversation_state.conversation_summary:
        lines.extend(
            [
                "",
                "#### 会話要約",
                conversation_state.conversation_summary,
            ]
        )

    return "\n".join(lines)


async def update_progress(message: cl.Message, done_event: asyncio.Event) -> None:
    index = 0
    while not done_event.is_set():
        dots = "." * ((index % 3) + 1)
        message.content = f"{PROGRESS_MESSAGES[index % len(PROGRESS_MESSAGES)]}{dots}"
        await message.update()
        index += 1
        try:
            await asyncio.wait_for(done_event.wait(), timeout=0.9)
        except asyncio.TimeoutError:
            pass


@cl.on_message
async def on_message(message: cl.Message) -> None:
    ensure_session_state()
    processing_message = cl.Message(content="問い合わせを整理しています...")
    await processing_message.send()
    done_event = asyncio.Event()
    progress_task = asyncio.create_task(update_progress(processing_message, done_event))

    try:
        agent = get_agent()
        conversation_state: ConversationState = cl.user_session.get("conversation_state")
        result: AgentResult = await cl.make_async(agent.run_agent)(message.content, conversation_state)
        triage_result = result.triage_result
        cl.user_session.set("conversation_state", result.conversation_state)
        append_inquiry_record(result)
    except Exception as exc:
        done_event.set()
        with suppress(asyncio.CancelledError):
            await progress_task
        processing_message.content = (
            "前さばき結果の生成に失敗しました。`.env` と `data/*.json` の内容を確認してください。\n\n"
            f"詳細: `{exc}`"
        )
        await processing_message.update()
        return

    done_event.set()
    with suppress(asyncio.CancelledError):
        await progress_task

    processing_message.content = format_triage_result(triage_result)
    await processing_message.update()
