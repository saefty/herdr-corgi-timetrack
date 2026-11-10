#!/usr/bin/env python3
"""Work-time tracker for Herdr. Python stdlib only.

A detached sampler (one instance, flock on daemon.lock) looks every `sample_seconds` at:
  - macOS HID idle time (ioreg), the frontmost app (lsappinfo): no extra permissions needed;
  - herdr.json: what the chip in Herdr reports every 10 s (the shown space and the working agents,
    each with its context, and which contexts count; see PROVIDERS.md). Older than 90 s = Herdr isn't
    running, so Herdr doesn't count.
A sample qualifies when the user is not idle AND (a work app is frontmost, OR Herdr is frontmost in a
context that counts, OR an agent in a context that counts is working (unless Herdr is frontmost in a
context that doesn't count)). Without a context hook every context counts.
Segments: qualifying samples less than `gap_minutes` apart are one segment; after a longer gap the
segment ends at its last qualifying sample (backdated). Only segments are stored, one JSONL per
month; a year workbook (.xlsx, written by hand with zipfile) mirrors them.

The daemon keeps running when Herdr quits (app/idle sampling is independent of Herdr; it costs two
tiny subprocesses per sample) and is (re)started by the chip via --facts. It re-execs
itself when this file changes. A kill loses nothing: the open segment is in state.json and gets
closed, backdated, by the next run.

CLI: --facts JSON (from Herdr; also starts the daemon) | --spawn | --run | --stop | --status
     | --pause | --resume | --notwork | --set DATE start=08:15 end= breaks=0:30 note=… | --excel | --selftest

Corrections: before every rewrite the workbook is read back; each Start/End/Breaks/Note that differs
from what the tracker last wrote (written.json) becomes a correction in overrides.json and wins from then
on (shown in italics). The popup's edits land there too. Nothing is written while the file is open
(lsof, or Excel running with its "~$" owner file), nor while timetrack-data/xlsx.hold exists.
"""
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import traceback
import zipfile
import xml.etree.ElementTree as ET  # our own workbook, or Excel's save of it
from collections import Counter
from contextlib import contextmanager
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
APP_HOME = os.path.dirname(os.path.dirname(HERE))  # this file is <app home>/mods/timetrack/track.py
DATA = os.path.expanduser(os.environ.get("TIMETRACK_DATA") or os.path.join(APP_HOME, "timetrack-data"))
CONFIG = os.path.join(HERE, "config.json")  # the mod's defaults
USER_CONFIG = os.path.join(APP_HOME, "timetrack.config.json")  # yours, on top; survives updating the mod
FACTS_MAX_AGE = 90
HERDR_BUNDLE = "dev.herdr.app"

DEFAULTS = {
    "sample_seconds": 30,
    "idle_seconds": 120,
    "gap_minutes": 10,
    "min_segment_seconds": 60,
    "week_target_hours": 40,
    "excel_dir": "~/Documents/Timesheets",
    "excel_file": "Timesheet-{year}.xlsx",
    "sheet_language": "en",
    "excel_every_minutes": 15,
    "keep_months": 24,
    "work_apps": [],
}


def path(name):
    return os.path.join(DATA, name)


def load_cfg():
    cfg = dict(DEFAULTS)
    for p in (CONFIG, USER_CONFIG):
        if p == USER_CONFIG and not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                cfg.update(json.load(f))
        except (OSError, ValueError) as e:
            log(f"{os.path.basename(p)} unreadable, skipped: {e}")
    return cfg


def log(msg):
    try:
        os.makedirs(DATA, exist_ok=True)
        p = path("tracker.log")
        if os.path.exists(p) and os.path.getsize(p) > 512_000:
            os.replace(p, p + ".1")
        with open(p, "a") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def read_json(p, default):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_atomic(p, data, mode="w"):
    tmp = f"{p}.tmp-{os.getpid()}"
    with open(tmp, mode) as f:
        f.write(data)
    os.replace(tmp, p)


