from langchain.tools import tool
from pydantic import BaseModel, Field

from src.knowledge_base import LocalKnowledgeBase
from src.models import SearchOutput

MAX_SEARCH_RESULTS = 3


class SearchKeywordInput(BaseModel):
    keywords: str = Field(description="Keyword search query")


class SearchQueryInput(BaseModel):
    query: str = Field(description="Question-like search query")


def build_tools(knowledge_base: LocalKnowledgeBase) -> list:
    @tool(args_schema=SearchKeywordInput)
    def search_knowledge_documents(keywords: str) -> list[SearchOutput]:
        """
        Search structured product or policy documents by keyword.
        Use this when the user asks for rules, specifications, settings, or step-by-step procedures.
        """

        return knowledge_base.search_documents(keywords, limit=MAX_SEARCH_RESULTS)

    @tool(args_schema=SearchQueryInput)
    def search_faq_answers(query: str) -> list[SearchOutput]:
        """
        Search FAQ-style past answers.
        Use this when the user asks a common operational question or a how-to question.
        """

        return knowledge_base.search_faq(query, limit=MAX_SEARCH_RESULTS)

    return [search_knowledge_documents, search_faq_answers]
