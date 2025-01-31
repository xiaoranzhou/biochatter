from biochatter.vectorstore import (
    DocumentEmbedder,
    DocumentReader,
)
import os
import pytest
from .conftest import calculate_test_score, RESULT_FILES

FILE_PATH = next(
    (s for s in RESULT_FILES if "vectorstore" in s),
    None,
)

# TODO: make vectorstore / retriever a part of the matrix

# setup milvus connection
if os.getenv("DEVCONTAINER"):
    _HOST = "milvus-standalone"
else:
    _HOST = "127.0.0.1"
_PORT = "19530"

EMBEDDING_MODELS = [
    "text-embedding-ada-002",
]
CHUNK_SIZES = [50, 1000]


@pytest.mark.parametrize("model", EMBEDDING_MODELS)
@pytest.mark.parametrize("chunk_size", CHUNK_SIZES)
def test_retrieval_augmented_generation(model, chunk_size):
    pdf_path = "test/bc_summary.pdf"
    with open(pdf_path, "rb") as f:
        doc_bytes = f.read()

    reader = DocumentReader()
    doc = reader.document_from_pdf(doc_bytes)

    doc_ids = []
    rag_agent = DocumentEmbedder(model=model, chunk_size=chunk_size)
    rag_agent.connect()
    doc_ids.append(rag_agent.save_document(doc))

    query = "What is BioCypher?"
    results = rag_agent.similarity_search(query)
    correct = ["BioCypher" in result.page_content for result in results]

    # delete embeddings
    [rag_agent.database_host.remove_document(doc_id) for doc_id in doc_ids]

    # record sum in CSV file
    with open(FILE_PATH, "a") as f:
        f.write(f"{model},{chunk_size},{calculate_test_score(correct)}\n")