@contextmanager
def locked():
    os.makedirs(DATA, exist_ok=True)
    with open(path("state.lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


# ---------- sampling ----------

def run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout


def sample_idle():
    """Seconds since the last mouse/keyboard input, or None if unknown."""
    try:
        m = re.search(r'"HIDIdleTime" = (\d+)', run(["ioreg", "-c", "IOHIDSystem", "-d", "4"]))
        return int(m.group(1)) / 1e9 if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def sample_front():
    """(name, bundle id) of the frontmost app, or (None, None)."""
    try:
        asn = run(["lsappinfo", "front"]).strip()
        out = run(["lsappinfo", "info", "-only", "name", "-only", "bundleid", asn]) if asn else ""
    except (OSError, subprocess.SubprocessError):
        return None, None
    name = re.search(r'^"([^"]*)"', out, re.M)
    bid = re.search(r'bundleID="([^"]*)"', out)
    return (name.group(1) if name else None), (bid.group(1) if bid else None)


def read_facts(now):
    f = read_json(path("herdr.json"), None)
    if not isinstance(f, dict) or now - f.get("at", 0) / 1000 > FACTS_MAX_AGE:
        return None
    return f


def counts(context, facts):
    """Does a Herdr context count as work? Without a context hook, all of Herdr counts. With one, a context in
    `ignore` doesn't, and when `count` lists contexts only those do (an unknown context then doesn't)."""
    if not facts.get("hook"):
        return True
    if context in (facts.get("ignore") or []):
        return False
    count = facts.get("count") or []
    return not count or context in count


def evaluate(idle, front, facts, cfg):
    """(qualifies, contexts, projects) for one sample. facts: herdr.json (PROVIDERS.md), None when stale."""
    if idle is not None and idle >= cfg["idle_seconds"]:
        return False, [], []
    name, bid = front
    herdr_front = bid == HERDR_BUNDLE or name == "Herdr"
    f = facts or {}
    shown = f.get("focused") or {}
    herdr_counts = bool(facts) and counts(shown.get("context"), f) and (f.get("current") is None or counts(f["current"], f))
    ctx, proj = [], []
    if herdr_front and herdr_counts:
        ctx.append("Herdr")
        if shown.get("name"):
            proj.append(shown["name"])
    working = [a.get("name") or "" for a in f.get("working") or [] if counts(a.get("context"), f)]
    if working and not (herdr_front and not herdr_counts):  # Herdr shown in a context that doesn't count wins
        ctx.append("Agents")
        proj += list(dict.fromkeys(working))
    apps = {a.lower() for a in cfg["work_apps"]}
    if not herdr_front and ((name or "").lower() in apps or (bid or "").lower() in apps):
        ctx.append(name or bid)
    return bool(ctx), ctx, [p for p in proj if p not in ("~", "")]  # "~": a space at home is no project


# ---------- segments ----------

def iso(ts):
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def epoch(s):
    return dt.datetime.fromisoformat(s).timestamp()


def day_of(ts):
    return dt.date.fromtimestamp(ts)


def midnight_after(ts):
    d = day_of(ts) + dt.timedelta(days=1)
    return dt.datetime(d.year, d.month, d.day).timestamp()


def split_days(s, e):
    """[(s, e)] cut at local midnights."""
    out = []
    while day_of(s) != day_of(e) and midnight_after(s) < e:
        m = midnight_after(s)
        out.append((s, m))
        s = m
    out.append((s, e))
    return out


def close(o, end=None):
    return {
        "start": o["start"], "end": o["last"] if end is None else end, "source": o["source"],
        "context": [k for k, _ in Counter(o["ctx"]).most_common()],
        "projects": [k for k, _ in Counter(o["proj"]).most_common(5)],
    }


def step(st, now, ok, ctx, proj, cfg, active=False):
    """Advance the state by one sample; returns the segments it closed (epoch start/end).
    active: the user isn't idle. A segment started by Resume (source manual) runs on that alone."""
    closed = []
    o = st.get("open")
    ok = ok or bool(o and o["source"] == "manual" and active)
    if o and now - o["last"] > cfg["gap_minutes"] * 60:
        closed.append(close(o))  # backdated to the last qualifying sample
        st["open"] = o = None
    if st.get("paused") or not ok:
        return closed
    if not o:
        o = st["open"] = {"start": now, "last": now, "source": "auto", "ctx": {}, "proj": {}}
    o["last"] = now
    for k in ctx:
        o["ctx"][k] = o["ctx"].get(k, 0) + 1
    for k in proj:
        o["proj"][k] = o["proj"].get(k, 0) + 1
    return closed


def pause(st, now):
    closed = [close(st["open"], now)] if st.get("open") else []
    st["open"] = None
    st["paused"] = day_of(now).isoformat()
    return closed


def resume(st, now):
    st["paused"] = None
    st["open"] = {"start": now, "last": now, "source": "manual", "ctx": {}, "proj": {}}
    return []


def not_work(st, now):
    # ponytail: deletes the open segment and pauses, so it doesn't restart 30 s later; Resume when back.
    st["open"] = None
    st["paused"] = day_of(now).isoformat()
    return []


def month_file(y, m):
    return path(f"segments-{y:04d}-{m:02d}.jsonl")


def store(segs, cfg):
    """Append closed segments (split at midnight, short ones dropped). Returns the years touched."""
    years = set()
    for seg in segs:
        for s, e in split_days(seg["start"], seg["end"]):
            if e - s < cfg["min_segment_seconds"]:
                continue
            d = day_of(s)
            rec = {"start": iso(s), "end": iso(e), "source": seg["source"],
                   "context": seg["context"], "projects": seg["projects"]}
            with open(month_file(d.year, d.month), "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            years.add(d.year)
    return years


def load_month(y, m):
    out = []
    try:
        with open(month_file(y, m)) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out.append({**r, "s": epoch(r["start"]), "e": epoch(r["end"])})
                except (ValueError, KeyError):
                    continue  # ponytail: a torn last line (crash mid-write) is skipped, not fatal
    except OSError:
        pass
    return out


def open_as_segments(st):
    o = st.get("open")
    if not o or o["last"] <= o["start"]:
        return []
    seg = close(o)
    return [{"s": s, "e": e, "open": True, "context": seg["context"], "projects": seg["projects"]}
            for s, e in split_days(seg["start"], seg["end"])]


def by_day(segs):
    days = {}
    for g in segs:
        days.setdefault(day_of(g["s"]), []).append(g)
    return days


def summarize(segs):
    segs = sorted(segs, key=lambda g: g["s"])
    proj = Counter(p for g in segs for p in g.get("projects", []) if p not in ("~", ""))
    return {"start": segs[0]["s"], "end": max(g["e"] for g in segs), "net": sum(g["e"] - g["s"] for g in segs),
            "n": len(segs), "projects": [k for k, _ in proj.most_common(3)]}


def prune(cfg, today):
    keep = today.year * 12 + today.month - 1 - cfg["keep_months"]
    for name in os.listdir(DATA):
        m = re.fullmatch(r"segments-(\d{4})-(\d{2})\.jsonl", name)
        if m and int(m.group(1)) * 12 + int(m.group(2)) - 1 < keep:
            os.remove(path(name))
    first = dt.date(keep // 12, keep % 12 + 1, 1).isoformat()
    for name in ("overrides.json", "written.json"):
        doc = read_json(path(name), {})
        if any(d < first for d in doc):
            write_atomic(path(name), json.dumps({d: v for d, v in doc.items() if d >= first}, indent=1))


# ---------- days: tracked values plus the user's corrections ----------
# A day row: start/end are seconds after its midnight, breaks/net durations in seconds, all whole minutes;
# net = end - start - breaks. overrides.json {"2026-11-10": {"start": 29700, "breaks": 1800, "note": "…"}}
# holds the user's corrections (from Excel or the popup); a corrected field wins over the tracked value.

FIELDS = ("start", "end", "breaks", "note")


def minute(s):
    return int(round(s / 60)) * 60


def midnight(d):
    return dt.datetime(d.year, d.month, d.day).timestamp()


def day_rows(segs, overrides, year=None):
    days = {d: summarize(g) for d, g in by_day(segs).items()}
    dates = set(days) | {dt.date.fromisoformat(k) for k, o in overrides.items() if "start" in o and "end" in o}
    rows = {}
    for d in sorted(dates):
        if year is not None and d.year != year:
            continue
        x, ov = days.get(d), overrides.get(d.isoformat(), {})
        if x:
            start = minute(x["start"] - midnight(d))
            end = min(minute(x["end"] - midnight(d)), 86340)  # ponytail: a day ends 23:59, hh:mm can't show 24:00
            auto = {"start": start, "end": end, "breaks": max(0, end - start - minute(x["net"]))}
        else:
            auto = {"start": 0, "end": 0, "breaks": 0}
        r = {f: ov.get(f, auto[f]) for f in ("start", "end", "breaks")}
        r.update(net=max(0, r["end"] - r["start"] - r["breaks"]), n=x["n"] if x else 0,
                 projects=x["projects"] if x else [], note=ov.get("note", ""),
                 edited=[f for f in FIELDS if f in ov])
        rows[d] = r
    return rows


def parse_hm(v):
    """'8:15' / '08:15' -> seconds; '45' -> 45 minutes; '' -> None. Raises ValueError."""
    v = v.strip()
    if not v:
        return None
    h, colon, m = v.rpartition(":")
    h, m = int(h or 0), int(m)
    if h < 0 or m < 0 or (colon and m >= 60) or (h * 60 + m) * 60 > 86400:
        raise ValueError(f"not a time: {v}")
    return (h * 60 + m) * 60


def set_override(date, changes):
    """changes: {field: seconds | str | None}; None (or "") drops the correction."""
    ov = read_json(path("overrides.json"), {})
    o = ov.setdefault(date, {})
    for f, v in changes.items():
        if v is None or v == "":
            o.pop(f, None)
        else:
            o[f] = v
    if not o:
        ov.pop(date)
    write_atomic(path("overrides.json"), json.dumps(ov, indent=1))


# ---------- xlsx (SpreadsheetML by hand) ----------

# The workbook's words per `sheet_language`. Reading back accepts the headers of every language.
LABELS = {
    "en": {"months": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(),
           "weekdays": "Mon Tue Wed Thu Fri Sat Sun".split(),
           "head": ["Date", "Weekday", "Start", "End", "Breaks", "Net work", "Segments", "Projects", "Note"],
           "total": "Total", "days": ("day", "days"), "weeks": "Weeks",
           "week_head": ["Week", "Monday", "Days", "Net work", "Target (h)", "Delta (h)"]},
    "de": {"months": "Jan Feb Mrz Apr Mai Jun Jul Aug Sep Okt Nov Dez".split(),
           "weekdays": "Mo Di Mi Do Fr Sa So".split(),
           "head": ["Datum", "Wochentag", "Beginn", "Ende", "Pausen", "Netto", "Abschnitte", "Projekte", "Notiz"],
           "total": "Summe", "days": ("Tag", "Tage"), "weeks": "Wochen",
           "week_head": ["Woche", "Montag", "Tage", "Netto", "Soll (h)", "Differenz (h)"]},
}
COLUMN = {"date": 0, "start": 2, "end": 3, "breaks": 4, "note": 8}  # a field's column in "head"


def labels(cfg):
    return LABELS.get(cfg.get("sheet_language"), LABELS["en"])


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PNS = "http://schemas.openxmlformats.org/package/2006/relationships"
# cellXfs: 0 plain, 1 date, 2 time of day, 3 duration, 4 bold, 5 bold duration, 6 0.00,
#          7 time of day italic, 8 duration italic (7/8: a value the user corrected)
STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{NS}"><numFmts count="3"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/><numFmt numFmtId="165" formatCode="hh:mm"/><numFmt numFmtId="166" formatCode="[h]:mm"/></numFmts><fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font><font><i/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="9"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="166" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/><xf numFmtId="166" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>"""
DATE, TIME, DUR, BOLD, BOLD_DUR, DEC, TIME_ED, DUR_ED = 1, 2, 3, 4, 5, 6, 7, 8
EPOCH0 = dt.date(1899, 12, 30)


def serial(d):
    return (d - EPOCH0).days


def num(v):
    return repr(round(float(v), 10))


def sheet_xml(rows, widths):
    """rows: lists of cells; a cell is None, a str, (value, style) or (value, style, formula)."""
    out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{NS}">',
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>']
    out += [f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)]
    out.append("</cols><sheetData>")
    for r, row in enumerate(rows, 1):
        out.append(f'<row r="{r}">')
        for c, cell in enumerate(row):
            if cell is None:
                continue
            ref = f"{chr(65 + c)}{r}"
            v, s, f = (cell + (None,))[:3] if isinstance(cell, tuple) else (cell, 0, None)
            st = f' s="{s}"' if s else ""
            if f:
                out.append(f'<c r="{ref}"{st}><f>{escape(f)}</f><v>{num(v)}</v></c>')
            elif isinstance(v, str):
                out.append(f'<c r="{ref}"{st} t="inlineStr"><is><t xml:space="preserve">{escape(v)}</t></is></c>')
            else:
                out.append(f'<c r="{ref}"{st}><v>{num(v)}</v></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def workbook_sheets(year, rows, target_h, lang=LABELS["en"]):
    """[(name, xml)] for one year's day rows: a sheet per month with data, then Weeks.
    Durations are day fractions ([h]:mm); Net and the totals are formulas with their values cached."""
    rows = {d: r for d, r in rows.items() if d.year == year}
    sheets = []
    for month in range(1, 13):
        mdays = sorted(d for d in rows if d.month == month)
        if not mdays:
            continue
        out = [[(h, BOLD) for h in lang["head"]]]
        for i, d in enumerate(mdays, 2):
            r = rows[d]
            ed = lambda f, plain, italic: italic if f in r["edited"] else plain  # noqa: E731
            out.append([(serial(d), DATE), lang["weekdays"][d.weekday()],
                        (r["start"] / 86400, ed("start", TIME, TIME_ED)), (r["end"] / 86400, ed("end", TIME, TIME_ED)),
                        (r["breaks"] / 86400, ed("breaks", DUR, DUR_ED)), (r["net"] / 86400, DUR, f"D{i}-C{i}-E{i}"),
                        r["n"], ", ".join(r["projects"]), r["note"] or None])
        last = len(mdays) + 1
        out.append([])
        out.append([(lang["total"], BOLD), f"{len(mdays)} {lang['days'][len(mdays) != 1]}", None, None,
                    (sum(rows[d]["breaks"] for d in mdays) / 86400, BOLD_DUR, f"SUM(E2:E{last})"),
                    (sum(rows[d]["net"] for d in mdays) / 86400, BOLD_DUR, f"SUM(F2:F{last})"),
                    (sum(rows[d]["n"] for d in mdays), 0, f"SUM(G2:G{last})")])
        sheets.append((lang["months"][month - 1], sheet_xml(out, [12, 9, 8, 8, 9, 10, 10, 40, 40])))
    weeks = {}
    for d, r in rows.items():
        y, w, _ = d.isocalendar()
        weeks.setdefault((y, w), []).append(r["net"])
    out = [[(h, BOLD) for h in lang["week_head"]]]
    for (y, w), nets in sorted(weeks.items()):
        net = sum(nets)
        out.append([f"{y}-W{w:02d}", (serial(dt.date.fromisocalendar(y, w, 1)), DATE), len(nets), (net / 86400, DUR),
                    target_h, (net / 3600 - target_h, DEC)])
    sheets.append((lang["weeks"], sheet_xml(out, [10, 12, 6, 10, 10, 10])))
    return sheets


def write_xlsx(dest, sheets):
    """Atomic: a temp file in the same folder, then rename. Raises OSError (caller retries later)."""
    n = len(sheets)
    ctypes = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, n + 1))
    parts = {
        "[Content_Types].xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{ctypes}</Types>',
        "_rels/.rels": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PNS}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="{NS}" xmlns:r="{RNS}"><bookViews><workbookView/></bookViews><sheets>'
        + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
        + '</sheets><calcPr calcId="0" fullCalcOnLoad="1"/></workbook>',
        "xl/_rels/workbook.xml.rels": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PNS}">'
        + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
        + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": STYLES,
    }
    for i, (_, xml) in enumerate(sheets, 1):
        parts[f"xl/worksheets/sheet{i}.xml"] = xml
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.tmp-{os.getpid()}")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in parts.items():
                z.writestr(name, text)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def cell_value(c, shared):
    t, v = c.get("t"), c.find(f"{{{NS}}}v")
    if t == "inlineStr":
        return "".join(c.find(f"{{{NS}}}is").itertext()) if c.find(f"{{{NS}}}is") is not None else ""
    if v is None or v.text is None:
        return None
    if t == "s":
        return shared[int(v.text)]
    if t in ("str", "e", "b"):
        return v.text
    return float(v.text)


