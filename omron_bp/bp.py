#!/usr/bin/env python3
"""
obp — Omron blood pressure CLI
Commands: scan | sync | list | stats | web
"""

import asyncio
import sys
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import box

from . import ble
from . import db
from . import devices
from . import tags

app = typer.Typer(no_args_is_help=True, help="Omron BP monitor sync tool")
console = Console()


def _validate_tags(names: list[str]) -> list[str]:
    """Ensure every name is in the vocabulary; else abort with a hint."""
    vocab = set(tags.list_all())
    unknown = [n for n in names if n not in vocab]
    if unknown:
        console.print(f"[red]Unknown tag(s):[/red] {', '.join(unknown)}")
        console.print("[dim]Add them first: obp tags add <name>[/dim]")
        raise typer.Exit(1)
    return names


def _date_range(days: int, since: Optional[str], until: Optional[str]):
    """Resolve --days/--since/--until into (since_iso, until_iso) or (None, None)."""
    from datetime import timedelta

    def _day(value: str, end: bool) -> str:
        d = datetime.strptime(value, "%Y-%m-%d")
        if end:
            d = d.replace(hour=23, minute=59, second=59)
        return d.isoformat()

    until_iso = _day(until, end=True) if until else None

    if since:
        return _day(since, end=False), until_iso
    if days and days > 0:
        start = datetime.now() - timedelta(days=days)
        return start.isoformat(), until_iso
    return None, until_iso


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
    t.add_column("ID",    style="dim", justify="right", min_width=3)
    t.add_column("Timestamp",  style="dim", min_width=19)
    t.add_column("Sys",   justify="right", min_width=4)
    t.add_column("Dia",   justify="right", min_width=4)
    t.add_column("MAP",   justify="right", min_width=4)
    t.add_column("Pulse", justify="right", min_width=5)
    t.add_column("User",  justify="right", min_width=4)
    t.add_column("Tags",  style="cyan")

    for r in records:
        color = _bp_color(r["systolic"], r["diastolic"])
        t.add_row(
            str(r.get("id") or "—"),
            r.get("timestamp") or "—",
            f"[{color}]{r['systolic']}[/{color}]",
            f"[{color}]{r['diastolic']}[/{color}]",
            str(r.get("mean_ap") or "—"),
            str(r.get("pulse") or "—"),
            str(r.get("user_id") or "1"),
            " ".join(r.get("tags") or []) or "[dim]—[/dim]",
        )

    console.print(t)


