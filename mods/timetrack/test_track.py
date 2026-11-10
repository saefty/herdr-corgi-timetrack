"""Self-check for track.py: python3 test_track.py (or python3 track.py --selftest)."""
import datetime as dt
import os
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import track  # noqa: E402

CFG = {**track.DEFAULTS, "work_apps": ["com.google.Chrome", "IntelliJ IDEA"], "excel_dir": "/nonexistent/timetrack-test"}
T0 = dt.datetime(2026, 11, 10, 9, 0).timestamp()  # Tue 09:00 local
HERDR = ("Herdr", "dev.herdr.app")
CHROME = ("Google Chrome", "com.google.Chrome")
# What the chip reports (PROVIDERS.md): a hook with contexts "work" and "private", only "work" counts.
WORK = {"hook": True, "count": ["work"], "ignore": ["private"], "current": None,
        "focused": {"name": "bookshelf", "context": "work"}, "working": []}
NO_HOOK = {"hook": False, "focused": {"name": "bookshelf", "context": None}, "working": []}


def run(st, start, minutes, ok, every=30):
    """Feed samples every `every` s for `minutes`; returns closed segments."""
    closed = []
    t = start
    while t < start + minutes * 60:
        closed += track.step(st, t, ok, ["Herdr"] if ok else [], ["P"] if ok else [], CFG)
        t += every
    return closed, t


def test_evaluate():
    ev = lambda idle, front, facts: track.evaluate(idle, front, facts, CFG)[0]
    assert ev(5, HERDR, WORK)
    assert ev(5, HERDR, {**WORK, "current": "work"})
    assert not ev(5, HERDR, {**WORK, "current": "private"}), "only private shown"
    assert not ev(5, HERDR, {**WORK, "focused": {"name": "diary", "context": "private"}}), "private space shown"
    assert not ev(5, HERDR, None), "Herdr facts missing/stale = Herdr not running"
    assert not ev(500, HERDR, WORK), "idle user"
    assert ev(5, CHROME, None) and ev(5, ("IntelliJ IDEA", "com.jetbrains.intellij"), None)
    assert not ev(5, ("Music", "com.apple.Music"), None)
    agents = {**WORK, "working": [{"name": "orbit-sync", "context": "work"}, {"name": "orbit-sync", "context": "work"},
                                  {"name": "diary", "context": "private"}]}
    ok, ctx, proj = track.evaluate(5, ("Music", "com.apple.Music"), agents, CFG)
    assert ok and ctx == ["Agents"] and proj == ["orbit-sync"], "a work agent counts while reading elsewhere"
    assert not ev(500, ("Music", "x"), agents), "but not while idle"
    assert not ev(5, HERDR, {**agents, "focused": {"name": "diary", "context": "private"}}), "private Herdr vetoes agents"
    private_only = {**WORK, "working": [{"name": "diary", "context": "private"}]}
    assert not ev(5, ("Music", "x"), private_only), "a private agent doesn't count"
    assert not ev(5, (None, None), None), "frontmost unknown"


def test_context_rule():
    c = track.counts
    # No hook: every Herdr context counts, the shown space and every working agent.
    assert c(None, NO_HOOK) and c("anything", NO_HOOK) and c("private", {})
    ok, ctx, proj = track.evaluate(5, HERDR, {**NO_HOOK, "working": [{"name": "orbit-sync", "context": None}]}, CFG)
    assert ok and ctx == ["Herdr", "Agents"] and proj == ["bookshelf", "orbit-sync"]
    # A hook with count: only those; an unknown context doesn't count.
    assert c("work", WORK) and not c("private", WORK) and not c("client-b", WORK) and not c(None, WORK)
    # A hook with only ignore: everything else counts.
    ign = {"hook": True, "ignore": ["private"]}
    assert c("work", ign) and c("client-b", ign) and c(None, ign) and not c("private", ign)
    # A hook without count or ignore: everything counts.
    assert c("private", {"hook": True}) and c(None, {"hook": True, "count": [], "ignore": []})
    assert track.evaluate(5, HERDR, {**ign, "focused": {"name": "x", "context": "client-b"}}, CFG)[0]


def test_idle_backdating():
    st = {}
    closed, t = run(st, T0, 30, True)
    last = st["open"]["last"]
    c2, t = run(st, t, 9, False)
    assert not c2 and st["open"], "still inside the gap"
    c3, t = run(st, t, 3, False)
    assert len(c3) == 1 and c3[0]["start"] == T0 and c3[0]["end"] == last, "ends at the last activity"
    assert st["open"] is None
    c4, t = run(st, t, 5, True)
    assert st["open"]["start"] > last, "auto-resume opens a new segment"