def read_back(dest):
    """{date: {start, end, breaks (seconds or None), note}} from the month sheets of a workbook, ours or
    as Excel saved it (shared strings, renumbered parts, formulas): columns are found by their headers."""
    with zipfile.ZipFile(dest) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{{{NS}}}si"):
                # plain <t>, or rich text runs <r><t>; phonetic hints (<rPh>) aren't part of the value
                t = si.find(f"{{{NS}}}t")
                shared.append(t.text or "" if t is not None else
                              "".join(x.text or "" for r in si.findall(f"{{{NS}}}r") for x in r.findall(f"{{{NS}}}t")))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        out = {}
        for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS}}}sheet"):
            target = rels.get(sh.get(f"{{{RNS}}}id"), "")
            part = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if sh.get("name") in {L["weeks"] for L in LABELS.values()} or part not in names:
                continue
            cols = None
            for row in ET.fromstring(z.read(part)).iter(f"{{{NS}}}row"):
                cells = {}
                for c in row.findall(f"{{{NS}}}c"):
                    m = re.match(r"[A-Z]+", c.get("r", ""))
                    if m:
                        cells[m.group(0)] = cell_value(c, shared)
                if cols is None:
                    found = {str(v).strip(): k for k, v in cells.items() if isinstance(v, str)}
                    cols = {}
                    for L in LABELS.values():
                        for f, i in COLUMN.items():
                            if L["head"][i] in found:
                                cols.setdefault(f, found[L["head"][i]])
                    if "date" not in cols:
                        break  # not one of our month sheets
                    continue
                d = cells.get(cols["date"])
                if not isinstance(d, float):
                    continue  # the Total row, blank rows
                rec = {}
                for f in ("start", "end", "breaks"):
                    v = cells.get(cols.get(f))
                    if isinstance(v, str):
                        try:
                            v = parse_hm(v)
                        except ValueError:
                            v = None
                        rec[f] = v
                    else:
                        rec[f] = round(v * 86400) if isinstance(v, float) else None  # seconds: keep 08:15:20
                note = cells.get(cols.get("note"))
                rec["note"] = "" if note is None else note if isinstance(note, str) else f"{note:g}"
                out[(EPOCH0 + dt.timedelta(days=int(d))).isoformat()] = rec
        return out


