# ChatGSE retrieval augmented generation (RAG) functionality
# split text
# connect to vector db
# do similarity search
# return x closes matches for in-context learning

from typing import List, Optional, Dict

from langchain.schema import Document
import openai
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.embeddings import XinferenceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.document_loaders import TextLoader
from langchain.vectorstores import Milvus

# To mock Client in tests, we need to import it in advance
from xinference.client import Client

import fitz  # this is PyMuPDF (PyPI pymupdf package, not fitz)
from transformers import GPT2TokenizerFast

from biochatter.vectorstore_host import VectorDatabaseHostMilvus


class DocumentEmbedder:
    def __init__(
        self,
        use_prompt: bool = True,
        used: bool = False,
        online: bool = False,
        chunk_size: int = 1000,
        chunk_overlap: int = 0,
        split_by_characters: bool = True,
        separators: Optional[list] = None,
        n_results: int = 3,
        model: Optional[str] = "text-embedding-ada-002",
        vector_db_vendor: Optional[str] = None,
        connection_args: Optional[dict] = None,
        embedding_collection_name: Optional[str] = None,
        metadata_collection_name: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embeddings: Optional[OpenAIEmbeddings | XinferenceEmbeddings] = None,
    ) -> None:
        """
        Class that handles the retrieval augmented generation (RAG) functionality
        of BioChatter. It splits text into chunks, embeds them, and stores them in
        a vector database. It can then be used to do similarity search on the
        database.

        Args:

            use_prompt (bool, optional): whether to use RAG (ChatGSE setting).
            Defaults to True.

            used (bool, optional): whether RAG has been used (ChatGSE setting).
            Defaults to False.

            online (bool, optional): whether we are running ChatGSE online.
            Defaults to False.

            chunk_size (int, optional): size of chunks to split text into.
            Defaults to 1000.

            chunk_overlap (int, optional): overlap between chunks. Defaults to 0.

            split_by_characters (bool, optional): whether to split by characters
            or tokens. Defaults to True.

            separators (Optional[list], optional): list of separators to use when
            splitting by characters. Defaults to [" ", ",", "\n"].

            n_results (int, optional): number of results to return from
            similarity search. Defaults to 3.

            model (Optional[str], optional): name of model to use for embeddings.
            Defaults to 'text-embedding-ada-002'.

            vector_db_vendor (Optional[str], optional): name of vector database
            to use. Defaults to Milvus.

            connection_args (Optional[dict], optional): arguments to pass to
            vector database connection. Defaults to None.

            embedding_collection_name (Optional[str], optional): name of
            collection to store embeddings in. Defaults to 'DocumentEmbeddings'.

            metadata_collection_name (Optional[str], optional): name of
            collection to store metadata in. Defaults to 'DocumentMetadata'.

            api_key (Optional[str], optional): OpenAI API key. Defaults to None.

            base_url (Optional[str], optional): base url of OpenAI API.

            embeddings (Optional[OpenAIEmbeddings | XinferenceEmbeddings],
            optional): Embeddings object to use. Defaults to OpenAI.

        """
        self.use_prompt = use_prompt
        self.used = used
        self.online = online
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or [" ", ",", "\n"]
        self.n_results = n_results
        self.split_by_characters = split_by_characters
        self.model_name = model

        # TODO API Key handling to central config?
        if base_url:
            openai.api_base = base_url

        if embeddings:
            self.embeddings = embeddings
        else:
            if not self.online:
                self.embeddings = OpenAIEmbeddings(
                    openai_api_key=api_key, model=model
                )
            else:
                self.embeddings = None

        # connection arguments
        self.connection_args = connection_args or {
            "host": "127.0.0.1",
            "port": "19530",
        }
        self.embedding_collection_name = embedding_collection_name
        self.metadata_collection_name = metadata_collection_name

        # TODO: vector db selection
        self.vector_db_vendor = vector_db_vendor or "milvus"
        # instantiate VectorDatabaseHost
        self.database_host = None
        self._init_database_host()

    def _set_embeddings(self, embeddings):
        print("setting embedder")
        self.embeddings = embeddings

    def _init_database_host(self):
        if self.vector_db_vendor == "milvus":
            self.database_host = VectorDatabaseHostMilvus(
                embedding_func=self.embeddings,
                connection_args=self.connection_args,
                embedding_collection_name=self.embedding_collection_name,
                metadata_collection_name=self.metadata_collection_name,
            )
        else:
            raise NotImplementedError(self.vector_db_vendor)

    def set_chunk_siue(self, chunk_size: int) -> None:
        self.chunk_size = chunk_size

    def set_chunk_overlap(self, chunk_overlap: int) -> None:
        self.chunk_overlap = chunk_overlap

    def set_separators(self, separators: list) -> None:
        self.separators = separators

    def _characters_splitter(self) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=self.separators,
        )

    def _tokens_splitter(self) -> RecursiveCharacterTextSplitter:
        DEFAULT_OPENAI_MODEL = "gpt-3.5-turbo"
        HUGGINGFACE_MODELS = ["bigscience/bloom"]
        if self.model_name and self.model_name in HUGGINGFACE_MODELS:
            tokenizer = GPT2TokenizerFast.from_pretrained("gpt2")
            return RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
                tokenizer,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                separators=self.separators,
            )
        else:
            return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                encoding_name="",
                model_name=DEFAULT_OPENAI_MODEL
                if not self.model_name
                else self.model_name,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                separators=self.separators,
            )

    def _text_splitter(self) -> RecursiveCharacterTextSplitter:
        return (
            self._characters_splitter()
            if self.split_by_characters
            else self._tokens_splitter()
        )

    def save_document(self, doc: List[Document]) -> str:
        """
        This function saves document to the vector database
        Args:
            doc List[Document]: document content, read with DocumentReader load_document(),
                or document_from_pdf(), document_from_txt()
        Returns:
            str: document id, which can be used to remove an uploaded document with remove_document()
        """
        splitted = self._split_document(doc)
        return self._store_embeddings(splitted)

    def _split_document(self, document: List[Document]) -> List[Document]:
        text_splitter = self._text_splitter()
        return text_splitter.split_documents(document)

    def _store_embeddings(self, doc: List[Document]) -> str:
        return self.database_host.store_embeddings(documents=doc)

    def similarity_search(self, query: str, k: int = 3):
        """
        Returns top n closest matches to query from vector store.

        Args:
            query (str): query string

            k (int, optional): number of closest matches to return. Defaults to
            3.

        """
        return self.database_host.similarity_search(
            query=query, k=k or self.n_results
        )

    def connect(self) -> None:
        self.database_host.connect()

    def get_all_documents(self) -> List[Dict]:
        return self.database_host.get_all_documents()

    def remove_document(self, doc_id: str) -> None:
        self.database_host.remove_document(doc_id)


