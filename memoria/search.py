"""
Vector Search — semantic search across all Memory Banks.
Supports multiple backends:
  - chromadb  (default, local, zero setup)
  - pinecone  (cloud, opt-in)
  - weaviate  (cloud, opt-in)

Configured via `vector_store:` block in config.yaml.
"""

import re
from pathlib import Path
from typing import List, Dict, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Public API — same signatures as before, routes to configured provider
# ─────────────────────────────────────────────────────────────────────────────

def index_book(project_name: str, book_content: str, books_dir: str = "books"):
    """
    Index a Memory Bank into the vector store.
    Called automatically after generate/update.
    """
    provider = _get_provider(books_dir)
    chunks = _split_into_sections(book_content, project_name)
    if not chunks:
        return
    provider.index(project_name, chunks)


def search(query: str, books_dir: str = "books", top_k: int = 5) -> List[Dict]:
    """
    Search across all indexed Memory Banks.
    Returns ranked list of {project, section, text, score}.
    """
    provider = _get_provider(books_dir)
    return provider.search(query, top_k)


def index_all_books(books_dir: str = "books"):
    """Re-index every book in the books directory."""
    books_path = Path(books_dir)
    books = list(books_path.glob("*_memory_bank.md"))
    indexed = 0
    for book in books:
        project = book.stem.replace("_memory_bank", "")
        content = book.read_text(encoding="utf-8")
        index_book(project, content, books_dir)
        indexed += 1
    return indexed


def purge_orphaned(books_dir: str = "books") -> list[str]:
    """
    Remove vector store embeddings for any project whose Memory Bank file
    no longer exists on disk.

    Returns a list of project names that were purged.
    Called automatically on server startup and after a delete.
    """
    books_path = Path(books_dir)

    # What's on disk right now
    live_projects = {
        f.stem.replace("_memory_bank", "")
        for f in books_path.glob("*_memory_bank.md")
        if not f.name.endswith("_draft.md")
    }

    # What's in the vector store
    try:
        provider = _get_provider(books_dir)
        indexed_projects = _get_indexed_projects(provider)
    except Exception:
        return []

    orphans = indexed_projects - live_projects
    purged  = []
    for name in orphans:
        try:
            provider.delete_project(name)
            purged.append(name)
        except Exception:
            pass

    return purged


def _get_indexed_projects(provider) -> set:
    """
    Return the set of project names currently in the vector store.
    Works across ChromaDB, Pinecone, and Weaviate providers.
    """
    try:
        # ChromaDB
        if hasattr(provider, "_collection"):
            result = provider._collection.get(include=["metadatas"])
            return {m.get("project") for m in result["metadatas"] if m.get("project")}
    except Exception:
        pass
    try:
        # Pinecone — fetch a broad sample of vectors and collect project metadata
        if hasattr(provider, "_index"):
            stats = provider._index.describe_index_stats()
            # Pinecone doesn't provide a list of all metadata values directly;
            # return empty set so orphan detection falls back to the safe no-op path.
            return set()
    except Exception:
        pass
    try:
        # Weaviate
        if hasattr(provider, "_client"):
            collection = provider._client.collections.get(provider._collection_name)
            projects: set = set()
            for obj in collection.iterator(include_vector=False):
                p = obj.properties.get("project")
                if p:
                    projects.add(p)
            return projects
    except Exception:
        pass
    return set()


# ─────────────────────────────────────────────────────────────────────────────
# Provider base class
# ─────────────────────────────────────────────────────────────────────────────

class VectorProvider:
    """Abstract base for vector store backends."""

    def index(self, project_name: str, chunks: List[Dict]):
        raise NotImplementedError

    def search(self, query: str, top_k: int) -> List[Dict]:
        raise NotImplementedError

    def delete_project(self, project_name: str):
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB provider (default — local, zero config)
# ─────────────────────────────────────────────────────────────────────────────

