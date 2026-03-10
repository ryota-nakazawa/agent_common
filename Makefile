.PHONY: sync
sync:
	@uv sync

.PHONY: build.knowledge
build.knowledge:
	@uv run python scripts/build_knowledge_documents.py

.PHONY: run.ui
run.ui:
	@trap 'kill 0' EXIT INT TERM; \
	uv run streamlit run admin_app.py --server.port 8501 >/tmp/agent_common_admin.log 2>&1 & \
	uv run chainlit run chainlit_app.py -w

.PHONY: run.ui.streamlit
run.ui.streamlit:
	@uv run streamlit run streamlit_app.py

.PHONY: run.admin
run.admin:
	@uv run streamlit run admin_app.py --server.port 8501
