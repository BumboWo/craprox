import asyncio
import websockets
import json
import hashlib
import time
import argparse
import sys
from datetime import timedelta

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel


from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.align import Align
from rich import box

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="FreezeHost AFK coin earner")
parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
args = parser.parse_args()

console = Console()

# ── Config ────────────────────────────────────────────────────────────────────
TARGET_URL         = "wss://free.freezehost.pro/afkwspath"
COOKIES            = "connect.sid=s%3ASeYhEwDf9qdfGDwpgCS2uFft6aNIk_V0.MqDa6OnALo0xFE6l331FG2fuMqYyBhqhHvapwL0E0cg"
USER_ID            = "771061351807713370"
EVERY              = 60
COINS              = 1
SESSION_COIN_LIMIT = 10

# ── Shared state ──────────────────────────────────────────────────────────────
state = {
    "session_num":       1,
    "total_coins":       0,
    "session_coins":     0,
    "coin_timer":        EVERY,
    "session_remaining": SESSION_COIN_LIMIT,
    "uptime":            0.0,
    "status":            "Connecting…",
    "challenge":         "Waiting",
    "connected":         False,
    "total_start":       time.time(),
}

events:    list[str]                = []
log_lines: list[tuple[str,str,str]] = []
cmd_log:   list[str]                = []   # command history / feedback
cmd_input: list[str]                = [""] # mutable string buffer (list so closures can mutate)

MAX_EVENTS = 8
MAX_LOG    = 12
MAX_CMD    = 5

def push_event(msg: str):
    ts = time.strftime("%H:%M:%S")
    events.append(f"[dim]{ts}[/dim]  {msg}")
    while len(events) > MAX_EVENTS:
        events.pop(0)

