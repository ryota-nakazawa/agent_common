from collections import Counter

import pandas as pd
import streamlit as st

from src.inquiry_store import load_inquiry_records

APP_TITLE = "Inquiry Operations Dashboard"
ROUTING_LABELS = {
    "fast_path": "Fast Path",
    "deep_path": "Deep Path",
    "human_handoff": "Human Handoff",
}
ROUTING_COLORS = {
    "Fast Path": "#0f766e",
    "Deep Path": "#b45309",
    "Human Handoff": "#b91c1c",
}
PRIORITY_COLORS = {
    "low": "#0f766e",
    "medium": "#b45309",
    "high": "#b91c1c",
}


@st.cache_data(show_spinner=False)
def load_dashboard_data() -> pd.DataFrame:
    records = load_inquiry_records()
    base_columns = [
        "id",
        "created_at",
        "routing_bucket",
        "routing_label",
        "processing_path",
        "category",
        "priority",
        "assigned_team",
        "inquiry",
        "draft_reply",
        "next_user_action",
        "needs_follow_up",
        "handoff_needed",
        "handoff_target",
        "resolution_mode",
        "confidence",
        "resolved_parts",
        "unresolved_parts",
        "blocking_items",
        "immediate_guidance",
    ]
    if not records:
        return pd.DataFrame(columns=base_columns)

    frame = pd.DataFrame(records)
    for column in base_columns:
        if column not in frame.columns:
            if column in {"resolved_parts", "unresolved_parts", "blocking_items", "immediate_guidance"}:
                frame[column] = [[] for _ in range(len(frame))]
            else:
                frame[column] = ""

    frame["routing_label"] = frame["routing_bucket"].map(ROUTING_LABELS).fillna(frame["routing_bucket"])
    frame["created_at"] = pd.to_datetime(frame["created_at"], errors="coerce")
    frame["display_time"] = frame["created_at"].dt.strftime("%Y-%m-%d %H:%M").fillna("-")
    return frame.sort_values("created_at", ascending=False, na_position="last")


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .dashboard-shell {
            background:
                radial-gradient(circle at top right, rgba(15,118,110,0.12), transparent 28%),
                radial-gradient(circle at top left, rgba(180,83,9,0.12), transparent 22%),
                linear-gradient(180deg, #fbfaf7 0%, #f3f0e8 100%);
            padding: 1.4rem 1.4rem 1rem 1.4rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 24px;
            margin-bottom: 1rem;
        }
        .dashboard-eyebrow {
            font-size: 0.78rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: #0f766e;
            font-weight: 700;
        }
        .dashboard-title {
            font-size: 2rem;
            line-height: 1.1;
            margin: 0.35rem 0 0.4rem 0;
            color: #111827;
            font-weight: 800;
        }
        .dashboard-subtitle {
            color: #4b5563;
            max-width: 60rem;
            font-size: 0.98rem;
        }
        .metric-card {
            background: rgba(255,255,255,0.78);
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 18px;
            padding: 1rem 1rem 0.9rem 1rem;
            min-height: 120px;
            box-shadow: 0 18px 40px rgba(15, 23, 42, 0.05);
        }
        .metric-label {
            color: #6b7280;
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 700;
        }
        .metric-value {
            color: #111827;
            font-size: 2rem;
            line-height: 1;
            margin-top: 0.55rem;
            font-weight: 800;
        }
        .metric-caption {
            color: #4b5563;
            font-size: 0.9rem;
            margin-top: 0.5rem;
        }
        .panel-card {
            background: rgba(255,255,255,0.82);
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 20px;
            padding: 1rem 1rem 0.7rem 1rem;
            box-shadow: 0 12px 32px rgba(15, 23, 42, 0.05);
        }
        .panel-title {
            color: #111827;
            font-weight: 700;
            margin-bottom: 0.65rem;
        }
        .route-pill, .priority-pill {
            display: inline-block;
            padding: 0.22rem 0.6rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 700;
            color: white;
            white-space: nowrap;
        }
        .inquiry-table table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            table-layout: fixed;
            background: rgba(255,255,255,0.78);
            border-radius: 18px;
            overflow: hidden;
        }
        .inquiry-table th, .inquiry-table td {
            border-bottom: 1px solid rgba(49, 51, 63, 0.10);
            padding: 12px 14px;
            text-align: left;
            vertical-align: top;
            white-space: normal;
            word-break: break-word;
            font-size: 0.92rem;
        }
        .inquiry-table th {
            background: rgba(17, 24, 39, 0.04);
            color: #111827;
            font-weight: 700;
        }
        .inquiry-table tr:last-child td {
            border-bottom: none;
        }
        .detail-block {
            background: rgba(255,255,255,0.82);
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 20px;
            padding: 1rem;
        }
        .detail-kv {
            color: #4b5563;
            margin-bottom: 0.2rem;
            font-size: 0.92rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.markdown(
        """
        <div class="dashboard-shell">
            <div class="dashboard-eyebrow">Operations View</div>
            <div class="dashboard-title">お問い合わせの流れを一目で確認</div>
            <div class="dashboard-subtitle">
                Fast Path / Deep Path / Human Handoff ごとの流入、カテゴリ傾向、ユーザーへの返信内容を
                JSON ログからまとめて確認できます。
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metric_card(label: str, value: str, caption: str, container) -> None:
    container.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-caption">{caption}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metrics(frame: pd.DataFrame) -> None:
    total = len(frame)
    routing_counts = Counter(frame["routing_bucket"]) if total else Counter()
    fast_count = routing_counts.get("fast_path", 0)
    deep_count = routing_counts.get("deep_path", 0)
    handoff_count = routing_counts.get("human_handoff", 0)
    fast_ratio = f"{(fast_count / total * 100):.0f}%" if total else "0%"
    handoff_ratio = f"{(handoff_count / total * 100):.0f}%" if total else "0%"

    col1, col2, col3, col4 = st.columns(4)
    render_metric_card("Total Inquiries", str(total), "蓄積済みの問い合わせ総数", col1)
    render_metric_card("Fast Path", str(fast_count), f"全体の {fast_ratio}", col2)
    render_metric_card("Deep Path", str(deep_count), "追加整理が必要だった件数", col3)
    render_metric_card("Human Handoff", str(handoff_count), f"全体の {handoff_ratio}", col4)


def render_charts(frame: pd.DataFrame) -> None:
    st.subheader("Overview")
    col1, col2 = st.columns([1.1, 1])

    with col1:
        st.markdown('<div class="panel-card"><div class="panel-title">Routing Breakdown</div></div>', unsafe_allow_html=True)
        routing_counts = (
            frame["routing_label"].value_counts().rename_axis("route").reset_index(name="count")
            if not frame.empty
            else pd.DataFrame({"route": [], "count": []})
        )
        if routing_counts.empty:
            st.info("まだデータがありません。")
        else:
            st.bar_chart(routing_counts.set_index("route"), color="#0f766e", use_container_width=True)

        st.markdown('<div class="panel-card"><div class="panel-title">Daily Volume</div></div>', unsafe_allow_html=True)
        daily_counts = (
            frame.assign(day=frame["created_at"].dt.date.astype(str))
            .groupby("day")
            .size()
            .reset_index(name="count")
            if not frame.empty
            else pd.DataFrame({"day": [], "count": []})
        )
        if daily_counts.empty:
            st.info("まだデータがありません。")
        else:
            st.area_chart(daily_counts.set_index("day"), color="#b45309", use_container_width=True)

    with col2:
        st.markdown('<div class="panel-card"><div class="panel-title">Top Categories</div></div>', unsafe_allow_html=True)
        category_counts = (
            frame["category"].value_counts().head(8).rename_axis("category").reset_index(name="count")
            if not frame.empty
            else pd.DataFrame({"category": [], "count": []})
        )
        if category_counts.empty:
            st.info("まだデータがありません。")
        else:
            st.bar_chart(category_counts.set_index("category"), color="#1d4ed8", use_container_width=True)

        st.markdown('<div class="panel-card"><div class="panel-title">Priority Mix</div></div>', unsafe_allow_html=True)
        priority_counts = (
            frame["priority"].value_counts().rename_axis("priority").reset_index(name="count")
            if not frame.empty
            else pd.DataFrame({"priority": [], "count": []})
        )
        if priority_counts.empty:
            st.info("まだデータがありません。")
        else:
            st.bar_chart(priority_counts.set_index("priority"), color="#7c3aed", use_container_width=True)


def render_filters(frame: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    st.subheader("Filters")
    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1.2, 1.2, 1, 1.4])
    with filter_col1:
        routing_options = ["All", *sorted(frame["routing_label"].dropna().unique().tolist())]
        selected_routing = st.selectbox("Routing", routing_options, index=0, key=f"{key_prefix}_routing")
    with filter_col2:
        category_options = ["All", *sorted(frame["category"].dropna().unique().tolist())]
        selected_category = st.selectbox("Category", category_options, index=0, key=f"{key_prefix}_category")
    with filter_col3:
        priority_options = ["All", *sorted(frame["priority"].dropna().unique().tolist())]
        selected_priority = st.selectbox("Priority", priority_options, index=0, key=f"{key_prefix}_priority")
    with filter_col4:
        keyword = st.text_input("Keyword", placeholder="問い合わせ文や返信を検索", key=f"{key_prefix}_keyword")

    filtered = frame.copy()
    if selected_routing != "All":
        filtered = filtered[filtered["routing_label"] == selected_routing]
    if selected_category != "All":
        filtered = filtered[filtered["category"] == selected_category]
    if selected_priority != "All":
        filtered = filtered[filtered["priority"] == selected_priority]
    if keyword:
        needle = keyword.strip()
        filtered = filtered[
            filtered["inquiry"].fillna("").str.contains(needle, case=False)
            | filtered["draft_reply"].fillna("").str.contains(needle, case=False)
        ]
    return filtered


def _badge(text: str, color: str, css_class: str) -> str:
    return f'<span class="{css_class}" style="background:{color};">{text}</span>'


def render_table(frame: pd.DataFrame) -> None:
    st.subheader("Inquiry List")
    if frame.empty:
        st.info("表示できる問い合わせログがありません。")
        return

    display = frame.copy()
    display["Route"] = display["routing_label"].apply(
        lambda value: _badge(value, ROUTING_COLORS.get(value, "#475569"), "route-pill")
    )
    display["Priority"] = display["priority"].apply(
        lambda value: _badge(str(value), PRIORITY_COLORS.get(str(value), "#475569"), "priority-pill")
    )
    display["Follow Up"] = display["needs_follow_up"].map({True: "Yes", False: "No"})
    display["Handoff"] = display["handoff_needed"].map({True: "Yes", False: "No"})

    table = display[
        [
            "display_time",
            "Route",
            "category",
            "Priority",
            "assigned_team",
            "Follow Up",
            "Handoff",
            "inquiry",
            "draft_reply",
            "next_user_action",
        ]
    ].rename(
        columns={
            "display_time": "Time",
            "category": "Category",
            "assigned_team": "Assigned Team",
            "inquiry": "Inquiry",
            "draft_reply": "Reply",
            "next_user_action": "Next Action",
        }
    )

    styled_html = table.to_html(index=False, escape=False)
    st.markdown(f'<div class="inquiry-table">{styled_html}</div>', unsafe_allow_html=True)


def render_detail_panel(frame: pd.DataFrame) -> None:
    st.subheader("Inquiry Detail")
    if frame.empty:
        st.info("表示できる問い合わせログがありません。")
        return

    options = [
        f"{row.display_time} | {row.category} | {str(row.inquiry)[:42]}"
        for row in frame.itertuples()
    ]
    selected_label = st.selectbox("表示する問い合わせ", options, index=0)
    selected_row = frame.iloc[options.index(selected_label)]

    route_badge = _badge(
        selected_row["routing_label"],
        ROUTING_COLORS.get(selected_row["routing_label"], "#475569"),
        "route-pill",
    )
    priority_badge = _badge(
        str(selected_row["priority"]),
        PRIORITY_COLORS.get(str(selected_row["priority"]), "#475569"),
        "priority-pill",
    )

    st.markdown('<div class="detail-block">', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="detail-kv">Time: {selected_row["display_time"]}</div>
        <div class="detail-kv">Route: {route_badge} &nbsp; Priority: {priority_badge}</div>
        <div class="detail-kv">Category: {selected_row["category"]}</div>
        <div class="detail-kv">Assigned Team: {selected_row["assigned_team"]}</div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("**Inquiry**")
    st.write(selected_row["inquiry"])
    st.markdown("**Reply**")
    st.info(selected_row["draft_reply"])
    st.markdown("**Next Action**")
    st.write(selected_row["next_user_action"] or "-")

    detail_col1, detail_col2 = st.columns(2)
    with detail_col1:
        st.markdown("**Immediate Guidance**")
        guidance = selected_row["immediate_guidance"] if isinstance(selected_row["immediate_guidance"], list) else []
        if guidance:
            for item in guidance:
                st.write(f"- {item}")
        else:
            st.write("- なし")

        st.markdown("**Resolved Parts**")
        resolved_parts = selected_row["resolved_parts"] if isinstance(selected_row["resolved_parts"], list) else []
        if resolved_parts:
            for item in resolved_parts:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    with detail_col2:
        st.markdown("**Unresolved Parts**")
        unresolved_parts = selected_row["unresolved_parts"] if isinstance(selected_row["unresolved_parts"], list) else []
        if unresolved_parts:
            for item in unresolved_parts:
                st.write(f"- {item}")
        else:
            st.write("- なし")

        st.markdown("**Blocking Items**")
        blocking_items = selected_row["blocking_items"] if isinstance(selected_row["blocking_items"], list) else []
        if blocking_items:
            for item in blocking_items:
                st.write(f"- {item}")
        else:
            st.write("- なし")

    if selected_row.get("handoff_needed"):
        st.warning(f"Human Handoff: {selected_row.get('handoff_target') or selected_row.get('assigned_team')}")

    st.markdown("</div>", unsafe_allow_html=True)


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon=":bar_chart:", layout="wide")
    inject_css()
    render_header()

    top_col1, top_col2 = st.columns([1, 5])
    with top_col1:
        if st.button("Refresh", use_container_width=True):
            load_dashboard_data.clear()
    with top_col2:
        st.link_button("問い合わせ画面を開く", "http://localhost:8000", use_container_width=True)

    frame = load_dashboard_data()
    render_metrics(frame)

    dashboard_tab, list_tab, detail_tab = st.tabs(["Dashboard", "Inquiries", "Detail"])
    with dashboard_tab:
        render_charts(frame)
    with list_tab:
        filtered = render_filters(frame, key_prefix="list")
        render_table(filtered)
    with detail_tab:
        filtered = render_filters(frame, key_prefix="detail")
        render_detail_panel(filtered)


if __name__ == "__main__":
    main()
