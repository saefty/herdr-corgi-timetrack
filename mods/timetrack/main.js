// Work time today. One mod, three roles by props().part:
//   "bar"    a chip ("● 6:12") filling a block in a bar;
//   "float"  the same chip in a floating block;
//   none     the popup the chip opens: timeline, edit form, Pause/Resume/Not work, Open Excel.
// track.py (next to this file) is the tracker, a detached sampler. Every 10 s a chip reports what Herdr shows
// (track.py --facts, which also starts it) and reads timetrack-data/status.json. track.py rewrites that file on
// every sample and after every action; the popup redraws from it. The popup never takes the keyboard from the
// terminal: its inputs only get focus when clicked.
// Which Herdr contexts count as work comes from the goto.context hook and timetrack.contexts (PROVIDERS.md).
import { View, div } from "gpui-kit";
import { h_flex, v_flex, InputState, Input } from "gpui-base";
import { theme, exec, appHome, readTextFile, watchFolder, props, openOverlay, shared, spaces, agents } from "herdr";

const DATA = () => `${appHome()}/timetrack-data`;
const TRACK = () => `${appHome()}/mods/timetrack/track.py`;
const pad = (n) => String(n).padStart(2, "0");
const hm = (s) => `${Math.floor(s / 3600)}:${pad(Math.floor(s / 60) % 60)}`;
const clock = (ts) => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const sod = (s) => `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}`; // seconds after midnight
const EDIT = [["start", "Start", "07:30", sod], ["end", "End", "17:00", sod], ["breaks", "Breaks", "0:30", hm], ["note", "Note", "Free text", (v) => v]];
const STATE = { tracking: "Tracking", paused: "Paused", off: "Not tracking" };
const POPUP = { mod: "timetrack", kind: "popup", width: 340, height: 320 };

// What Herdr shows, for track.py: the shown space and the working agents, each with its context from the
// goto.context hook (terminal, else space), plus which contexts count (timetrack.contexts).
function facts() {
  const hook = shared("goto.context"), rule = shared("timetrack.contexts") ?? {};
  const shown = spaces().find((s) => s.active);
  return {
    hook: Boolean(hook), count: rule.count ?? [], ignore: rule.ignore ?? [], current: hook?.current ?? null,
    focused: shown ? { name: shown.name, context: hook?.spaces?.[shown.id] ?? null } : null,
    working: agents().filter((a) => a.status === "working")
      .map((a) => ({ name: a.space_name, context: hook?.terminals?.[a.address] ?? hook?.spaces?.[a.space] ?? null })),
  };
}

export default class TimeTrack extends View {
  init(_props, cx) {
    this.part = props()?.part ?? null;
    if (this.part) return this.initChip(cx);
    this.s = null;
    this.err = null;
    this.editing = false;
    // Inputs exist from the start but only take the keyboard when clicked: the popup never grabs focus.
    this.inputs = Object.fromEntries(EDIT.map(([f, , placeholder]) => [f, InputState.new({ placeholder })]));
    cx.spawn(async (cx) => {
      for (;;) {
        try { this.s = JSON.parse((await readTextFile(`${DATA()}/status.json`)) ?? "null"); } catch {}
        cx.notify();
        try { await watchFolder(DATA()); } catch { return; }
      }
    });
  }

  // ponytail: polled with the facts (the chip lags a popup action by up to 10 s); cheap, and watchFolder from a
  // bar block didn't wake reliably.
  initChip(cx) {
    const tick = (cx) => cx.spawn(async (cx) => {
      await exec("python3", [TRACK(), "--facts", JSON.stringify(facts())]).catch(() => null);
      try { this.s = JSON.parse((await readTextFile(`${DATA()}/status.json`)) ?? "null"); } catch {}
      const key = this.s ? `${this.s.state}|${hm(this.s.net)}|${Math.floor(this.s.at / 60)}` : "";
      if (key !== this.key) { this.key = key; cx.notify(); }
    });
    cx.timer.every(10000, tick);
    tick(cx);
  }

  chip(t) {
    // Stale for 3 min = the tracker isn't running (the next report restarts it).
    const s = this.s && Date.now() / 1000 - this.s.at < 180 ? this.s : null;
    const state = s?.state ?? "off";
    return h_flex().id("timetrack-chip").h(26).flex_none().px(6).gap(5).items_center().rounded(6).text_size(12.5)
      .text_color(state === "tracking" ? t.foreground : state === "paused" ? t.muted : t.dim).hover((st) => st.bg(t.hover))
      .tooltip(state === "tracking" ? "Work time today: tracking" : state === "paused" ? "Work time today: paused" : "Work time today")
      .on_mouse_down("left", (_e, cx) => cx.stop_propagation()) // a bar may drag the window
      .on_click(() => openOverlay(POPUP))
      .child(div().size(7).flex_none().rounded_full().bg(state === "tracking" ? t.ansi2 : state === "paused" ? t.muted : t.dim))
      .child(s ? hm(s.net) : "–");
  }

