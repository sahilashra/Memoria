# Framework Choices — Why LangGraph, Hybrid RAG, and LangChain

> Technical rationale for the framework decisions, including what was rejected and why.

---

## LangGraph — Workflow Orchestration

### Why LangGraph

The adaptive agent has requirements that rule out simpler approaches:

1. **Conditional routing:** the workflow branches based on capability gaps — different paths for different work types
2. **Human-in-the-loop with persistence:** the agent must pause, wait for user review (possibly hours later), and resume from exactly where it stopped — not restart
3. **Multi-step state:** the state evolves across multiple LLM calls and the final state (approved artifacts, actions taken) needs to be retrievable even after a server restart
4. **Rejection feedback loops:** when an artifact is rejected with a reason, that reason needs to be injected into the next generation attempt — stateful context threading

A simple request/response endpoint cannot do items 2 and 3. A custom state machine stored in SQLite could — and that was the alternative seriously considered.

### Why Not a Custom State Machine

A SQLite-backed state machine with `workflow_state` columns and transition functions is simpler to debug and has no external dependencies. It was the recommended fallback in the architecture review. The reason LangGraph was chosen over it:

- LangGraph's `interrupt()` + `Command(resume=...)` pattern maps exactly to the "pause and wait" requirement — implementing this correctly without LangGraph requires careful asyncio coordination that is error-prone
- LangGraph's checkpointer (SQLite, Postgres, Redis) is the same persistence model we'd build anyway, without reinventing it
- The conditional routing DSL is readable — adding a new capability node (e.g., `generate_documentation`) is additive, not structural

The trade-off accepted: LangGraph's observability tooling is weak; when a workflow crashes mid-node, the error context is poor. We mitigate this with explicit structured logging at every node boundary and SSE error events that surface the exception message to the UI.

### Known Constraints

**Single-worker Phase 1:** LangGraph's `interrupt()` suspends a coroutine in the process that called it. The `/api/workflow/approve` endpoint must hit the same process. This means Phase 1 requires `--workers 1`. Phase 2 adds Redis pub/sub for cross-worker resume.

**State serialization:** All fields in `WorkState` must be JSON-serializable. No objects, no dataclasses without `asdict()`, no non-primitive types.

**Async only in FastAPI context:** Always use `workflow.ainvoke()`, never `workflow.invoke()`. The sync version calls `asyncio.run()` internally, which raises `RuntimeError` inside FastAPI's event loop.

---

## Hybrid RAG — Why Not LCEL Chains

### What Was Rejected

The original proposal included LangChain LCEL (LangChain Expression Language) chains wrapping ChromaDB + graph traversal. LCEL adds a chainable operator syntax (`retriever | reranker | llm`) but:

- The actual retrieval logic still needs to be written — LCEL is syntax, not semantics
- The existing `search.py` ChromaDB interface is already a clean Python function; wrapping it in a LangChain `Retriever` adds an abstraction layer with no benefit
- LCEL pulls in the full LangChain dependency stack, which adds ~150MB of transitive dependencies for users who don't need the agent layer

### What We Use Instead

**Direct hybrid retrieval** in `agent/retrieval.py`:

```
Step 1: Vector search
  query_embedding = embed(ticket_text)
  vector_results = chromadb.query(query_embedding, n_results=top_k)
  → list of (sub_bank_path, chunk_text, score) ordered by similarity

Step 2: Graph expansion
  matched_paths = [r.sub_bank_path for r in vector_results]
  edges = load_file_graph(project_name)
  neighbor_paths = [e.target for e in edges if e.source in matched_paths]
  graph_results = chromadb.query_by_metadata(paths=neighbor_paths, ...)
  → list of (sub_bank_path, chunk_text, base_score)

Step 3: Reciprocal Rank Fusion (RRF)
  For each result:
    rrf_score = 1/(rank_vector + 60) + 1/(rank_graph + 60)
  Sort by rrf_score descending

Return: top-N results with combined scores
```

**Why RRF over a cross-encoder:** A cross-encoder re-ranker (e.g., `ms-marco-MiniLM-L-6-v2`) would give higher quality ranking but requires loading a model, which adds 100ms+ latency per retrieval and another large dependency. RRF is deterministic, cheap, and works well for this use case where the two result sets (vector matches and graph neighbors) have different coverage — RRF handles this without model inference.

### LangChain — What It IS Used For

LangChain is used for one thing only: `langchain_community.llms.litellm.LiteLLM` as the LLM wrapper inside any LangChain-adjacent code. This routes all LLM calls through the existing `ModelProvider` config (`config.yaml`) rather than requiring `langchain-anthropic` or `langchain-openai` as hard dependencies.

This means the LLM configuration (provider, model, temperature, tokens) stays in one place. The LangChain integration does not add model-specific dependencies — only the LiteLLM bridge package.

---

## Why LangGraph + LiteLLM (Not a Unified Framework)

Alternatives considered:

| Option | Rejected because |
|---|---|
| LangChain Agents (ReAct, OpenAI Functions) | No durable human-in-the-loop; state not persistent across requests |
| CrewAI | Multi-agent system; this is a single sequential workflow; overhead without benefit |
| AutoGen | Conversational agent model; doesn't map to ticket-scoped capability workflows |
| Custom Python | Correct choice if LangGraph fails; kept as fallback (see Build Order Step 7 notes) |
| LlamaIndex | Document-first; the MCP integration and action execution model don't fit |

LangGraph was chosen specifically because it is the only framework that makes human-in-the-loop with durable pause/resume a first-class primitive, not a workaround.

---

## Why ChromaDB (Not Pinecone, Weaviate, etc.)

ChromaDB is already in the codebase, embedded, local, and working. It requires zero infrastructure setup. The existing `search.py` module manages the index lifecycle.

The decision would be reconsidered if:
- Teams have >10,000 sub-unit Memory Banks (ChromaDB embedded SQLite has performance limits at scale)
- Multi-tenant deployment requires isolation at the vector DB layer

For Phase 1 and Phase 2 target scale (1–500 sub-units per team), embedded ChromaDB is the correct choice.

---

## Dependency Strategy

LangGraph and LangChain add significant transitive dependencies (~150–200MB including numpy, tokenizers, huggingface-hub). Not all users of Memoria Business need the agent layer — some will use only the Memory Bank generation and intelligence features.

**Solution: optional dependency group**

```toml
# pyproject.toml
[project.optional-dependencies]
agent = [
    "langgraph>=0.2.0",
    "langchain-community>=0.2.0",
]
```

```bash
# Full install with agent capabilities
pip install memoria-business[agent]

# Core only (Memory Banks, graph, MCP connectors, web UI)
pip install memoria-business
```

The agent layer (`memoria/agent/`) imports LangGraph only inside function bodies, not at module level:

```python
# In agent/graph.py
def build_workflow():
    try:
        from langgraph.graph import StateGraph
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        raise ImportError(
            "The agent workflow requires langgraph. "
            "Install with: pip install memoria-business[agent]"
        )
    ...
```

This means the core package imports without error even if `langgraph` is not installed. The UI shows a "Agent features not available — install memoria-business[agent]" banner if the import fails.
