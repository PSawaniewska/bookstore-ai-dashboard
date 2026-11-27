import altair as alt
import pandas as pd
import streamlit as st
from openai import APIError, RateLimitError

from assistant import ask, run_sql
from store import STORES, load_tables, process_store

REPO_URL = "https://github.com/PSawaniewska/bookstore-ai-dashboard"
MAX_QUESTIONS = 20
BORDEAUX, GREEN = "#7A2E2E", "#2F5D50"

# the data folders keep their original names; the app shows branch names
BRANCHES = dict(zip(STORES, ["Downtown", "Riverside", "Old Town", "Harbor", "University"]))
BRANCH_DIRS = {name: path for path, name in BRANCHES.items()}

WELCOME = """Hi! I'm the data assistant for this bookstore. Ask me about sales, customers or books, in any language. Try:"""
EXAMPLES = [
    "Who is the best buyer?",
    "Which branch has the highest revenue?",
    "Show users with e-mails starting with s or p",
]
WELCOME_NOTE = "Answers can take a moment, as I check everything in the data."

COLUMN_LABELS = {
    "id": "ID", "title": "Title", "authors": "Authors", "genre": "Genre", "publisher": "Publisher",
    "year": "Year", "name": "Name", "address": "Address", "phone": "Phone", "email": "E-mail",
    "user_id": "User ID", "book_id": "Book ID", "quantity": "Qty", "unit_price": "Unit price",
    "paid_price": "Paid", "timestamp": "Time", "date": "Date",
}

# the theme cannot style chat roles or fit boxes to the window height, so a few CSS rules do it
STYLE = """
<style>
[data-testid="stMainBlockContainer"] { padding-top: 2.5rem; padding-bottom: 1rem; }
[data-testid="stChatMessage"] { padding: 0.75rem 1rem; }
[data-testid="stChatMessage"]:has([aria-label="Chat message from assistant"]) { background-color: #E3ECE6; }
.st-key-chat_box, div:has(> .st-key-chat_box) { --box: calc(100vh - 200px); }
.st-key-explorer_box { --box: calc(100vh - 320px); }
.st-key-chart_box { --box: calc(100vh - 300px); }
.st-key-chat_box, .st-key-explorer_box, .st-key-chart_box, div:has(> .st-key-chat_box) {
    height: max(var(--box), 320px) !important; max-height: none !important; flex: 0 0 auto !important;
}
.st-key-explorer_box [data-testid="stElementContainer"], .st-key-chart_box [data-testid="stElementContainer"] {
    flex: 1 1 auto; height: 100%;
}
</style>
"""

# small base widths: the table stretches them to fill wider screens, so it never scrolls sideways
COLUMN_WIDTHS = {
    "id": 45, "user_id": 55, "book_id": 55, "year": 45, "quantity": 35, "unit_price": 60, "paid_price": 55,
    "title": 120, "authors": 135, "genre": 95, "publisher": 110,
    "name": 100, "address": 170, "phone": 80, "email": 150, "timestamp": 120, "date": 75,
}

st.set_page_config(page_title="Quill & Page", page_icon=":material/auto_stories:", layout="wide")
st.html(STYLE)


# data is processed once per branch and then kept in memory
@st.cache_data
def get_results(store_dir):
    return process_store(store_dir)


@st.cache_data
def get_tables(store_dir):
    return load_tables(store_dir)


def column_config(df):
    # ids and years without thousands separators, prices in dollars, internal person_id hidden
    config = {"person_id": None}
    for column in df.columns:
        label = COLUMN_LABELS.get(column, column)
        width = COLUMN_WIDTHS.get(column, "medium")
        if column in ("id", "user_id", "book_id", "year"):
            config[column] = st.column_config.NumberColumn(label, format="%d", width=width)
        elif column in ("unit_price", "paid_price"):
            config[column] = st.column_config.NumberColumn(label, format="$%.2f", width=width)
        elif column != "person_id":
            config[column] = st.column_config.Column(label, width=width)
    return config


def nice_date(day):
    # %-d is not supported on Windows, so the day number is added separately
    return f"{day.day} {day:%B %Y}"


def best_buyer(result, tables):
    ids = result["best_buyer"]
    names = sorted(tables["users"].loc[tables["users"]["id"].isin(ids), "name"].unique())
    orders = tables["orders"][tables["orders"]["user_id"].isin(ids)]
    return names, orders["paid_price"].sum(), len(orders)


