from pathlib import Path

import chainlit as cl

from src.agent import SupportAgent
from src.configs import Settings
from src.knowledge_base import LocalKnowledgeBase
from src.models import AgentResult, HearingPlan, TaskEvaluation, TriageResult
from src.tools import build_tools


def get_agent() -> SupportAgent:
    settings = Settings()
    knowledge_base = LocalKnowledgeBase.from_paths(
        documents_path=cl.user_session.get("documents_path"),
        faq_path=cl.user_session.get("faq_path"),
    )
    tools = build_tools(knowledge_base=knowledge_base)
    return SupportAgent(settings=settings, tools=tools)


def format_triage_result(result: TriageResult) -> str:
    missing_information = "\n".join(f"- {item}" for item in result.missing_information) or "- なし"
    follow_up = "はい" if result.needs_follow_up else "いいえ"

    return f"""## 前さばき結果

**カテゴリ**: {result.category}  
**優先度**: {result.priority}  
**担当先**: {result.assigned_team}  
**追加確認要否**: {follow_up}  
**信頼度**: {result.confidence:.2f}

### 不足情報
{missing_information}

### 返信案
{result.draft_reply}

### 判断根拠
{result.reasoning_summary}
"""


def format_task_evaluation(task_evaluation: TaskEvaluation | None) -> str:
    if task_evaluation is None:
        return ""

    issues = "\n".join(f"- {item}" for item in task_evaluation.issues) or "- なし"
    sufficiency = "十分" if task_evaluation.is_sufficient else "不十分"

    return f"""### 評価結果

**十分性**: {sufficiency}  
**信頼度**: {task_evaluation.confidence:.2f}  
**次の推奨アクション**: {task_evaluation.recommended_next_action}

#### 懸念点
{issues}
"""


def format_hearing_plan(hearing_plan: HearingPlan | None) -> str:
    if hearing_plan is None:
        return ""

    follow_up = "必要" if hearing_plan.should_ask_follow_up else "不要"
    questions = (
        "\n".join(f"- {item.question} ({item.purpose})" for item in hearing_plan.questions)
        if hearing_plan.questions
        else "- なし"
    )
    required_information = (
        "\n".join(f"- {item}" for item in hearing_plan.required_information)
        if hearing_plan.required_information
        else "- なし"
    )

    return f"""### Hearing Plan

**追加確認**: {follow_up}

#### 必要情報
{required_information}

#### 聞き返し候補
{questions}

#### 理由
{hearing_plan.reason}
"""


@cl.on_chat_start
async def on_chat_start() -> None:
    settings = Settings()
    app_dir = Path(__file__).resolve().parent
    documents_path = app_dir / "data" / "knowledge_documents.json"
    faq_path = app_dir / "data" / "faq_items.json"

    cl.user_session.set("documents_path", documents_path)
    cl.user_session.set("faq_path", faq_path)

    await cl.Message(
        content=(
            f"{settings.domain_name} 向けの前さばき UI です。\n\n"
            "問い合わせを送ると、カテゴリ、優先度、担当先、不足情報、返信案を返します。"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    processing_message = cl.Message(content="前さばき結果を生成中です...")
    await processing_message.send()

    try:
        agent = get_agent()
        result: AgentResult = await cl.make_async(agent.run_agent)(message.content)
        triage_result = result.triage_result
    except Exception as exc:
        processing_message.content = (
            "前さばき結果の生成に失敗しました。`.env` と `data/*.json` の内容を確認してください。\n\n"
            f"詳細: `{exc}`"
        )
        await processing_message.update()
        return

    processing_message.content = (
        f"{format_triage_result(triage_result)}\n\n"
        f"{format_task_evaluation(result.task_evaluation)}\n\n"
        f"{format_hearing_plan(result.hearing_plan)}"
    )
    await processing_message.update()
    await cl.Message(content=f"```json\n{triage_result.model_dump_json(indent=2)}\n```").send()
