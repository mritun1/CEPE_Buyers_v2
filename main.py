from flask import Flask, jsonify
app = Flask(__name__)

@app.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify(log_messages[-50:])  # Return last 50 logs

@app.route('/api/trades', methods=['GET'])
def get_trades():
    return jsonify(paper_trades)

@app.route('/api/summary', methods=['GET'])
def get_summary():
    return jsonify({
        "day_pnl": day_pnl,
        "capital_used": capital_used,
        "return_pct": (day_pnl / capital_used * 100) if capital_used > 0 else 0.0
    })

if __name__ == "__main__":
    app.run(port=5000)