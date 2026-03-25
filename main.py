from flask import Flask
import threading
import os

from app import run_afk  # import your function

app = Flask(__name__)

# ---- HEALTH CHECK ROUTE ----
@app.route("/")
def home():
    return "OK", 200

# ---- RUN FLASK ----
def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

# ---- START EVERYTHING ----
if __name__ == "__main__":
    # start flask in background
    threading.Thread(target=run_flask).start()

    # run your worker in main thread (so logs show properly)
    run_afk()