def absorb(back, written, overrides):
    """Every Start/End/Breaks/Note the user changed in the workbook (compared with what the tracker last
    wrote there) becomes a correction; a cleared cell drops it. A row the tracker never wrote counts whole."""
    for d, rb in back.items():
        wr = written.get(d)
        o = overrides.setdefault(d, {})
        for f in FIELDS:
            new = rb.get(f)
            old = wr.get(f) if wr else None
            if f == "note":
                new, old = new or "", old or ""
            if wr is not None and new == old:
                continue
            if new is None or new == "":
                if wr is not None:
                    o.pop(f, None)
            else:
                o[f] = new
        if not o:
            overrides.pop(d)
    return overrides


def xlsx_path(cfg, year):
    return os.path.join(os.path.expanduser(cfg["excel_dir"]), cfg["excel_file"].format(year=year))


def excel_running():
    try:
        return subprocess.run(["pgrep", "-x", "Microsoft Excel"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def in_use(dest):
    """Some process has the file open (lsof), or Excel runs and its "~$" owner file is there.
    When that can't be told, it counts as in use: skipping a write is harmless, replacing an open file isn't."""
    try:
        r = subprocess.run(["lsof", "-t", "--", dest], capture_output=True, text=True, timeout=15)
        if r.stdout.strip():
            return True
    except (OSError, subprocess.SubprocessError):
        return True
    owner = os.path.join(os.path.dirname(dest), "~$" + os.path.basename(dest))
    return os.path.exists(owner) and excel_running()


def export_year(cfg, st, year):
    dest = xlsx_path(cfg, year)
    if os.path.exists(dest) and in_use(dest):
        raise OSError("open in Excel; writing after it's closed")
    overrides = read_json(path("overrides.json"), {})
    written = read_json(path("written.json"), {})
    if os.path.exists(dest):
        # The user's edits first: read them back, so the rewrite below carries them.
        try:
            back = read_back(dest)
        except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, IndexError) as e:
            raise OSError(f"can't read your edits back ({e}); not overwriting it")
        absorb(back, written, overrides)
        write_atomic(path("overrides.json"), json.dumps(overrides, indent=1))
    segs = [g for m in range(1, 13) for g in load_month(year, m)] + open_as_segments(st)
    rows = day_rows(segs, overrides, year)
    write_xlsx(dest, workbook_sheets(year, rows, cfg["week_target_hours"], labels(cfg)))
    written = {d: v for d, v in written.items() if not d.startswith(f"{year}-")}
    written.update({d.isoformat(): {f: r[f] for f in FIELDS} for d, r in rows.items()})
    write_atomic(path("written.json"), json.dumps(written, indent=1))


def maybe_export(cfg, st, now, years=(), force=False):
    if os.path.exists(path("xlsx.hold")):  # emergency stop: no xlsx writes while this file exists
        return
    pending = set(st.get("xlsx_years") or []) | set(years)
    if force or (st.get("open") and now - st.get("xlsx_at", 0) >= cfg["excel_every_minutes"] * 60):
        pending.add(day_of(now).year)
    for y in sorted(pending):
        try:
            export_year(cfg, st, y)
            pending.discard(y)
            st["xlsx_at"], st["xlsx_error"] = now, None
        except OSError as e:
            st["xlsx_error"] = str(e)
            log(f"xlsx {y}: {e}")
    st["xlsx_years"] = sorted(pending)


# ---------- state, status ----------

def load_state():
    return read_json(path("state.json"), {}) or {}


def save_state(st):
    write_atomic(path("state.json"), json.dumps(st))


def status(st, cfg, now):
    today = day_of(now)
    mon = today - dt.timedelta(days=today.weekday())
    months = {(today.year, today.month), (mon.year, mon.month)}
    segs = [g for y, m in months for g in load_month(y, m)] + open_as_segments(st)
    overrides = read_json(path("overrides.json"), {})
    try:  # show edits saved in Excel at once; they're stored for good at the next rewrite
        overrides = absorb(read_back(xlsx_path(cfg, today.year)), read_json(path("written.json"), {}), overrides)
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, IndexError):
        pass
    rows = day_rows(segs, overrides)
    r = rows.get(today)
    o = st.get("open")
    return {
        "at": now, "day": today.isoformat(), "midnight": midnight(today),
        "state": "paused" if st.get("paused") else "tracking" if o else "off",
        "net": r["net"] if r else 0, "start": r and r["start"], "end": r and r["end"],
        "breaks": r["breaks"] if r else 0, "projects": r["projects"] if r else [],
        "note": r["note"] if r else "", "edited": r["edited"] if r else [],
        "segments": [[g["s"], g["e"], bool(g.get("open"))] for g in sorted(by_day(segs).get(today, []), key=lambda g: g["s"])],
        "context": close(o)["context"] if o else [],
        "week": sum(x["net"] for d, x in rows.items() if mon <= d <= today), "target": cfg["week_target_hours"],
        "xlsx": xlsx_path(cfg, today.year), "xlsx_error": st.get("xlsx_error"),
        "xlsx_hold": os.path.exists(path("xlsx.hold")),
    }


