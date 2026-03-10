import operator
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
    decomposed_inquiry: DecomposedInquiry
    plan: list[str]
    current_step: int
    subtask_results: Annotated[Sequence[Subtask], operator.add]
    task_evaluation: TaskEvaluation
    hearing_plan: HearingPlan
    triage_result: TriageResult


class AgentSubGraphState(TypedDict):
    inquiry: str
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

    def _format_decomposed_inquiry(self, decomposed_inquiry: DecomposedInquiry) -> str:
        return "\n".join(
            [
                f"normalized_inquiry: {decomposed_inquiry.normalized_inquiry}",
                f"sub_inquiries: {decomposed_inquiry.sub_inquiries}",
                f"detected_intents: {decomposed_inquiry.detected_intents}",
                f"assumptions: {decomposed_inquiry.assumptions}",
            ]
        )

    def _format_task_evaluation(self, task_evaluation: TaskEvaluation | None) -> str:
        if task_evaluation is None:
            return "task_evaluation: not available"
        return "\n".join(
            [
                f"is_sufficient: {task_evaluation.is_sufficient}",
                f"issues: {task_evaluation.issues}",
                f"recommended_next_action: {task_evaluation.recommended_next_action}",
                f"confidence: {task_evaluation.confidence}",
            ]
        )

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
                "content": self.prompts.query_decomposition_user_prompt.format(inquiry=state["inquiry"]),
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
        return {"plan": plan.subtasks}

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
        return {"triage_result": triage_result}

    def create_task_evaluation(self, state: AgentState) -> dict:
        logger.info("Creating task evaluation")
        subtask_results = [(result.task_name, result.subtask_answer) for result in state["subtask_results"]]
        messages = [
            {"role": "system", "content": self.prompts.task_evaluation_system_prompt},
            {
                "role": "user",
                "content": self.prompts.task_evaluation_user_prompt.format(
                    inquiry=state["inquiry"],
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
        return {"task_evaluation": task_evaluation}

    def create_hearing_plan(self, state: AgentState) -> dict:
        logger.info("Creating hearing plan")
        if state["task_evaluation"].is_sufficient:
            return {
                "hearing_plan": HearingPlan(
                    should_ask_follow_up=False,
                    questions=[],
                    required_information=[],
                    reason="Current evidence is sufficient for triage, so no follow-up hearing is required.",
                )
            }

        subtask_results = [(result.task_name, result.subtask_answer) for result in state["subtask_results"]]
        messages = [
            {"role": "system", "content": self.prompts.hearing_system_prompt},
            {
                "role": "user",
                "content": self.prompts.hearing_user_prompt.format(
                    inquiry=state["inquiry"],
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
        return {"hearing_plan": hearing_plan}

    def _execute_subgraph(self, state: AgentState) -> dict:
        subgraph = self._create_subgraph()
        result = subgraph.invoke(
            {
                "inquiry": state["inquiry"],
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
        workflow.add_node("decompose_inquiry", self.decompose_inquiry)
        workflow.add_node("create_plan", self.create_plan)
        workflow.add_node("execute_subtasks", self._execute_subgraph)
        workflow.add_node("create_task_evaluation", self.create_task_evaluation)
        workflow.add_node("create_hearing_plan", self.create_hearing_plan)
        workflow.add_node("create_triage_result", self.create_triage_result)

        workflow.add_edge(START, "decompose_inquiry")
        workflow.add_edge("decompose_inquiry", "create_plan")
        workflow.add_conditional_edges("create_plan", self._should_continue_exec_subtasks)
        workflow.add_edge("execute_subtasks", "create_task_evaluation")
        workflow.add_edge("create_task_evaluation", "create_hearing_plan")
        workflow.add_edge("create_hearing_plan", "create_triage_result")
        workflow.set_finish_point("create_triage_result")
        return workflow.compile()

    def run_agent(self, inquiry: str) -> AgentResult:
        app = self.create_graph()
        result = app.invoke(
            {
                "inquiry": inquiry,
                "current_step": 0,
            }
        )
        return AgentResult(
            inquiry=inquiry,
            decomposed_inquiry=result["decomposed_inquiry"],
            plan=Plan(subtasks=result["plan"]),
            subtasks=result["subtask_results"],
            task_evaluation=result["task_evaluation"],
            hearing_plan=result["hearing_plan"],
            triage_result=result["triage_result"],
        )