def test_short_gap_merged():
    st = {}
    _, t = run(st, T0, 30, True)
    _, t = run(st, t, 8, False)
    _, t = run(st, t, 30, True)
    closed, _ = run(st, t, 15, False)
    assert len(closed) == 1 and closed[0]["start"] == T0
    assert closed[0]["end"] - closed[0]["start"] > 67 * 60, "the 8 min gap counts as work"


def test_manual():
    st = {}
    run(st, T0, 20, True)
    closed = track.pause(st, T0 + 25 * 60)
    assert len(closed) == 1 and closed[0]["end"] == T0 + 25 * 60 and st["paused"]
    c, _ = run(st, T0 + 26 * 60, 10, True)
    assert not c and st["open"] is None, "paused: activity is ignored"
    track.resume(st, T0 + 40 * 60)
    assert st["paused"] is None and st["open"]["source"] == "manual"
    track.step(st, T0 + 41 * 60, False, [], [], CFG, active=True)
    assert st["open"]["last"] == T0 + 41 * 60, "after Resume, any activity keeps the segment going"
    run(st, T0 + 40 * 60, 5, True)
    assert track.not_work(st, T0 + 46 * 60) == [] and st["open"] is None, "not work deletes the segment"
    # A pause ends with its day.
    track.housekeeping(st, CFG, T0 + 86400)
    assert st["paused"] is None


def cells_of(dest, part="xl/worksheets/sheet1.xml"):
    """{ref: (value, style, formula)} of one sheet, values resolved (numbers as float)."""
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None
        for n in z.namelist():
            ET.fromstring(z.read(n))  # every part is well-formed XML
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(si.itertext()) for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        out = {}
        for c in ET.fromstring(z.read(part)).iter(f"{{{track.NS}}}c"):
            f = c.find(f"{{{track.NS}}}f")
            out[c.get("r")] = (track.cell_value(c, shared), c.get("s"), f.text if f is not None else None)
        return out


def test_store_and_xlsx():
    # Before midnight to after: split into two days; a 30 s blip is dropped.
    late = dt.datetime(2026, 11, 10, 23, 0).timestamp()
    segs = [
        {"start": T0, "end": T0 + 4 * 3600, "source": "auto", "context": ["Herdr"], "projects": ["A", "~"]},
        {"start": T0 + 5 * 3600, "end": T0 + 8 * 3600, "source": "manual", "context": ["Slack"], "projects": ["B"]},
        {"start": late, "end": late + 2 * 3600, "source": "auto", "context": [], "projects": []},
        {"start": T0 + 9 * 3600, "end": T0 + 9 * 3600 + 30, "source": "auto", "context": [], "projects": []},
    ]
    assert track.store(segs, CFG) == {2026}
    segs = track.load_month(2026, 11)
    assert len(segs) == 4, segs
    rows = track.day_rows(segs, {})
    tue, wed = rows[dt.date(2026, 11, 10)], rows[dt.date(2026, 11, 11)]
    assert tue["net"] == 8 * 3600 and tue["n"] == 3 and tue["start"] == 9 * 3600 and tue["end"] == 86340
    assert tue["net"] == tue["end"] - tue["start"] - tue["breaks"] and tue["projects"] == ["A", "B"], "no '~' project"
    assert wed["net"] == 3600

    s = track.status({}, CFG, T0 + 10 * 3600)
    assert s["state"] == "off" and s["net"] == 8 * 3600 and s["week"] == 8 * 3600

    dest = os.path.join(track.DATA, "x", "Timesheet-2026.xlsx")
    track.write_xlsx(dest, track.workbook_sheets(2026, rows, 40))
    with zipfile.ZipFile(dest) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        assert [e.get("name") for e in wb.iter(f"{{{track.NS}}}sheet")] == ["Nov", "Weeks"]
    c = cells_of(dest)
    v = lambda r: c[r][0]  # noqa: E731
    assert v("A1") == "Date" and v("F1") == "Net work" and v("I1") == "Note"
    assert v("A2") == (dt.date(2026, 11, 10) - dt.date(1899, 12, 30)).days and c["A2"][1] == "1"
    assert v("B2") == "Tue" and abs(v("C2") - 9 / 24) < 1e-9 and abs(v("D2") - 86340 / 86400) < 1e-9
    # Durations are day fractions shown as [h]:mm; net = end - start - breaks, as a formula with its value.
    for r in (2, 3):
        assert c[f"E{r}"][1] == "3" and c[f"F{r}"][1] == "3" and c[f"F{r}"][2] == f"D{r}-C{r}-E{r}"
        assert abs(v(f"F{r}") - (v(f"D{r}") - v(f"C{r}") - v(f"E{r}"))) < 1e-9
    assert abs(v("F2") - 8 / 24) < 1e-9 and abs(v("F3") - 1 / 24) < 1e-9
    assert v("G2") == 3 and v("H2") == "A, B"
    assert v("A5") == "Total" and c["F5"][2] == "SUM(F2:F3)" and abs(v("F5") - (v("F2") + v("F3"))) < 1e-9
    assert c["E5"][2] == "SUM(E2:E3)" and abs(v("E5") - (v("E2") + v("E3"))) < 1e-9 and c["F5"][1] == "5"
    w = cells_of(dest, "xl/worksheets/sheet2.xml")
    assert w["A2"][0] == "2026-W46" and abs(w["F2"][0] - (9 - 40)) < 1e-9


