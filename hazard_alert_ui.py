# -*- coding: utf-8 -*-
"""
hazard_alert_ui.py - חלון "LifeGuard זיהה סכנה אפשרית" (Windows). עיצוב: כחול-כהה / לבן / כחול של האפליקציה.

- סוג, חומרה, ביטחון בזיהוי, הסבר קצר ומיקום (אם ידוע). "זיהוי צליל בלבד - לא אישור לסכנה".
- פעולות: SOS (אחרי אישור), מקום בטוח, התראה לאנשי הקשר (אחרי אישור; רק כשמחובר לשרת), חיוג לחירום (אחרי
  אישור; tel: - אם אין אפליקציית חיוג במחשב אומרים זאת), התראת שווא (עם סיבה אופציונלית), סגירה.
- שום פעולה לא "מצליחה" בלי תוצאה אמיתית: אנשי קשר - מוצג למי השרת שלח, ואחר כך מי אישר קבלה באפליקציה.
- עברית RTL / אנגלית LTR לפי ההעדפה (hazard_alerts.DEFAULTS["language"]).
"""
import os
import time
import tkinter as tk
from tkinter import messagebox

import hazard_alerts as HA
import ui_theme as T

SEV_COLORS = {"high": "SOS_FILL", "medium": "ORANGE", "low": "BLUE", "info": "TEXT_MUTED"}


