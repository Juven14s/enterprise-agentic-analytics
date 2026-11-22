import re
import unicodedata
from importlib.resources import files

import faiss
import numpy as np
from pydantic import BaseModel, model_validator


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


class Table(BaseModel):
    name: str
    description: str
    columns: dict[str, str]


class Definition(BaseModel):
    id: str
    tables: list[str]
    definition: str


class Catalog(BaseModel):
    tables: list[Table]
    relations: list[Definition] = []
    metrics: list[Definition] = []

    @model_validator(mode="after")
    def validate_references(self):
        names = {table.name for table in self.tables}
        ids = [item.id for item in self.relations + self.metrics]
        if not names or len(names) != len(self.tables) or len(set(ids)) != len(ids):
            raise ValueError("The catalog needs unique tables and definition identifiers.")
        if any(not set(item.tables) <= names for item in self.relations + self.metrics):
            raise ValueError("The catalog references an unknown table.")
        return self

    @classmethod
    def retail(cls):
        return cls.model_validate_json(
            files("analytics_agent").joinpath("data/catalog.json").read_text(encoding="utf-8")
        )


class ContextDocument(BaseModel):
    id: str
    text: str
    schema_sql: str | None = None


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(value))


def _sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SchemaRetriever:
    """Local FAISS cosine search over lexical vectors, with exact catalog references."""

    def __init__(self, catalog: Catalog):
        table_docs = [
            ContextDocument(
                id=f"table:{table.name}",
                schema_sql=f"{_sql_identifier(table.name)} ("
                + ", ".join(_sql_identifier(name) for name in table.columns)
                + ")",
                text=f"Table {_sql_identifier(table.name)} — {table.description}\n"
                + "\n".join(
                    f"{_sql_identifier(name)}: {description}"
                    for name, description in table.columns.items()
                ),
            )
            for table in catalog.tables
        ]
        definition_docs = [
            ContextDocument(id=f"{kind}:{item.id}", text=item.definition)
            for kind, definitions in (("relation", catalog.relations), ("metric", catalog.metrics))
            for item in definitions
        ]
        self.documents = table_docs + definition_docs
        self.dependencies = {
            f"{kind}:{item.id}": item.tables
            for kind, definitions in (("relation", catalog.relations), ("metric", catalog.metrics))
            for item in definitions
        }
        self.table_docs = dict(
            zip((table.name for table in catalog.tables), table_docs, strict=True)
        )
        terms = sorted({token for doc in self.documents for token in _tokens(doc.text)})
        self.vocabulary = {token: index for index, token in enumerate(terms)}
        matrix = self._encode([doc.text for doc in self.documents])
        document_frequency = np.count_nonzero(matrix, axis=0)
        self.weights = (1 + np.log((1 + len(self.documents)) / (1 + document_frequency))).astype(
            np.float32
        )
        matrix *= self.weights
        faiss.normalize_L2(matrix)
        self.index = faiss.IndexFlatIP(matrix.shape[1])
        self.index.add(matrix)

    def _encode(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), max(1, len(self.vocabulary))), dtype=np.float32)
        for row, value in enumerate(texts):
            for token in set(_tokens(value)):
                if token in self.vocabulary:
                    matrix[row, self.vocabulary[token]] = 1
        return matrix

    def retrieve(self, question: str, top_k: int = 5) -> list[ContextDocument]:
        query = self._encode([question]) * self.weights
        faiss.normalize_L2(query)
        scores, indexes = self.index.search(query, min(top_k, len(self.documents)))
        selected = {
            self.documents[index].id: self.documents[index]
            for score, index in zip(scores[0], indexes[0], strict=True)
            if score > 0 and index >= 0
        }
        for doc_id in list(selected):
            for name in self.dependencies.get(doc_id, []):
                document = self.table_docs[name]
                selected.setdefault(document.id, document)
        if len(self.table_docs) <= top_k:
            for document in self.table_docs.values():
                selected.setdefault(document.id, document)
        return list(selected.values())