class ChromaProvider(VectorProvider):
    def __init__(self, books_dir: str):
        import chromadb
        db_path = str(Path(books_dir) / ".chromadb")
        self._client = chromadb.PersistentClient(path=db_path)
        self._collection = self._client.get_or_create_collection(
            name="memoria_books",
            metadata={"hnsw:space": "cosine"},
        )

    def index(self, project_name: str, chunks: List[Dict]):
        # Remove existing entries for this project
        try:
            existing = self._collection.get(where={"project": project_name})
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
        except Exception:
            pass

        self._collection.add(
            documents=[c["text"] for c in chunks],
            metadatas=[{"project": project_name, "section": c["section"]} for c in chunks],
            ids=[f"{project_name}__{i}" for i in range(len(chunks))],
        )

    def search(self, query: str, top_k: int) -> List[Dict]:
        count = self._collection.count()
        if count == 0:
            return []

        results = self._collection.query(
            query_texts=[query],
            n_results=min(top_k, count),
        )

        hits = []
        for i, doc in enumerate(results["documents"][0]):
            hits.append({
                "project": results["metadatas"][0][i]["project"],
                "section": results["metadatas"][0][i]["section"],
                "text": doc,
                "score": round(1 - results["distances"][0][i], 3),
            })
        return hits

    def delete_project(self, project_name: str):
        try:
            existing = self._collection.get(where={"project": project_name})
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Pinecone provider (cloud — requires pinecone-client + API key)
# ─────────────────────────────────────────────────────────────────────────────

class PineconeProvider(VectorProvider):
    def __init__(self, config: dict):
        try:
            from pinecone import Pinecone
        except ImportError:
            raise ImportError(
                "Pinecone not installed. Run: pip install pinecone-client"
            )

        import os
        api_key = config.get("pinecone_api_key") or os.getenv("PINECONE_API_KEY", "")
        if not api_key:
            raise ValueError("Pinecone API key not set. Add PINECONE_API_KEY to .env or vector_store.pinecone_api_key in config.yaml")

        self._pc = Pinecone(api_key=api_key)
        index_name = config.get("pinecone_index", "memoria")

        # Create index if it doesn't exist
        existing_indexes = [idx.name for idx in self._pc.list_indexes()]
        if index_name not in existing_indexes:
            from pinecone import ServerlessSpec
            self._pc.create_index(
                name=index_name,
                dimension=384,  # sentence-transformers default
                metric="cosine",
                spec=ServerlessSpec(
                    cloud=config.get("pinecone_cloud", "aws"),
                    region=config.get("pinecone_region", "us-east-1"),
                ),
            )

        self._index = self._pc.Index(index_name)
        self._embedder = _get_embedder()

    def index(self, project_name: str, chunks: List[Dict]):
        # Delete existing vectors for this project
        self.delete_project(project_name)

        # Embed and upsert
        texts = [c["text"] for c in chunks]
        embeddings = self._embedder.encode(texts).tolist()

        vectors = []
        for i, (emb, chunk) in enumerate(zip(embeddings, chunks)):
            vectors.append({
                "id": f"{project_name}__{i}",
                "values": emb,
                "metadata": {
                    "project": project_name,
                    "section": chunk["section"],
                    "text": chunk["text"][:1000],  # Pinecone metadata limit
                },
            })

        # Upsert in batches of 100
        for batch_start in range(0, len(vectors), 100):
            batch = vectors[batch_start:batch_start + 100]
            self._index.upsert(vectors=batch)

    def search(self, query: str, top_k: int) -> List[Dict]:
        query_emb = self._embedder.encode([query]).tolist()[0]

        results = self._index.query(
            vector=query_emb,
            top_k=top_k,
            include_metadata=True,
        )

        hits = []
        for match in results.get("matches", []):
            meta = match.get("metadata", {})
            hits.append({
                "project": meta.get("project", "unknown"),
                "section": meta.get("section", ""),
                "text": meta.get("text", ""),
                "score": round(match.get("score", 0), 3),
            })
        return hits

    def delete_project(self, project_name: str):
        try:
            self._index.delete(filter={"project": {"$eq": project_name}})
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Weaviate provider (cloud — requires weaviate-client + API key)
# ─────────────────────────────────────────────────────────────────────────────

