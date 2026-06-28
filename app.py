"""
app.py
------
Provenance Guard — main Flask application.

Milestone 3 endpoints:
  POST /submit   — classify content, write audit log, return result
  GET  /log      — return recent audit log entries as JSON

Milestone 5 endpoints (stubbed here, implemented fully in M5):
  POST /appeal   — contest a classification

Rate limiting (Flask-Limiter):
  /submit is limited to 10 requests/minute and 100 requests/day per IP.
  Reasoning (documented in README):
    - A real creator submits their own work infrequently; 10/min is generous.
    - 100/day prevents scripted flooding while allowing power users.
  The storage_uri="memory://" is required by Flask-Limiter ≥ 3.x for local dev.
"""

import os
import uuid
import time 
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from database import init_db, insert_submission, get_log, get_submission, update_appeal
from signals import classify_with_llm, compute_stylometric_score, compute_confidence
from labels import score_to_attribution, generate_label

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

# Load GROQ_API_KEY (and any other secrets) from .env file into os.environ.
# This must happen before any code that reads os.environ.get("GROQ_API_KEY").
load_dotenv()

app = Flask(__name__)

# Rate limiter — see docstring above for limit reasoning.
# storage_uri="memory://" keeps state in process memory (fine for dev/single-process).
# A production deployment would use storage_uri="redis://..." for persistence
# across restarts and multiple workers.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],          # no default limits — only apply where decorated
    storage_uri="memory://",
)

# Initialize the SQLite database (creates table if not exists).
# Called at import time so the DB is ready before the first request.
with app.app_context():
    init_db()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# POST /submit
# ---------------------------------------------------------------------------

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute; 1000 per day")
def submit():
    """
    Accept a piece of text for attribution analysis.

    Request JSON:
        {
            "text":       "string, required, at least 30 words",
            "creator_id": "string, required"
        }

    Response JSON (200):
        {
            "content_id":  "uuid4",
            "creator_id":  "string",
            "attribution": "likely_ai | uncertain | likely_human",
            "confidence":  0.82,
            "llm_score":   0.85,
            "stylo_score": 0.77,
            "label_text":  "full transparency label string",
            "status":      "classified",
            "timestamp":   "ISO-8601"
        }

    Error responses:
        400 — missing fields or text too short
        429 — rate limit exceeded (handled automatically by Flask-Limiter)
        500 — upstream Groq API failure
    """
    data = request.get_json(silent=True)

    # --- Input validation ---
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    text = data.get("text", "").strip()
    creator_id = data.get("creator_id", "").strip()

    if not text:
        return jsonify({"error": "Missing required field: text"}), 400
    if not creator_id:
        return jsonify({"error": "Missing required field: creator_id"}), 400

    word_count = len(text.split())
    if word_count < 30:
        return jsonify({
            "error": f"Text too short for reliable analysis ({word_count} words). "
                     f"Please submit at least 30 words."
        }), 400

    # --- Run detection signals ---
    try:
        llm_score = classify_with_llm(text)
    except Exception as e:
        # Groq API failures should surface as 500, not crash the server
        app.logger.error(f"LLM signal failed: {e}")
        return jsonify({
            "error": "Detection service unavailable. Please try again shortly.",
            "detail": str(e)
        }), 500

    # Signal 2: stylometric heuristics (pure Python, cannot fail externally)
    stylo_score = compute_stylometric_score(text)

    # --- Combine signals into confidence score ---
    confidence = compute_confidence(llm_score, stylo_score)

    # --- Derive attribution and label ---
    attribution = score_to_attribution(confidence)
    label_text  = generate_label(attribution, confidence)

    # --- Build the full response / audit entry ---
    content_id = str(uuid.uuid4())
    timestamp  = utc_now()

    entry = {
        "content_id":   content_id,
        "creator_id":   creator_id,
        "text_snippet": text[:200],     # store only first 200 chars
        "attribution":  attribution,
        "confidence":   confidence,
        "llm_score":    llm_score,
        "stylo_score":  stylo_score,
        "label_text":   label_text,
        "status":       "classified",
        "created_at":   timestamp,
    }

    # --- Persist to audit log ---
    insert_submission(entry)

    # --- Return response (same shape as audit entry, minus text_snippet) ---
    response = {k: v for k, v in entry.items() if k != "text_snippet"}
    response["timestamp"] = response.pop("created_at")   # friendlier key name in API
    return jsonify(response)


# ---------------------------------------------------------------------------
# POST /appeal
# ---------------------------------------------------------------------------

@app.route("/appeal", methods=["POST"])
def appeal():
    """
    Contest a classification.

    Request JSON:
        {
            "content_id":        "uuid4 from /submit response",
            "creator_reasoning": "string, 10–2000 chars"
        }

    Response JSON (200):
        {
            "content_id": "uuid4",
            "status":     "under_review",
            "message":    "Your appeal has been received..."
        }

    Error responses:
        400 — missing fields or creator_reasoning out of bounds
        404 — content_id not found
        409 — content is already under review
    """
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    content_id = data.get("content_id", "").strip()
    reasoning  = data.get("creator_reasoning", "").strip()

    if not content_id:
        return jsonify({"error": "Missing required field: content_id"}), 400
    if not reasoning:
        return jsonify({"error": "Missing required field: creator_reasoning"}), 400
    if len(reasoning) < 10:
        return jsonify({"error": "creator_reasoning must be at least 10 characters."}), 400
    if len(reasoning) > 2000:
        return jsonify({"error": "creator_reasoning must be 2000 characters or fewer."}), 400

    # Look up the submission
    submission = get_submission(content_id)
    if not submission:
        return jsonify({"error": f"No submission found with content_id: {content_id}"}), 404

    # Prevent duplicate appeals
    if submission["status"] != "classified":
        return jsonify({
            "error": f"Cannot appeal: content is already '{submission['status']}'."
        }), 409

    # Update the record
    appeal_timestamp = utc_now()
    update_appeal(content_id, reasoning, appeal_timestamp)

    return jsonify({
        "content_id":      content_id,
        "status":          "under_review",
        "appeal_timestamp": appeal_timestamp,
        "message": (
            "Your appeal has been received and will be reviewed by our moderation team."
        )
    }), 200


# ---------------------------------------------------------------------------
# GET /log
# ---------------------------------------------------------------------------

@app.route("/log", methods=["GET"])
def log():
    """
    Return recent audit log entries as JSON.

    Query params:
        status  (optional) — filter by status, e.g. ?status=under_review

    Response JSON (200):
        {
            "count":   3,
            "entries": [ { ...submission fields... }, ... ]
        }

    In a production system this endpoint would require authentication.
    Here it exists for documentation, grading visibility, and manual testing.
    """
    status_filter = request.args.get("status", None)
    entries = get_log(status_filter=status_filter)

    return jsonify({
        "count":   len(entries),
        "entries": entries
    }), 200


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(429)
def rate_limit_exceeded(e):
    """Return a clean JSON 429 when Flask-Limiter fires."""
    return jsonify({
        "error": "Rate limit exceeded. Please wait before submitting again.",
        "detail": str(e.description)
    }), 429

 
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found."}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed."}), 405


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # debug=True → auto-reloads on code changes and shows full tracebacks.
    # Never use debug=True in production.
    app.run(debug=True, port=5000)