"""Tkinter user interface.

Key Tkinter ideas used here (coming from Streamlit):
* Tkinter does NOT re-run your script. It runs an event loop (root.mainloop())
  and calls your functions when buttons are clicked.
* Widgets keep their own state; you update them by calling methods on them.
* Only the main thread may touch widgets. Background threads therefore put
  messages on a queue.Queue and the main thread polls it every 100 ms.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import duplicate_detector
import history
import organizer
import scanner
from scanner import FolderError, ScanResult, format_size

APP_TITLE = "Smart File Organizer & Duplicate Detector"

# Colour scheme
BG = "#f4f6fa"
PRIMARY = "#1f3a5f"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
TEXT = "#1f2937"
MUTED = "#6b7280"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")


class SmartFileOrganizerApp:
    def __init__(self, root: tk.Tk, db: history.HistoryDB) -> None:
        self.root = root
        self.db = db

        self.scan_result: ScanResult | None = None
        self.duplicate_report: duplicate_detector.DuplicateReport | None = None
        self.plan: organizer.OrganizationPlan | None = None
        self.can_undo = False
        self.busy = False
        self.cancel_event = threading.Event()
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._duplicate_paths: dict[str, Path] = {}

        # StringVar/BooleanVar: variables that widgets read from and write to.
        self.folder_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a folder, then click Scan Files.")
        self.summary_var = tk.StringVar()
        self.separate_var = tk.BooleanVar(value=False)

        self._configure_style()
        self._build_layout()
        self._refresh_history()
        self._update_buttons()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_messages)

    # ------------------------------------------------------------------ style
    def _configure_style(self) -> None:
        self.root.configure(bg=BG)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=FONT, background=BG, foreground=TEXT)
        style.configure("Header.TFrame", background=PRIMARY)
        style.configure("Title.TLabel", background=PRIMARY, foreground="white",
                        font=("Segoe UI", 16, "bold"))
        style.configure("Subtitle.TLabel", background=PRIMARY, foreground="#c7d2e5")
        style.configure("Status.TLabel", background="#e2e8f0", padding=(10, 5))
        style.configure("Summary.TLabel", foreground=MUTED)
        style.configure("TButton", padding=(12, 6), background="#e2e8f0", borderwidth=0)
        style.map("TButton", background=[("active", "#cbd5e1"), ("disabled", "#eef1f5")],
                  foreground=[("disabled", "#9ca3af")])
        style.configure("Accent.TButton", background=ACCENT, foreground="white", font=FONT_BOLD)
        style.map("Accent.TButton",
                  background=[("active", ACCENT_DARK), ("disabled", "#a5bff3")],
                  foreground=[("disabled", "white")])
        style.configure("Treeview", rowheight=24, background="white",
                        fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", font=FONT_BOLD, background="#e2e8f0", padding=4)
        style.map("Treeview", background=[("selected", "#bfdbfe")],
                  foreground=[("selected", TEXT)])
        style.configure("TNotebook", background=BG)
        style.configure("TNotebook.Tab", padding=(16, 6), font=FONT_BOLD)
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", background=BG, font=FONT_BOLD)
        style.configure("TCheckbutton", background=BG)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#dbe1ea")

    # ----------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(18, 12))
        header.pack(side="top", fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, style="Subtitle.TLabel",
                  text="Scan, preview, organize and undo. Nothing is deleted or overwritten.",
                  ).pack(anchor="w")

        bottom = ttk.Frame(self.root, padding=(12, 6))
        bottom.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var, style="Status.TLabel",
                  anchor="w").pack(fill="x", pady=(4, 0))

        body = ttk.Frame(self.root, padding=(12, 10, 12, 0))
        body.pack(fill="both", expand=True)

        folder_row = ttk.Frame(body)
        folder_row.pack(fill="x")
        ttk.Label(folder_row, text="Folder:", font=FONT_BOLD).pack(side="left")
        ttk.Entry(folder_row, textvariable=self.folder_var).pack(
            side="left", fill="x", expand=True, padx=8, ipady=3)
        self.browse_btn = ttk.Button(folder_row, text="Browse...", command=self.on_browse)
        self.browse_btn.pack(side="left")

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 4))
        self.scan_btn = ttk.Button(actions, text="Scan Files", style="Accent.TButton",
                                   command=self.on_scan)
        self.detect_btn = ttk.Button(actions, text="Detect Duplicates", command=self.on_detect)
        self.preview_btn = ttk.Button(actions, text="Preview Organization",
                                      command=self.on_preview)
        self.organize_btn = ttk.Button(actions, text="Organize Files", style="Accent.TButton",
                                       command=self.on_organize)
        self.undo_btn = ttk.Button(actions, text="Undo Last Operation", command=self.on_undo)
        self.cancel_btn = ttk.Button(actions, text="Cancel", command=self.on_cancel)
        for button in (self.scan_btn, self.detect_btn, self.preview_btn,
                       self.organize_btn, self.undo_btn):
            button.pack(side="left", padx=(0, 6))
        self.cancel_btn.pack(side="right")

        ttk.Checkbutton(
            body, variable=self.separate_var, command=self._invalidate_plan,
            text=f"Move extra duplicate copies into a '{organizer.DUPLICATES_FOLDER}' folder "
                 "(they are never deleted)",
        ).pack(anchor="w", pady=(0, 6))

        paned = ttk.PanedWindow(body, orient="vertical")
        paned.pack(fill="both", expand=True)

        top = ttk.Frame(paned)
        paned.add(top, weight=3)
        self.notebook = ttk.Notebook(top)
        self.notebook.pack(fill="both", expand=True)
        files_tab, preview_tab, dup_tab = (ttk.Frame(self.notebook) for _ in range(3))
        self.notebook.add(files_tab, text="Files")
        self.notebook.add(preview_tab, text="Preview")
        self.notebook.add(dup_tab, text="Duplicates")
        ttk.Label(top, textvariable=self.summary_var, style="Summary.TLabel").pack(
            anchor="w", pady=(4, 2))

        self.files_tree = self._make_tree(files_tab, [
            ("name", "Name", 280, "w"), ("ext", "Extension", 80, "center"),
            ("size", "Size", 90, "e"), ("category", "Category", 110, "w"),
            ("dup", "Duplicate Status", 170, "w")])
        self.preview_tree = self._make_tree(preview_tab, [
            ("name", "Original Filename", 190, "w"), ("source", "Current Location", 250, "w"),
            ("dest", "Proposed Destination", 300, "w"), ("category", "Category", 100, "w"),
            ("dup", "Duplicate Status", 130, "w"), ("action", "Planned Action", 210, "w")])
        self.preview_tree.tag_configure("skip", foreground=MUTED)

        toolbar = ttk.Frame(dup_tab)
        toolbar.pack(fill="x", pady=(4, 4), padx=4)
        ttk.Button(toolbar, text="Show Selected in Explorer",
                   command=self.on_show_in_explorer).pack(side="left")
        ttk.Label(toolbar, style="Summary.TLabel",
                  text="  Review each group yourself. Nothing is deleted automatically.",
                  ).pack(side="left")
        self.dup_tree = self._make_tree(
            dup_tab, [("size", "Size", 90, "e"), ("folder", "Location", 320, "w"),
                      ("modified", "Modified", 150, "w")],
            show="tree headings", tree_title="Group / File", tree_width=380)

        history_frame = ttk.LabelFrame(paned, text="Activity History", padding=6)
        paned.add(history_frame, weight=2)
        ttk.Button(history_frame, text="Refresh", command=self._refresh_history).pack(
            anchor="e", pady=(0, 4))
        self.history_tree = self._make_tree(history_frame, [
            ("time", "Date / Time", 140, "w"), ("operation", "Operation", 70, "w"),
            ("filename", "Filename", 160, "w"), ("source", "Source Path", 260, "w"),
            ("dest", "Destination Path", 260, "w"), ("status", "Status", 70, "w"),
            ("error", "Error Message", 240, "w")])
        self.history_tree.tag_configure("Failed", foreground="#b91c1c")
        self.history_tree.tag_configure("Skipped", foreground=MUTED)

    def _make_tree(self, parent: tk.Misc, columns: list[tuple[str, str, int, str]],
                   show: str = "headings", tree_title: str = "",
                   tree_width: int = 0) -> ttk.Treeview:
        """A Treeview (table) with vertical and horizontal scrollbars."""
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show=show)
        for key, title, width, anchor in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor=anchor, stretch=True)
        if "tree" in show:
            tree.heading("#0", text=tree_title)
            tree.column("#0", width=tree_width, stretch=True)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    @staticmethod
    def _clear(tree: ttk.Treeview) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    # ------------------------------------------------------- background work
    def _run_background(self, work: Callable[[], Any], on_done: Callable[[Any], None],
                        message: str) -> None:
        """Run `work` in a thread; call `on_done(result)` on the main thread."""
        if self.busy:
            return
        self.cancel_event.clear()
        self.progress.configure(value=0, maximum=1)
        self.status_var.set(message)
        self._set_busy(True)

        def target() -> None:
            try:
                result = work()
            except Exception as exc:          # reported on the main thread
                self.messages.put(("error", exc))
            else:
                self.messages.put(("done", (on_done, result)))

        threading.Thread(target=target, daemon=True).start()

    def _progress_callback(self, done: int, total: int) -> None:
        """Called from worker threads: only queues a message (thread-safe)."""
        self.messages.put(("progress", (done, total)))

    def _poll_messages(self) -> None:
        latest_progress = None
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "progress":
                    latest_progress = payload
                elif kind == "done":
                    self._set_busy(False)
                    on_done, result = payload
                    try:
                        on_done(result)
                    except Exception as exc:
                        self._show_error(exc)
                elif kind == "error":
                    self._set_busy(False)
                    self._show_error(payload)
        except queue.Empty:
            pass
        if latest_progress and self.busy:
            done, total = latest_progress
            self.progress.configure(maximum=max(total, 1), value=done)
        self.root.after(100, self._poll_messages)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if not busy:
            self.progress.configure(value=0)
        self._update_buttons()

    def _update_buttons(self) -> None:
        def state(enabled: bool) -> str:
            return "normal" if enabled and not self.busy else "disabled"

        has_scan = self.scan_result is not None
        self.scan_btn.configure(state=state(True))
        self.browse_btn.configure(state=state(True))
        self.detect_btn.configure(state=state(has_scan))
        self.preview_btn.configure(state=state(has_scan))
        self.organize_btn.configure(state=state(bool(self.plan and self.plan.moves)))
        self.undo_btn.configure(state=state(self.can_undo))
        self.cancel_btn.configure(state="normal" if self.busy else "disabled")

    def _show_error(self, exc: BaseException) -> None:
        if isinstance(exc, organizer.StalePlanError):
            lines = "\n".join(f"- {name}: {why}" for name, why in exc.changed[:10])
            more = f"\n...and {len(exc.changed) - 10} more" if len(exc.changed) > 10 else ""
            messagebox.showwarning(
                "Files changed",
                f"Some files changed since the preview:\n\n{lines}{more}\n\n"
                "Nothing was moved. Please scan and preview again.")
            self._invalidate_plan()
            self.status_var.set("Files changed since preview. Scan and preview again.")
            return
        messagebox.showerror("Error", str(exc) or exc.__class__.__name__)
        self.status_var.set(f"Error: {exc}")

    # ---------------------------------------------------------------- actions
    def on_browse(self) -> None:
        chosen = filedialog.askdirectory(title="Select a folder to organize", mustexist=True)
        if chosen:
            self.folder_var.set(os.path.normpath(chosen))
            self._reset_results()
            self.status_var.set("Folder selected. Click Scan Files.")

    def on_scan(self) -> None:
        try:
            folder = scanner.validate_folder(self.folder_var.get())
        except FolderError as exc:
            messagebox.showerror("Invalid folder", str(exc))
            return
        self._reset_results()
        self._run_background(
            lambda: scanner.scan_folder(folder, self._progress_callback, self.cancel_event),
            self._scan_done, f"Scanning {folder} ...")

    def _scan_done(self, result: ScanResult) -> None:
        if result.cancelled:
            self.status_var.set("Scan cancelled.")
            return
        self.scan_result = result
        self._populate_files()
        self._update_buttons()
        skipped = f" ({len(result.skipped)} skipped)" if result.skipped else ""
        self.status_var.set(f"Scan complete: {result.total_files} files found{skipped}.")
        self.notebook.select(0)

    def on_detect(self) -> None:
        if not self.scan_result:
            return
        files = self.scan_result.files
        self._run_background(
            lambda: duplicate_detector.find_duplicates(
                files, self._progress_callback, self.cancel_event),
            self._detect_done, "Looking for duplicate files (SHA-256) ...")

    def _detect_done(self, report: duplicate_detector.DuplicateReport) -> None:
        if report.cancelled:
            self.duplicate_report = None
            self.status_var.set("Duplicate detection cancelled.")
            self._populate_files()
            return
        self.duplicate_report = report
        self._invalidate_plan()
        self._populate_files()
        self._populate_duplicates()
        if report.groups:
            self.status_var.set(
                f"Found {len(report.groups)} duplicate group(s); "
                f"extra copies use {format_size(report.wasted_bytes)}.")
            self.notebook.select(2)
        else:
            self.status_var.set("No duplicate files found.")
        if report.errors:
            messagebox.showwarning(
                "Some files could not be read",
                "\n".join(f"{n}: {why}" for n, why in report.errors[:10]))

    def on_preview(self) -> None:
        if not self.scan_result:
            return
        try:
            self.plan = organizer.build_plan(
                self.scan_result, self.separate_var.get(),
                organizer.get_protected_paths([self.db.path]))
        except organizer.PlanError as exc:
            self.plan = None
            messagebox.showerror("Cannot organize this folder", str(exc))
            self._update_buttons()
            return
        self._populate_preview()
        self._update_buttons()
        self.notebook.select(1)
        self.status_var.set(
            f"Preview ready: {len(self.plan.moves)} to move, {len(self.plan.skips)} skipped. "
            "No files were changed.")

    def on_organize(self) -> None:
        plan = self.plan
        if not plan or not plan.moves:
            return
        if not messagebox.askyesno(
                "Confirm organization",
                f"Move {len(plan.moves)} file(s) into category folders inside:\n\n"
                f"{plan.folder}\n\nNothing will be deleted or overwritten, and you can "
                "undo this afterwards.\n\nContinue?"):
            return
        self._run_background(
            lambda: organizer.execute_plan(plan, self.db, self._progress_callback,
                                           self.cancel_event),
            self._organize_done, "Organizing files ...")

    def _organize_done(self, result: organizer.OrganizeResult) -> None:
        lines = [f"Moved: {result.moved}", f"Skipped: {result.skipped}",
                 f"Failed: {len(result.failed)}"]
        if result.cancelled:
            lines.append("The operation was cancelled before it finished.")
        if result.failed:
            lines.append("")
            lines += [f"- {n}: {why}" for n, why in result.failed[:15]]
            if len(result.failed) > 15:
                lines.append(f"...and {len(result.failed) - 15} more (see Activity History)")
        self._reset_results()
        self._refresh_history()
        self.status_var.set(f"Organization finished: {result.moved} moved. Scan again to refresh.")
        messagebox.showinfo("Organization finished", "\n".join(lines))

    def on_undo(self) -> None:
        operation = self.db.get_last_undoable_operation()
        if not operation:
            messagebox.showinfo("Nothing to undo", "There is no operation to undo.")
            self._refresh_history()
            return
        if not messagebox.askyesno(
                "Confirm undo",
                f"Undo the organization from {operation['started_at']}?\n\n"
                f"Folder: {operation['folder']}\n"
                f"Files to restore: {operation['pending']}\n\n"
                "Files that changed, or whose original location is now occupied, "
                "are left where they are and reported."):
            return
        self._run_background(
            lambda: history.undo_last_operation(self.db, self._progress_callback),
            self._undo_done, "Undoing last operation ...")

    def _undo_done(self, result: history.UndoResult) -> None:
        lines = [f"Restored: {result.restored}", f"Could not restore: {len(result.failed)}"]
        if result.failed:
            lines.append("")
            lines += [f"- {n}: {why}" for n, why in result.failed[:15]]
            if len(result.failed) > 15:
                lines.append(f"...and {len(result.failed) - 15} more (see Activity History)")
        self._reset_results()
        self._refresh_history()
        self.status_var.set(f"Undo finished: {result.restored} restored, "
                            f"{len(result.failed)} not restored.")
        (messagebox.showwarning if result.failed else messagebox.showinfo)(
            "Undo finished", "\n".join(lines))

    def on_cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("Cancelling ...")

    def on_show_in_explorer(self) -> None:
        selected = self.dup_tree.selection()
        path = self._duplicate_paths.get(selected[0]) if selected else None
        if path is None:
            messagebox.showinfo("Select a file", "Select a file inside a duplicate group first.")
        elif os.name == "nt":
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            messagebox.showinfo("File location", str(path))

    def on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
                "Work in progress", "An operation is still running. Quit anyway?"):
            return
        self.db.close()
        self.root.destroy()

    # ------------------------------------------------------------ table views
    def _reset_results(self) -> None:
        self.scan_result = None
        self.duplicate_report = None
        self.plan = None
        self._duplicate_paths.clear()
        for tree in (self.files_tree, self.preview_tree, self.dup_tree):
            self._clear(tree)
        self.summary_var.set("")
        self._update_buttons()

    def _invalidate_plan(self) -> None:
        """Statuses or options changed, so an old preview must not be used."""
        self.plan = None
        self._clear(self.preview_tree)
        self._update_buttons()

    def _populate_files(self) -> None:
        assert self.scan_result
        self._clear(self.files_tree)
        for info in self.scan_result.files:
            self.files_tree.insert("", "end", values=(
                info.name, info.extension or "-", format_size(info.size_bytes),
                info.category, info.duplicate_status))
        result = self.scan_result
        self.summary_var.set(
            f"{result.total_files} files   |   total size {format_size(result.total_size)}"
            f"   |   {len(result.skipped)} skipped")

    def _populate_preview(self) -> None:
        assert self.plan
        self._clear(self.preview_tree)
        for action in self.plan.actions:
            self.preview_tree.insert(
                "", "end", tags=("skip",) if action.action == "Skip" else (),
                values=(action.name, str(action.source.parent),
                        str(action.destination) if action.destination else "-",
                        action.category, action.duplicate_status, action.description))

    def _populate_duplicates(self) -> None:
        assert self.duplicate_report
        self._clear(self.dup_tree)
        self._duplicate_paths.clear()
        for group in self.duplicate_report.groups:
            parent = self.dup_tree.insert(
                "", "end", open=True,
                text=f"Group {group.group_id}: {len(group.files)} identical files, "
                     f"{format_size(group.size_bytes)} each",
                values=("", "", ""))
            for info in group.files:
                label = info.name + ("   (oldest)" if info.is_primary_copy else "")
                modified = datetime.fromtimestamp(info.modified_ns / 1e9)
                iid = self.dup_tree.insert(
                    parent, "end", text=label,
                    values=(format_size(info.size_bytes), str(info.path.parent),
                            modified.strftime("%Y-%m-%d %H:%M")))
                self._duplicate_paths[iid] = info.path

    def _refresh_history(self) -> None:
        self._clear(self.history_tree)
        for row in self.db.get_recent_activity(300):
            self.history_tree.insert("", "end", tags=(row["status"],), values=(
                row["timestamp"], row["operation"], row["filename"], row["source_path"],
                row["destination_path"], row["status"], row["error_message"]))
        self.can_undo = self.db.get_last_undoable_operation() is not None
        self._update_buttons()


def run() -> None:
    """Create the window and start the Tkinter event loop."""
    try:                                    # crisp text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1150x800")
    root.minsize(940, 640)
    db = history.HistoryDB(history.default_db_path())
    SmartFileOrganizerApp(root, db)
    root.mainloop()
