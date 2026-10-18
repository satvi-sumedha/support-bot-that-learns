"""
Demo web page for the Support Bot That Learns.
Run:  python app.py     then open  http://127.0.0.1:5000
The page itself lives in index.html (same folder).
"""

import os
from flask import Flask, request, jsonify, send_file
import memory_agent as m

app = Flask(__name__)
TENANTS = ["acme", "zeta"]
HERE = os.path.dirname(os.path.abspath(__file__))


@app.route("/")
def home():
    return send_file(os.path.join(HERE, "index.html"), mimetype="text/html")


@app.route("/api/ticket", methods=["POST"])
def api_ticket():
    data = request.get_json()
    if data.get("tenant") not in TENANTS:
        return jsonify({"error": "unknown customer"}), 400
    return jsonify(m.handle_ticket(data["tenant"], data.get("text", "")))


@app.route("/api/state/<tenant>")
def api_state(tenant):
    if tenant not in TENANTS:
        return jsonify({"error": "unknown customer"}), 400
    return jsonify(m.get_state(tenant))


@app.route("/api/reset/<tenant>", methods=["POST"])
def api_reset(tenant):
    if tenant not in TENANTS:
        return jsonify({"error": "unknown customer"}), 400
    m.reset(tenant)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=False, port=5000)