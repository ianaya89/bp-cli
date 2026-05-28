#!/usr/bin/env python3
"""
bp — Omron blood pressure CLI
Commands: scan | sync | list | stats
"""

import asyncio
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import box

import ble
import db
import devices

app = typer.Typer(no_args_is_help=True, help="Omron BP monitor sync tool")
console = Console()


def _bp_color(systolic: int, diastolic: int) -> str:
    if systolic >= 140 or diastolic >= 90:
        return "red"
    if systolic >= 130 or diastolic >= 80:
        return "yellow"
    return "green"


def _print_records(records: list[dict]):
    if not records:
        console.print("[dim]No records.[/dim]")
        return

    t = Table(box=box.SIMPLE_HEAD, show_footer=False)
    t.add_column("Timestamp",  style="dim", min_width=19)
    t.add_column("Sys",   justify="right", min_width=4)
    t.add_column("Dia",   justify="right", min_width=4)
    t.add_column("MAP",   justify="right", min_width=4)
    t.add_column("Pulse", justify="right", min_width=5)
    t.add_column("User",  justify="right", min_width=4)

    for r in records:
        color = _bp_color(r["systolic"], r["diastolic"])
        t.add_row(
            r.get("timestamp") or "—",
            f"[{color}]{r['systolic']}[/{color}]",
            f"[{color}]{r['diastolic']}[/{color}]",
            str(r.get("mean_ap") or "—"),
            str(r.get("pulse") or "—"),
            str(r.get("user_id") or "1"),
        )

    console.print(t)


def _print_device_table(pairs):
    t = Table(box=box.SIMPLE_HEAD)
    t.add_column("Name")
    t.add_column("Address / UUID")
    t.add_column("RSSI", justify="right")
    for dev, adv in pairs:
        rssi = f"{adv.rssi} dBm" if adv.rssi else "—"
        t.add_row(dev.name or "[dim]<unknown>[/dim]", dev.address, rssi)
    console.print(t)


async def _scan_all_fallback(timeout: float):
    """Single coroutine: scan Omron, fallback to all if none found."""
    pairs = await ble.scan_devices(timeout=timeout, all_devices=False)
    if pairs:
        return pairs, False  # (results, is_fallback)
    all_pairs = await ble.scan_devices(timeout=5.0, all_devices=True)
    return all_pairs, True


# ── Commands ──────────────────────────────────────────────────────────────────

@app.command()
def scan(
    timeout: float = typer.Option(10.0, help="Scan duration in seconds"),
    all: bool = typer.Option(False, "--all", "-a", help="Show all BLE devices, not just Omron"),
):
    """Scan for nearby Omron BLE devices."""
    console.print(f"Scanning for [bold]{timeout}s[/bold]…")

    if all:
        pairs = asyncio.run(ble.scan_devices(timeout=timeout, all_devices=True))
        if not pairs:
            console.print("[red]No BLE devices found — Terminal may lack Bluetooth permission.[/red]")
            console.print("[dim]System Settings → Privacy & Security → Bluetooth → add Terminal[/dim]")
            raise typer.Exit(1)
        _print_device_table(pairs)
    else:
        pairs, is_fallback = asyncio.run(_scan_all_fallback(timeout))
        if not pairs:
            console.print("[red]No BLE devices found at all — Terminal lacks Bluetooth permission.[/red]")
            console.print("[dim]System Settings → Privacy & Security → Bluetooth → add Terminal[/dim]")
            raise typer.Exit(1)
        if is_fallback:
            console.print("[yellow]No Omron devices found. All visible BLE devices:[/yellow]")
            console.print("[dim]Identify your Omron in the list, then: bp sync <address>[/dim]")
        _print_device_table(pairs)

    console.print("[dim]Use Address/UUID with [bold]bp sync <address>[/bold][/dim]")


@app.command()
def sync(
    address: str = typer.Argument(..., help="Device name (from bp devices) or UUID/address"),
    user: Optional[int] = typer.Option(None, help="Filter by user slot (1 or 2)"),
):
    """Connect to device and sync all stored records."""
    resolved = devices.resolve(address)
    if resolved != address:
        console.print(f"[dim]{address}[/dim] → [bold]{resolved}[/bold]")

    conn = db.init_db()
    new_count = 0
    total = 0

    def on_record(rec: dict):
        nonlocal new_count, total
        total += 1
        if user and rec.get("user") and rec["user"] != user:
            return
        is_new = db.insert_measurement(conn, rec)
        if is_new:
            new_count += 1
        ts = rec.get("timestamp", "no timestamp")
        status = "[green]+new[/green]" if is_new else "[dim]dup[/dim]"
        console.print(
            f"  {status}  {ts}  "
            f"[bold]{rec['systolic']}/{rec['diastolic']}[/bold] mmHg  "
            f"pulse {rec.get('pulse', '—')}"
        )

    console.print(f"Connecting to [bold]{resolved}[/bold]…")
    try:
        asyncio.run(ble.sync_records(resolved, progress_cb=on_record))
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"\n[bold]Done.[/bold] {total} records received, {new_count} new saved.")