def dashboard_facts(result, tables):
    # the assistant gets the dashboard numbers, so its answers cannot contradict the tabs
    names, spent, _ = best_buyer(result, tables)
    return (
        f"best buyer (most money spent): {', '.join(names)} "
        f"(user ids: [{', '.join(map(str, result['best_buyer']))}], spent ${spent:,.2f}); "
        f"most popular author set(s): {'; '.join(', '.join(authors) for authors in result['top_authors'])}; "
        f"top 5 days by revenue: {', '.join(result['top5_days'])}; unique customers: {result['unique_users']}"
    )


def branch_summary(store_dir):
    result = get_results(store_dir)
    return (
        f"{BRANCHES[store_dir]}: {result['orders']:,} orders, ${result['daily_revenue'].sum():,.2f} revenue, "
        f"{result['unique_users']:,} unique customers, {result['author_sets']} unique author sets"
    )


def revenue_chart(revenue, top_days):
    data = pd.DataFrame({"Date": revenue.index, "Revenue": revenue.values, "Average": revenue.rolling(7).mean().values})
    top = data[data["Date"].dt.strftime("%Y-%m-%d").isin(top_days)]
    x = alt.X("Date:T", title=None)
    daily = alt.Chart(data).mark_line(color=BORDEAUX, opacity=0.45, strokeWidth=1).encode(
        x, alt.Y("Revenue:Q", title="Revenue (USD)"),
        tooltip=[alt.Tooltip("Date:T", format="%Y-%m-%d"), alt.Tooltip("Revenue:Q", format="$,.2f")],
    )
    average = alt.Chart(data).mark_line(color=GREEN, strokeWidth=2.5).encode(x, alt.Y("Average:Q"))
    peaks = alt.Chart(top).mark_point(color=BORDEAUX, filled=True, size=90).encode(
        x, alt.Y("Revenue:Q"),
        tooltip=[alt.Tooltip("Date:T", format="%Y-%m-%d"), alt.Tooltip("Revenue:Q", format="$,.2f")],
    )
    return daily + average + peaks


st.session_state.setdefault("history", [])
st.session_state.setdefault("view", None)

# the assistant can switch branches; widget state can only be changed before the widget is drawn
if "next_store" in st.session_state:
    st.session_state.store = st.session_state.pop("next_store")

with st.sidebar:
    st.markdown("## :material/auto_stories: Quill & Page")
    st.caption("Welcome to our bookstore dashboard")
    store_dir = st.selectbox("Choose a branch", STORES, format_func=BRANCHES.get, key="store")

    st.space("large")
    with st.expander("About", icon=":material/info:"):
        st.markdown(
            "Built by Paulina Sawaniewska as a task in the Itransition Data Engineering internship program. "
            "The data was provided by Itransition for training purposes.\n\n"
            "The assistant runs on a free AI plan with small limits, so complex questions can take a minute.\n\n"
            f"[Source code on GitHub]({REPO_URL})"
        )

branch = BRANCHES[store_dir]
result = get_results(store_dir)
tables = get_tables(store_dir)
revenue = result["daily_revenue"]

dashboard, chat = st.columns([2, 1], gap="large")

