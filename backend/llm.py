import os
import base64
import re
import json
import mimetypes
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document


# ============================================================
# PROJECT PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

ENV_FILE = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"
CHUNKS_FILE = DATA_DIR / "chunks.json"


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv(dotenv_path=ENV_FILE)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# OpenAI sirf answer generation / image OCR ke liye (embedding ke liye nahi)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Gemini (free API key: https://aistudio.google.com/apikey)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# True  = har naye upload par purane documents hat jate hain (sirf latest document par sawal)
# False = saare uploaded documents ek saath search hote hain
SINGLE_DOCUMENT_MODE = True


def ask_gemini(prompt, image_bytes=None, mime_type=None):
    """Gemini ko text (ya text + image) bhejna (REST API, extra package nahi chahiye)."""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )

    parts = [{"text": prompt}]

    if image_bytes is not None:
        parts.append({
            "inline_data": {
                "mime_type": mime_type or "image/png",
                "data": base64.b64encode(image_bytes).decode("utf-8")
            }
        })

    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY
        },
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0.2}
        },
        timeout=90
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Gemini error {response.status_code}: {response.text[:300]}"
        )

    data = response.json()

    out_parts = data["candidates"][0]["content"]["parts"]

    return "".join(p.get("text", "") for p in out_parts).strip()


def short_extract(question, retrieved, max_sentences=3):
    """
    Bina AI ke: retrieved text mein se question se sabse milte-julte
    2-3 sentences nikalta hai (poora chunk dikhane ki jagah).
    """

    sentences = []

    for text in retrieved:
        for part in re.split(r"(?<=[.!?])\s+|\n+", text):
            part = part.strip()
            if len(part) > 20:
                sentences.append(part)

    if not sentences:
        return "\n\n".join(retrieved)[:500]

    try:
        vectorizer = TfidfVectorizer(lowercase=True, sublinear_tf=True)
        matrix = vectorizer.fit_transform(sentences)
        scores = linear_kernel(
            vectorizer.transform([question]), matrix
        ).flatten()
    except ValueError:
        return " ".join(sentences[:max_sentences])

    best = sorted(
        scores.argsort()[::-1][:max_sentences]
    )

    return " ".join(sentences[i] for i in best if scores[i] > 0) \
        or " ".join(sentences[:max_sentences])



# ============================================================
# Q&A ASSISTANT
# ============================================================

