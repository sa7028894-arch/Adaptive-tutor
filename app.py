Rebuild the Classroom — Adaptive Tutor for Python Functions
Flask backend. Calls the Claude API server-side on every turn to decide
what the student needs next: slow down, jump ahead, reframe, or probe deeper.
"""

import os
from flask import Flask, jsonify, render_template, request

from tutor_engine import get_next_turn, TutorEngineError

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/turn", methods=["POST"])
def turn():
    payload = request.get_json(force=True, silent=True) or {}
    history = payload.get("history", [])
    student_answer = payload.get("student_answer")

    if not (os.environ.get("GROQ_API_KEY") or os.environ.get("XAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        return jsonify({
            "error": "missing_api_key",
            "message": "Set GROQ_API_KEY, XAI_API_KEY, or ANTHROPIC_API_KEY and restart the server."
        }), 400

    try:
        next_turn = get_next_turn(history=history, student_answer=student_answer)
    except TutorEngineError as e:
        return jsonify({"error": "engine_error", "message": str(e)}), 502

    return jsonify(next_turn)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