class WeaviateProvider(VectorProvider):
    def __init__(self, config: dict):
        try:
            import weaviate
            from weaviate.classes.init import Auth
        except ImportError:
            raise ImportError(
                "Weaviate not installed. Run: pip install weaviate-client"
            )

        import os
        url = config.get("weaviate_url") or os.getenv("WEAVIATE_URL", "")
        api_key = config.get("weaviate_api_key") or os.getenv("WEAVIATE_API_KEY", "")

        if not url:
            raise ValueError("Weaviate URL not set. Add WEAVIATE_URL to .env or vector_store.weaviate_url in config.yaml")

        if api_key:
            self._client = weaviate.connect_to_weaviate_cloud(
                cluster_url=url,
                auth_credentials=Auth.api_key(api_key),
            )
        else:
            self._client = weaviate.connect_to_custom(
                http_host=url,
                http_port=8080,
                http_secure=url.startswith("https"),
                grpc_host=url.replace("https://", "").replace("http://", ""),
                grpc_port=50051,
            )

        self._collection_name = config.get("weaviate_collection", "MemoriaBooks")
        self._ensure_collection()
        self._embedder = _get_embedder()

    def _ensure_collection(self):
        """Create collection schema if it doesn't exist."""
        import weaviate.classes.config as wc

        if not self._client.collections.exists(self._collection_name):
            self._client.collections.create(
                name=self._collection_name,
                vectorizer_config=wc.Configure.Vectorizer.none(),
                properties=[
                    wc.Property(name="project", data_type=wc.DataType.TEXT),
                    wc.Property(name="section", data_type=wc.DataType.TEXT),
                    wc.Property(name="text", data_type=wc.DataType.TEXT),
                    wc.Property(name="chunk_id", data_type=wc.DataType.TEXT),
                ],
            )

    def index(self, project_name: str, chunks: List[Dict]):
        self.delete_project(project_name)

        collection = self._client.collections.get(self._collection_name)
        texts = [c["text"] for c in chunks]
        embeddings = self._embedder.encode(texts).tolist()

        with collection.batch.dynamic() as batch:
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                batch.add_object(
                    properties={
                        "project": project_name,
                        "section": chunk["section"],
                        "text": chunk["text"],
                        "chunk_id": f"{project_name}__{i}",
                    },
                    vector=emb,
                )

    def search(self, query: str, top_k: int) -> List[Dict]:
        import weaviate.classes.query as wq

        collection = self._client.collections.get(self._collection_name)
        query_emb = self._embedder.encode([query]).tolist()[0]

        results = collection.query.near_vector(
            near_vector=query_emb,
            limit=top_k,
            return_metadata=wq.MetadataQuery(distance=True),
        )

        hits = []
        for obj in results.objects:
            props = obj.properties
            score = 1 - (obj.metadata.distance or 0)  # cosine distance → similarity
            hits.append({
                "project": props.get("project", "unknown"),
                "section": props.get("section", ""),
                "text": props.get("text", ""),
                "score": round(score, 3),
            })
        return hits

    def delete_project(self, project_name: str):
        import weaviate.classes.query as wq

        try:
            collection = self._client.collections.get(self._collection_name)
            collection.data.delete_many(
                where=wq.Filter.by_property("project").equal(project_name)
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Shared embedder for cloud providers (they need explicit embeddings)
# ─────────────────────────────────────────────────────────────────────────────

_embedder_instance = None


def _get_embedder():
    """
    Get sentence-transformers embedder (lazy singleton).
    Cloud providers need explicit embeddings — ChromaDB handles its own.
    """
    global _embedder_instance
    if _embedder_instance is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedder_instance = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. Required for cloud vector stores.\n"
                "Run: pip install sentence-transformers"
            )
    return _embedder_instance