class XinferenceDocumentEmbedder(DocumentEmbedder):
    def __init__(
        self,
        use_prompt: bool = True,
        used: bool = False,
        chunk_size: int = 1000,
        chunk_overlap: int = 0,
        split_by_characters: bool = True,
        separators: Optional[list] = None,
        n_results: int = 3,
        model: Optional[str] = "auto",
        vector_db_vendor: Optional[str] = None,
        connection_args: Optional[dict] = None,
        embedding_collection_name: Optional[str] = None,
        metadata_collection_name: Optional[str] = None,
        api_key: Optional[str] = "none",
        base_url: Optional[str] = None,
    ):
        """
        Extension of the DocumentEmbedder class that uses Xinference for
        embeddings.

        Args:

            use_prompt (bool, optional): whether to use RAG (ChatGSE setting).

            used (bool, optional): whether RAG has been used (ChatGSE setting).

            chunk_size (int, optional): size of chunks to split text into.

            chunk_overlap (int, optional): overlap between chunks.

            split_by_characters (bool, optional): whether to split by characters
            or tokens.

            separators (Optional[list], optional): list of separators to use when
            splitting by characters.

            n_results (int, optional): number of results to return from
            similarity search.

            model (Optional[str], optional): name of model to use for embeddings.
            Can be "auto" to use the first available model.

            vector_db_vendor (Optional[str], optional): name of vector database
            to use.

            connection_args (Optional[dict], optional): arguments to pass to
            vector database connection.

            embedding_collection_name (Optional[str], optional): name of
            collection to store embeddings in.

            metadata_collection_name (Optional[str], optional): name of
            collection to store metadata in.

            api_key (Optional[str], optional): Xinference API key.

            base_url (Optional[str], optional): base url of Xinference API.

        """
        self.model_name = model
        self.client = Client(base_url=base_url)
        self.models = {}
        self.load_models()

        if self.model_name is None or self.model_name == "auto":
            self.model_name = self.list_models_by_type("embedding")[0]
        self.model_uid = self.models[self.model_name]["id"]

        super().__init__(
            use_prompt=use_prompt,
            used=used,
            online=True,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            split_by_characters=split_by_characters,
            separators=separators,
            n_results=n_results,
            model=model,
            vector_db_vendor=vector_db_vendor,
            connection_args=connection_args,
            embedding_collection_name=embedding_collection_name,
            metadata_collection_name=metadata_collection_name,
            api_key=api_key,
            base_url=base_url,
            embeddings=XinferenceEmbeddings(
                server_url=base_url, model_uid=self.model_uid
            ),
        )

    def load_models(self):
        """
        Return all models that are currently available on the Xinference server.

        Returns:
            dict: dict of models
        """
        for id, model in self.client.list_models().items():
            model["id"] = id
            self.models[model["model_name"]] = model

    def list_models_by_type(self, type: str):
        """
        Return all models of a certain type that are currently available on the
        Xinference server.

        Args:
            type (str): type of model to list (e.g. "embedding", "chat")

        Returns:
            List[str]: list of model names
        """
        names = []
        for name, model in self.models.items():
            if "model_ability" in model:
                if type in model["model_ability"]:
                    names.append(name)
            elif model["model_type"] == type:
                names.append(name)
        return names


