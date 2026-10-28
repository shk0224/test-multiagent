"""TripMate AI backend: four-agent LangGraph with persistent chat history.

Existing workflow preserved:
START -> flight_agent -> hotel_agent -> itinerary_agent -> final_agent -> END

Chat history is stored in PostgreSQL LangGraph checkpoints. A thread_id must
only be exposed/accepted by an API after that API verifies conversation ownership.
"""

import os
import uuid
import operator
from typing import Annotated, TypedDict

import certifi
from dotenv import load_dotenv

load_dotenv()
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import psycopg
from psycopg.rows import dict_row
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage, SystemMessage
from langchain_groq import ChatGroq
from tools.tavily_tool import tavily_search
from tools.flight_tool import search_flights


def get_database_url():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is missing. Please add it to your .env file.")
    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"
    return database_url


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")

llm = ChatGroq(model="openai/gpt-oss-120b", api_key=GROQ_API_KEY)


class TravelState(TypedDict):
    # Only user questions and final assistant answers are added to messages.
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str       # Original user question
    search_query: str     # Standalone question resolved using previous chat
    flight_results: str
    hotel_results: str
    itinerary: str
    llm_calls: int


def flight_agent(state: TravelState):
    flight_data = search_flights(state["search_query"])
    return {
        "flight_results": flight_data,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['search_query']}"
    hotel_results = tavily_search(query)
    return {
        "hotel_results": hotel_results,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def itinerary_agent(state: TravelState):
    prompt = f"""Create a complete travel itinerary.

Current user request (with earlier chat context resolved):
{state['search_query']}

Flight Results:
{state['flight_results']}

Hotel Results:
{state['hotel_results']}

Make the itinerary practical, budget-aware, and easy to follow.
"""
    response = llm.invoke([
        SystemMessage(content="You are an expert travel planner."),
        HumanMessage(content=prompt),
    ])
    return {
        "itinerary": response.content,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def final_agent(state: TravelState):
    final_prompt = f"""Generate the final travel response for the user.

Original user request:
{state['user_query']}

Request interpreted using conversation context:
{state['search_query']}

Flights:
{state['flight_results']}

Hotels:
{state['hotel_results']}

Itinerary:
{state['itinerary']}

Format the final answer using these sections:
1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Day-by-Day Itinerary
5. Estimated Budget
6. Final Recommendations

Important:
- Be clear and practical.
- Respond to the latest user request, preserving relevant prior trip preferences.
- Mention that live flight API may not provide ticket prices if pricing is unavailable.
- Never claim estimated prices are verified live prices.
- Keep the response useful for real travel planning.
- Complete all sections; avoid ending in the middle of a sentence.
"""
    response = llm.invoke([
        SystemMessage(content="You are a professional AI travel booking assistant."),
        HumanMessage(content=final_prompt),
    ])
    # Only the FINAL answer is recorded as an assistant chat message.
    return {
        "messages": [AIMessage(content=response.content, additional_kwargs={"tripmate_final": True})],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# Preserve the existing four-agent sequential workflow.
graph = StateGraph(TravelState)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("final_agent", final_agent)
graph.add_edge(START, "flight_agent")
graph.add_edge("flight_agent", "hotel_agent")
graph.add_edge("hotel_agent", "itinerary_agent")
graph.add_edge("itinerary_agent", "final_agent")
graph.add_edge("final_agent", END)


DATABASE_URL = get_database_url()
_conn = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)
checkpointer = PostgresSaver(_conn)
checkpointer.setup()
travel_graph = graph.compile(checkpointer=checkpointer)


# Older checkpoints may contain these internal messages from the original code.
_INTERNAL_STATUS_MESSAGES = {"Flight results fetched.", "Hotel information fetched."}


def _is_final_ai_message(message):
    if not isinstance(message, AIMessage):
        return False
    if message.content in _INTERNAL_STATUS_MESSAGES:
        return False
    if message.additional_kwargs.get("tripmate_final"):
        return True
    # Legacy checkpoints stored itinerary-agent output as well as final output.
    # Legacy history is best reconstructed by grouping messages between user turns
    # and selecting the LAST AI message from each group (see _clean_messages).
    return True


def _clean_messages(messages):
    """Return visible user/final-assistant pairs, including older checkpoints."""
    visible = []
    pending_user = None
    last_ai = None
    for message in messages:
        if isinstance(message, HumanMessage):
            if pending_user is not None:
                visible.append({"role": "user", "content": str(pending_user.content)})
                if last_ai is not None:
                    visible.append({"role": "assistant", "content": str(last_ai.content)})
            pending_user = message
            last_ai = None
        elif pending_user is not None and _is_final_ai_message(message):
            last_ai = message
    if pending_user is not None:
        visible.append({"role": "user", "content": str(pending_user.content)})
        if last_ai is not None:
            visible.append({"role": "assistant", "content": str(last_ai.content)})
    return visible


def get_travel_history(thread_id: str):
    """Get visible messages for an existing thread; no thread means [].

    IMPORTANT: FastAPI must verify the requesting user's ownership of thread_id
    BEFORE calling this function. Checkpoint IDs are not authorization tokens.
    """
    if not thread_id:
        return []
    snapshot = travel_graph.get_state({"configurable": {"thread_id": thread_id}})
    if not snapshot.values:
        return []
    return _clean_messages(snapshot.values.get("messages", []))


def _resolve_followup(user_input: str, history: list[dict]):
    """Rewrite follow-up into a standalone travel request using chat context."""
    if not history:
        return user_input
    # Avoid sending a potentially huge full itinerary back to the LLM.
    recent = history[-8:]
    context = "\n".join(
        f"{item['role']}: {item['content'][:2500]}" for item in recent
    )
    response = llm.invoke([
        SystemMessage(content=(
            "You resolve travel-planning follow-up questions. Rewrite the latest "
            "user message as a standalone travel-planning request using relevant "
            "details from the prior conversation (destination, dates, budget, "
            "preferences). Respect the user's latest corrections. Do not invent "
            "missing details. Output ONLY the rewritten request, no commentary."
        )),
        HumanMessage(content=f"Previous conversation:\n{context}\n\nLatest user message:\n{user_input}"),
    ])
    rewritten = str(response.content).strip()
    return rewritten or user_input


def run_travel_agent(user_input: str, thread_id: str | None = None):
    """Run one chat turn and return the answer and stable conversation ID.

    A new thread is created when thread_id is None. The FastAPI layer is
    responsible for verifying ownership before reusing a supplied thread_id.
    """
    if not isinstance(user_input, str) or not user_input.strip():
        raise ValueError("Please enter a travel question.")
    user_input = user_input.strip()
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {"configurable": {"thread_id": thread_id}}
    history = get_travel_history(thread_id)
    search_query = _resolve_followup(user_input, history)

    result = travel_graph.invoke(
        {
            "messages": [HumanMessage(content=user_input)],
            "user_query": user_input,
            "search_query": search_query,
            "flight_results": "",
            "hotel_results": "",
            "itinerary": "",
            "llm_calls": 0,
        },
        config=config,
    )

    # The last message is produced by final_agent.
    final_answer = str(result["messages"][-1].content)
    return {
        "thread_id": thread_id,
        "answer": final_answer,
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "itinerary": result.get("itinerary", ""),
        "llm_calls": result.get("llm_calls", 0),
    }
