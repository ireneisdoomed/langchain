"""Chain for question-answering against a vector database."""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Extra, Field, root_validator

from langchain.chains.base import Chain
from langchain.chains.combine_documents.base import BaseCombineDocumentsChain
from langchain.chains.combine_documents.stuff import StuffDocumentsChain
from langchain.chains.llm import LLMChain
from langchain.chains.question_answering import load_qa_chain
from langchain.chains.question_answering.stuff_prompt import PROMPT_SELECTOR
from langchain.prompts import PromptTemplate
from langchain.schema import BaseLanguageModel, BaseRetriever, Document
from langchain.vectorstores.base import VectorStore


class BaseRetrievalQA(Chain, BaseModel):
    combine_documents_chain: BaseCombineDocumentsChain
    """Chain to use to combine the documents."""
    input_key: str = "query"  #: :meta private:
    output_key: str = "result"  #: :meta private:
    return_source_documents: bool = False
    """Return the source documents."""

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid
        arbitrary_types_allowed = True
        allow_population_by_field_name = True

    @property
    def input_keys(self) -> List[str]:
        """Return the input keys.

        :meta private:
        """
        return [self.input_key]

    @property
    def output_keys(self) -> List[str]:
        """Return the output keys.

        :meta private:
        """
        _output_keys = [self.output_key]
        if self.return_source_documents:
            _output_keys = _output_keys + ["source_documents"]
        return _output_keys

    @classmethod
    def from_llm(
        cls,
        llm: BaseLanguageModel,
        prompt: Optional[PromptTemplate] = None,
        **kwargs: Any,
    ) -> BaseRetrievalQA:
        """Initialize from LLM."""
        _prompt = prompt or PROMPT_SELECTOR.get_prompt(llm)
        llm_chain = LLMChain(llm=llm, prompt=_prompt)
        document_prompt = PromptTemplate(
            input_variables=["page_content"], template="Context:\n{page_content}"
        )
        combine_documents_chain = StuffDocumentsChain(
            llm_chain=llm_chain,
            document_variable_name="context",
            document_prompt=document_prompt,
        )

        return cls(combine_documents_chain=combine_documents_chain, **kwargs)

    @classmethod
    def from_chain_type(
        cls,
        llm: BaseLanguageModel,
        chain_type: str = "stuff",
        chain_type_kwargs: Optional[dict] = None,
        **kwargs: Any,
    ) -> BaseRetrievalQA:
        """Load chain from chain type."""
        _chain_type_kwargs = chain_type_kwargs or {}
        combine_documents_chain = load_qa_chain(
            llm, chain_type=chain_type, **_chain_type_kwargs
        )
        return cls(combine_documents_chain=combine_documents_chain, **kwargs)

    @abstractmethod
    def _get_docs(self, question: str) -> List[Document]:
        """Get documents to do question answering over."""

    def _call(self, inputs: Dict[str, str]) -> Dict[str, Any]:
        """Run get_relevant_text and llm on input query.

        If chain has 'return_source_documents' as 'True', returns
        the retrieved documents as well under the key 'source_documents'.

        Example:
        .. code-block:: python

        res = indexqa({'query': 'This is my query'})
        answer, docs = res['result'], res['source_documents']
        """
        question = inputs[self.input_key]

        docs = self._get_docs(question)
        answer, _ = self.combine_documents_chain.combine_docs(docs, question=question)

        if self.return_source_documents:
            return {self.output_key: answer, "source_documents": docs}
        else:
            return {self.output_key: answer}


class RetrievalQA(BaseRetrievalQA, BaseModel):
    """Chain for question-answering against an index.

    Example:
        .. code-block:: python

            from langchain.llms import OpenAI
            from langchain.chains import RetrievalQA
            from langchain.faiss import FAISS
            vectordb = FAISS(...)
            retrievalQA = RetrievalQA.from_llm(llm=OpenAI(), retriever=vectordb)

    """

    retriever: BaseRetriever = Field(exclude=True)

    def _get_docs(self, question: str) -> List[Document]:
        return self.retriever.get_relevant_texts(question)


class VectorDBQA(BaseRetrievalQA, BaseModel):
    """Chain for question-answering against a vector database."""

    vectorstore: VectorStore = Field(exclude=True, alias="vectorstore")
    """Vector Database to connect to."""
    k: int = 4
    """Number of documents to query for."""
    search_type: str = "similarity"
    """Search type to use over vectorstore. `similarity` or `mmr`."""
    search_kwargs: Dict[str, Any] = Field(default_factory=dict)
    """Extra search args."""

    @root_validator()
    def validate_search_type(cls, values: Dict) -> Dict:
        """Validate search type."""
        if "search_type" in values:
            search_type = values["search_type"]
            if search_type not in ("similarity", "mmr"):
                raise ValueError(f"search_type of {search_type} not allowed.")
        return values

    def _get_docs(self, question: str) -> List[Document]:
        if self.search_type == "similarity":
            docs = self.vectorstore.similarity_search(
                question, k=self.k, **self.search_kwargs
            )
        elif self.search_type == "mmr":
            docs = self.vectorstore.max_marginal_relevance_search(
                question, k=self.k, **self.search_kwargs
            )
        else:
            raise ValueError(f"search_type of {self.search_type} not allowed.")
        return docs

    @property
    def _chain_type(self) -> str:
        """Return the chain type."""
        return "vector_db_qa"