@app.command()
def discover(address: str = typer.Argument(..., help="Device UUID from scan")):
    """Dump all GATT services and characteristics (debug)."""
    async def _discover():
        from bleak import BleakClient
        async with BleakClient(address) as client:
            for svc in client.services:
                console.print(f"\n[bold cyan]Service[/bold cyan] {svc.uuid}  [dim]{svc.description}[/dim]")
                for char in svc.characteristics:
                    props = ", ".join(char.properties)
                    console.print(f"  [green]Char[/green] {char.uuid}  [{props}]  [dim]{char.description}[/dim]")
                    for desc in char.descriptors:
                        console.print(f"    [dim]Desc {desc.uuid}[/dim]")

    console.print(f"Connecting to [bold]{address}[/bold]…")
    try:
        asyncio.run(_discover())
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command(name="probe")
def probe(
    address: str = typer.Argument(..., help="Device UUID from scan"),
    wait: int = typer.Option(15, help="Seconds to listen for notifications"),
):
    """Subscribe to all Omron chars, write trigger commands, dump raw bytes (debug)."""
    OMRON_DATA   = "b305b680-aee7-11e1-a730-0002a5d5c51b"
    OMRON_CMD    = "db5b55e0-aee7-11e1-965e-0002a5d5c51b"
    OMRON_STATUS = "49123040-aee8-11e1-a74d-0002a5d5c51b"
    BP_MEASURE   = "00002a35-0000-1000-8000-00805f9b34fb"

    async def _probe():
        from bleak import BleakClient
        import struct
        from datetime import datetime

        def dump(label):
            def handler(_, data: bytearray):
                console.print(f"[bold yellow]{label}[/bold yellow]  {data.hex()}  {list(data)}")
            return handler

        async with BleakClient(address) as client:
            console.print("Connected. Subscribing to notifications…")

            for uuid, label in [
                (BP_MEASURE,   "BP_MEAS  "),
                (OMRON_DATA,   "OMRON_DAT"),
                (OMRON_STATUS, "OMRON_STA"),
            ]:
                try:
                    await client.start_notify(uuid, dump(label))
                    console.print(f"  [green]subscribed[/green] {label} {uuid}")
                except Exception as e:
                    console.print(f"  [dim]skip {label}: {e}[/dim]")

            # Try reading readable chars
            for uuid, label in [
                ("00002a29-0000-1000-8000-00805f9b34fb", "Manufacturer"),
                ("00002a24-0000-1000-8000-00805f9b34fb", "Model"),
                ("00002a25-0000-1000-8000-00805f9b34fb", "Serial"),
                (OMRON_CMD, "OMRON_CMD (read)"),
            ]:
                try:
                    val = await client.read_gatt_char(uuid)
                    console.print(f"  [cyan]read[/cyan] {label}: {bytes(val).decode(errors='replace')!r}  hex={bytes(val).hex()}")
                except Exception as e:
                    console.print(f"  [dim]read {label} failed: {e}[/dim]")

            # Write trigger commands and watch what comes back
            commands = [
                ("zeros-20",    bytes(20)),
                ("zeros-17",    bytes(17)),
                ("01-request",  bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                                       0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                                       0x00, 0x00, 0x00, 0x00])),
            ]
            for name, cmd in commands:
                try:
                    console.print(f"\nWriting [{name}] → OMRON_CMD: {cmd.hex()}")
                    await client.write_gatt_char(OMRON_CMD, cmd, response=True)
                    console.print("  write OK — waiting for notifications…")
                    await asyncio.sleep(wait / len(commands))
                except Exception as e:
                    console.print(f"  [red]write failed:[/red] {e}")

            console.print(f"\nListening {wait}s total — press Ctrl-C to stop early…")
            await asyncio.sleep(wait)

    console.print(f"Probing [bold]{address}[/bold]…")
    try:
        asyncio.run(_probe())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command(name="list")
def list_records(
    user: Optional[int] = typer.Option(None, help="Filter by user slot"),
    limit: int = typer.Option(50, help="Max records to show"),
):
    """List stored blood pressure records with averages footer."""
    conn = db.init_db()
    records = db.fetch_all(conn, user_id=user)[:limit]
    _print_records(records)

    s = db.fetch_stats(conn, user_id=user)
    if s["count"]:
        sv, dv, pv = s["systolic"], s["diastolic"], s["pulse"]
        avg_color = _bp_color(int(sv["avg"] or 0), int(dv["avg"] or 0))
        min_color = _bp_color(int(sv["min"] or 0), int(dv["min"] or 0))
        max_color = _bp_color(int(sv["max"] or 0), int(dv["max"] or 0))
        console.print(
            f"[dim]{s['count']} records[/dim]  "
            f"avg [{avg_color}][bold]{sv['avg']}/{dv['avg']}[/bold][/{avg_color}]  "
            f"min [{min_color}]{sv['min']}/{dv['min']}[/{min_color}]  "
            f"max [{max_color}]{sv['max']}/{dv['max']}[/{max_color}]  "
            f"[dim]mmHg[/dim]  pulse avg {pv['avg']} min {pv['min']} max {pv['max']}"
        )


