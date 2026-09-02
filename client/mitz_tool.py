#!/usr/bin/env python3
"""
Mitz MLBB Checker (V1.0)
API key from Telegram bot only. Cannot change key inside tool.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def _ensure():
    need = []
    for name, pkg in (("requests", "requests"), ("rich", "rich")):
        if importlib.util.find_spec(name) is None:
            need.append(pkg)
    if not need:
        return
    print(f"[Auto-install] {', '.join(need)}")
    for pkg in need:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", pkg],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"  {pkg} OK")
        except Exception:
            print(f"  {pkg} FAILED — run: pip install {pkg}")
            input("Press Enter…")
            sys.exit(1)

_ensure()

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box
from rich.align import Align

VERSION = "1.0"
STATE_FILE = Path(__file__).with_name("mitz_conf.json")
RESULTS_DIR = Path("results")
DEVICE_RE = re.compile(r"^(and_|ios_)[A-Za-z0-9\-]+", re.IGNORECASE)
DEFAULT_API = os.environ.get("MITZ_PUBLIC_API", "https://YOUR-MITZ-HOST.up.railway.app")

console = Console()

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"api_key": "", "api_base": DEFAULT_API}

def save_state(st: dict):
    STATE_FILE.write_text(json.dumps(st, indent=2), encoding="utf-8")

class MitzClient:
    def __init__(self, api_key: str, base: str):
        self.api_key = api_key.strip()
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": f"MitzTool/{VERSION}",
                "X-Api-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        self.s.timeout = 30

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def balance(self) -> dict:
        r = self.s.post(self._url("/api/v1/balance"), json={})
        return r.json()

    def device_check(self, device_id: str, mode: str = "valid") -> dict:
        r = self.s.post(
            self._url("/api/v1/device/check"),
            json={"device_id": device_id, "mode": mode},
        )
        return r.json()

    def device_bulk(self, devices: List[str], mode: str = "valid") -> dict:
        r = self.s.post(
            self._url("/api/v1/device/bulk"),
            json={"devices": devices, "mode": mode},
        )
        return r.json()

def clr():
    os.system("cls" if os.name == "nt" else "clear")

BANNER_ART = r"""
███╗   ███╗██╗████████╗███████╗
████╗ ████║██║╚══██╔══╝╚══███╔╝
██╔████╔██║██║   ██║     ███╔╝ 
██║╚██╔╝██║██║   ██║    ███╔╝  
██║ ╚═╝ ██║██║   ██║   ███████╗
╚═╝     ╚═╝╚═╝   ╚═╝   ╚══════╝
        MLBB CHECKER