def publish(st, cfg, now):
    save_state(st)
    s = status(st, cfg, now)
    write_atomic(path("status.json"), json.dumps(s))
    return s


def housekeeping(st, cfg, now):
    today = day_of(now).isoformat()
    if st.get("day") != today:
        st["day"] = today
        if st.get("paused") and st["paused"] != today:
            st["paused"] = None  # a pause ends with its day
        prune(cfg, day_of(now))


def tick():
    cfg = load_cfg()
    idle, front = sample_idle(), sample_front()
    now = time.time()
    ok, ctx, proj = evaluate(idle, front, read_facts(now), cfg)
    with locked():
        st = load_state()
        housekeeping(st, cfg, now)
        active = idle is None or idle < cfg["idle_seconds"]
        years = store(step(st, now, ok, ctx, proj, cfg, active), cfg)
        maybe_export(cfg, st, now, years)
        publish(st, cfg, now)


def command(fn):
    cfg = load_cfg()
    now = time.time()
    with locked():
        st = load_state()
        housekeeping(st, cfg, now)
        years = store(fn(st, now), cfg)
        maybe_export(cfg, st, now, years, force=True)
        return publish(st, cfg, now)


def correct(date, args):
    """--set DATE start=08:15 end= breaks=0:30 note=…: the popup's corrections ("" = back to tracked)."""
    changes = {}
    for a in args:
        f, _, v = a.partition("=")
        if f not in FIELDS:
            raise SystemExit(f"unknown field {f}")
        changes[f] = v if f == "note" else parse_hm(v)

    def fn(st, now):
        set_override(date, changes)
        return []
    return command(fn)