class HazardAlertWindow(tk.Toplevel):
    def __init__(self, master, app, alert, prefs=None, confirm=None, dialer=None):
        super().__init__(master)
        self.app, self.alert = app, alert
        self.prefs = prefs or HA.load_prefs(getattr(app, "username", None))
        self.s = HA.STRINGS.get(self.prefs.get("language"), HA.STRINGS["he"])
        self.rtl = self.s["dir"] == "rtl"
        self.anchor, self.side = ("e", "right") if self.rtl else ("w", "left")
        self.confirm = confirm or (lambda title, text: messagebox.askyesno(title, text, parent=self))
        self.dialer = dialer or (lambda number: os.startfile(f"tel:{number}"))   # noqa: S606 - פעולה יזומה
        self.closed = False
        self.title("LifeGuard")
        self.configure(bg=T.BG)
        self.attributes("-topmost", True)
        self.minsize(T.px(560), T.px(420))
        self.protocol("WM_DELETE_WINDOW", self.dismiss)
        self.bind("<Escape>", lambda e: self.dismiss())
        self._build()
        self.refresh(alert)
        if self.prefs.get("sound", True):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ בנייה
    def _lbl(self, parent, text, size, fg, bg, bold=False, wrap=520):
        return tk.Label(parent, text=text, font=T.F(size, "bold" if bold else None), fg=fg, bg=bg, anchor=self.anchor,
                        justify="right" if self.rtl else "left", wraplength=T.px(wrap))

    def _build(self):
        head = tk.Frame(self, bg=T.NAVY)
        head.pack(fill="x")
        self.sev_bar = tk.Frame(head, bg=T.SOS_FILL, width=T.px(8))
        self.sev_bar.pack(side=self.side, fill="y")
        box = tk.Frame(head, bg=T.NAVY)
        box.pack(side=self.side, fill="x", expand=True, padx=14, pady=12)
        self._lbl(box, "⚠️ " + self.s["title"], 13, T.ON_NAVY, T.NAVY, bold=True).pack(fill="x")
        self.kind = self._lbl(box, "", 20, T.ON_NAVY, T.NAVY, bold=True)
        self.kind.pack(fill="x", pady=(4, 0))
        self.updated = self._lbl(box, "", 10, T.ON_NAVY_MUTED, T.NAVY)
        self.updated.pack(fill="x")

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        chips = tk.Frame(body, bg=T.BG)
        chips.pack(fill="x")
        self.sev_chip = tk.Label(chips, text="", font=T.F(11, "bold"), fg="#FFFFFF", padx=10, pady=4)
        self.sev_chip.pack(side=self.side, padx=(0, 6) if not self.rtl else (6, 0))
        self.conf_chip = tk.Label(chips, text="", font=T.F(11), fg=T.TEXT, bg=T.CARD_BG_ALT, padx=10, pady=4)
        self.conf_chip.pack(side=self.side)
        self.explain = self._lbl(body, "", 12, T.TEXT, T.BG)
        self.explain.pack(fill="x", pady=(10, 2))
        self._lbl(body, self.s["not_verified"], 10, T.TEXT_MUTED, T.BG).pack(fill="x")
        self.loc = self._lbl(body, "", 10, T.TEXT_SECONDARY, T.BG)
        self.loc.pack(fill="x", pady=(2, 8))
        self._lbl(body, self.s["question"], 13, T.TEXT, T.BG, bold=True).pack(fill="x", pady=(4, 6))

        grid = tk.Frame(body, bg=T.BG)
        grid.pack(fill="x")
        for c in (0, 1):
            grid.columnconfigure(c, weight=1, uniform="a")
        self.buttons = {}
        connected = self._connected()
        actions = [("sos", self.activate_sos, T.SOS_FILL, "#FFFFFF"),
                   ("safe_place", self.safe_place, T.BLUE, T.ON_ACCENT),
                   ("contacts", self.alert_contacts, T.BLUE, T.ON_ACCENT),
                   ("call", self.call_emergency, T.NAVY, T.ON_NAVY)]
        for i, (key, cmd, bg, fg) in enumerate(actions):
            col = (1 - i % 2) if self.rtl else i % 2
            b = tk.Button(grid, text=self.s[key], command=cmd, font=T.F(13, "bold"), bg=bg, fg=fg,
                          activebackground=bg, activeforeground=fg, bd=0, relief="flat", cursor="hand2",
                          padx=8, pady=T.px(12), wraplength=T.px(240))
            b.grid(row=i // 2, column=col, sticky="nsew", padx=4, pady=4)
            self.buttons[key] = b
        if not connected:
            self.buttons["contacts"].configure(state="disabled", bg=T.CARD_BG_ALT, disabledforeground=T.TEXT_MUTED,
                                               text=self.s["contacts"] + f"\n({self.s['not_connected']})")
        low = tk.Frame(body, bg=T.BG)
        low.pack(fill="x", pady=(8, 0))
        self.buttons["false_alarm"] = tk.Button(low, text=self.s["false_alarm"], command=self.show_reasons,
                                                font=T.F(11), bg=T.CARD_BG_ALT, fg=T.TEXT, bd=0, padx=12, pady=8)
        self.buttons["false_alarm"].pack(side=self.side, padx=4)
        self.buttons["dismiss"] = tk.Button(low, text=self.s["dismiss"], command=self.dismiss, font=T.F(11),
                                            bg=T.CARD_BG_ALT, fg=T.TEXT_SECONDARY, bd=0, padx=12, pady=8)
        self.buttons["dismiss"].pack(side=self.side, padx=4)
        self.status = self._lbl(body, "", 11, T.TEXT_SECONDARY, T.BG)
        self.status.pack(fill="x", pady=(8, 0))
        self.reasons = tk.Frame(body, bg=T.BG)
        self.reason_var = tk.StringVar(value="")

    def _connected(self):
        eng = getattr(self.app, "engine", None)
        try:
            return bool(eng and eng.gateway.connected())
        except Exception:  # noqa: BLE001
            return False

    def refresh(self, alert, updated=False):
        self.alert = alert
        sev = alert["severity"]
        color = getattr(T, SEV_COLORS.get(sev, "BLUE"))
        self.sev_bar.configure(bg=color)
        self.sev_chip.configure(text=f"{self.s['severity']}: {self.s['sev'].get(sev, sev)}", bg=color)
        self.conf_chip.configure(text=f"{self.s['confidence']}: {alert['confidence']:.0%}")
        if self.rtl:
            self.kind.configure(text=alert.get("label_he") or alert["category"])
            self.explain.configure(text=alert.get("explanation") or "")
        else:
            self.kind.configure(text=HA.LABELS_EN.get(alert["category"], alert["category"]))
            self.explain.configure(text=HA.EXPLAIN_EN.get(alert["category"], ""))
        self.updated.configure(text=f"{self.s['updated']} (×{alert['count']})" if updated or alert["count"] > 1 else
                               time.strftime("%H:%M:%S", time.localtime(alert["first_ts"])))
        self.loc.configure(text=self._location_text())

    def _location_text(self):
        try:
            import location_module
            loc = location_module.get_last_known()
        except Exception:  # noqa: BLE001
            loc = None
        if not loc or loc.get("lat") is None:
            return f"📍 {self.s['no_location']}"
        age = time.time() - float(loc.get("timestamp") or loc.get("captured_at") or time.time())
        approx = "" if loc.get("is_precise") else f" ({self.s['approx']})"
        when = " · " + self.s["min_ago"].format(m=int(age // 60)) if age > 120 else ""
        return f"📍 {self.s['location']}: {loc['lat']:.4f}, {loc['lon']:.4f}{approx}{when}"

    # ------------------------------------------------------------ פעולות
    def _close(self):
        self.closed = True
        if self.winfo_exists():
            self.destroy()

    def activate_sos(self):
        if not self.confirm("SOS", self.s["confirm_sos"]):
            return
        self.app.engine.hazard_activate_sos(self.alert)
        self._close()

    def safe_place(self):
        self.app.engine.hazard_respond(self.alert, "safe_place")
        try:
            self.app.show_screen("map")
            scr = self.app.screens["map"]
            if getattr(scr, "origin", None) is None and hasattr(scr, "refresh_location"):
                scr.refresh_location()
            scr.find_safe_place()
        finally:
            self._close()

    def alert_contacts(self):
        if not self.confirm(self.s["contacts"], self.s["confirm_contacts"]):
            return
        self.buttons["contacts"].configure(state="disabled")
        self.status.configure(text=self.s["sending"], fg=T.TEXT_SECONDARY)
        from app_screens import run_bg
        lang = self.prefs.get("language", "he")
        run_bg(self.app, lambda: self.app.engine.hazard_notify_contacts(self.alert, lang), self._sent, self._send_failed)

    def _sent(self, res):
        to = res.get("sent_to") or []
        if not to:
            self.status.configure(text=self.s["no_contacts"], fg=T.RED)
            return
        self.status.configure(text=self.s["sent"].format(n=len(to)), fg=T.TEXT)
        ids = res.get("notification_ids") or {}
        self.after(6000, lambda: self._check_delivery(ids, len(to)))

    def _check_delivery(self, ids, n):
        if not self.winfo_exists():
            return
        from app_screens import run_bg

        def done(st):
            if self.winfo_exists():
                ok = sum(1 for v in (st or {}).values() if v)
                self.status.configure(text=self.s["delivered"].format(ok=ok, n=n), fg=T.GREEN if ok else T.YELLOW)
        run_bg(self.app, lambda: self.app.engine.hazard_delivery_status(ids), done, lambda e: None)

    def _send_failed(self, e):
        self.buttons["contacts"].configure(state="normal")
        self.status.configure(text=self.s["send_failed"].format(e=e), fg=T.RED)

    def call_emergency(self):
        import emergency_numbers_module as EN
        number = EN.format_primary_number(getattr(self.app, "emergency_numbers", None) or EN.get_emergency_numbers(None))
        if not self.confirm(self.s["call"], self.s["confirm_call"].format(n=number)):
            return
        try:
            self.dialer("".join(ch for ch in number if ch.isdigit()))
            self.status.configure(text=self.s["dial_opened"].format(n=number), fg=T.TEXT)
        except (OSError, AttributeError):
            self.status.configure(text=self.s["no_dialer"].format(n=number), fg=T.RED)
        self.app.engine.hazard_respond(self.alert, "call")

    def show_reasons(self):
        for w in self.reasons.winfo_children():
            w.destroy()
        self._lbl(self.reasons, self.s["why"], 11, T.TEXT, T.BG).pack(fill="x")
        names = HA.FALSE_REASONS if self.rtl else HA.REASONS_EN
        grid = tk.Frame(self.reasons, bg=T.BG)
        grid.pack(fill="x")
        for i, (key, label) in enumerate(names.items()):
            col = (1 - i % 2) if self.rtl else i % 2
            tk.Radiobutton(grid, text=label, value=key, variable=self.reason_var, font=T.F(11), bg=T.BG,
                           fg=T.TEXT, selectcolor=T.CARD_BG_ALT, anchor=self.anchor,
                           activebackground=T.BG).grid(row=i // 2, column=col, sticky="ew", padx=4)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        tk.Button(self.reasons, text=self.s["false_alarm"] + " ✓", command=self.false_alarm, font=T.F(11, "bold"),
                  bg=T.BLUE, fg=T.ON_ACCENT, bd=0, padx=12, pady=6).pack(anchor=self.anchor, pady=(4, 0))
        self.reasons.pack(fill="x", pady=(8, 0))

    def false_alarm(self):
        reason = self.reason_var.get() or None
        self.app.engine.hazard_respond(self.alert, "false_alarm", reason)
        self._close()

    def dismiss(self):
        if self.closed:
            return
        if self.alert.get("response") is None:
            self.app.engine.hazard_respond(self.alert, "dismiss")
        self._close()