def test_sheet_language():
    """English is the default; German labels the sheets, headers and totals; edits read back in either."""
    rows = track.day_rows([], {"2026-03-02": {"start": 8 * 3600, "end": 16 * 3600, "breaks": 1800, "note": "n"}})
    assert track.labels({}) is track.LABELS["en"] and track.labels({"sheet_language": "xx"}) is track.LABELS["en"]
    for lang, names, a1, b2, total, days in (("en", ["Mar", "Weeks"], "Date", "Mon", "Total", "1 day"),
                                             ("de", ["Mrz", "Wochen"], "Datum", "Mo", "Summe", "1 Tag")):
        cfg = {**CFG, "sheet_language": lang}
        dest = os.path.join(track.DATA, lang, "t.xlsx")
        track.write_xlsx(dest, track.workbook_sheets(2026, rows, 40, track.labels(cfg)))
        with zipfile.ZipFile(dest) as z:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            assert [e.get("name") for e in wb.iter(f"{{{track.NS}}}sheet")] == names
        c = cells_of(dest)
        assert c["A1"][0] == a1 and c["B2"][0] == b2 and c["A4"][0] == total and c["B4"][0] == days, lang
        assert [c[f"{x}1"][0] for x in "ABCDEFGHI"] == track.LABELS[lang]["head"]
        assert cells_of(dest, "xl/worksheets/sheet2.xml")["A1"][0] == track.LABELS[lang]["week_head"][0]
        back = track.read_back(dest)
        assert back == {"2026-03-02": {"start": 8 * 3600, "end": 16 * 3600, "breaks": 1800, "note": "n"}}, (lang, back)
    # Two days: German plural.
    two = track.day_rows([], {"2026-03-02": {"start": 1, "end": 2}, "2026-03-03": {"start": 1, "end": 2}})
    sheet = dict(track.workbook_sheets(2026, two, 40, track.LABELS["de"]))["Mrz"]
    assert ">2 Tage<" in sheet
    # The file name follows excel_file.
    assert track.xlsx_path({"excel_dir": "/x", "excel_file": "Hours {year}.xlsx"}, 2027) == "/x/Hours 2027.xlsx"
    assert track.xlsx_path(track.DEFAULTS, 2026).endswith("/Timesheets/Timesheet-2026.xlsx")


def excel_save(dest, edits):
    """Rewrite the workbook roughly the way Excel saves it: strings into a shared table, the user's edits
    applied ({"C2": 7.37 / 24, "I2": "note"}; None clears a cell), formulas kept."""
    with zipfile.ZipFile(dest) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    m = f"{{{track.NS}}}"
    sheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    shared = []
    for row in sheet.iter(f"{m}row"):
        for ref, val in edits.items():
            if ref[1:] == row.get("r") and not any(c.get("r") == ref for c in row):
                ET.SubElement(row, f"{m}c", r=ref)
        for c in list(row):
            ref = c.get("r")
            if ref in edits:
                for ch in list(c):
                    c.remove(ch)
                c.attrib.pop("t", None)
                if edits[ref] is None:
                    continue
                if isinstance(edits[ref], str):
                    c.set("t", "s")
                    ET.SubElement(c, f"{m}v").text = str(len(shared))
                    shared.append(edits[ref])
                else:
                    ET.SubElement(c, f"{m}v").text = repr(edits[ref])
            elif c.get("t") == "inlineStr":
                text = "".join(c.itertext())
                for ch in list(c):
                    c.remove(ch)
                c.set("t", "s")
                ET.SubElement(c, f"{m}v").text = str(len(shared))
                shared.append(text)
    ET.register_namespace("", track.NS)
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet)
    sst = ET.Element(f"{m}sst")
    for t in shared:
        ET.SubElement(ET.SubElement(sst, f"{m}si"), f"{m}t").text = t
    parts["xl/sharedStrings.xml"] = ET.tostring(sst)
    with zipfile.ZipFile(dest, "w") as z:
        for n, b in parts.items():
            z.writestr(n, b)


