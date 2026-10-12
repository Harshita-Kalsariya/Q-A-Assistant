from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from backend.llm import QAAssistant


# ---------------------------------------------------------
# PROJECT PATH
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

FRONTEND_DIR = BASE_DIR / "frontend"
UPLOAD_FOLDER = BASE_DIR / "uploads"

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------
# FLASK APP
# ---------------------------------------------------------

app = Flask(__name__)
CORS(app)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)


# ---------------------------------------------------------
# AI ASSISTANT
# ---------------------------------------------------------

assistant = QAAssistant()


# ---------------------------------------------------------
# ALLOWED FILE TYPES
# ---------------------------------------------------------

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".docx", ".jpg", ".jpeg", ".png"}


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------
# PAGES
# ---------------------------------------------------------

@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/style.css")
def style():
    return send_from_directory(FRONTEND_DIR, "style.css")


@app.route("/health")
def health():
    return jsonify({
        "status": "success",
        "message": "Backend is running successfully."
    })


# ---------------------------------------------------------
# UPLOAD DOCUMENT
# ---------------------------------------------------------

@app.route("/upload", methods=["POST"])
def upload_file():

    try:

        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file was selected."}), 400

        file = request.files["file"]

        if file.filename == "":
            return jsonify({"success": False, "error": "No file was selected."}), 400

        if not allowed_file(file.filename):
            return jsonify({"success": False, "error": "Unsupported file type."}), 400

        filename = Path(file.filename).name
        file_path = UPLOAD_FOLDER / filename

        file.save(str(file_path))

        print(f"File saved: {file_path}")

        result = assistant.load_and_index_document(str(file_path))

        return jsonify({"success": True, "message": result})

    except Exception as e:

        print("UPLOAD ERROR:", str(e))

        return jsonify({
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}"
        }), 500


# ---------------------------------------------------------
# ASK QUESTION
# ---------------------------------------------------------

@app.route("/ask", methods=["POST"])
def ask_question():

    try:

        data = request.get_json(silent=True)

        if not data:
            return jsonify({"success": False, "error": "No JSON data received."}), 400

        question = data.get("question", "").strip()

        if not question:
            return jsonify({"success": False, "error": "Please enter a question."}), 400

        answer = assistant.answer_question(question)

        return jsonify({"success": True, "answer": answer})

    except Exception as e:

        print("ASK ERROR:", str(e))

        return jsonify({
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}"
        }), 500


# ---------------------------------------------------------
# RUN SERVER
# ---------------------------------------------------------

if __name__ == "__main__":

    print("=" * 60)
    print("Q&A ASSISTANT BACKEND")
    print("=" * 60)
    print("Server: http://127.0.0.1:5000")
    print("Health: http://127.0.0.1:5000/health")
    print("=" * 60)

    # use_reloader=False: model dobara load nahi hoga aur
    # venv ki files change hone par server restart nahi hoga
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        use_reloader=False
    )