class DocumentReader:
    def load_document(self, path: str) -> List[Document]:
        """
        Loads a document from a path; accepts txt and pdf files. Txt files are
        loaded as-is, pdf files are converted to text using fitz.

        Args:
            path (str): path to document

        Returns:
            List[Document]: list of documents
        """
        if path.endswith(".txt"):
            loader = TextLoader(path)
            return loader.load()

        elif path.endswith(".pdf"):
            doc = fitz.open(path)
            text = ""
            for page in doc:
                text += page.get_text()

            meta = {k: v for k, v in doc.metadata.items() if v}
            meta.update({"source": path})

            return [
                Document(
                    page_content=text,
                    metadata=meta,
                )
            ]

    def document_from_pdf(self, pdf: bytes) -> List[Document]:
        """
        Receive a byte representation of a pdf file and return a list of Documents
        with metadata.

        Args:
            pdf (bytes): byte representation of pdf file

        Returns:
            List[Document]: list of documents
        """
        doc = fitz.open(stream=pdf, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()

        meta = {k: v for k, v in doc.metadata.items() if v}
        meta.update({"source": "pdf"})

        return [
            Document(
                page_content=text,
                metadata=meta,
            )
        ]

    def document_from_txt(self, txt: bytes) -> List[Document]:
        """
        Receive a byte representation of a txt file and return a list of Documents
        with metadata.

        Args:
            txt (bytes): byte representation of txt file

        Returns:
            List[Document]: list of documents
        """
        meta = {"source": "txt"}
        return [
            Document(
                page_content=txt,
                metadata=meta,
            )
        ]