def push_log(level: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    log_lines.append((ts, level, msg))

    while len(log_lines) > MAX_LOG * 3:
        log_lines.pop(0)

def push_cmd(msg: str):
    cmd_log.append(msg)
    while len(cmd_log) > MAX_CMD:
        cmd_log.pop(0)

# ── Commands ──────────────────────────────────────────────────────────────────
COMMANDS = {
    "help":     "Show this help",
    "skip":     "Set session_remaining to 1 (triggers reconnect on next coin)",
    "status":   "Print current state",
    "coins":    "Show total coins earned",
    "reset":    "Reset session coin counter",
}

def run_command(raw: str) -> str:
    parts = raw.strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower()

    if cmd == "help":
        lines = ["[bold cyan]Available commands:[/bold cyan]"]
        for c, desc in COMMANDS.items():
            lines.append(f"  [yellow]{c}[/yellow] — {desc}")
        return "\n".join(lines)

    elif cmd == "skip":
        state["session_remaining"] = 1
        return "[green]✓[/green] Session will reset on next coin"

    elif cmd == "status":
        total = state["total_coins"] + state["session_coins"]
        return (
            f"session=[cyan]#{state['session_num']}[/cyan]  "
            f"coins=[yellow]{state['session_coins']}/{SESSION_COIN_LIMIT}[/yellow]  "
            f"all-time=[green]{total}[/green]  "
            f"connected=[{'green' if state['connected'] else 'red'}]{state['connected']}[/]"
        )

    elif cmd == "coins":
        total = state["total_coins"] + state["session_coins"]
        return f"[green]All-time: {total} coins[/green]"

    elif cmd == "reset":
        state["session_coins"]     = 0
        state["session_remaining"] = SESSION_COIN_LIMIT
        return "[green]✓[/green] Session coin counter reset"

    else:
        return f"[red]Unknown command:[/red] {cmd}  (type [yellow]help[/yellow])"

# ── Progress bar ──────────────────────────────────────────────────────────────
def make_bar(ratio: float, color: str, width: int = 34) -> Text:
    ratio = max(0.0, min(1.0, ratio))
    n = int(width * ratio)
    return Text.from_markup(
        f"[{color}]{'█' * n}[/][dim white]{'░' * (width - n)}[/]"
    )

# ── Layout builder ────────────────────────────────────────────────────────────
def build_layout() -> Panel:
    s = state

    # ─ header ─────────────────────────────────────────────────────────────────
    header = Align.center(
        Text("⚡  FreezeHost AFK Coin Earner", style="bold cyan")
    )

    # ─ status row ─────────────────────────────────────────────────────────────
    conn_style = "bold green" if s["connected"]           else "bold red"
    chal_style = "bold green" if s["challenge"] == "✓ OK" else "bold yellow"

    status_tbl = Table.grid(expand=True)
    status_tbl.add_column(justify="left",   ratio=1)
    status_tbl.add_column(justify="center", ratio=1)
    status_tbl.add_column(justify="right",  ratio=1)
    status_tbl.add_row(
        Text(f"{'●' if s['connected'] else '○'}  {'Connected' if s['connected'] else 'Disconnected'}", style=conn_style),
        Text(f"⚿  Challenge: {s['challenge']}",  style=chal_style),
        Text(f"◈  {s['status']}",                style="white"),
    )

    # ─ progress bars ──────────────────────────────────────────────────────────
    coin_ratio  = (EVERY - s["coin_timer"]) / EVERY
    sess_ratio  = (SESSION_COIN_LIMIT - s["session_remaining"]) / SESSION_COIN_LIMIT
    coins_left  = s["session_remaining"]

    bar_tbl = Table.grid(expand=True, padding=(0, 1))
    bar_tbl.add_column(width=28, no_wrap=True)
    bar_tbl.add_column(ratio=1)

    bar_tbl.add_row(
        Text.from_markup(f"Next coin in  [bold yellow]{s['coin_timer']:2d}s[/bold yellow]"),
        make_bar(coin_ratio, "cyan"),
    )
    bar_tbl.add_row(
        Text.from_markup(f"Session resets in [bold magenta]{coins_left:2d}[/bold magenta] coins"),
        make_bar(sess_ratio, "magenta"),
    )

    bars_panel = Panel(bar_tbl, border_style="dim blue", padding=(0, 1))

    # ─ stats ──────────────────────────────────────────────────────────────────
    total      = s["total_coins"] + s["session_coins"]
    uptime_str = str(timedelta(seconds=int(s["uptime"]))).zfill(8)

    stats = Table(box=box.SIMPLE_HEAD, expand=True, show_header=False, padding=(0, 3))
    stats.add_column(style="dim white", no_wrap=True, ratio=1)
    stats.add_column(justify="right",   ratio=1)
    stats.add_column(style="dim white", no_wrap=True, ratio=1)
    stats.add_column(justify="right",   ratio=1)

    stats.add_row(
        "Session #",      Text(f"#{s['session_num']}",   style="bold cyan"),
        "Session coins",  Text(f"{s['session_coins']}/{SESSION_COIN_LIMIT}", style="bold yellow"),
    )
    stats.add_row(
        "All-time coins", Text(str(total),               style="bold green"),
        "Uptime",         Text(uptime_str,               style="white"),
    )

    stats_panel = Panel(stats, title="[bold blue]Stats[/bold blue]",
                        border_style="dim blue", padding=(0, 1))

    # ─ events ─────────────────────────────────────────────────────────────────
    rows = list(events)
    while len(rows) < MAX_EVENTS:
        rows.append("")

    ev_tbl = Table.grid(expand=True, padding=(0, 1))
    ev_tbl.add_column(ratio=1)
    for row in rows[-MAX_EVENTS:]:
        ev_tbl.add_row(Text.from_markup(row) if row else Text(" "))

    events_panel = Panel(ev_tbl, title="[bold]Recent Events[/bold]",
                         border_style="dim blue", padding=(0, 1))

    # ─ command box ────────────────────────────────────────────────────────────
    cmd_history_tbl = Table.grid(expand=True, padding=(0, 1))
    cmd_history_tbl.add_column(ratio=1)
    history_rows = list(cmd_log)
    while len(history_rows) < MAX_CMD:
        history_rows.insert(0, "")
    for row in history_rows:
        cmd_history_tbl.add_row(Text.from_markup(row) if row else Text(" "))

    prompt_line = Text.from_markup(
        f"[bold cyan]>[/bold cyan] [white]{cmd_input[0]}[/white][blink]█[/blink]"
    )

    cmd_panel = Panel(
        Group(cmd_history_tbl, Rule(style="dim blue"), prompt_line),
        title="[bold]Command[/bold]  [dim](type [yellow]help[/yellow] for commands)[/dim]",
        border_style="cyan",
        padding=(0, 1),
    )

    # ─ verbose log ────────────────────────────────────────────────────────────
    parts: list = [
        header,
        Rule(style="dim blue"),
        status_tbl,
        Rule(style="dim blue"),
        bars_panel,
        stats_panel,
        events_panel,
        cmd_panel,
    ]

    if args.verbose:
        level_colors = {"INFO": "cyan", "DEBUG": "dim white", "WARN": "yellow", "ERROR": "red"}
        log_tbl = Table(box=box.SIMPLE, expand=True, show_header=True,
                        header_style="bold dim", padding=(0, 1))
        log_tbl.add_column("Time",    width=10, style="dim")
        log_tbl.add_column("Level",   width=7)
        log_tbl.add_column("Message", style="dim white", no_wrap=True)
        for ts, lvl, msg in log_lines[-MAX_LOG:]:
            c = level_colors.get(lvl, "white")
            log_tbl.add_row(ts, f"[{c}]{lvl}[/{c}]", msg[:120])
        parts.append(Rule(style="dim"))
        parts.append(Panel(log_tbl, title="[dim]Debug Log[/dim]",
                           border_style="dim", padding=(0, 1)))

    parts.append(Rule(style="dim blue"))

    return Panel(
        Group(*parts),
        border_style="blue",
        padding=(0, 1),
        subtitle="[dim]ctrl+c to quit[/dim]",
    )

# ── SHA-256 ───────────────────────────────────────────────────────────────────
async def sha256(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()

# ── Keyboard input reader ─────────────────────────────────────────────────────
async def input_loop(live: Live):
    """Reads keypresses from stdin without blocking the event loop."""
    loop = asyncio.get_event_loop()

    if sys.platform == "win32":
        # Windows: use msvcrt in a thread
        import msvcrt

        def read_char():
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    return ch
                time.sleep(0.05)

        while True:
            ch = await loop.run_in_executor(None, read_char)
            if ch in ("\r", "\n"):
                raw = cmd_input[0]
                cmd_input[0] = ""
                if raw.strip():
                    push_cmd(f"[dim cyan]>[/dim cyan] [white]{raw}[/white]")
                    result = run_command(raw)
                    if result:
                        for line in result.split("\n"):
                            push_cmd(line)
                live.update(build_layout())
            elif ch in ("\x08", "\x7f"):  # backspace
                cmd_input[0] = cmd_input[0][:-1]
                live.update(build_layout())
            elif ch == "\x03":  # ctrl+c
                raise KeyboardInterrupt
            elif ch.isprintable():
                cmd_input[0] += ch
                live.update(build_layout())
    else:
        # Unix: set raw mode
        import tty, termios, select

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                await asyncio.sleep(0.05)
                if select.select([sys.stdin], [], [], 0)[0]:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        raw = cmd_input[0]
                        cmd_input[0] = ""
                        if raw.strip():
                            push_cmd(f"[dim cyan]>[/dim cyan] [white]{raw}[/white]")
                            result = run_command(raw)
                            if result:
                                for line in result.split("\n"):
                                    push_cmd(line)
                        live.update(build_layout())
                    elif ch in ("\x08", "\x7f"):
                        cmd_input[0] = cmd_input[0][:-1]
                        live.update(build_layout())
                    elif ch == "\x03":
                        raise KeyboardInterrupt
                    elif ch.isprintable():
                        cmd_input[0] += ch
                        live.update(build_layout())
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

# ── Single WebSocket session ──────────────────────────────────────────────────
async def afk_session(live: Live) -> int:
    state.update({
        "session_coins":     0,
        "coin_timer":        EVERY,
        "session_remaining": SESSION_COIN_LIMIT,
        "connected":         False,
        "challenge":         "Waiting",
        "status":            "Connecting…",
    })

    challenge_validated = False
    session_done        = asyncio.Event()

    push_event(f"[cyan]Session #{state['session_num']} starting…[/cyan]")
    push_log("INFO", f"Session #{state['session_num']} starting")
    live.update(build_layout())

    try:
        async with websockets.connect(
            TARGET_URL,
            additional_headers={
                "Origin": "https://free.freezehost.pro",
                "Cookie": COOKIES,
                "User-Agent": "Mozilla/5.0",
            }
        ) as ws:
            state["connected"] = True
            state["status"]    = "Awaiting challenge…"
            push_event("[green]WebSocket connected[/green]")
            push_log("INFO", "Connected")
            live.update(build_layout())

            async def receive_loop():
                nonlocal challenge_validated
                async for raw in ws:
                    # Stop processing once session is done
                    if session_done.is_set():
                        return
                    data     = json.loads(raw)
                    msg_type = data.get("type")
                    push_log("DEBUG", f"RECV: {raw[:120]}")

                    if msg_type == "challenge":
                        state["challenge"] = "Sent"
                        state["status"]    = "Solving challenge…"
                        push_event("[yellow]🔐 Challenge received — solving…[/yellow]")
                        push_log("INFO", "Challenge received")
                        live.update(build_layout())
                        response = await sha256(
                            data["challenge"] + str(data["timestamp"]) + USER_ID
                        )
                        payload = json.dumps({"type": "challenge_response", "response": response})
                        push_log("DEBUG", f"SEND: {payload[:120]}")
                        await ws.send(payload)

                    elif msg_type == "challenge_ok":
                        state["challenge"]  = "✓ OK"
                        state["status"]     = "Earning coins…"
                        challenge_validated = True
                        push_event("[bold green]✅ Challenge validated — earning started![/bold green]")
                        push_log("INFO", "Challenge validated")
                        live.update(build_layout())

                    elif msg_type in ("error", "rejected"):
                        push_event(f"[red]⚠  Server rejected: {data}[/red]")
                        push_log("WARN", f"Rejected: {data}")
                        live.update(build_layout())

                    else:
                        push_log("DEBUG", f"UNHANDLED: {data}")

            async def tick_loop():
                while not challenge_validated:
                    await asyncio.sleep(0.5)

                while True:
                    await asyncio.sleep(1)

                    state["coin_timer"] = max(0, state["coin_timer"] - 1)
                    state["uptime"]     = time.time() - state["total_start"]

                    if state["coin_timer"] <= 0:
                        state["coin_timer"]        = EVERY
                        state["session_coins"]     += COINS
                        state["session_remaining"]  = max(0, state["session_remaining"] - COINS)
                        total = state["total_coins"] + state["session_coins"]
                        push_event(
                            f"[bold yellow]🪙 +{COINS} coin[/bold yellow]  "
                            f"session=[yellow]{state['session_coins']}[/yellow]  "
                            f"all-time=[green]{total}[/green]"
                        )
                        push_log("INFO", f"+{COINS} | session={state['session_coins']} total={total}")

                        if state["session_remaining"] <= 0:
                            push_event(
                                f"[magenta]🔄 Session #{state['session_num']} complete "
                                f"— {SESSION_COIN_LIMIT} coins reached[/magenta]"
                            )
                            push_log("INFO", f"Session #{state['session_num']} complete")
                            session_done.set()
                            live.update(build_layout())
                            return  # exit tick_loop; ws.close() below will unblock receive_loop

                    live.update(build_layout())

            async def heartbeat_loop():
                while not challenge_validated:
                    await asyncio.sleep(0.5)
                while not session_done.is_set():
                    await asyncio.sleep(30)
                    if session_done.is_set():
                        break
                    await ws.send(json.dumps({"type": "heartbeat"}))
                    push_log("DEBUG", "SEND: heartbeat")

            # Spin up as cancellable tasks
            tasks = [
                asyncio.create_task(receive_loop()),
                asyncio.create_task(tick_loop()),
                asyncio.create_task(heartbeat_loop()),
            ]

            # Block until tick_loop signals session complete
            await session_done.wait()

            # Cancel everything and close the socket
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await ws.close()
            except Exception:
                pass

    except websockets.exceptions.ConnectionClosedError as e:
        if not session_done.is_set():
            state["connected"] = False
            state["status"]    = f"Closed ({e.code})"
            push_event(f"[red]🔌 Closed: {e.code} {e.reason or ''}[/red]")
            push_log("WARN", f"Closed: {e.code} {e.reason}")
            live.update(build_layout())

    except Exception as e:
        state["connected"] = False
        state["status"]    = "Error"
        push_event(f"[red]❌ {e}[/red]")
        push_log("ERROR", str(e))
        live.update(build_layout())

    state["connected"] = False
    return state["session_coins"]

# ── Entry point ───────────────────────────────────────────────────────────────
async def main():
    with Live(
        build_layout(),
        console=console,
        screen=True,
        refresh_per_second=4,
    ) as live:
        # Start keyboard input as a background task
        input_task = asyncio.create_task(input_loop(live))

        try:
            while True:
                earned = await afk_session(live)
                state["total_coins"] += earned or 0

                push_event(
                    f"[dim]⏸  Reconnecting in 3s…  "
                    f"all-time=[green]{state['total_coins']}[/green][/dim]"
                )
                push_log("INFO", f"Reconnect in 3s | all-time={state['total_coins']}")

                for i in range(3, 0, -1):
                    state["status"] = f"Reconnecting in {i}s…"
                    live.update(build_layout())
                    await asyncio.sleep(1)

                state["session_num"] += 1
        finally:
            input_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        total = state["total_coins"] + state["session_coins"]
        console.print()
        console.print(Panel(
            f"[bold green]Session ended.[/bold green]\n\n"
            f"  All-time coins: [bold yellow]{total}[/bold yellow]\n"
            f"  Uptime:         [cyan]{str(timedelta(seconds=int(state['uptime'])))}[/cyan]",
            border_style="green",
            title="[bold]Summary[/bold]",
            padding=(1, 4),
        ))