  act(arg) {
    return (_e, cx) => cx.spawn(async (cx) => {
      const r = await exec(arg === "open" ? "open" : "python3", arg === "open" ? [this.s.xlsx] : [TRACK(), arg])
        .catch((e) => ({ code: 1, stderr: String(e) }));
      this.err = r.code === 0 ? null : (r.stderr || "failed").trim().split("\n").pop();
      if (r.code === 0 && arg !== "open") try { this.s = JSON.parse(r.stdout); } catch {}
      cx.notify();
    });
  }

  // Prefill with today's values; Save sends only fields that changed ("" = back to the tracked value).
  edit(on) {
    this.editing = on;
    this.before = {};
    if (on) for (const [f, , , fmt] of EDIT) {
      const v = this.s[f];
      this.before[f] = v === null || v === undefined || v === "" ? "" : fmt(v);
      this.inputs[f].set_value(this.before[f]);
    }
  }

  save(reset) {
    return (_e, cx) => cx.spawn(async (cx) => {
      const args = reset ? ["start=", "end=", "breaks="]
        : EDIT.map(([f]) => [f, this.inputs[f].value().trim()]).filter(([f, v]) => v !== this.before[f]).map(([f, v]) => `${f}=${v}`);
      if (args.length) {
        const r = await exec("python3", [TRACK(), "--set", this.s.day, ...args]).catch((e) => ({ code: 1, stderr: String(e) }));
        this.err = r.code === 0 ? null : (r.stderr || "failed").trim().split("\n").pop();
        if (r.code === 0) try { this.s = JSON.parse(r.stdout); } catch {}
      }
      if (!this.err) this.edit(false);
      cx.notify();
    });
  }

  editor(t, box) {
    const field = (f, label) => v_flex().gap(2).flex_1().child(div().text_size(10.5).text_color(t.dim).child(label))
      .child(Input.new(this.inputs[f]).h(28).px(8).rounded(6).border_1().border_color(t.input).bg(t.background)
        .text_size(12.5).text_color(t.foreground));
    const button = (id, label, onClick, primary) => h_flex().id(id).h(26).px(10).items_center().justify_center().rounded(6)
      .text_size(12).bg(primary ? t.selected : t.surface).border(1).border_color(t.border).text_color(t.foreground)
      .hover((st) => st.bg(t.hover)).on_click(onClick).child(label);
    return box
      .child(h_flex().justify_between().child(div().font_semibold().child(`Correct ${this.s.day}`))
        .child(div().text_size(11).text_color(t.dim).child("empty = tracked")))
      .child(h_flex().gap(8).child(field("start", "Start")).child(field("end", "End")).child(field("breaks", "Breaks (h:mm)")))
      .child(field("note", "Note"))
      .child(div().text_size(11).text_color(t.dim).child("Edits in the Excel file are kept the same way."))
      .child(h_flex().gap(6)
        .child(button("tt-save", "Save", this.save(false), true))
        .child(button("tt-cancel", "Cancel", (_e, cx) => { this.edit(false); cx.notify(); }, false))
        .child(div().flex_1())
        .child(button("tt-reset", "Use tracked times", this.save(true), false).tooltip("Drop today's start/end/break corrections")))
      .when(Boolean(this.err), (el) => el.child(div().text_size(11).text_color(t.blocked).truncate().child(this.err)));
  }