def test_user_edits_survive():
    """The tracker writes; the user edits Start to 08:15 and types a Note in Excel; Resume and a new
    segment rewrite the file: Start stays 08:15, the Note stays, Net is computed from 08:15."""
    cfg = {**CFG, "excel_dir": os.path.join(track.DATA, "xl")}
    dest = track.xlsx_path(cfg, 2026)
    st = {}
    run(st, T0 + 22 * 60, 11, True)              # 09:22 .. 09:32:30, open
    track.store(track.pause(st, T0 + 33 * 60), cfg)  # Pause at 09:33 -> segment 09:22-09:33
    track.maybe_export(cfg, st, T0 + 33 * 60, force=True)
    c = cells_of(dest)
    assert abs(c["C2"][0] * 1440 - (9 * 60 + 22)) < 1e-6 and c.get("I2") is None

    excel_save(dest, {"C2": (8 * 60 + 15) / 1440, "I2": "started at home"})  # the user's edit, saved

    track.resume(st, T0 + 60 * 60)               # Resume at 10:00, work until 11:00
    run(st, T0 + 60 * 60, 60, True)
    track.store(track.pause(st, T0 + 2 * 3600), cfg)
    track.maybe_export(cfg, st, T0 + 2 * 3600, force=True)
    for _ in range(2):  # and the rewrite after that too
        c = cells_of(dest)
        start, end, breaks, net = (round(c[r][0] * 1440) for r in ("C2", "D2", "E2", "F2"))
        assert start == 8 * 60 + 15 and c["C2"][1] == "7", "the user's start, in italics"
        assert c["I2"][0] == "started at home", "the note stays"
        assert end == 11 * 60, "End still follows the tracker"
        assert breaks == 27 and net == end - start - breaks == 11 * 60 - 8 * 60 - 15 - 27, "net from 08:15"
        assert c["F2"][2] == "D2-C2-E2"
        track.maybe_export(cfg, st, T0 + 2 * 3600 + 60, force=True)
    assert track.read_json(track.path("overrides.json"), {}) == {"2026-11-10": {"start": 29700, "note": "started at home"}}

    # Clearing the note in Excel drops that correction; the start stays.
    excel_save(dest, {"I2": None})
    track.maybe_export(cfg, st, T0 + 3 * 3600, force=True)
    assert track.read_json(track.path("overrides.json"), {}) == {"2026-11-10": {"start": 29700}}
    assert cells_of(dest).get("I2") is None

    # The popup's corrections land in the same place: a 30 min break, then back to the tracked start.
    track.set_override("2026-11-10", {"breaks": track.parse_hm("0:30"), "start": None})
    track.maybe_export(cfg, st, T0 + 3 * 3600, force=True)
    c = cells_of(dest)
    assert round(c["C2"][0] * 1440) == 9 * 60 + 22 and round(c["E2"][0] * 1440) == 30 and c["E2"][1] == "8"
    assert round(c["F2"][0] * 1440) == 11 * 60 - (9 * 60 + 22) - 30
    s = track.status(st, cfg, T0 + 3 * 3600)
    assert s["edited"] == ["breaks"] and s["breaks"] == 1800

    # A day the user adds by hand in Excel (no tracking that day) is kept too.
    rows = track.day_rows([], {"2026-11-09": {"start": 8 * 3600, "end": 16 * 3600, "breaks": 1800}})
    assert rows[dt.date(2026, 11, 9)]["net"] == 7.5 * 3600


def test_never_writes_open_file():
    cfg = {**CFG, "excel_dir": os.path.join(track.DATA, "xl")}
    dest = track.xlsx_path(cfg, 2026)
    st = {}
    run(st, T0, 20, True)
    track.maybe_export(cfg, st, T0 + 20 * 60, force=True)
    before = open(dest, "rb").read()
    run(st, T0 + 20 * 60, 20, True)
    with open(dest, "rb"):  # held open by a process: lsof sees it
        track.maybe_export(cfg, st, T0 + 40 * 60, force=True)
    assert open(dest, "rb").read() == before and "open" in st["xlsx_error"] and st["xlsx_years"] == [2026]
    owner = os.path.join(os.path.dirname(dest), "~$" + os.path.basename(dest))
    open(owner, "w").close()
    real, track.excel_running = track.excel_running, lambda: True
    try:
        track.maybe_export(cfg, st, T0 + 41 * 60)
        assert open(dest, "rb").read() == before, "Excel's owner file while Excel runs"
        track.excel_running = lambda: False
        track.maybe_export(cfg, st, T0 + 42 * 60)
        assert open(dest, "rb").read() != before and not st["xlsx_years"], "a stale owner file doesn't block"
    finally:
        track.excel_running = real
    open(track.path("xlsx.hold"), "w").close()
    before = open(dest, "rb").read()
    run(st, T0 + 42 * 60, 5, True)
    track.maybe_export(cfg, st, T0 + 50 * 60, force=True)
    assert open(dest, "rb").read() == before, "xlsx.hold stops all writes"


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                track.DATA = d
                fn()
            print("ok", name)
    print("all ok")


if __name__ == "__main__":
    main()