# ─────────────────────────────────────────────────────────────────────────────
# Provider factory — reads config and returns the right backend
# ─────────────────────────────────────────────────────────────────────────────

_provider_cache: Dict[str, VectorProvider] = {}


def _get_provider(books_dir: str) -> VectorProvider:
    """Get or create the configured vector store provider."""
    config = _load_vector_config()
    provider_name = config.get("provider", "chromadb")

    cache_key = f"{provider_name}:{books_dir}"
    if cache_key in _provider_cache:
        return _provider_cache[cache_key]

    if provider_name == "pinecone":
        provider = PineconeProvider(config)
    elif provider_name == "weaviate":
        provider = WeaviateProvider(config)
    else:
        provider = ChromaProvider(books_dir)

    _provider_cache[cache_key] = provider
    return provider


def _load_vector_config() -> dict:
    """Load vector_store config from config.yaml."""
    import yaml

    # Walk up from CWD to find config.yaml
    cfg_path = _find_config()
    if cfg_path and cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                cfg = yaml.safe_load(f) or {}
                return cfg.get("vector_store", {})
        except Exception:
            pass

    return {}


def _find_config() -> Optional[Path]:
    """Find config.yaml — project-level first, then user-level."""
    # Project-level: walk up from CWD
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "config.yaml"
        if candidate.exists():
            return candidate

    # User-level
    user_cfg = Path.home() / ".memoria" / "config.yaml"
    if user_cfg.exists():
        return user_cfg

    return None


def get_provider_info(books_dir: str = "books") -> dict:
    """Return info about the active vector store (for Settings UI)."""
    config = _load_vector_config()
    provider_name = config.get("provider", "chromadb")

    info = {
        "provider": provider_name,
        "status": "active",
    }

    if provider_name == "chromadb":
        db_path = Path(books_dir) / ".chromadb"
        info["local"] = True
        info["path"] = str(db_path)
        info["exists"] = db_path.exists()
    elif provider_name == "pinecone":
        info["local"] = False
        info["index"] = config.get("pinecone_index", "memoria")
        info["region"] = config.get("pinecone_region", "us-east-1")
    elif provider_name == "weaviate":
        info["local"] = False
        info["url"] = config.get("weaviate_url", "")
        info["collection"] = config.get("weaviate_collection", "MemoriaBooks")

    return info


# ─────────────────────────────────────────────────────────────────────────────
# Text processing (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Remove markdown noise before indexing."""
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # [text](url) → text
    text = re.sub(r'<[^>]+>', '', text)                    # <html tags>
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)            # ![images](url)
    text = re.sub(r'`{3}.*?`{3}', '', text, flags=re.DOTALL)  # code blocks
    text = re.sub(r'`([^`]+)`', r'\1', text)              # `inline code`
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # ### headings
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)        # **bold**
    text = re.sub(r'\*([^*]+)\*', r'\1', text)            # *italic*
    text = re.sub(r'_([^_]+)_', r'\1', text)              # _italic_
    text = re.sub(r'\n{3,}', '\n\n', text)                 # excess blank lines
    return text.strip()


def _split_into_sections(content: str, project_name: str) -> List[Dict]:
    """Split a Memory Bank into sections for granular retrieval."""
    chunks = []
    current_section = "overview"
    current_lines = []

    for line in content.splitlines():
        if line.startswith("## "):
            if current_lines:
                raw = "\n".join(current_lines).strip()
                text = f"[{project_name}] {current_section}\n" + _clean_text(raw)
                if len(text) > 50:
                    chunks.append({"section": current_section, "text": text})
            current_section = line.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Last section
    if current_lines:
        raw = "\n".join(current_lines).strip()
        text = f"[{project_name}] {current_section}\n" + _clean_text(raw)
        if len(text) > 50:
            chunks.append({"section": current_section, "text": text})

    return chunks
