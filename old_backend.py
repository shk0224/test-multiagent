
"""
LANGGRAPH PROJECT BLUEPRINT

1. State          -> Define shared data structure (TypedDict).
2. Agent Functions -> Create functions using tools when needed.
3. Graph          -> Create StateGraph(State).
4. Nodes          -> Register agents using add_node().
5. Edges          -> Define execution flow using add_edge().
6. Checkpointer   -> Set up database persistence (optional).
7. Compile        -> Prepare workflow using graph.compile().
8. Invoke         -> Execute workflow using invoke().

Optional: Create a wrapper function for FastAPI integration.
"""

import os 
import certifi
from dotenv import load_dotenv

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import TypedDict, Annotated
import operator
import uuid

import psycopg
from psycopg.rows import dict_row

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)
from langchain_groq import ChatGroq
from tools.tavily_tool import tavily_search
from tools.flight_tool import search_flights


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. Please add your Render PostgreSQL External Database URL to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url

# url= get_database_url()
# print ( f" This is the url: {url}")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")


# =========================
# LLM
# =========================

llm = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=GROQ_API_KEY
)


# =========================
# State
# =========================


"""
What is TravelState and why do we need it?

Imagine we have a travel agency in Bangladesh.

A customer says:
"I want to travel from Bangladesh to Japan."

Our application has four agents:
1. Flight Agent - Finds flight information.
2. Hotel Agent - Finds hotel information.
3. Itinerary Agent - Creates the travel plan.
4. Final Agent - Combines everything into a final answer.

Each agent has a different job.

But how will one agent know what another agent has found?

We need a shared notebook where agents can read and share information.

In LangGraph, this shared notebook is called STATE.

But we cannot organize information randomly in this notebook.
We need to define what information should be stored and where.

That is why we create TravelState using TypedDict.

TypedDict defines the structure of our shared notebook.

For example:
- messages: Stores conversation messages.
- user_query: Stores the customer's travel request (string).
- flight_results: Stores flight information (string).
- hotel_results: Stores hotel information (string).
- itinerary: Stores the travel plan (string).
- llm_calls: Stores a number used to count calls (integer).

Each agent reads the information it needs from State.
After completing its task, the agent returns new information.
LangGraph then updates the shared State.

Important:
TravelState only defines the structure of the State.
It does not search flights, find hotels, or save data by itself.

PostgresSaver is used separately to save LangGraph checkpoints
in PostgreSQL so the State can persist across runs.
"""

class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    flight_results: str
    hotel_results: str
    itinerary: str
    llm_calls: int


# =========================
# Flight Agent
# =========================

def flight_agent(state: TravelState):
    query = state["user_query"]
    flight_data = search_flights(query)

    return {
        "flight_results": flight_data,
        "messages": [
            AIMessage(content="Flight results fetched.")
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }


# =========================
# Hotel Agent
# =========================

def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['user_query']}"
    hotel_results = tavily_search(query)

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(content="Hotel information fetched.")
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }


# =========================
# Itinerary Agent
# =========================

def itinerary_agent(state: TravelState):
    prompt = f"""
Create a complete travel itinerary.

User Query:
{state['user_query']}

Flight Results:
{state['flight_results']}

Hotel Results:
{state['hotel_results']}

Make the itinerary practical, budget-aware, and easy to follow.
"""

    response = llm.invoke([
        SystemMessage(content="You are an expert travel planner."),
        HumanMessage(content=prompt)
    ])

    return {
        "itinerary": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1
    }




# =========================
# Final Response Agent
# =========================

def final_agent(state: TravelState):
    final_prompt = f"""
Generate the final travel response for the user.

User Request:
{state['user_query']}

Flights:
{state['flight_results']}

Hotels:
{state['hotel_results']}

Itinerary:
{state['itinerary']}

Format the final answer beautifully using these sections:

1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Day-by-Day Itinerary
5. Estimated Budget
6. Final Recommendations

Important:
- Be clear and practical.
- Mention that live flight API may not provide ticket prices if pricing is unavailable.
- Keep the response useful for real travel planning.
"""

    response = llm.invoke([
        SystemMessage(content="You are a professional AI travel booking assistant."),
        HumanMessage(content=final_prompt)
    ])

    return {
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1
    }