def _print_grouped(groups: list[dict], expand: bool):
    """Print sessions: lead (lowest) row, members nested when expand=True."""
    if not groups:
        console.print("[dim]No records.[/dim]")
        return

    t = Table(box=box.SIMPLE_HEAD, show_footer=False)
    t.add_column("ID",    style="dim", justify="right", min_width=3)
    t.add_column("Timestamp",  style="dim", min_width=19)
    t.add_column("Sys",   justify="right", min_width=4)
    t.add_column("Dia",   justify="right", min_width=4)
    t.add_column("MAP",   justify="right", min_width=4)
    t.add_column("Pulse", justify="right", min_width=5)
    t.add_column("User",  justify="right", min_width=4)
    t.add_column("Tags",  style="cyan")

    for grp in groups:
        lead = grp["lead"]
        color = _bp_color(lead["systolic"], lead["diastolic"])
        badge = f"  [yellow]▸{grp['count']}[/yellow]" if grp["count"] > 1 else ""
        t.add_row(
            str(lead.get("id") or "—"),
            (lead.get("timestamp") or "—") + badge,
            f"[{color}]{lead['systolic']}[/{color}]",
            f"[{color}]{lead['diastolic']}[/{color}]",
            str(lead.get("mean_ap") or "—"),
            str(lead.get("pulse") or "—"),
            str(lead.get("user_id") or "1"),
            " ".join(lead.get("tags") or []) or "[dim]—[/dim]",
        )
        if expand and grp["count"] > 1:
            for m in grp["members"]:
                if m is lead:
                    continue
                t.add_row(
                    f"[dim]{m.get('id')}[/dim]",
                    f"[dim]  └ {m.get('timestamp') or '—'}[/dim]",
                    f"[dim]{m['systolic']}[/dim]",
                    f"[dim]{m['diastolic']}[/dim]",
                    f"[dim]{m.get('mean_ap') or '—'}[/dim]",
                    f"[dim]{m.get('pulse') or '—'}[/dim]",
                    f"[dim]{m.get('user_id') or 1}[/dim]",
                    f"[dim]{' '.join(m.get('tags') or []) or '—'}[/dim]",
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
            console.print("[dim]Identify your Omron in the list, then: obp sync <address>[/dim]")
        _print_device_table(pairs)

    console.print("[dim]Use Address/UUID with [bold]obp sync <address>[/bold][/dim]")


@app.command()
def sync(
    address: str = typer.Argument(..., help="Device name (from obp devices) or UUID/address"),
    user: Optional[int] = typer.Option(None, help="Filter by user slot (1 or 2)"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Print raw BLE protocol events"),
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

    def on_debug(msg: str):
        console.print(f"[dim cyan]dbg  {msg}[/dim cyan]")

    console.print(f"Connecting to [bold]{resolved}[/bold]…")
    try:
        asyncio.run(ble.sync_records(
            resolved,
            progress_cb=on_record,
            debug_cb=on_debug if debug else None,
        ))
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
    days: int = typer.Option(30, help="Show last N days (0 = all time)"),
    since: Optional[str] = typer.Option(None, help="From date 'YYYY-MM-DD' (overrides --days)"),
    until: Optional[str] = typer.Option(None, help="To date 'YYYY-MM-DD'"),
    tag: Optional[str] = typer.Option(None, help="Filter by tag"),
    group: bool = typer.Option(True, "--group/--no-group", help="Group readings within --window minutes"),
    expand: bool = typer.Option(False, "--expand", "-e", help="Show grouped readings (not just the lowest)"),
    window: int = typer.Option(5, help="Session window in minutes for grouping"),
):
    """List stored blood pressure records with averages footer."""
    since_iso, until_iso = _date_range(days, since, until)
    if since_iso or until_iso:
        lo = since_iso[:10] if since_iso else "start"
        hi = until_iso[:10] if until_iso else "now"
        console.print(f"[dim]Range: {lo} → {hi}[/dim]")

    conn = db.init_db()
    records = db.fetch_all(conn, user_id=user, since=since_iso, until=until_iso, tag=tag)

    if group:
        groups = db.group_sessions(records, window_minutes=window)[:limit]
        _print_grouped(groups, expand=expand)
        if not expand and any(g["count"] > 1 for g in groups):
            console.print("[dim]▸N = readings within "
                          f"{window}m; lowest shown. Use --expand to see all.[/dim]")
    else:
        _print_records(records[:limit])

    s = db.fetch_stats(conn, user_id=user, since=since_iso, until=until_iso, tag=tag)
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
def stats(
    user: Optional[int] = typer.Option(None, help="Filter by user slot"),
    tag: Optional[str] = typer.Option(None, help="Filter by tag"),
):
    """Show averages and min/max from stored records."""
    conn = db.init_db()
    s = db.fetch_stats(conn, user_id=user, tag=tag)

    if s["count"] == 0:
        console.print("[yellow]No records. Run [bold]obp sync[/bold] first.[/yellow]")
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
    tag: Optional[str] = typer.Option(None, help="Filter by tag"),
):
    """Plot systolic/diastolic (and optionally pulse) over time."""
    import plotext as plt

    conn = db.init_db()
    records = db.fetch_all(conn, user_id=user, tag=tag)[:limit]
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


@app.command(name="pdf")
def export_pdf(
    user:   Optional[int] = typer.Option(None, help="Filter by user slot"),
    limit:  int           = typer.Option(200, help="Max records to include"),
    pulse:  bool          = typer.Option(False, "--pulse", "-p", help="Include pulse line in chart"),
    tag:    Optional[str] = typer.Option(None, help="Filter by tag"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path (default: ~/bp_report_YYYYMMDD.pdf)"),
):
    """Generate a printable PDF report with table and chart."""
    import io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table as RLTable, TableStyle, KeepTogether
    from reportlab.graphics.shapes import Drawing, Line, String
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics import renderPDF

    conn = db.init_db()
    records = db.fetch_all(conn, user_id=user, tag=tag)[:limit]

    if not records:
        console.print("[yellow]No records.[/yellow]")
        raise typer.Exit(1)

    out_path = output or f"{datetime.now().strftime('%Y%m%d')}_bp_report.pdf"

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], fontSize=16, spaceAfter=6)
    sub_style   = ParagraphStyle("sub",   parent=styles["Normal"],   fontSize=9, textColor=colors.grey)

    story = []

    # ── Title ─────────────────────────────────────────────────────────────────
    user_label = f" — User {user}" if user else ""
    story.append(Paragraph(f"Blood Pressure Report{user_label}", title_style))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}  ·  {len(records)} records", sub_style))
    story.append(Spacer(1, 0.4*cm))

    # ── Stats summary ─────────────────────────────────────────────────────────
    s = db.fetch_stats(conn, user_id=user, tag=tag)
    if s["count"]:
        sv, dv, pv = s["systolic"], s["diastolic"], s["pulse"]
        summary = (
            f"<b>Avg:</b> {sv['avg']}/{dv['avg']} mmHg  "
            f"<b>Min:</b> {sv['min']}/{dv['min']}  "
            f"<b>Max:</b> {sv['max']}/{dv['max']}  "
            f"<b>Pulse avg:</b> {pv['avg']} bpm"
        )
        story.append(Paragraph(summary, styles["Normal"]))
        story.append(Spacer(1, 0.5*cm))

    # ── Chart ─────────────────────────────────────────────────────────────────
    chart_records = list(reversed(records))  # oldest → newest left-to-right
    xs     = list(range(1, len(chart_records) + 1))
    sys_v  = [r["systolic"]  for r in chart_records]
    dia_v  = [r["diastolic"] for r in chart_records]
    pul_v  = [r.get("pulse") or 0 for r in chart_records]

    W, H = 17*cm, 8*cm
    d = Drawing(W, H)

    lp = LinePlot()
    lp.x, lp.y = 1.2*cm, 0.8*cm
    lp.width    = W - 2.4*cm
    lp.height   = H - 1.6*cm

    series = [(sys_v, colors.red, "Systolic"), (dia_v, colors.steelblue, "Diastolic")]
    if pulse and any(pul_v):
        series.append((pul_v, colors.darkorange, "Pulse"))

    lp.data = [list(zip(xs, vals)) for vals, _, _ in series]

    for i, (_, color, _) in enumerate(series):
        lp.lines[i].strokeColor = color
        lp.lines[i].strokeWidth = 1.5

    lp.xValueAxis.valueMin = 1
    lp.xValueAxis.valueMax = len(xs)
    lp.xValueAxis.labelTextFormat = ""
    lp.yValueAxis.valueMin = 40
    lp.yValueAxis.valueMax = max(max(sys_v), 160) + 10

    # Reference lines at 140 (sys) and 90 (dia)
    def _ref_line(y_val, color):
        y_pct = (y_val - lp.yValueAxis.valueMin) / (lp.yValueAxis.valueMax - lp.yValueAxis.valueMin)
        y_px  = lp.y + y_pct * lp.height
        ln = Line(lp.x, y_px, lp.x + lp.width, y_px)
        ln.strokeColor    = color
        ln.strokeWidth    = 0.5
        ln.strokeDashArray = [4, 3]
        return ln

    d.add(lp)
    d.add(_ref_line(140, colors.red))
    d.add(_ref_line(90,  colors.steelblue))

    legend = Legend()
    legend.x, legend.y   = lp.x, H - 0.7*cm
    legend.deltax = 80
    legend.colorNamePairs = [(color, name) for _, color, name in series]
    legend.columnMaximum  = len(series)
    legend.fontName = "Helvetica"
    legend.fontSize = 8
    d.add(legend)

    story.append(KeepTogether([renderPDF.GraphicsFlowable(d)]))
    story.append(Spacer(1, 0.4*cm))

    # ── Table ─────────────────────────────────────────────────────────────────
    header = ["#", "Timestamp", "Systolic", "Diastolic", "MAP", "Pulse", "User", "Tags"]
    rows   = [header]

    def _rl_color(sys, dia):
        if sys >= 140 or dia >= 90: return colors.HexColor("#c0392b")
        if sys >= 130 or dia >= 80: return colors.HexColor("#e67e22")
        return colors.HexColor("#27ae60")

    cell_colors = [None]  # header row placeholder
    for r in records:
        rows.append([
            str(r["id"]),
            r.get("timestamp") or "—",
            str(r["systolic"]),
            str(r["diastolic"]),
            str(r.get("mean_ap") or "—"),
            str(r.get("pulse") or "—"),
            str(r.get("user_id") or "1"),
            ", ".join(r.get("tags") or []) or "—",
        ])
        cell_colors.append(_rl_color(r["systolic"], r["diastolic"]))

    tbl = RLTable(rows, repeatRows=1)

    tbl_style = [
        ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#dee2e6")),
        ("ALIGN",       (2, 0), (6, -1), "RIGHT"),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for row_idx, color in enumerate(cell_colors[1:], start=1):
        for col in (2, 3):  # systolic, diastolic columns
            tbl_style.append(("TEXTCOLOR", (col, row_idx), (col, row_idx), color))

    tbl.setStyle(TableStyle(tbl_style))
    story.append(tbl)

    doc.build(story)
    console.print(f"[green]PDF saved:[/green] {out_path}")


def _parse_ts(value: str) -> str:
    """Accept 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD HH:MM:SS', return ISO string."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).isoformat()
        except ValueError:
            pass
    raise typer.BadParameter(f"expected YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS, got {value!r}")


@app.command(name="add")
def add_record(
    systolic:  Optional[int] = typer.Argument(None, help="Systolic (mmHg)"),
    diastolic: Optional[int] = typer.Argument(None, help="Diastolic (mmHg)"),
    pulse:     Optional[int] = typer.Option(None,  help="Pulse (bpm)"),
    timestamp: Optional[str] = typer.Option(None,  help="'YYYY-MM-DD HH:MM'"),
    user:      int            = typer.Option(1,     help="User slot (1 or 2)"),
    tag:       list[str]      = typer.Option(None,  "--tag", "-t", help="Attach tag (repeatable)"),
    interactive: bool         = typer.Option(False, "--interactive", "-i"),
):
    """Manually add a blood pressure record."""
    tag = _validate_tags(tag) if tag else []
    if interactive or systolic is None:
        systolic  = typer.prompt("Systolic",  type=int, default=systolic)
        diastolic = typer.prompt("Diastolic", type=int, default=diastolic)
        pulse     = typer.prompt("Pulse",     type=int, default=pulse or 0) or None
        ts_raw    = typer.prompt("Timestamp (YYYY-MM-DD HH:MM, blank=none)", default="")
        timestamp = _parse_ts(ts_raw) if ts_raw.strip() else None
        user      = typer.prompt("User slot", type=int, default=user)
    else:
        if diastolic is None:
            console.print("[red]Error:[/red] diastolic required")
            raise typer.Exit(1)
        timestamp = _parse_ts(timestamp) if timestamp else None

    conn = db.init_db()
    rec = {
        "systolic":  systolic,
        "diastolic": diastolic,
        "pulse":     pulse,
        "timestamp": timestamp,
        "user":      user,
        "tags":      tag,
    }
    if db.insert_measurement(conn, rec):
        tag_str = ("  tags: " + " ".join(tag)) if tag else ""
        console.print(f"[green]Added.[/green]  {timestamp or '—'}  {systolic}/{diastolic} mmHg  pulse {pulse or '—'}{tag_str}")
    else:
        console.print("[yellow]Duplicate — not inserted.[/yellow]")


@app.command(name="edit")
def edit_record(
    record_id: int            = typer.Argument(..., help="Record ID (from obp list)"),
    systolic:  Optional[int]  = typer.Option(None, help="New systolic"),
    diastolic: Optional[int]  = typer.Option(None, help="New diastolic"),
    pulse:     Optional[int]  = typer.Option(None, help="New pulse"),
    timestamp: Optional[str]  = typer.Option(None, help="New timestamp 'YYYY-MM-DD HH:MM'"),
    user:      Optional[int]  = typer.Option(None, help="New user slot"),
    add_tag:   list[str]      = typer.Option(None, "--add-tag", help="Add tag (repeatable)"),
    rm_tag:    list[str]      = typer.Option(None, "--rm-tag", help="Remove tag (repeatable)"),
    interactive: bool         = typer.Option(False, "--interactive", "-i"),
):
    """Edit a stored record by ID."""
    conn = db.init_db()
    rec = db.fetch_by_id(conn, record_id)
    if not rec:
        console.print(f"[red]No record with id {record_id}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[dim]#{rec['id']}[/dim]  {rec.get('timestamp') or '—'}  "
        f"[bold]{rec['systolic']}/{rec['diastolic']}[/bold] mmHg  "
        f"pulse {rec.get('pulse') or '—'}  user {rec.get('user_id') or 1}  "
        f"tags {' '.join(rec.get('tags') or []) or '—'}"
    )

    if interactive:
        ts_raw    = typer.prompt("Timestamp (blank=keep)", default=rec.get("timestamp") or "")
        systolic  = typer.prompt("Systolic",  type=int, default=rec["systolic"])
        diastolic = typer.prompt("Diastolic", type=int, default=rec["diastolic"])
        pulse     = typer.prompt("Pulse",     type=int, default=rec.get("pulse") or 0) or None
        user      = typer.prompt("User slot", type=int, default=rec.get("user_id") or 1)
        timestamp = _parse_ts(ts_raw) if ts_raw.strip() else rec.get("timestamp")

    fields: dict = {}
    if systolic  is not None: fields["systolic"]  = systolic
    if diastolic is not None: fields["diastolic"] = diastolic
    if pulse     is not None: fields["pulse"]     = pulse
    if user      is not None: fields["user_id"]   = user
    if timestamp is not None: fields["timestamp"] = _parse_ts(timestamp) if not interactive else timestamp

    if add_tag or rm_tag:
        _validate_tags(add_tag or [])
        new_tags = list(rec.get("tags") or [])
        for tg in (add_tag or []):
            if tg not in new_tags:
                new_tags.append(tg)
        for tg in (rm_tag or []):
            if tg in new_tags:
                new_tags.remove(tg)
        fields["tags"] = new_tags

    if not fields:
        console.print("[dim]Nothing to update.[/dim]")
        return

    if db.update_measurement(conn, record_id, fields):
        console.print(f"[green]Updated #{record_id}.[/green]")
    else:
        console.print(f"[red]Update failed.[/red]")


@app.command(name="rm")
def remove_record(
    record_id: int  = typer.Argument(..., help="Record ID (from obp list)"),
    yes:       bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a stored record by ID."""
    conn = db.init_db()
    rec = db.fetch_by_id(conn, record_id)
    if not rec:
        console.print(f"[red]No record with id {record_id}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[dim]#{rec['id']}[/dim]  {rec.get('timestamp') or '—'}  "
        f"[bold]{rec['systolic']}/{rec['diastolic']}[/bold] mmHg  "
        f"pulse {rec.get('pulse') or '—'}"
    )

    if not yes:
        typer.confirm("Delete this record?", abort=True)

    if db.delete_measurement(conn, record_id):
        console.print(f"[green]Deleted #{record_id}.[/green]")
    else:
        console.print(f"[red]Delete failed.[/red]")


@app.command(name="web")
def web_ui(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
):
    """Start a local web UI to browse records."""
    from . import web

    url = f"http://{host}:{port}"
    console.print(f"Serving on [bold]{url}[/bold]  [dim](Ctrl-C to stop)[/dim]")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    try:
        web.serve(host, port)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


devices_app = typer.Typer(help="Manage saved device aliases.")
app.add_typer(devices_app, name="devices")


@devices_app.command(name="add")
def devices_add(
    name: str = typer.Argument(..., help="Friendly name"),
    address: str = typer.Argument(..., help="Device UUID/address from obp scan"),
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
        console.print("[dim]No saved devices. Use: obp devices add <name> <uuid>[/dim]")
        return
    t = Table(box=box.SIMPLE_HEAD)
    t.add_column("Name", style="bold")
    t.add_column("Address / UUID")
    for name, addr in saved.items():
        t.add_row(name, addr)
    console.print(t)


tags_app = typer.Typer(help="Manage the tag vocabulary.")
app.add_typer(tags_app, name="tags")


@tags_app.command(name="add")
def tags_add(name: str = typer.Argument(..., help="Tag name")):
    """Add a tag to the vocabulary."""
    if tags.add(name):
        console.print(f"[green]Added tag:[/green] [cyan]{name}[/cyan]")
    else:
        console.print(f"[yellow]Tag already exists or empty:[/yellow] {name}")


@tags_app.command(name="rm")
def tags_rm(name: str = typer.Argument(..., help="Tag name to remove")):
    """Remove a tag from the vocabulary and strip it from all records."""
    if tags.remove(name):
        conn = db.init_db()
        n = db.remove_tag_from_all(conn, name)
        console.print(f"[dim]Removed tag:[/dim] {name}  [dim](cleared from {n} record(s))[/dim]")
    else:
        console.print(f"[yellow]Not found:[/yellow] {name}")
        raise typer.Exit(1)


@tags_app.command(name="ls")
def tags_ls():
    """List all tags in the vocabulary."""
    vocab = tags.list_all()
    if not vocab:
        console.print("[dim]No tags. Use: obp tags add <name>[/dim]")
        return
    console.print("  ".join(f"[cyan]{t}[/cyan]" for t in vocab))


if __name__ == "__main__":
    app()