  render() {
    const t = theme();
    // "bar": the look of Herdr's tab bar (chrome, a bottom border), so it sits in a row beside it.
    if (this.part === "bar") return h_flex().size_full().items_center().justify_center().bg(t.chrome).border_b(1).border_color(t.border)
      .child(this.chip(t));
    if (this.part) return h_flex().px(2).child(this.chip(t)); // "float": Herdr draws the frame
    const s = this.s;
    const box = v_flex().size_full().p(12).gap(10).bg(t.popover).text_size(12.5).text_color(t.foreground);
    if (!s) return box.child(div().text_color(t.muted).child("The tracker hasn't reported yet. It starts with the chip."));
    if (this.editing) return this.editor(t, box);
    const stale = Date.now() / 1000 - s.at > 180;
    const state = stale ? "off" : s.state;
    const green = t.ansi2;
    const dot = state === "tracking" ? green : state === "paused" ? t.muted : t.dim;

    // Timeline: the day from 08:00 (or earlier) to 18:00 (or later), segments as bars.
    const now = Date.now() / 1000;
    const mid = new Date(); mid.setHours(0, 0, 0, 0);
    const day0 = mid.getTime() / 1000;
    const hours = (ts) => (ts - day0) / 3600;
    const segs = s.segments ?? [];
    const from = Math.min(8, ...segs.map(([a]) => Math.floor(hours(a))));
    const to = Math.max(18, Math.ceil(hours(now)), ...segs.map(([, b]) => Math.ceil(hours(b))));
    const pct = (h) => `${((h - from) / (to - from)) * 100}%`;
    const timeline = div().relative().h(14).w_full().rounded(4).bg(t.surface).overflow_hidden()
      .children(segs.map(([a, b, open], i) => div().id(`seg-${i}`).absolute().top(0).h_full()
        .left(pct(hours(a))).w(`${Math.max(0.6, ((b - a) / 3600 / (to - from)) * 100)}%`)
        .bg(green).opacity(open ? 1 : 0.7).tooltip(`${clock(a)}–${clock(b)} · ${hm(b - a)}`)))
      .child(div().absolute().top(0).h_full().w(1).left(pct(hours(now))).bg(t.foreground).opacity(0.5));
    const ticks = h_flex().justify_between().text_size(10.5).text_color(t.dim)
      .children([from, Math.round((from + to) / 2), to].map((h) => div().child(`${pad(h % 24)}:00`)));

    const edited = s.edited ?? [];
    const fact = (label, value, f) => v_flex().gap(1).child(div().text_size(10.5).text_color(t.dim).child(edited.includes(f) ? `${label} ✎` : label))
      .child(div().text_size(13).text_color(edited.includes(f) ? t.accent : t.foreground).child(value));
    const week = s.week / 3600, target = s.target || 40;
    const button = (id, label, onClick, primary) => h_flex().id(id).h(26).px(10).items_center().justify_center().rounded(6)
      .text_size(12).bg(primary ? t.selected : t.surface).border(1).border_color(t.border).text_color(t.foreground)
      .hover((st) => st.bg(t.hover)).on_click(onClick).child(label);

    return box
      .child(h_flex().items_center().justify_between()
        .child(h_flex().gap(8).items_center()
          .child(div().size(9).rounded_full().bg(dot))
          .child(div().text_size(20).font_semibold().child(hm(s.net)))
          .child(div().text_color(t.muted).child("today")))
        .child(div().text_color(state === "tracking" ? green : t.muted).child(stale ? "Tracker not running" : STATE[state])))
      .child(v_flex().gap(3).child(timeline).child(ticks))
      .child(h_flex().justify_between()
        .child(fact("Start", s.start === null ? "–" : sod(s.start), "start"))
        .child(fact("End", s.end === null ? "–" : sod(s.end), "end"))
        .child(fact("Breaks", hm(s.breaks), "breaks"))
        .child(fact("Net", hm(s.net))))
      .child(v_flex().gap(3)
        .child(h_flex().justify_between().text_color(t.muted)
          .child(div().child("This week"))
          .child(div().child(`${hm(s.week)} / ${target} h`)))
        .child(div().h(5).w_full().rounded(3).bg(t.surface).overflow_hidden()
          .child(div().h_full().w(`${Math.min(100, (week / target) * 100)}%`).bg(week >= target ? green : t.accent))))
      .when(Boolean(s.note), (el) => el.child(div().text_size(11.5).text_color(t.accent).truncate().child(`✎ ${s.note}`)))
      .when(!s.note && ((s.context ?? []).length > 0 || (s.projects ?? []).length > 0), (el) => el.child(div().text_size(11.5).text_color(t.dim).truncate()
        .child([...(s.context ?? []), ...(s.projects ?? [])].join(" · "))))
      .child(h_flex().gap(6)
        .child(state === "paused" ? button("tt-resume", "Resume", this.act("--resume"), true) : button("tt-pause", "Pause", this.act("--pause"), false))
        .when(state === "tracking", (el) => el.child(button("tt-notwork", "Not work", this.act("--notwork"), false).tooltip("Delete the current segment and pause")))
        .child(button("tt-edit", "Edit", (_e, cx) => { this.edit(true); cx.notify(); }, false).tooltip("Correct today's start, end, breaks or note"))
        .child(div().flex_1())
        .child(button("tt-excel", "Open Excel", this.act("open"), false).tooltip(s.xlsx)))
      .when(Boolean(this.err || s.xlsx_error || s.xlsx_hold), (el) => el.child(div().text_size(11).truncate()
        .text_color(this.err || (s.xlsx_error && !/open in Excel/.test(s.xlsx_error)) ? t.blocked : t.dim)
        .child(this.err || (s.xlsx_hold ? "Excel file: writing is on hold" : `Excel file: ${s.xlsx_error}`))));
  }
}
