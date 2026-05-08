"""知末下载器 GUI —— tkinter 界面"""
import os
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DOWNLOAD_DIR, DB_PATH
from db import init_db, get_cookies, get_daily_summary, get_records_by_date
from downloader import DownloadJob, get_enterprise_info, build_session
from login import login_and_save_cookies


class DownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("知末下载器")
        self.root.geometry("780x700")
        self.root.minsize(600, 500)

        self.job = None
        self.job_thread = None
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue()

        self._setup_ui()
        self._refresh_account_info()
        self._refresh_history()
        self.root.after(100, self._poll_queues)

    def _setup_ui(self):
        pad = {"padx": 10, "pady": 5}

        # 1. 账号与存储
        account_frame = ttk.LabelFrame(self.root, text="账号与存储", padding=8)
        account_frame.pack(fill=tk.X, **pad)

        self.account_status = ttk.Label(account_frame, text="未登录",
                                        foreground="red", font=("", 10, "bold"))
        self.account_status.pack(side=tk.LEFT, padx=(0, 8))

        self.account_name_label = ttk.Label(account_frame, text="")
        self.account_name_label.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Button(account_frame, text="重新登录",
                   command=self._on_login).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Separator(account_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(account_frame, text="保存到:").pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=DOWNLOAD_DIR)
        dir_entry = ttk.Entry(account_frame, textvariable=self.dir_var, width=32)
        dir_entry.pack(side=tk.LEFT, padx=(4, 4))
        ttk.Button(account_frame, text="浏览",
                   command=self._on_browse_dir).pack(side=tk.LEFT)

        # 2. 下载设置
        setting_frame = ttk.LabelFrame(self.root, text="下载设置", padding=8)
        setting_frame.pack(fill=tk.X, **pad)

        ttk.Label(setting_frame, text="日期范围").pack(side=tk.LEFT, padx=(0, 6))

        today = datetime.now()
        default_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        default_end = today.strftime("%Y-%m-%d")

        self.start_var = tk.StringVar(value=default_start)
        self.end_var = tk.StringVar(value=default_end)

        ttk.Entry(setting_frame, textvariable=self.start_var, width=12).pack(side=tk.LEFT)
        ttk.Label(setting_frame, text=" — ").pack(side=tk.LEFT)
        ttk.Entry(setting_frame, textvariable=self.end_var, width=12).pack(side=tk.LEFT)

        ttk.Separator(setting_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(setting_frame, text="最近7天",
                   command=self._set_recent_7).pack(side=tk.LEFT, padx=2)
        ttk.Button(setting_frame, text="本月",
                   command=self._set_this_month).pack(side=tk.LEFT, padx=2)
        ttk.Button(setting_frame, text="上月",
                   command=self._set_last_month).pack(side=tk.LEFT, padx=2)

        ttk.Separator(setting_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        self.skip_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(setting_frame, text="跳过已下载过的",
                        variable=self.skip_var).pack(side=tk.LEFT)

        # 3. 控制区
        control_frame = ttk.Frame(self.root, padding=5)
        control_frame.pack(fill=tk.X, **pad)

        self.start_btn = ttk.Button(control_frame, text="开始下载",
                                    command=self._on_start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = ttk.Button(control_frame, text="停止",
                                   command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        self.progress_label = ttk.Label(control_frame, text="就绪")
        self.progress_label.pack(side=tk.LEFT, padx=(16, 0))

        self.progress_bar = ttk.Progressbar(control_frame, mode="indeterminate", length=200)
        self.progress_bar.pack(side=tk.RIGHT, padx=(0, 5))

        # 4. 运行日志
        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, **pad)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED,
                                font=("Consolas", 10), bg="#fafafa", relief=tk.SOLID,
                                borderwidth=1)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_configure("error", foreground="#d32f2f")
        self.log_text.tag_configure("success", foreground="#2e7d32")
        self.log_text.tag_configure("warn", foreground="#e65100")

        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 5. 下载历史
        history_frame = ttk.LabelFrame(self.root, text="下载历史（按日期汇总）", padding=5)
        history_frame.pack(fill=tk.BOTH, **pad)

        columns = ("日期", "总数", "成功", "失败")
        self.history_tree = ttk.Treeview(history_frame, columns=columns,
                                         show="headings", height=6)
        for col in columns:
            self.history_tree.heading(col, text=col)
            self.history_tree.column(col, width=80, anchor=tk.CENTER)
        self.history_tree.column("日期", width=100)

        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        hist_scroll = ttk.Scrollbar(history_frame, command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=hist_scroll.set)
        hist_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.history_tree.bind("<Double-1>", self._on_history_double_click)

    # ── 日期快捷按钮 ──

    def _set_recent_7(self):
        today = datetime.now()
        self.start_var.set((today - timedelta(days=7)).strftime("%Y-%m-%d"))
        self.end_var.set(today.strftime("%Y-%m-%d"))

    def _set_this_month(self):
        today = datetime.now()
        self.start_var.set(today.replace(day=1).strftime("%Y-%m-%d"))
        self.end_var.set(today.strftime("%Y-%m-%d"))

    def _set_last_month(self):
        today = datetime.now()
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        self.start_var.set(last_month_start.strftime("%Y-%m-%d"))
        self.end_var.set(last_month_end.strftime("%Y-%m-%d"))

    # ── 账号 ──

    def _refresh_account_info(self):
        cookies_json = get_cookies(DB_PATH)
        if not cookies_json:
            self.account_status.config(text="未登录", foreground="red")
            self.account_name_label.config(text="")
            return

        try:
            session = build_session(cookies_json)
            info = get_enterprise_info(session)
            nick = info.get("nick_name", "")
            eid = info.get("enterprise_id", "")
            self.account_status.config(text="已登录", foreground="green")
            self.account_name_label.config(text=f"{nick}  (ID: {eid})")
        except Exception as e:
            self.account_status.config(text="登录过期", foreground="red")
            self.account_name_label.config(text=str(e)[:40])

    def _on_login(self):
        self._log("打开浏览器登录...")
        try:
            login_and_save_cookies()
            self._log("登录完成")
            self._refresh_account_info()
        except Exception as e:
            self._log(f"登录失败: {e}", "error")

    def _on_browse_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get())
        if path:
            self.dir_var.set(path)

    # ── 下载控制 ──

    def _on_start(self):
        start_date = self.start_var.get().strip()
        end_date = self.end_var.get().strip()

        if not start_date or not end_date:
            messagebox.showwarning("提示", "请填写日期范围")
            return

        cookies_json = get_cookies(DB_PATH)
        if not cookies_json:
            messagebox.showwarning("提示", "请先登录")
            return

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_bar.start()
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

        self.job = DownloadJob(
            mode="incremental",
            start_date=start_date,
            end_date=end_date,
            skip_downloaded=self.skip_var.get(),
            download_dir=self.dir_var.get(),
            log_callback=self._enqueue_log,
            progress_callback=self._enqueue_progress,
        )

        self.job_thread = threading.Thread(target=self._run_job, daemon=True)
        self.job_thread.start()

    def _run_job(self):
        try:
            self.job.run()
        except Exception as e:
            self._enqueue_log(f"下载任务异常: {e}", "error")
        finally:
            self._enqueue_log("", "done")

    def _on_stop(self):
        if self.job:
            self.job.stop()
            self._log("正在停止...", "warn")

    # ── 日志 / 进度队列 ──

    def _enqueue_log(self, msg, level="info"):
        self.log_queue.put((level, msg))

    def _enqueue_progress(self, page, total_pages, count):
        self.progress_queue.put((page, total_pages, count))

    def _log(self, msg, level="info"):
        self.log_text.configure(state=tk.NORMAL)
        tag = {"error": "error", "success": "success", "warn": "warn"}.get(level, None)
        if tag:
            self.log_text.insert(tk.END, msg + "\n", tag)
        else:
            self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _poll_queues(self):
        try:
            while True:
                level, msg = self.log_queue.get_nowait()
                if level == "done":
                    self.progress_bar.stop()
                    self.progress_label.config(text="下载完成")
                    self.start_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                    self._refresh_history()
                    return
                self._log(msg, level)
        except queue.Empty:
            pass

        try:
            while True:
                page, total, count = self.progress_queue.get_nowait()
                if total:
                    self.progress_label.config(
                        text=f"第 {page}/{total} 页 · 已下载 {count} 条")
                else:
                    self.progress_label.config(
                        text=f"第 {page} 页 · 已下载 {count} 条")
        except queue.Empty:
            pass

        self.root.after(100, self._poll_queues)

    # ── 下载历史 ──

    def _refresh_history(self):
        for row in self.history_tree.get_children():
            self.history_tree.delete(row)
        try:
            rows = get_daily_summary(DB_PATH)
            for date_str, total, done, failed in rows:
                self.history_tree.insert("", tk.END,
                                         values=(date_str, total, done, failed))
        except Exception:
            pass

    def _on_history_double_click(self, event):
        selection = self.history_tree.selection()
        if not selection:
            return
        values = self.history_tree.item(selection[0], "values")
        date_str = values[0]

        detail_win = tk.Toplevel(self.root)
        detail_win.title(f"下载详情 - {date_str}")
        detail_win.geometry("600x350")

        cols = ("模型ID", "模型名", "状态", "路径")
        tree = ttk.Treeview(detail_win, columns=cols, show="headings")
        for col in cols:
            tree.heading(col, text=col)
        tree.column("模型ID", width=100)
        tree.column("模型名", width=140)
        tree.column("状态", width=60, anchor=tk.CENTER)
        tree.column("路径", width=280)

        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        try:
            records = get_records_by_date(DB_PATH, date_str)
            for r in records:
                tree.insert("", tk.END, values=(
                    r["model_id"], r["model_name"], r["status"],
                    r.get("file_path", "") or r.get("error_msg", "")))
        except Exception as e:
            tree.insert("", tk.END, values=("", f"加载失败: {e}", "", ""))


def main():
    init_db(DB_PATH)
    root = tk.Tk()
    app = DownloaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
