import { View, div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { exec, appHome, props, shared, setShared, openOverlay, closeOverlay, theme, nextCommand, window, notify } from "herdr";

const KEY = "herdr-lazyagent.snapshot";
const BUSY = "herdr-lazyagent.busy";
const INSTALL_URL = "https://github.com/illegalstudio/lazyagent#install";
const percent = n => `${Math.round(Number(n) || 0)}%`;
const clamp = n => Math.max(0, Math.min(100, Number(n) || 0));
function duration(seconds) {
  const n = Math.max(0, Math.floor(seconds || 0));
  if (n >= 86400) return `${Math.floor(n / 86400)}d ${Math.floor(n % 86400 / 3600)}h`;
  if (n >= 3600) return `${Math.floor(n / 3600)}h ${Math.floor(n % 3600 / 60)}m`;
  return n >= 60 ? `${Math.floor(n / 60)}m` : `${n}s`;
}
function tone(window, t) {
  if (window.used_percent >= 90) return t.blocked;
  if (window.used_percent >= 75 || window.pace_label === "overutilizing") return t.ansi3;
  return t.accent;
}

export default class Limits extends View {
  init(_props, cx) {
    this.panel = props()?.role === "panel";
    this.now = Date.now();
    this.refresh(cx, "auto");
    if (!this.panel) {
      cx.timer.every(300000, cx => this.refresh(cx, "auto"));
      cx.spawn(async cx => {
        for (;;) {
          const command = await nextCommand();
          if (command === "open") this.open();
          if (command === "refresh") this.refresh(cx, "refresh");
        }
      });
    }
    if (this.panel) cx.timer.every(30000, cx => { this.now = Date.now(); cx.notify(); });
  }

  refresh(cx, mode) {
    const active = Number(shared(BUSY) || 0);
    if (active && Date.now() - active < 35000) return;
    setShared(BUSY, Date.now());
    cx.spawn(async cx => {
      try {
        const result = await exec("python3", [`${appHome()}/mods/herdr-lazyagent/snapshot.py`, mode]);
        const value = JSON.parse(result.stdout);
        if (!value || !Array.isArray(value.reports)) throw new Error(value?.error || "Invalid limits response");
        const previous = shared(KEY);
        // A transient failure should leave the last visible data available.
        if (!value.reports.length && value.ok === false && previous?.reports?.length) {
          setShared(KEY, { ...previous, error: value.error, ok: false });
        } else setShared(KEY, value);
      } catch (error) {
        setShared(KEY, { ...(shared(KEY) || { reports: [], errors: [] }), ok: false, error: String(error.message || error) });
      } finally {
        setShared(BUSY, 0);
        this.now = Date.now();
        cx.notify();
      }
    });
  }

  open() {
    openOverlay({ mod: "herdr-lazyagent", kind: "dialog", width: 560, height: 610, props: { role: "panel" } });
  }

  installInstructions(cx) {
    cx.spawn(async () => {
      try {
        const result = await exec(window()?.platform === "macos" ? "open" : "xdg-open", [INSTALL_URL]);
        if (result.code !== 0) throw new Error("Could not open the browser");
      } catch (_error) {
        notify({ title: "Open the installation guide", body: INSTALL_URL, level: "error" });
      }
    });
  }

  compact(data, busy, t) {
    const reports = data?.reports || [];
    const windows = reports.flatMap(r => r.windows || []);
    const worst = windows.reduce((a, b) => b.used_percent > (a?.used_percent ?? -1) ? b : a, null);
    const label = worst ? `${percent(worst.used_percent)} used` : busy ? "Loading…" : "View limits";
    const issue = data?.error || (data?.errors || []).some(e => e.kind === "error") || reports.some(r => r.stale);
    return h_flex().id("lazyagent-limits").size_full().px(12).gap(8).items_center()
      .bg(t.chrome).border_t(1).border_color(t.border).text_size(11.8).text_color(t.muted)
      .cursor_pointer().hover(s => s.bg(t.hover)).on_click(() => this.open())
      .child(div().size(6).rounded(3).bg(issue ? t.ansi3 : worst ? tone(worst, t) : t.dim))
      .child("Limits").child(div().flex_1())
      .child(div().text_color(t.foreground).child(label))
      .child(div().text_color(t.dim).child(busy ? "↻" : "↗"));
  }

  window(window, report, data, t, index) {
    const fetched = Number(report.fetched_at || data.fetchedAt || 0) * 1000;
    const remaining = Math.max(0, Number(window.reset_in_seconds || 0) - Math.max(0, this.now - fetched) / 1000);
    const pace = window.pace_known ? ({ underutilizing: "Under pace", "on track": "On pace", overutilizing: "Over pace" })[window.pace_label] || "" : "Pace unavailable";
    return v_flex().gap(7).pt(index ? 14 : 10)
      .child(h_flex().items_center().justify_between()
        .child(div().text_color(t.muted).child(window.label || "Usage"))
        .child(div().font_semibold().text_color(tone(window, t)).child(`${percent(window.used_percent)} used`)))
      .child(div().relative().w_full().h(6).rounded(3).bg(t.hover)
        .child(div().h(6).w(`${clamp(window.used_percent)}%`).rounded(3).bg(tone(window, t)))
        .when(window.pace_known === true, el => el.child(div().absolute().left(`${clamp(window.expected_percent)}%`).top(-2).w(2).h(10).bg(t.foreground))))
      .child(h_flex().items_center().justify_between().text_size(11).text_color(t.dim)
        .child(window.resets_at ? remaining > 0 ? `Reset in ${duration(remaining)}` : "Reset due" : "Reset unavailable")
        .child(`${pace}${window.pace_known ? ` · ${percent(window.expected_percent)} expected` : ""}`));
  }

  panelView(data, busy, t) {
    const reports = data?.reports || [];
    const errors = data?.errors || [];
    const failures = errors.filter(e => e.kind === "error");
    const skipped = errors.filter(e => e.kind !== "error");
    const fetched = Number(data?.fetchedAt || 0) * 1000;
    const until = Number(data?.backoffUntil || 0) * 1000;
    const button = (id, text, click) => div().id(id).px(10).py(6).rounded(6).bg(t.raised)
      .text_size(12).cursor_pointer().hover(s => s.bg(t.hover)).on_click(click).child(text);
    return v_flex().id("limits-panel").size_full().bg(t.popover).text_color(t.foreground).p(18).gap(12)
      .child(h_flex().items_center().gap(8)
        .child(v_flex().flex_1().gap(3)
          .child(div().text_size(16).font_semibold().child("Agent limits"))
          .child(div().text_size(11).text_color(t.dim).child(`${data?.machine || "This computer"} · ${fetched ? `updated ${duration((this.now - fetched) / 1000)} ago` : "No snapshot yet"}`)))
        .child(button("limits-refresh", busy ? "Refreshing…" : "Refresh", (_e, cx) => this.refresh(cx, "refresh")))
        .child(button("limits-close", "×", () => closeOverlay(props().overlay))))
      .when(until > this.now, el => el.child(div().text_size(11).text_color(t.ansi3).child(`Provider rate limit · retry in ${duration((until - this.now) / 1000)}`)))
      .child(v_flex().id("limits-scroll").flex_1().min_h(0).overflow_y_scrollbar().gap(10).text_size(12)
        .when(!reports.length, el => el.child(!busy && data?.installed === false
          ? v_flex().py(24).gap(12)
            .child(div().font_semibold().text_size(14).child("lazyagent was not found on this computer."))
            .child(div().text_color(t.muted).child("Install lazyagent to read your agent limits."))
            .child(div().text_color(t.muted).child("Follow the GitHub guide for your operating system."))
            .child(h_flex().child(button("limits-install", "Installation instructions on GitHub ↗", (_e, cx) => this.installInstructions(cx))))
            .child(div().text_size(11).text_color(t.dim).child("Make sure lazyagent is on your PATH, then click Refresh."))
          : div().py(30).text_color(t.muted).child(busy ? "Reading your agent limits…" : "No usage windows available. Sign in to an agent, then refresh.")))
        .children(reports.map((r, i) => v_flex().id(`limit-report-${i}`).p(14).gap(2).rounded(9).border_1().border_color(t.border).bg(t.surface)
          .child(h_flex().items_center().justify_between()
            .child(div().font_semibold().text_size(13).child(r.provider || r.short_name || r.agent))
            .when(r.stale === true, el => el.child(div().text_size(10).text_color(t.ansi3).child("Stale data"))))
          .children((r.windows || []).map((w, index) => this.window(w, r, data, t, index)))))
        .when(Boolean(data?.error), el => el.child(div().text_color(t.blocked).child(data.error)))
        .children(failures.map((e, i) => div().id(`limits-error-${i}`).text_size(11).text_color(t.ansi3).child(`${e.agent}: ${e.message}`)))
        .when(skipped.length > 0, el => el.child(div().text_size(10.5).text_color(t.dim).child(`Not available: ${skipped.map(e => e.agent).join(", ")}`))))
      .child(div().text_size(10).text_color(t.dim).child("Usage and pace from lazyagent · marker = expected usage · refresh every 5 min"));
  }

  render() {
    const data = shared(KEY);
    const busy = Number(shared(BUSY) || 0) > Date.now() - 35000;
    return this.panel ? this.panelView(data, busy, theme()) : this.compact(data, busy, theme());
  }
}
