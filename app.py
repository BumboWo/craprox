import asyncio
import websockets
import json
import hashlib
import logging
import time
import argparse
import sys

TARGET_URL = "wss://free.freezehost.pro/afkwspath"
COOKIES = "connect.sid=s%3ASeYhEwDf9qdfGDwpgCS2uFft6aNIk_V0.MqDa6OnALo0xFE6l331FG2fuMqYyBhqhHvapwL0E0cg"
USER_ID = "771061351807713370"
EVERY = 60
COINS = 1
SESSION_MINUTES = 20

parser = argparse.ArgumentParser(description="FreezeHost AFK coin earner")
parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
args = parser.parse_args()


class CleanFormatter(logging.Formatter):
    """Clears the status line before printing a log, then reprints it after."""
    last_status = ""

    def format(self, record):
        # Clear current status line before log message
        if self.last_status:
            sys.stdout.write("\r" + " " * len(self.last_status) + "\r")
            sys.stdout.flush()
        return super().format(record)


handler = logging.StreamHandler(sys.stdout)
formatter = CleanFormatter("%(asctime)s [%(levelname)s] %(message)s")
handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.DEBUG if args.verbose else logging.INFO,
    handlers=[handler]
)


def print_status(line: str):
    CleanFormatter.last_status = line
    sys.stdout.write("\r" + line)
    sys.stdout.flush()


async def sha256(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()


async def afk_session(session_num: int, total_coins: int, total_start: float) -> int:
    coin_timer = EVERY
    session_coins = 0
    start_time = None
    challenge_validated = False
    session_done = asyncio.Event()

    try:
        async with websockets.connect(
            TARGET_URL,
            additional_headers={
                "Origin": "https://free.freezehost.pro",
                "Cookie": COOKIES,
                "User-Agent": "Mozilla/5.0"
            }
        ) as ws:
            logging.info(f"🔌 Session #{session_num} connected")

            async def receive_loop():
                nonlocal challenge_validated, start_time
                async for raw in ws:
                    data = json.loads(raw)
                    msg_type = data.get("type")

                    logging.debug(f"[RECV] {raw}")

                    if msg_type == "challenge":
                        logging.info("🔐 Challenge received, responding...")
                        response = await sha256(
                            data["challenge"] + str(data["timestamp"]) + USER_ID
                        )
                        payload = json.dumps({"type": "challenge_response", "response": response})
                        logging.debug(f"[SEND] {payload}")
                        await ws.send(payload)

                    elif msg_type == "challenge_ok":
                        logging.info("✅ Challenge validated, session running")
                        challenge_validated = True
                        start_time = time.time()

                    elif msg_type in ("error", "rejected"):
                        logging.warning(f"⚠️  Rejected: {data}")

                    else:
                        logging.debug(f"[UNHANDLED] {data}")

            async def tick_loop():
                nonlocal session_coins, coin_timer
                while not challenge_validated:
                    await asyncio.sleep(0.5)

                session_end = time.time() + SESSION_MINUTES * 60

                while True:
                    await asyncio.sleep(1)

                    if time.time() >= session_end:
                        logging.info(f"🔄 Session #{session_num} complete — reconnecting")
                        session_done.set()
                        return

                    coin_timer -= 1
                    if coin_timer <= 0:
                        coin_timer = EVERY
                        session_coins += COINS
                        logging.info(
                            f"🪙 +{COINS} coin | "
                            f"Session #{session_num}: {session_coins} | "
                            f"All-time: {total_coins + session_coins}"
                        )

                    elapsed = int(time.time() - total_start)
                    hrs  = elapsed // 3600
                    mins = (elapsed % 3600) // 60
                    secs = elapsed % 60

                    session_remaining = max(0, int(session_end - time.time()))
                    s_mins = session_remaining // 60
                    s_secs = session_remaining % 60

                    print_status(
                        f"⏳ Next coin: {coin_timer:2d}s | "
                        f"Session #{session_num} resets: {s_mins:02d}:{s_secs:02d} | "
                        f"Uptime: {hrs:02d}:{mins:02d}:{secs:02d} | "
                        f"Total: {total_coins + session_coins} coins"
                    )

            async def heartbeat_loop():
                while not challenge_validated:
                    await asyncio.sleep(0.5)

                while not session_done.is_set():
                    await asyncio.sleep(30)
                    if session_done.is_set():
                        break
                    payload = json.dumps({"type": "heartbeat"})
                    logging.debug(f"[SEND] {payload}")
                    await ws.send(payload)
                    logging.debug("💓 Heartbeat sent")

            await asyncio.gather(
                receive_loop(),
                tick_loop(),
                heartbeat_loop(),
                return_exceptions=True
            )

    except websockets.exceptions.ConnectionClosedError as e:
        logging.warning(f"🔌 Connection closed: code={e.code} reason={e.reason}")
    except Exception as e:
        logging.error(f"❌ Error: {e}")

    return session_coins


async def main():
    total_coins = 0
    total_start = time.time()
    session_num = 0

    while True:
        session_num += 1
        logging.info(f"🚀 Starting session #{session_num}")
        earned = await afk_session(session_num, total_coins, total_start)
        total_coins += earned or 0
        logging.info(f"💰 Session #{session_num} ended | All-time total: {total_coins} coins")
        logging.info(f"⏸️  Reconnecting in 3s...")
        await asyncio.sleep(3)



def run_afk():
    asyncio.run(main())