# ---------- daemon ----------

def daemon_running():
    os.makedirs(DATA, exist_ok=True)
    with open(path("daemon.lock"), "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def spawn():
    if daemon_running():
        return False
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--run"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd=DATA)
    return True


def daemon():
    os.makedirs(DATA, exist_ok=True)
    lock = open(path("daemon.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # another instance runs
    write_atomic(path("daemon.pid"), str(os.getpid()))
    me = os.path.abspath(__file__)
    born = os.path.getmtime(me)
    log(f"daemon started (pid {os.getpid()})")
    while True:
        try:
            tick()
        except Exception:
            log("tick failed:\n" + traceback.format_exc())
        time.sleep(max(5, load_cfg()["sample_seconds"]))
        try:
            if os.path.getmtime(me) != born:
                log("track.py changed, restarting")
                lock.close()
                os.execv(sys.executable, [sys.executable, me, "--run"])
        except OSError:
            pass


def main(argv):
    a = argv[1] if len(argv) > 1 else "--status"
    if a == "--facts":
        os.makedirs(DATA, exist_ok=True)
        facts = json.loads(argv[2])
        facts["at"] = time.time() * 1000
        write_atomic(path("herdr.json"), json.dumps(facts))
        spawn()
    elif a == "--spawn":
        print("started" if spawn() else "already running")
    elif a == "--run":
        daemon()
    elif a == "--stop":
        pid = int(read_json(path("daemon.pid"), 0) or 0)
        if pid and daemon_running():
            os.kill(pid, 15)
            print(f"stopped {pid}")
        else:
            print("not running")
    elif a in ("--pause", "--resume", "--notwork"):
        print(json.dumps(command({"--pause": pause, "--resume": resume, "--notwork": not_work}[a])))
    elif a == "--set":
        print(json.dumps(correct(argv[2], argv[3:])))
    elif a == "--excel":
        print(json.dumps(command(lambda st, now: [])))
    elif a == "--selftest":
        import test_track
        test_track.main()
    else:
        cfg, now = load_cfg(), time.time()
        with locked():
            print(json.dumps(status(load_state(), cfg, now), indent=1))


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main(sys.argv)