with dashboard:
    st.title(f"{branch} branch")
    st.caption(
        f"{nice_date(revenue.index.min())} to {nice_date(revenue.index.max())}  ·  "
        f"{result['orders']:,} orders  ·  ${revenue.sum():,.2f} total revenue"
    )

    tab_days, tab_metrics, tab_chart, tab_explorer = st.tabs([
        ":material/calendar_month: Top 5 days",
        ":material/insights: Key metrics",
        ":material/show_chart: Daily revenue",
        ":material/table_view: Explorer",
    ])

    with tab_days:
        top_days = pd.DataFrame({
            "Date": [nice_date(pd.Timestamp(day)) for day in result["top5_days"]],
            "Revenue": [revenue[pd.Timestamp(day)] for day in result["top5_days"]],
        })
        st.dataframe(
            top_days,
            row_height=64,
            hide_index=True,
            column_config={
                "Revenue": st.column_config.ProgressColumn(
                    "Revenue (USD)", format="$%.2f", min_value=0, max_value=top_days["Revenue"].max()
                )
            },
        )

    with tab_metrics:
        cols = st.columns(4)
        cols[0].metric("Unique customers", f"{result['unique_users']:,}", border=True)
        cols[1].metric("Unique author sets", result["author_sets"], border=True)
        cols[2].metric("Copies sold", f"{int(tables['orders']['quantity'].sum()):,}", border=True)
        cols[3].metric("Average order", f"${revenue.sum() / result['orders']:,.2f}", border=True)

        col_authors, col_buyer = st.columns(2)
        with col_authors.container(border=True, height="stretch"):
            st.caption("Most popular author(s)")
            for authors in result["top_authors"]:
                st.markdown(f"#### {', '.join(authors)}")
        with col_buyer.container(border=True, height="stretch"):
            names, spent, orders = best_buyer(result, tables)
            st.caption("Best buyer")
            st.markdown(f"#### {' / '.join(names)}")
            st.markdown(f"${spent:,.2f} spent in {orders} orders")
            # one person can have several accounts, so all of their user ids are shown
            st.markdown("User ids: " + " ".join(f":red-badge[{user_id}]" for user_id in result["best_buyer"]))

    with tab_chart:
        with st.container(key="chart_box"):
            st.altair_chart(revenue_chart(revenue, result["top5_days"]), width="stretch", height="stretch")
        st.caption("Bordeaux line: daily revenue · green line: 7-day average · dots: top 5 days")

    with tab_explorer:
        view = st.session_state.view
        if view and view["store"] == store_dir:
            st.markdown(f"**{view['title']}**")
            if st.button("Show all rows", icon=":material/filter_alt_off:"):
                st.session_state.view = None
                st.rerun()
            shown = run_sql(tables, view["query"])
        else:
            table = st.segmented_control("Table", ["books", "users", "orders"], default="books")
            shown = tables[table or "books"]
        with st.container(key="explorer_box"):
            st.dataframe(shown, height="stretch", hide_index=True, column_config=column_config(shown))

with chat:
    st.subheader(":material/chat: AI assistant")
    messages = st.container(height=520, key="chat_box")
    avatars = {"user": ":material/person:", "assistant": ":material/auto_stories:"}

    # the welcome message is only shown, it is not sent to the model
    with messages.chat_message("assistant", avatar=avatars["assistant"]):
        st.markdown(WELCOME)
        for example in EXAMPLES:
            if st.button(example, key=example):
                st.session_state.example = example
        st.caption(WELCOME_NOTE)
    for message in st.session_state.history:
        messages.chat_message(message["role"], avatar=avatars[message["role"]]).markdown(message["content"])

    question = st.chat_input("Ask a question...") or st.session_state.pop("example", None)
    if question:
        asked = sum(m["role"] == "user" for m in st.session_state.history)
        if asked >= MAX_QUESTIONS:
            st.warning("Question limit for this session reached. Refresh the page to start again.")
        else:
            st.session_state.history.append({"role": "user", "content": question})
            messages.chat_message("user", avatar=avatars["user"]).markdown(question)
            with messages.chat_message("assistant", avatar=avatars["assistant"]), st.spinner("Checking the data..."):
                try:
                    answer, actions = ask(
                        st.secrets["GROQ_API_KEY"], st.session_state.history, tables, branch, list(BRANCH_DIRS),
                        # SQL only sees the current branch, so a summary of all branches allows comparisons
                        dashboard_facts(result, tables) + ". All branches: " + " | ".join(map(branch_summary, STORES)),
                    )
                except RateLimitError as error:
                    if "per day" in str(error):
                        answer = "The free daily AI limit has been used up. Please come back tomorrow."
                    else:
                        answer = "The free AI quota is busy right now. Please try again in a minute."
                    actions = []
                except APIError as error:
                    # shown in the terminal, so failures can be checked later
                    print(f"AI error: {error!r}")
                    answer, actions = "The AI service could not handle this request. Try a shorter or simpler question.", []

            st.session_state.history.append({"role": "assistant", "content": answer})
            for action in actions:
                if "view" in action:
                    st.session_state.view = action["view"] and {**action["view"], "store": store_dir}
                if "store" in action:
                    st.session_state.next_store = BRANCH_DIRS[action["store"]]
                    st.session_state.view = None
            st.rerun()