"""
BUILD GRAPH — LANGGRAPH WORKFLOW

PURPOSE:
Connect our four existing agent functions into one workflow.
LangGraph will use these connections to decide which agent runs next.

--------------------------------------------------
1. graph = StateGraph(TravelState)
--------------------------------------------------

StateGraph:
    Class imported from the LangGraph library.
    Used to create a graph-based workflow.

TravelState:
    Our custom TypedDict class.
    Defines the expected keys and value types of
    the shared State dictionary.

graph:
    Variable that stores the StateGraph object.

--------------------------------------------------
2. graph.add_node("flight_agent", flight_agent)
--------------------------------------------------

graph:
    Variable holding our StateGraph object.

add_node():
    Method provided by the LangGraph StateGraph class.
    Registers a function as a node in the graph.

"flight_agent":
    String used as the node's name (identifier).
    We can choose another name if we update its references.

flight_agent:
    Our existing Python function defined using
    def flight_agent(state: TravelState).

IMPORTANT:
    "flight_agent" = String (node name)
    flight_agent   = Python function (no quotes)

The other three add_node() lines work the same way.

add_node() only registers the functions.
It does not execute them immediately.

--------------------------------------------------
3. graph.add_edge(START, "flight_agent")
--------------------------------------------------

add_edge():
    Method provided by the LangGraph StateGraph class.
    Connects two nodes and defines the workflow path.

START:
    Special constant imported from LangGraph.
    Represents the beginning of the workflow.

END:
    Special constant imported from LangGraph.
    Represents the end of the workflow.

"flight_agent", "hotel_agent", etc.:
    Strings identifying our registered nodes.

Example:
    graph.add_edge("flight_agent", "hotel_agent")

Meaning:
    After flight_agent finishes, hotel_agent runs.

--------------------------------------------------
4. OUR WORKFLOW EXECUTION ORDER
--------------------------------------------------

START
  |
  v
flight_agent
  |
  v
hotel_agent
  |
  v
itinerary_agent
  |
  v
final_agent
  |
  v
END

This is a SEQUENTIAL workflow.
Each node runs after the previous node completes.

--------------------------------------------------
5. IMPORTANT DIFFERENCES
--------------------------------------------------

StateGraph = LangGraph library class
TravelState = Our custom TypedDict class
graph = Variable holding a StateGraph object

add_node() = LangGraph method for registering nodes
add_edge() = LangGraph method for connecting nodes

"flight_agent" = Node name (string)
flight_agent = Our Python function

START / END = Special LangGraph constants

--------------------------------------------------
6. DOES THIS CODE EXECUTE OUR AGENTS?
--------------------------------------------------

NO.

This section only builds the graph structure.

Later:
graph.compile() creates an executable graph.

The compiled graph can then be executed using
invoke() or another supported execution method.

SUMMARY:
add_node() tells LangGraph WHAT functions are available.
add_edge() tells LangGraph HOW the nodes are connected.
"""

# =========================
# Build Graph
# =========================

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



"""
POSTGRESQL CHECKPOINTER

PURPOSE:
Connect to PostgreSQL, set up checkpoint storage,
and prepare our LangGraph workflow for execution.

1. DATABASE_URL = get_database_url()
   DATABASE_URL     -> Variable storing the database URL.
   get_database_url() -> Our custom Python function.
   This does NOT connect to the database.

2. _conn = psycopg.connect(...)
   psycopg          -> Imported Python module.
   connect()        -> Function from psycopg.
   _conn            -> Variable holding the connection object.
   autocommit=True  -> Automatically commit database operations.
   dict_row         -> Makes fetched rows dictionary-like.

3. checkpointer = PostgresSaver(_conn)
   PostgresSaver    -> LangGraph library class.
   _conn            -> Database connection argument.
   checkpointer     -> Variable holding a PostgresSaver object.

4. checkpointer.setup()
   setup()          -> PostgresSaver method.
   Prepares the required PostgreSQL checkpoint tables.

5. travel_graph = graph.compile(checkpointer=checkpointer)
   graph            -> Our StateGraph object.
   compile()        -> LangGraph method.
   checkpointer     -> Our PostgresSaver object.
   travel_graph     -> Variable holding the compiled graph.

FLOW:
Database URL -> Connection -> Checkpointer -> Compiled Graph

NOTE:
This code does NOT execute our agents.
Later, travel_graph.invoke() executes the workflow.
"""



# =========================
# PostgreSQL Checkpointer
# =========================
DATABASE_URL = get_database_url()

_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row
)

checkpointer = PostgresSaver(_conn)
checkpointer.setup()

travel_graph = graph.compile(checkpointer=checkpointer)



# =========================
# Python Function to Run LangGraph
# Can Be Called from FastAPI
# =========================

def run_travel_agent(user_input: str, thread_id: str | None = None):
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    result = travel_graph.invoke(
        {
            "messages": [
                HumanMessage(content=user_input)
            ],
            "user_query": user_input,
            "flight_results": "",
            "hotel_results": "",
            "itinerary": "",
            "llm_calls": 0
        },
        config=config
    )

    final_answer = result["messages"][-1].content

    return {
        "thread_id": thread_id,
        "answer": final_answer,
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "itinerary": result.get("itinerary", ""),
        "llm_calls": result.get("llm_calls", 0),
    }