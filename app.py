"""
QA Dashboard — run with:  streamlit run scripts/qa_dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from freescout_bot.qa.storage import SQLiteStorage

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Support QA Dashboard",
    page_icon="📊",
    layout="wide",
)

DB_PATH = Path(__file__).parent.parent / "qa_results.db"

# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_data() -> pd.DataFrame:
    storage = SQLiteStorage(DB_PATH)
    return storage.load_dataframe()


if not DB_PATH.exists():
    st.error("No data yet. Run the evaluation script first:")
    st.code("python scripts/evaluate_quality.py --days 30")
    st.stop()

df = load_data()

if df.empty:
    st.warning("Database exists but has no records yet.")
    st.stop()

# ── Sidebar filters ───────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Filters")

    agents    = ["All"] + sorted(df["agent_name"].dropna().unique().tolist())
    mailboxes = ["All"] + sorted(df["mailbox_name"].dropna().unique().tolist())

    sel_agent   = st.selectbox("Agent",   agents)
    sel_mailbox = st.selectbox("Mailbox", mailboxes)

    min_date   = df["run_date"].min().date()
    max_date   = df["run_date"].max().date()
    date_range = st.date_input("Date range", value=(min_date, max_date))

    min_score = st.slider("Min score", 0, 10, 0)

    st.divider()
    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()

# ── Apply filters ─────────────────────────────────────────────────────────────

filtered = df.copy()

if sel_agent != "All":
    filtered = filtered[filtered["agent_name"] == sel_agent]
if sel_mailbox != "All":
    filtered = filtered[filtered["mailbox_name"] == sel_mailbox]
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    filtered = filtered[
        (filtered["run_date"] >= pd.Timestamp(date_range[0])) &
        (filtered["run_date"] <= pd.Timestamp(date_range[1]))
    ]
filtered = filtered[filtered["total_score"] >= min_score]

# ── Header ────────────────────────────────────────────────────────────────────

st.title("📊 Support QA Dashboard")
st.caption(
    f"Last run: {df['run_date'].max().strftime('%Y-%m-%d')}  ·  "
    f"{len(filtered):,} ticket(s) shown  ·  "
    f"{filtered['agent_name'].nunique()} agent(s)"
)

# ── KPI cards ─────────────────────────────────────────────────────────────────

agent_avgs   = filtered.groupby("agent_name")["total_score"].mean()
avg_score    = filtered["total_score"].mean() if not filtered.empty else 0
top_agent    = agent_avgs.idxmax() if not agent_avgs.empty else "—"
below_target = int((agent_avgs < 6).sum()) if not agent_avgs.empty else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Tickets",       f"{len(filtered):,}")
c2.metric("Team Avg Score",      f"{avg_score:.1f} / 10")
c3.metric("Top Agent",           top_agent)
c4.metric("Below Target (< 6)",  below_target)

st.divider()

# ── Automated Insights ────────────────────────────────────────────────────────

st.subheader("📋 Automated Insights")

# -- Team trend (compare latest week vs all previous weeks)
weekly_avg = (
    filtered.groupby("week")["total_score"]
    .mean()
    .sort_index()
)

trend_label = "—"
trend_delta = None
if len(weekly_avg) >= 2:
    latest_avg   = weekly_avg.iloc[-1]
    previous_avg = weekly_avg.iloc[:-1].mean()
    delta        = round(latest_avg - previous_avg, 2)
    if delta >= 0.3:
        trend_label = "📈 Improving"
    elif delta <= -0.3:
        trend_label = "📉 Declining"
    else:
        trend_label = "➡️ Stable"
    trend_delta = f"{delta:+.2f} vs previous weeks"

# -- Most common weakest area across all filtered tickets
def _weakest_for_row(row: pd.Series) -> str:
    ratios = {
        "Accuracy":     row["accuracy"]     / 3,
        "Clarity":      row["clarity"]      / 3,
        "Tone":         row["tone"]         / 2,
        "Completeness": row["completeness"] / 2,
    }
    return min(ratios, key=ratios.get)

if not filtered.empty:
    filtered["_weakest"] = filtered.apply(_weakest_for_row, axis=1)
    pattern_counts = filtered["_weakest"].value_counts()
    top_issue      = pattern_counts.index[0]
    top_issue_pct  = round(pattern_counts.iloc[0] / len(filtered) * 100, 1)
else:
    top_issue     = "—"
    top_issue_pct = 0

# -- Top performers (avg >= 8, or top 2 if none)
top_performers = agent_avgs[agent_avgs >= 8].index.tolist()
if not top_performers and not agent_avgs.empty:
    top_performers = agent_avgs.nlargest(2).index.tolist()

# -- Agents below target
below_agents = agent_avgs[agent_avgs < 6].index.tolist()

i1, i2, i3, i4 = st.columns(4)

with i1:
    if top_performers:
        st.success(f"🏆 **Top performers**\n\n{', '.join(top_performers)}")
    else:
        st.info("🏆 **Top performers**\n\nNo data yet")

with i2:
    if below_agents:
        st.error(f"⚠️ **Below target**\n\n{', '.join(below_agents)}")
    else:
        st.success("✅ **All agents above target**")

with i3:
    st.warning(f"🔍 **Most common issue**\n\n{top_issue} ({top_issue_pct}% of tickets)")

with i4:
    if trend_delta:
        if "Improving" in trend_label:
            st.success(f"**Overall trend**\n\n{trend_label}\n\n{trend_delta}")
        elif "Declining" in trend_label:
            st.error(f"**Overall trend**\n\n{trend_label}\n\n{trend_delta}")
        else:
            st.info(f"**Overall trend**\n\n{trend_label}\n\n{trend_delta}")
    else:
        st.info("**Overall trend**\n\nNeed 2+ weeks of data")

st.divider()

# ── Agent rankings + score breakdown ─────────────────────────────────────────

summary = (
    filtered.groupby("agent_name")
    .agg(
        Tickets=         ("total_score",  "count"),
        Avg_Score=       ("total_score",  "mean"),
        Avg_Accuracy=    ("accuracy",     "mean"),
        Avg_Clarity=     ("clarity",      "mean"),
        Avg_Tone=        ("tone",         "mean"),
        Avg_Completeness=("completeness", "mean"),
    )
    .round(2)
    .sort_values("Avg_Score", ascending=False)
    .reset_index()
    .rename(columns={"agent_name": "Agent"})
)
summary["Status"] = summary["Avg_Score"].apply(
    lambda s: "✅ OK" if s >= 6 else "⚠️ Below target"
)

st.subheader("Agent Rankings")
st.caption("Scores are averages across all evaluated tickets per agent.")
st.dataframe(
    summary[[
        "Agent", "Tickets", "Avg_Score",
        "Avg_Accuracy", "Avg_Clarity", "Avg_Tone", "Avg_Completeness",
        "Status",
    ]].rename(columns={
        "Avg_Score":        "Score /10",
        "Avg_Accuracy":     "Accuracy /3",
        "Avg_Clarity":      "Clarity /3",
        "Avg_Tone":         "Tone /2",
        "Avg_Completeness": "Completeness /2",
    }),
    hide_index=True,
    use_container_width=True,
    column_config={
        "Agent":          st.column_config.TextColumn("Agent",         width="medium"),
        "Tickets":        st.column_config.NumberColumn("Tickets",     width="small", format="%d"),
        "Score /10":      st.column_config.NumberColumn("Score /10",   width="small"),
        "Accuracy /3":    st.column_config.NumberColumn("Accuracy /3", width="small"),
        "Clarity /3":     st.column_config.NumberColumn("Clarity /3",  width="small"),
        "Tone /2":        st.column_config.NumberColumn("Tone /2",     width="small"),
        "Completeness /2":st.column_config.NumberColumn("Comp /2",     width="small"),
        "Status":         st.column_config.TextColumn("Status",        width="small"),
    },
)

st.divider()

st.subheader("Score Breakdown by Agent")
st.caption("Top 20 agents by score — use the Agent filter in the sidebar to focus on specific agents.")
top20 = summary.head(20)
breakdown = top20[
    ["Agent", "Avg_Accuracy", "Avg_Clarity", "Avg_Tone", "Avg_Completeness"]
].melt(id_vars="Agent", var_name="Dimension", value_name="Score")
breakdown["Dimension"] = breakdown["Dimension"].str.replace("Avg_", "", regex=False)

fig = px.bar(
    breakdown,
    x="Agent", y="Score", color="Dimension",
    barmode="group",
    color_discrete_map={
        "Accuracy":     "#4C9BE8",
        "Clarity":      "#5BC4A0",
        "Tone":         "#F5A623",
        "Completeness": "#E86B6B",
    },
)
fig.update_layout(
    margin=dict(t=10, b=120),
    legend_title="",
    xaxis_tickangle=-35,
    height=420,
)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# ── Weekly trends ─────────────────────────────────────────────────────────────

st.subheader("📈 Weekly Score Trends")

weekly = (
    filtered.groupby(["week", "agent_name"])["total_score"]
    .mean()
    .reset_index()
    .rename(columns={"total_score": "Avg Score", "agent_name": "Agent", "week": "Week"})
    .sort_values("Week")
)

if weekly["Week"].nunique() >= 2:
    fig_trend = px.line(
        weekly,
        x="Week", y="Avg Score", color="Agent",
        markers=True,
    )
    fig_trend.add_hline(
        y=6, line_dash="dash", line_color="red",
        annotation_text="Target (6.0)",
        annotation_position="bottom right",
    )
    fig_trend.update_layout(margin=dict(t=10, b=10), legend_title="", yaxis_range=[0, 10])
    st.plotly_chart(fig_trend, width="stretch")
else:
    st.info("Need data from at least 2 different weeks to show trends.")

st.divider()

# ── Team pattern detection ────────────────────────────────────────────────────

st.subheader("🔍 Team Pattern Detection")

if not filtered.empty:
    pattern_pct = (
        filtered["_weakest"]
        .value_counts(normalize=True)
        .mul(100)
        .round(1)
        .reset_index()
        .rename(columns={"_weakest": "Dimension", "proportion": "Percentage"})
    )
    # Handle both old and new pandas column naming
    if "count" in pattern_pct.columns:
        pattern_pct = pattern_pct.rename(columns={"count": "Percentage"})

    col_pattern, col_dim_avg = st.columns(2)

    with col_pattern:
        st.markdown("**Most common weakest area per ticket**")
        fig_pattern = px.bar(
            pattern_pct,
            x="Dimension", y="Percentage",
            text="Percentage",
            color="Dimension",
            color_discrete_map={
                "Accuracy":     "#4C9BE8",
                "Clarity":      "#5BC4A0",
                "Tone":         "#F5A623",
                "Completeness": "#E86B6B",
            },
        )
        fig_pattern.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        fig_pattern.update_layout(
            margin=dict(t=10, b=10),
            showlegend=False,
            yaxis_title="% of tickets",
            yaxis_range=[0, 100],
        )
        st.plotly_chart(fig_pattern, width="stretch")

    with col_dim_avg:
        st.markdown("**Team dimension averages (normalised to 10)**")
        dim_avgs = {
            "Accuracy":     filtered["accuracy"].mean()     / 3 * 10,
            "Clarity":      filtered["clarity"].mean()      / 3 * 10,
            "Tone":         filtered["tone"].mean()         / 2 * 10,
            "Completeness": filtered["completeness"].mean() / 2 * 10,
        }
        dim_df = pd.DataFrame(
            list(dim_avgs.items()), columns=["Dimension", "Score (out of 10)"]
        ).round(2)

        fig_radar = go.Figure()
        dims   = list(dim_avgs.keys())
        values = list(dim_avgs.values())

        fig_radar.add_trace(go.Scatterpolar(
            r=values + [values[0]],
            theta=dims + [dims[0]],
            fill="toself",
            fillcolor="rgba(76, 155, 232, 0.25)",
            line_color="#4C9BE8",
            name="Team avg",
        ))
        fig_radar.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 10])),
            margin=dict(t=30, b=30),
            showlegend=False,
        )
        st.plotly_chart(fig_radar, width="stretch")

    # Coaching recommendations based on patterns
    st.markdown("**Coaching recommendations**")
    coaching = {
        "Accuracy":     "Product knowledge training — agents are providing incorrect or incomplete information.",
        "Clarity":      "Communication training — responses are hard to follow or poorly structured.",
        "Tone":         "Customer empathy training — tone is too robotic or lacks acknowledgement.",
        "Completeness": "Process training — agents are not fully addressing all points or anticipating follow-ups.",
    }
    for dim, pct in pattern_pct.values:
        if pct >= 25:
            st.warning(f"⚠️ **{dim}** is weakest in **{pct}%** of tickets — {coaching.get(dim, '')}")

st.divider()

# ── Score distribution ────────────────────────────────────────────────────────

st.subheader("Score Distribution")

col_hist, col_box = st.columns(2)

with col_hist:
    fig_hist = px.histogram(
        filtered, x="total_score",
        nbins=11, range_x=[-0.5, 10.5],
        labels={"total_score": "Score", "count": "Tickets"},
        color_discrete_sequence=["#4C9BE8"],
        title="All Tickets",
    )
    fig_hist.update_layout(margin=dict(t=40, b=10), bargap=0.1)
    st.plotly_chart(fig_hist, width="stretch")

with col_box:
    fig_box = px.box(
        filtered,
        x="agent_name", y="total_score",
        color="agent_name",
        labels={"agent_name": "Agent", "total_score": "Score"},
        title="Score Range per Agent",
    )
    fig_box.update_layout(margin=dict(t=40, b=10), showlegend=False)
    fig_box.add_hline(y=6, line_dash="dash", line_color="red", annotation_text="Target")
    st.plotly_chart(fig_box, width="stretch")

st.divider()

# ── Ticket details ────────────────────────────────────────────────────────────

st.subheader("Ticket Details")
st.caption("Each row is one individual ticket. Scores here are per-ticket, not averages.")

display = filtered[[
    "run_date", "ticket_number", "mailbox_name", "agent_name",
    "total_score", "accuracy", "clarity", "tone", "completeness", "feedback",
]].copy()
display["run_date"] = display["run_date"].dt.strftime("%Y-%m-%d")
display = display.sort_values("total_score", ascending=True)

table_display = display.drop(columns=["feedback"]).copy()
table_display.columns = [
    "Date", "Ticket #", "Mailbox", "Agent",
    "Score /10", "Acc /3", "Clar /3", "Tone /2", "Comp /2",
]

st.dataframe(
    table_display,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Date":       st.column_config.TextColumn("Date",      width="small"),
        "Ticket #":   st.column_config.TextColumn("Ticket #",  width="small"),
        "Mailbox":    st.column_config.TextColumn("Mailbox",   width="small"),
        "Agent":      st.column_config.TextColumn("Agent",     width="medium"),
        "Score /10":  st.column_config.NumberColumn("Score /10", width="small", format="%d"),
        "Acc /3":     st.column_config.NumberColumn("Acc /3",    width="small", format="%d"),
        "Clar /3":    st.column_config.NumberColumn("Clar /3",   width="small", format="%d"),
        "Tone /2":    st.column_config.NumberColumn("Tone /2",   width="small", format="%d"),
        "Comp /2":    st.column_config.NumberColumn("Comp /2",   width="small", format="%d"),
    },
)

# AI Feedback shown as clean cards below the table
st.markdown("#### 💬 AI Feedback")
st.caption("Showing all tickets sorted by score (worst first).")

for _, row in display.iterrows():
    score = int(row["total_score"])
    color = "#E86B6B" if score < 6 else "#F5A623" if score < 8 else "#5BC4A0"
    with st.expander(
        f"**{row['agent_name']}** — Ticket #{row['ticket_number']} — "
        f"Score: {score}/10 · {row['mailbox_name']} · {row['run_date']}"
    ):
        cols = st.columns(5)
        cols[0].metric("Score",        f"{score}/10")
        cols[1].metric("Accuracy",     f"{int(row['accuracy'])}/3")
        cols[2].metric("Clarity",      f"{int(row['clarity'])}/3")
        cols[3].metric("Tone",         f"{int(row['tone'])}/2")
        cols[4].metric("Completeness", f"{int(row['completeness'])}/2")
        st.info(row["feedback"])

# ── Customer rating validation ────────────────────────────────────────────────

rated = filtered[
    filtered["rating_verdict"].notna() & (filtered["rating_verdict"] != "")
]

if not rated.empty:
    st.divider()
    st.subheader("⭐ Customer Rating Validation")

    col_pie, col_detail = st.columns([1, 2])

    with col_pie:
        verdict_counts = rated["rating_verdict"].value_counts().reset_index()
        verdict_counts.columns = ["Verdict", "Count"]
        fig_pie = px.pie(
            verdict_counts, names="Verdict", values="Count",
            color="Verdict",
            color_discrete_map={
                "Valid":   "#5BC4A0",
                "Invalid": "#E86B6B",
                "Unclear": "#F5A623",
            },
        )
        fig_pie.update_layout(margin=dict(t=10, b=10))
        st.plotly_chart(fig_pie, width="stretch")

        invalid_pct = round(
            len(rated[rated["rating_verdict"] == "Invalid"]) / len(rated) * 100, 1
        )
        if invalid_pct >= 20:
            st.warning(f"⚠️ {invalid_pct}% of customer ratings appear biased or invalid.")

    with col_detail:
        st.dataframe(
            rated[[
                "ticket_number", "agent_name", "customer_rating",
                "rating_verdict", "rating_feedback",
            ]].rename(columns={
                "ticket_number":   "Ticket #",
                "agent_name":      "Agent",
                "customer_rating": "Rating",
                "rating_verdict":  "Verdict",
                "rating_feedback": "Reason",
            }),
            width="stretch",
            hide_index=True,
        )
