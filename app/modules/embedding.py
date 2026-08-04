"""
Embedding module
----------------
Two things live in this single file, matching the flat modules/ layout
used in this repo (embedding.py sits directly under app/modules/, no
sub-folder):

1. Embedder
   Offline step (run once, separately from the live chat process):
   converts the Job Description PDF into a Chroma vector store.

2. build_search_job_description_tool
   Returns the @tool that gets passed into Agent(info_tools=[...]) so the
   Info Advisor can query that vector store at runtime - this is the RAG
   piece described in info_advisor.md ("Utilize Context Tools: Query the
   provided Chroma vector database tools ... before answering").
"""

import json
import os
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma


class Embedder:
    """
    Handles building and loading the Chroma vector store used by the
    Info Advisor.

    Two separate concerns, on purpose:
    * build_vectorstore          -> run OFFLINE, once, whenever the job description changes
    * load_existing_vectorstore  -> run at RUNTIME, cheap, just opens the persisted store
    """

    def __init__(
        self,
        api_key=None,
        embedding_model="text-embedding-3-small",
        base_url=None,
        persist_directory="chroma_db",
        collection_name="job_description",
        chunk_size=300,   # in TOKENS, not characters (see splitter below)
        chunk_overlap=30,
    ):
        # fall back to OPENAI_API_KEY from the environment (.env) if not passed explicitly
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "No API key provided and OPENAI_API_KEY is not set in the environment."
            )

        self.persist_directory = persist_directory
        self.collection_name = collection_name

        self.embeddings = OpenAIEmbeddings(
            api_key=api_key,
            model=embedding_model,
            base_url=base_url,
        )

        # Split by TOKEN count (via tiktoken) rather than raw character count,
        # so chunk_size maps accurately to what the model actually sees.
        # Requires the `tiktoken` package.
        self.splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def _load_and_split(self, pdf_path):
        """Load a PDF and split it into overlapping text chunks."""
        pages = PyPDFLoader(pdf_path).load()
        chunks = self.splitter.split_documents(pages)

        # Sanity check - cheap, and catches an empty/unreadable PDF before
        # spending API calls embedding nothing useful.
        print(f"[Embedder] Loaded '{pdf_path}': {len(pages)} page(s) -> {len(chunks)} chunk(s).")
        if chunks:
            preview = chunks[0].page_content[:200].replace("\n", " ")
            print(f"[Embedder] First chunk preview: {preview}...")
        else:
            print("[Embedder] WARNING: no chunks produced - check the PDF content.")

        return chunks

    def build_vectorstore(self, pdf_path, overwrite=True):
        """
        OFFLINE step. Reads the PDF at pdf_path, chunks it, embeds every
        chunk, and persists everything to disk under self.persist_directory.

        Run this once whenever "Python Developer Job Description.pdf"
        changes - not on every chat turn.

        overwrite=True (default) clears any previously stored chunks for
        this collection first, so re-running on an updated PDF doesn't
        leave stale/duplicate chunks behind. Only clears this collection
        (self.collection_name), not the whole persist_directory - safe even
        if other collections share the same directory.
        """
        if overwrite:
            try:
                self.load_existing_vectorstore().delete_collection()
                print(f"[Embedder] Cleared existing collection '{self.collection_name}'.")
            except Exception:
                pass  # nothing existed yet for this collection - nothing to clear

        chunks = self._load_and_split(pdf_path)

        vectorstore = Chroma.from_documents(
            documents=chunks,
            embedding=self.embeddings,
            collection_name=self.collection_name,
            persist_directory=self.persist_directory,
        )
        print(f"[Embedder] Stored {len(chunks)} chunk(s) in '{self.persist_directory}' "
              f"(collection='{self.collection_name}').")
        return vectorstore

    def load_existing_vectorstore(self):
        """
        RUNTIME step. Opens the already-built Chroma store from disk.
        Cheap - does not re-embed anything, just points at the persisted data.
        """
        return Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory,
        )


def build_search_job_description_tool(api_key=None, base_url=None, persist_directory="chroma_db", k=3):
    """
    Factory that returns a ready-to-use @tool bound to a specific Chroma
    store. Built as a factory (instead of a bare @tool function) so
    api_key / persist_directory can be injected at Agent construction time -
    the same pattern as choose_advisor being defined inside Agent.__init__
    in agents.py.

    Usage (e.g. in app/main.py, when constructing the Agent):
        from app.modules.embedding import build_search_job_description_tool
        info_tool = build_search_job_description_tool()
        agent = Agent(..., info_tools=[info_tool])
    """
    embedder = Embedder(api_key=api_key, base_url=base_url, persist_directory=persist_directory)
    vectorstore = embedder.load_existing_vectorstore()

    @tool
    def search_job_description(query: str) -> str:
        """
        ## query parameter
        A natural-language question about the Python Developer position
        (e.g. "is this role remote?", "what's the required experience?").

        ## Returns
        The top matching chunks of the job description, as a JSON formatted
        string. Use these chunks as the ONLY source of truth when answering
        candidate questions - do not invent details that aren't returned here.

        ## Example
        Input: {"query": "is this position remote?"}
        Output: {"results": ["This is a hybrid position, 2 days/week in the Tel Aviv office...", ...]}
        """
        try:
            docs = vectorstore.similarity_search(query, k=k)
            return json.dumps({"results": [d.page_content for d in docs]})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return search_job_description


# ---- run this file directly to (re)build the vector store ----
# Example: python -m app.modules.embedding --pdf "Python Developer Job Description.pdf"
# (reads OPENAI_API_KEY from .env in the current working directory by default)
if __name__ == "__main__":
    import argparse

    load_dotenv()  # reads .env in the current working directory into os.environ

    parser = argparse.ArgumentParser(description="Build the Chroma vector store from the job description PDF.")
    parser.add_argument("--pdf", required=True, help="Path to the job description PDF")
    parser.add_argument("--api_key", default=None, help="OpenAI API key (defaults to OPENAI_API_KEY env var)")
    parser.add_argument("--persist_directory", default="chroma_db")
    parser.add_argument("--no-overwrite", action="store_true", help="Add to the existing collection instead of clearing it first")
    args = parser.parse_args()

    embedder = Embedder(api_key=args.api_key, persist_directory=args.persist_directory)
    embedder.build_vectorstore(args.pdf, overwrite=not args.no_overwrite)