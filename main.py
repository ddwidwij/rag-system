"""main.py — CLI 入口（build / query）"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from sentence_transformers import CrossEncoder, SentenceTransformer

from core.config import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_DB_DIR,
    DEFAULT_DOCS_DIR,
    DEFAULT_QUERY,
    EMBEDDING_MODEL_NAME,
    RERANK_MODEL_NAME,
    ZHIPU_BASE_URL,
)
from core.parsers import SUPPORTED_SUFFIXES
from core.store import retrieve, scan_docs_state, sync_collection_incremental
from core.rag_chain import generate_answer, rerank


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a simple RAG pipeline.")
    subparsers = parser.add_subparsers(dest="command", help="build: 构建向量库; query: 查询")

    # 公共参数
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db-dir", default=DEFAULT_DB_DIR)
    common.add_argument("--collection-name", default=DEFAULT_COLLECTION_NAME)

    # build 子命令
    build_parser = subparsers.add_parser("build", parents=[common], help="扫描文档并构建/更新向量库")
    build_parser.add_argument("--docs-dir", default=DEFAULT_DOCS_DIR)

    # query 子命令
    query_parser = subparsers.add_parser("query", parents=[common], help="查询知识库并生成答案")
    query_parser.add_argument("question", nargs="?", default=DEFAULT_QUERY, help="查询问题")
    query_parser.add_argument("--retrieve-top-k", type=int, default=5)
    query_parser.add_argument("--rerank-top-k", type=int, default=3)

    args = parser.parse_args()
    # 未指定子命令时走原来的完整流程（兼容旧用法）
    if args.command is None:
        args.command = "full"
        args.docs_dir = DEFAULT_DOCS_DIR
        args.question = DEFAULT_QUERY
        args.retrieve_top_k = 5
        args.rerank_top_k = 3
    return args


def run_build(args: argparse.Namespace, embedding_model: SentenceTransformer | None = None) -> None:
    docs_dir = Path(args.docs_dir)
    db_dir = Path(args.db_dir)
    if not docs_dir.exists():
        raise FileNotFoundError(f"Docs directory not found: {docs_dir}")

    print(f"Scanning documents from: {docs_dir} (支持格式: {', '.join(sorted(SUPPORTED_SUFFIXES))})")
    doc_state = scan_docs_state(docs_dir)
    if not doc_state:
        raise ValueError(f"No Markdown chunks found in: {docs_dir}")
    print(f"Detected {len(doc_state)} source documents")

    if embedding_model is None:
        print(f"Loading embedding model: {EMBEDDING_MODEL_NAME}")
        embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")

    print(f"Incrementally syncing persistent vector store in: {db_dir}")
    sync_collection_incremental(
        docs_dir=docs_dir,
        embedding_model=embedding_model,
        db_dir=db_dir,
        collection_name=args.collection_name,
    )
    print("Incremental build complete.")


def run_query(args: argparse.Namespace, embedding_model: SentenceTransformer | None = None) -> None:
    db_dir = Path(args.db_dir)
    query = args.question

    if embedding_model is None:
        print(f"Loading embedding model: {EMBEDDING_MODEL_NAME}")
        embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")

    client = chromadb.PersistentClient(path=str(db_dir))
    collection = client.get_or_create_collection(name=args.collection_name)

    print(f"Retrieving top {args.retrieve_top_k} chunks")
    retrieved_chunks = retrieve(
        collection=collection,
        embedding_model=embedding_model,
        query=query,
        top_k=args.retrieve_top_k,
    )

    print(f"Loading rerank model: {RERANK_MODEL_NAME}")
    cross_encoder = CrossEncoder(RERANK_MODEL_NAME)

    print(f"Reranking to top {args.rerank_top_k} chunks")
    reranked_chunks = rerank(
        cross_encoder=cross_encoder,
        query=query,
        retrieved_chunks=retrieved_chunks,
        top_k=args.rerank_top_k,
    )

    print("Generating answer with Zhipu glm-4-flash")
    zhipu_client = OpenAI(
        api_key=os.environ.get("ZHIPU_API_KEY"),
        base_url=ZHIPU_BASE_URL,
    )
    answer = generate_answer(zhipu_client, query, reranked_chunks)

    print("\nQuestion:")
    print(query)
    print("\nRetrieved Chunks:")
    for index, chunk in enumerate(retrieved_chunks):
        print(f"[{index}] {chunk}\n")
    print("Reranked Chunks:")
    for index, chunk in enumerate(reranked_chunks):
        print(f"[{index}] {chunk}\n")
    print("Answer:")
    print(answer)


def main() -> None:
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    load_dotenv()
    args = parse_args()

    if args.command == "build":
        run_build(args)
    elif args.command == "query":
        run_query(args)
    else:  # full（兼容旧用法，无子命令时）
        args.docs_dir = DEFAULT_DOCS_DIR
        print(f"Loading embedding model: {EMBEDDING_MODEL_NAME}")
        embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")
        run_build(args, embedding_model=embedding_model)
        run_query(args, embedding_model=embedding_model)


if __name__ == "__main__":
    main()
