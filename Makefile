.PHONY: sync
sync:
	@uv sync

.PHONY: build.knowledge
build.knowledge:
	@uv run python scripts/build_knowledge_documents.py

.PHONY: run.ui
run.ui:
	@uv run chainlit run chainlit_app.py -w

.PHONY: run.ui.streamlit
run.ui.streamlit:
	@uv run streamlit run streamlit_app.py
