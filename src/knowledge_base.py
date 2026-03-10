import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from src.models import SearchOutput

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9一-龥ぁ-んァ-ヶー]{2,}")


class KnowledgeDocument(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)


class FaqItem(BaseModel):
    question: str
    answer: str
    tags: list[str] = Field(default_factory=list)


class LocalKnowledgeBase:
    def __init__(
        self,
        documents: list[KnowledgeDocument],
        faq_items: list[FaqItem],
    ) -> None:
        self.documents = documents
        self.faq_items = faq_items

    @classmethod
    def from_paths(
        cls,
        documents_path: Path,
        faq_path: Path,
    ) -> "LocalKnowledgeBase":
        documents_data = json.loads(documents_path.read_text(encoding="utf-8"))
        faq_data = json.loads(faq_path.read_text(encoding="utf-8"))

        documents = [KnowledgeDocument.model_validate(item) for item in documents_data]
        faq_items = [FaqItem.model_validate(item) for item in faq_data]
        return cls(documents=documents, faq_items=faq_items)

    def search_documents(self, keywords: str, limit: int = 3) -> list[SearchOutput]:
        query_tokens = self._tokenize(keywords)
        ranked = sorted(
            self.documents,
            key=lambda item: self._score(
                query_tokens=query_tokens,
                primary_text=f"{item.title} {item.content}",
                tags=item.tags,
            ),
            reverse=True,
        )
        return [
            SearchOutput(file_name=item.title, content=item.content)
            for item in ranked[:limit]
            if self._score(
                query_tokens=query_tokens,
                primary_text=f"{item.title} {item.content}",
                tags=item.tags,
            )
            > 0
        ]

    def search_faq(self, query: str, limit: int = 3) -> list[SearchOutput]:
        query_tokens = self._tokenize(query)
        ranked = sorted(
            self.faq_items,
            key=lambda item: self._score(
                query_tokens=query_tokens,
                primary_text=f"{item.question} {item.answer}",
                tags=item.tags,
            ),
            reverse=True,
        )
        return [
            SearchOutput(
                file_name=item.question,
                content=item.answer,
            )
            for item in ranked[:limit]
            if self._score(
                query_tokens=query_tokens,
                primary_text=f"{item.question} {item.answer}",
                tags=item.tags,
            )
            > 0
        ]

    def _tokenize(self, text: str) -> set[str]:
        return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)}

    def _score(
        self,
        query_tokens: set[str],
        primary_text: str,
        tags: list[str],
    ) -> int:
        if not query_tokens:
            return 0

        text_tokens = self._tokenize(primary_text)
        tag_tokens = {tag.lower() for tag in tags}
        overlap = len(query_tokens & text_tokens)
        tag_overlap = len(query_tokens & tag_tokens) * 2
        raw_match_bonus = 2 if any(token in primary_text.lower() for token in query_tokens) else 0
        return overlap + tag_overlap + raw_match_bonus