@app.command()
def stats(user: Optional[int] = typer.Option(None, help="Filter by user slot")):
    """Show averages and min/max from stored records."""
    conn = db.init_db()
    s = db.fetch_stats(conn, user_id=user)

    if s["count"] == 0:
        console.print("[yellow]No records. Run [bold]bp sync[/bold] first.[/yellow]")
        raise typer.Exit(1)

    t = Table(box=box.SIMPLE_HEAD, title=f"Stats ({s['count']} records)")
    t.add_column("Metric",    style="bold")
    t.add_column("Avg",  justify="right")
    t.add_column("Min",  justify="right")
    t.add_column("Max",  justify="right")

    for key in ("systolic", "diastolic", "pulse"):
        v = s[key]
        t.add_row(
            key.capitalize(),
            str(v["avg"]) if v["avg"] else "—",
            str(v["min"]) if v["min"] else "—",
            str(v["max"]) if v["max"] else "—",
        )

    console.print(t)

    avg_sys = s["systolic"]["avg"] or 0
    avg_dia = s["diastolic"]["avg"] or 0
    color = _bp_color(int(avg_sys), int(avg_dia))
    label = {
        "green": "Normal",
        "yellow": "Elevated",
        "red": "High",
    }[color]
    console.print(f"Average category: [{color}]{label}[/{color}]")


@app.command()
def chart(
    user: Optional[int] = typer.Option(None, help="Filter by user slot"),
    limit: int = typer.Option(60, help="Max records to plot"),
    pulse: bool = typer.Option(False, "--pulse", "-p", help="Also plot pulse rate"),
):
    """Plot systolic/diastolic (and optionally pulse) over time."""
    import plotext as plt

    conn = db.init_db()
    records = db.fetch_all(conn, user_id=user)[:limit]
    records = list(reversed(records))  # oldest first for left→right

    if not records:
        console.print("[yellow]No records.[/yellow]")
        raise typer.Exit(1)

    xs     = list(range(1, len(records) + 1))
    sys_v  = [r["systolic"]  for r in records]
    dia_v  = [r["diastolic"] for r in records]
    pul_v  = [r.get("pulse") or 0 for r in records]

    # x-axis labels: timestamp if available, else record index
    labels = []
    for r in records:
        ts = r.get("timestamp")
        if ts and ts != "None":
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(ts)
                labels.append(dt.strftime("%m/%d %H:%M"))
            except Exception:
                labels.append(str(r["id"]))
        else:
            labels.append(str(r["id"]))

    plt.clf()
    plt.theme("dark")
    plt.plot_size(plt.terminal_width(), 28)

    plt.plot(xs, sys_v,  label="Systolic",  color="red")
    plt.plot(xs, dia_v,  label="Diastolic", color="cyan")
    if pulse and any(pul_v):
        plt.plot(xs, pul_v, label="Pulse", color="yellow")

    # Reference lines
    plt.hline(140, color="red+")
    plt.hline(90,  color="cyan+")

    plt.xticks(xs[::max(1, len(xs)//10)], labels[::max(1, len(xs)//10)])
    plt.xlabel("Measurement")
    plt.ylabel("mmHg")
    plt.title(f"Blood Pressure{' — User ' + str(user) if user else ''}")

    plt.show()


devices_app = typer.Typer(help="Manage saved device aliases.")
app.add_typer(devices_app, name="devices")


@devices_app.command(name="add")
def devices_add(
    name: str = typer.Argument(..., help="Friendly name"),
    address: str = typer.Argument(..., help="Device UUID/address from bp scan"),
):
    """Save a friendly name for a device address."""
    devices.add(name, address)
    console.print(f"[green]Saved:[/green] [bold]{name}[/bold] → {address}")


@devices_app.command(name="rm")
def devices_rm(name: str = typer.Argument(..., help="Name to remove")):
    """Remove a saved device alias."""
    if devices.remove(name):
        console.print(f"[dim]Removed:[/dim] {name}")
    else:
        console.print(f"[yellow]Not found:[/yellow] {name}")
        raise typer.Exit(1)


@devices_app.command(name="ls")
def devices_ls():
    """List all saved device aliases."""
    saved = devices.list_all()
    if not saved:
        console.print("[dim]No saved devices. Use: bp devices add <name> <uuid>[/dim]")
        return
    t = Table(box=box.SIMPLE_HEAD)
    t.add_column("Name", style="bold")
    t.add_column("Address / UUID")
    for name, addr in saved.items():
        t.add_row(name, addr)
    console.print(t)


if __name__ == "__main__":
    app()