"""

def big_banner(credits: Optional[float] = None, role: str = "—"):
    clr()
    art = Text(BANNER_ART.strip(), style="bold magenta")
    console.print(Align.center(art))
    info = Text()
    info.append("  Mitz MLBB Checker ", style="bold cyan")
    info.append(f"(V{VERSION})\n", style="bold white")
    info.append("  Credits : ", style="dim")
    info.append(f"{credits if credits is not None else '—'}\n", style="bold green")
    info.append("  Role    : ", style="dim")
    info.append(f"{role}\n", style="bold yellow")
    info.append("  " + "-" * 28, style="dim")
    console.print(Align.center(Panel(info, border_style="magenta", expand=False, padding=(0, 1))))
    console.print()

def pause():
    console.print()
    Prompt.ask("[dim]Press Enter — return menu[/dim]", default="")

def normalize_device(line: str) -> str:
    raw = (line or "").strip().strip("\"'")
    if not raw:
        return ""
    for sep in (" | ", "\t", " ", ":"):
        if sep in raw and raw.lower().startswith(("and_", "ios_")):
            raw = raw.split(sep, 1)[0].strip()
            break
    m = DEVICE_RE.match(raw)
    return m.group(0) if m else (raw if raw.lower().startswith(("and_", "ios_")) else "")

def save_line(kind: str, text: str):
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"device_{kind}.txt"
    with path.open("a", encoding="utf-8") as f:
        f.write(text + "\n")

def login_screen(state: dict) -> Optional[MitzClient]:
    big_banner()
    console.print(
        Panel(
            "[bold]API Key required[/bold]\n\n"
            "Get your key from the [cyan]Mitz Telegram bot[/cyan]\n"
            "Commands: /key   /balance   /revoke\n\n"
            "[dim]Key cannot be changed inside this tool.\n"
            "Only the bot can revoke and issue a new one.[/dim]",
            border_style="blue",
            expand=False,
        )
    )

    current = state.get("api_key", "").strip()
    if current:
        masked = current[:8] + "…" + current[-4:] if len(current) > 14 else "***"
        console.print(f"\n[dim]Saved key:[/dim] {masked}")
        if Confirm.ask("Use saved key?", default=True):
            key = current
        else:
            console.print(
                "[yellow]To change key you must /revoke on the bot first,[/yellow]\n"
                "[yellow]then paste the new key here.[/yellow]"
            )
            key = Prompt.ask("Api Key").strip()
    else:
        key = Prompt.ask("Api Key").strip()

    if not key:
        console.print("[red]No key.[/red]")
        time.sleep(1)
        return None

    base = state.get("api_base") or DEFAULT_API
    if "YOUR-MITZ-HOST" in base or not base:
        base = Prompt.ask("Api Base Url", default=base).strip().rstrip("/")
        state["api_base"] = base
    else:
        console.print(f"[dim]Api Base Url: {base}[/dim]")
        if Confirm.ask("Change Api Base Url?", default=False):
            base = Prompt.ask("Api Base Url", default=base).strip().rstrip("/")
            state["api_base"] = base

    client = MitzClient(key, base)
    console.print("\n[dim]Validating key…[/dim]")
    try:
        res = client.balance()
    except Exception as e:
        console.print(f"[red]Connection failed:[/red] {e}")
        pause()
        return None

    if not res.get("ok"):
        err = res.get("error", "unknown")
        if err in ("invalid_or_revoked_key", "missing_api_key"):
            console.print("[bold red]Invalid or revoked API key.[/bold red]")
            console.print("Open the Telegram bot → /key or /revoke")
            state["api_key"] = ""
            save_state(state)
        else:
            console.print(f"[red]Error:[/red] {err}")
        pause()
        return None

    state["api_key"] = key
    state["api_base"] = base
    save_state(state)
    console.print(
        f"[bold green]OK[/bold green]  Credits: {res.get('credits')}  Role: {res.get('role')}"
    )
    time.sleep(0.8)
    return client

def refresh_balance(client: MitzClient) -> Tuple[float, str]:
    try:
        res = client.balance()
        if res.get("ok"):
            return float(res.get("credits", 0)), str(res.get("role", "user"))
    except Exception:
        pass
    return 0.0, "—"

def single_device_check(client: MitzClient):
    credits, role = refresh_balance(client)
    big_banner(credits, role)
    console.print("[bold cyan]1. Device Id Single Check[/bold cyan]\n")

    if credits < 1:
        console.print("[bold red]No Credits....[/bold red]")
        console.print("Buy / ask owner for credits via Telegram bot.")
        pause()
        return

    raw = Prompt.ask("Enter Device Id").strip()
    did = normalize_device(raw)
    if not did:
        console.print("[red]Invalid device id (need and_… or ios_…)[/red]")
        pause()
        return

    _, mode_idx = _pick(
        ["Valid only", "Ban check", "Ban + Info"],
        "Mode",
    )
    mode = ("valid", "ban", "ban_info")[mode_idx]

    console.print("\n[cyan]Checking......[/cyan]")
    try:
        res = client.device_check(did, mode=mode)
    except Exception as e:
        console.print(f"[red]Request failed:[/red] {e}")
        pause()
        return

    if res.get("error") == "no_credits":
        console.print("[bold red]No Credits....[/bold red]")
        pause()
        return

    if not res.get("ok") and res.get("error"):
        console.print(f"[red]{res.get('error')}[/red]")
        pause()
        return

    result = res.get("result", "invalid")
    line = (
        f"{did} | Result: {result} | Role: {res.get('role_id')} ({res.get('zone_id')}) "
        f"| Country: {res.get('country')} | Created: {res.get('created')}"
    )
    if res.get("ban"):
        b = res["ban"]
        line += f" | Ban: {b.get('reason')} / Until {b.get('until')}"
    if res.get("info"):
        inf = res["info"]
        line += (
            f" | Name: {inf.get('name')} | Lv: {inf.get('level')} "
            f"| Devices: {inf.get('device_count')}"
        )

    color = "green" if result in ("valid", "active") else "magenta" if result == "banned" else "white"
    console.print(f"[{color}]{result.upper()}[/{color}]  {line}")

    kind = "banned" if result == "banned" else "valid" if result in ("valid", "active") else "invalid"
    save_line(kind, line)
    if res.get("info"):
        save_line("info", line)

    console.print(f"\n[dim]Credits left: {res.get('credits')}[/dim]")
    pause()

def bulk_device_check(client: MitzClient):
    credits, role = refresh_balance(client)
    big_banner(credits, role)
    console.print("[bold cyan]2. Bulk Device Id Check[/bold cyan]\n")
    console.print("[dim]Every device id costs 1 credit.[/dim]\n")

    if credits < 1:
        console.print("[bold red]No Credits....[/bold red]")
        pause()
        return

    choice, _ = _pick(["Load from .txt file", "Paste devices (one per line)"], "Input")
    devices: List[str] = []

    if choice.startswith("Load"):
        txts = sorted(f for f in os.listdir(".") if f.endswith(".txt") and os.path.isfile(f))
        opts = txts + ["[ Enter path ]"]
        sel, _ = _pick(opts, "File")
        path = Prompt.ask("Path").strip().strip("\"'") if sel == "[ Enter path ]" else sel
        if not os.path.isfile(path):
            console.print(f"[red]Not found: {path}[/red]")
            pause()
            return
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                d = normalize_device(line)
                if d:
                    devices.append(d)
    else:
        console.print("[dim]Paste devices, empty line to finish:[/dim]")
        while True:
            try:
                line = input()
            except EOFError:
                break
            if not line.strip():
                break
            d = normalize_device(line)
            if d:
                devices.append(d)

    seen = set()
    uniq = []
    for d in devices:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    devices = uniq

    if not devices:
        console.print("[red]No valid device ids.[/red]")
        pause()
        return

    console.print(f"[green]Loaded {len(devices)} device ids.[/green]")
    needed = len(devices)
    if credits < needed:
        console.print(
            f"[yellow]You have {credits} credits, need {needed}. "
            f"Will process first {int(credits)} only.[/yellow]"
        )
        devices = devices[: int(credits)]
        needed = len(devices)

    _, mode_idx = _pick(["Valid only", "Ban check"], "Mode")
    mode = ("valid", "ban")[mode_idx]

    chunk_size = 50
    chunks = [devices[i : i + chunk_size] for i in range(0, len(devices), chunk_size)]

    console.print(f"\n[cyan]Checking {len(devices)} devices……[/cyan]")
    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Bulk", total=len(chunks))
        for chunk in chunks:
            try:
                res = client.device_bulk(chunk, mode=mode)
            except Exception as e:
                console.print(f"[red]Chunk error:[/red] {e}")
                progress.advance(task)
                continue
            if res.get("error") == "no_credits":
                console.print("[bold red]No Credits....[/bold red]")
                break
            if not res.get("ok"):
                console.print(f"[red]{res.get('error')}[/red]")
                progress.advance(task)
                continue
            for item in res.get("results") or []:
                results.append(item)
                result = item.get("result", "invalid")
                line = (
                    f"{item.get('device_id')} | Result: {result} | "
                    f"Role: {item.get('role_id')} ({item.get('zone_id')})"
                )
                if item.get("ban"):
                    line += f" | Ban: {item['ban'].get('reason')} / {item['ban'].get('until')}"
                kind = (
                    "banned"
                    if result == "banned"
                    else "valid"
                    if result in ("valid", "active")
                    else "invalid"
                )
                save_line(kind, line)
            progress.advance(task)

    table = Table(title="Bulk summary", box=box.SIMPLE)
    table.add_column("Result")
    table.add_column("Count", justify="right")
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.get("result", "invalid")] = counts.get(r.get("result", "invalid"), 0) + 1
    for k, v in sorted(counts.items()):
        table.add_row(k, str(v))
    console.print(table)
    console.print(f"[dim]Saved under ./{RESULTS_DIR}/device_*.txt[/dim]")
    credits_left, _ = refresh_balance(client)
    console.print(f"[dim]Credits left: {credits_left}[/dim]")
    pause()

def more_menu(client: MitzClient):
    credits, role = refresh_balance(client)
    big_banner(credits, role)
    console.print(
        Panel(
            "[bold]More[/bold]\n\n"
            "• Account check / Full info → next update\n"
            "• Creation date / Ban check account → same gateway\n"
            "• All heavy logic stays on the server\n\n"
            "[dim]Your upstream MLBB keys never leave the server.[/dim]",
            border_style="magenta",
            expand=False,
        )
    )
    pause()

def _pick(options: List[str], title: str) -> Tuple[str, int]:
    console.print(f"\n[bold green]?[/bold green] [bold]{title}[/bold]")
    for i, o in enumerate(options, 1):
        console.print(f"  [cyan]{i}[/cyan]) {o}")
    while True:
        try:
            c = IntPrompt.ask(f"[yellow]Select (1-{len(options)})[/yellow]", default=1)
            if 1 <= c <= len(options):
                return options[c - 1], c - 1
        except Exception:
            pass
        console.print("[red]Invalid[/red]")

def main_menu(client: MitzClient):
    while True:
        credits, role = refresh_balance(client)
        big_banner(credits, role)
        console.print(
            "1. Device Id Single Check\n"
            "2. Bulk Device Id Check\n"
            "3. More (Account / Ban / etc)\n"
            "4. Refresh balance\n"
            "5. Exit — Close the tool\n"
        )
        choice = Prompt.ask("> Choice", default="1").strip()
        if choice == "1":
            single_device_check(client)
        elif choice == "2":
            bulk_device_check(client)
        elif choice == "3":
            more_menu(client)
        elif choice == "4":
            console.print(f"Credits: [green]{credits}[/green]  Role: {role}")
            time.sleep(1)
        elif choice == "5":
            console.print("\n[bold cyan]Goodbye![/bold cyan]")
            break
        else:
            console.print("[red]Invalid choice[/red]")
            time.sleep(0.6)

def main():
    state = load_state()
    client = login_screen(state)
    if not client:
        return
    try:
        main_menu(client)
    except KeyboardInterrupt:
        console.print("\n[cyan]Bye.[/cyan]")

if __name__ == "__main__":
    main()