class QAAssistant:

    def __init__(self):

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Har chunk: {"text": ..., "source": ..., "chunk_index": ...}
        self.chunks = []

        if CHUNKS_FILE.exists():
            try:
                with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
                    self.chunks = json.load(f)
            except Exception as e:
                print("Could not read saved chunks:", e)
                self.chunks = []

        print(f"Assistant ready. Saved chunks: {len(self.chunks)}")


    def _save_chunks(self):

        with open(CHUNKS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.chunks, f, ensure_ascii=False)


    # ========================================================
    # LOAD PDF
    # ========================================================

    def _load_pdf(self, file_path):

        loader = PyPDFLoader(file_path)

        return loader.load()


    # ========================================================
    # LOAD TXT
    # ========================================================

    def _load_txt(self, file_path):

        try:
            loader = TextLoader(file_path, encoding="utf-8")
            return loader.load()

        except Exception:
            loader = TextLoader(file_path, encoding="latin-1")
            return loader.load()


    # ========================================================
    # LOAD DOCX
    # ========================================================

    def _load_docx(self, file_path):

        from docx import Document as DocxDocument

        doc = DocxDocument(file_path)

        text_parts = []

        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if text:
                text_parts.append(text)

        for table in doc.tables:
            for row in table.rows:
                row_text = []
                for cell in row.cells:
                    cell_text = cell.text.strip()
                    if cell_text:
                        row_text.append(cell_text)
                if row_text:
                    text_parts.append(" | ".join(row_text))

        full_text = "\n".join(text_parts)

        if not full_text.strip():
            return []

        return [
            Document(
                page_content=full_text,
                metadata={"source": str(file_path)}
            )
        ]


    # ========================================================
    # IMAGE -> TEXT (OpenAI vision, credits chahiye)
    # ========================================================

    def _load_image(self, file_path):

        mime_type, _ = mimetypes.guess_type(file_path)

        if not mime_type:
            mime_type = "image/png"

        with open(file_path, "rb") as image_file:
            image_bytes = image_file.read()

        instruction = (
            "Look at this image carefully.\n"
            "1. First, extract ALL readable text exactly as written.\n"
            "2. Then, on a new line starting with 'IMAGE DESCRIPTION:', "
            "describe in 2-4 sentences what the image shows "
            "(objects, people, scene, charts, diagrams, colors).\n"
            "If the image has no text, give only the description."
        )

        extracted_text = ""

        # ----------------------------------------------------
        # 1) Gemini (free)
        # ----------------------------------------------------

        if GEMINI_API_KEY:
            try:
                extracted_text = ask_gemini(
                    instruction,
                    image_bytes=image_bytes,
                    mime_type=mime_type
                )
            except Exception as e:
                print("Gemini image extraction failed:", e)
                if client is None:
                    raise RuntimeError(f"Gemini image extraction failed: {e}")

        # ----------------------------------------------------
        # 2) OpenAI (agar Gemini nahi chala)
        # ----------------------------------------------------

        if not extracted_text and client is not None:

            data_url = (
                f"data:{mime_type};base64,"
                + base64.b64encode(image_bytes).decode("utf-8")
            )

            response = client.responses.create(
                model="gpt-4o-mini",
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": instruction},
                            {"type": "input_image", "image_url": data_url}
                        ]
                    }
                ]
            )

            extracted_text = response.output_text.strip()

        print("Extracted image text (first 300 chars):", extracted_text[:300])

        if not GEMINI_API_KEY and client is None:
            raise RuntimeError(
                "Image upload needs GEMINI_API_KEY (free) or OPENAI_API_KEY in .env file."
            )

        if not extracted_text:
            return []

        return [
            Document(
                page_content=extracted_text,
                metadata={"source": str(file_path)}
            )
        ]


    # ========================================================
    # LOAD DOCUMENT BASED ON EXTENSION
    # ========================================================

    def _load_document(self, file_path):

        path = Path(file_path)
        extension = path.suffix.lower()

        if extension == ".pdf":
            return self._load_pdf(str(path))

        elif extension == ".txt":
            return self._load_txt(str(path))

        elif extension == ".docx":
            return self._load_docx(str(path))

        elif extension in [".jpg", ".jpeg", ".png"]:
            return self._load_image(str(path))

        else:
            raise ValueError(f"Unsupported file type: {extension}")


    # ========================================================
    # LOAD AND INDEX DOCUMENT
    # ========================================================

    def load_and_index_document(self, file_path):

        print()
        print("=" * 60)
        print("PROCESSING DOCUMENT")
        print("=" * 60)
        print(f"File: {file_path}")

        documents = self._load_document(file_path)

        if not documents:
            raise ValueError("No readable text was found in the document.")

        print(f"Extracted documents: {len(documents)}")

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100
        )

        chunks = text_splitter.split_documents(documents)

        if not chunks:
            raise ValueError("No text chunks were created from the document.")

        print(f"Created chunks: {len(chunks)}")

        if SINGLE_DOCUMENT_MODE:
            # Naya document aaya to purane saare documents hata do
            self.chunks = []
        else:
            # Same file dobara upload ho to sirf uske purane chunks hata do
            self.chunks = [
                c for c in self.chunks if c["source"] != str(file_path)
            ]

        for i, chunk in enumerate(chunks):
            self.chunks.append({
                "text": chunk.page_content,
                "source": str(file_path),
                "chunk_index": i
            })

        self._save_chunks()

        print("Document indexed successfully.")
        print("=" * 60)

        return (
            f"Document processed successfully. "
            f"{len(chunks)} chunks indexed."
        )


    # ========================================================
    # ANSWER QUESTION
    # ========================================================

    def answer_question(self, question):

        if not self.chunks:
            return "Please upload a document first."

        # ----------------------------------------------------
        # Search relevant chunks (TF-IDF, no model needed)
        # ----------------------------------------------------

        texts = [c["text"] for c in self.chunks]

        try:
            vectorizer = TfidfVectorizer(
                lowercase=True,
                ngram_range=(1, 2),
                sublinear_tf=True
            )
            matrix = vectorizer.fit_transform(texts)
            query_vec = vectorizer.transform([question])
            scores = linear_kernel(query_vec, matrix).flatten()
        except ValueError:
            return "I don't know based on the provided documents."

        top_indexes = scores.argsort()[::-1][:4]

        retrieved = [
            texts[i] for i in top_indexes if scores[i] > 0
        ]

        if not retrieved:
            return "I don't know based on the provided documents."

        context = "\n\n".join(retrieved)

        # ----------------------------------------------------
        # Prompt
        # ----------------------------------------------------

        prompt = f"""
You are a document question-answering assistant.

Answer the user's question using ONLY the provided document context.

Rules:
- Give a SHORT and DIRECT answer (2 to 4 sentences, or a few bullet points if the question asks for a list).
- Do NOT copy large parts of the context. Do NOT repeat the question.
- If the answer cannot be found in the context, respond exactly:
I don't know based on the provided documents.
- Do not use outside knowledge.
- Reply in the same language as the user's question.

DOCUMENT CONTEXT:
----------------
{context}
----------------

USER QUESTION:
{question}
"""

        # ----------------------------------------------------
        # 1) Gemini (free)
        # ----------------------------------------------------

        if GEMINI_API_KEY:
            try:
                return ask_gemini(prompt)
            except Exception as e:
                print("Gemini answer failed:", e)

        # ----------------------------------------------------
        # 2) OpenAI
        # ----------------------------------------------------

        if client is not None:
            try:
                response = client.responses.create(
                    model="gpt-4o-mini",
                    input=prompt
                )
                return response.output_text.strip()
            except Exception as e:
                print("OpenAI answer failed:", e)

        # ----------------------------------------------------
        # 3) Bina AI ke: sirf best 2-3 sentences
        # ----------------------------------------------------

        return short_extract(question, retrieved)


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("Q&A ASSISTANT TEST")
    print("=" * 60)

    assistant = QAAssistant()

    print("Assistant initialized successfully.")