
import os
import logging
from pathlib import Path
from uuid import uuid4

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

from backend.llm import QAAssistant


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
UPLOAD_FOLDER = BASE_DIR / "uploads"

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

# Maximum upload size: 10 MB per file.
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

# Same-origin frontend requests do not require CORS.
# Keep CORS enabled for API requests from other origins if needed.
CORS(app, resources={r"/upload": {"origins": "*"},
                     r"/ask": {"origins": "*"},
                     r"/health": {"origins": "*"}})


# ============================================================
# AI ASSISTANT
# ============================================================

assistant = QAAssistant()


# ============================================================
# ALLOWED FILE TYPES
# ============================================================

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".docx",
    ".jpg",
    ".jpeg",
    ".png",
}


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(413)
def file_too_large(error):
    return jsonify({
        "success": False,
        "error": "File is too large. Maximum allowed size is 10 MB."
    }), 413


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "The requested endpoint was not found."
    }), 404


@app.errorhandler(500)
def internal_server_error(error):
    logger.exception("Unhandled server error")
    return jsonify({
        "success": False,
        "error": "An internal server error occurred. Check Render logs."
    }), 500


# ============================================================
# FRONTEND PAGES
# ============================================================

@app.route("/")
def home():
    return send_from_directory(str(FRONTEND_DIR), "index.html")


@app.route("/style.css")
def style():
    return send_from_directory(str(FRONTEND_DIR), "style.css")


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "success",
        "message": "Backend is running successfully."
    }), 200


# ============================================================
# UPLOAD DOCUMENT
# ============================================================

@app.route("/upload", methods=["POST"])
def upload_file():

    logger.info("UPLOAD REQUEST RECEIVED")
    logger.info("Upload content type: %s", request.content_type)
    logger.info("Upload content length: %s", request.content_length)

    saved_path = None

    try:
        if "file" not in request.files:
            return jsonify({
                "success": False,
                "error": "No file was received. Please select a file."
            }), 400

        uploaded_file = request.files["file"]

        if not uploaded_file.filename:
            return jsonify({
                "success": False,
                "error": "No file was selected."
            }), 400

        safe_filename = secure_filename(uploaded_file.filename)

        if not safe_filename:
            return jsonify({
                "success": False,
                "error": "Invalid filename."
            }), 400

        if not allowed_file(safe_filename):
            return jsonify({
                "success": False,
                "error": (
                    "Unsupported file type. "
                    "Upload PDF, TXT, DOCX, JPG, JPEG or PNG."
                )
            }), 400

        # Unique filenames prevent collisions between uploads.
        extension = Path(safe_filename).suffix.lower()
        unique_filename = f"{uuid4().hex}{extension}"
        saved_path = UPLOAD_FOLDER / unique_filename

        uploaded_file.save(str(saved_path))

        if not saved_path.exists() or saved_path.stat().st_size == 0:
            logger.error("Uploaded file is empty or was not saved.")
            return jsonify({
                "success": False,
                "error": "The uploaded file is empty."
            }), 400

        logger.info(
            "File saved successfully. Extension: %s, size: %s bytes",
            extension,
            saved_path.stat().st_size,
        )

        # Process and index the document.
        result = assistant.load_and_index_document(str(saved_path))

        logger.info("Document processing completed successfully.")

        return jsonify({
            "success": True,
            "message": result,
        }), 200

    except Exception:
        # Log traceback on the server, but do not expose internal
        # paths, document contents or provider errors to website users.
        logger.exception("UPLOAD PROCESSING FAILED")

        return jsonify({
            "success": False,
            "error": (
                "File upload or processing failed. "
                "Please check Render logs for details."
            ),
        }), 500

    finally:
        # Do not delete the file here: the assistant stores its path
        # in chunk metadata and may need the file for later processing.
        pass


# ============================================================
# ASK QUESTION
# ============================================================

@app.route("/ask", methods=["POST"])
def ask_question():

    logger.info("ASK REQUEST RECEIVED")

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "success": False,
                "error": "Please send a valid JSON request."
            }), 400

        question = data.get("question", "")

        if not isinstance(question, str) or not question.strip():
            return jsonify({
                "success": False,
                "error": "Please enter a question."
            }), 400

        answer = assistant.answer_question(question.strip())

        return jsonify({
            "success": True,
            "answer": answer,
        }), 200

    except Exception:
        logger.exception("QUESTION ANSWERING FAILED")

        return jsonify({
            "success": False,
            "error": (
                "Could not answer the question. "
                "Please check Render logs."
            ),
        }), 500


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    logger.info("Starting Q&A Assistant backend")
    logger.info("Frontend directory exists: %s", FRONTEND_DIR.exists())
    logger.info("Upload directory: %s", UPLOAD_FOLDER)

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )
