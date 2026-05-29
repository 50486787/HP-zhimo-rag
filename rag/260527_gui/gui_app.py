"""gui_app.py — v2 景观搜图系统 GUI 管理台

左侧导航 + 四标签页：配置 / 流水线 / 搜索验证 / 服务面板
"""
import os
import sys
import re
import json
import queue
import threading
import subprocess
import socket
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
THUMB_CACHE_DIR = os.path.join(BASE_DIR, ".thumb_cache")
LOG_PATH = os.path.join(BASE_DIR, "运行日志.log")

# 重模块懒加载
_requests = None
_Image = None
_ImageTk = None
_Searcher = None
_step00 = _step01 = _step02 = _step03 = _step04 = _step05 = None


def _get_requests():
    global _requests
    if _requests is None:
        import requests as _r
        _requests = _r
    return _requests


def _get_PIL():
    global _Image, _ImageTk
    if _Image is None:
        from PIL import Image as _I, ImageTk as _It
        _Image, _ImageTk = _I, _It
    return _Image, _ImageTk


def _get_searcher():
    global _Searcher
    if _Searcher is None:
        from search_core import Searcher as _S
        _Searcher = _S
    return _Searcher


def _get_step(mod_name):
    """懒加载 step 模块，传入模块名（如 'step00_整理目录'）"""
    import importlib
    return importlib.import_module(mod_name)

DEFAULT_CFG = {
    "vision_model": {"api_key": "", "base_url": "", "model": "", "temperature": 0.6, "thinking": False},
    "text_model": {"api_key": "", "base_url": "", "model": "", "temperature": 0.6, "thinking": False},
    "ragflow": {"api_key": "", "base_url": "http://127.0.0.1:9900/api/v1", "dataset_ids": []},
    "bge": {"base_url": "http://127.0.0.1:9997/v1", "model": "bge-m3"},
    "paths": {"nas_base": ""},
    "web": {"port": 8088},
    "xinference": {"command": "xinference-local", "host": "127.0.0.1", "port": 9997},
    "prompts": {
        "step1_blind": (
            "# Role\n你是景观建筑设计院的 3D 资产标注专家。\n\n"
            "# 任务\n根据模型预览图和文件名，为 SU 模型打上一组标签和一段自然语言描述。\n\n"
            "# 规则\n- 参考文件名中的描述信息\n- 标签用逗号分隔，自由发挥\n"
            "- 描述 50-80 字\n- 禁止标注水印内容\n- 禁止标注文件名末尾的 ID_数字\n\n"
            "# 输出格式\n严格只输出 JSON：{\"tags\": \"...\", \"description\": \"...\"}"
        ),
        "step2_merge": (
            "# Role\n你是景观建筑设计院的 3D 资产标注专家。\n\n"
            "# 任务\n标签层级归并：把过于具体的下层标签用更通用的上层范畴词替代。\n\n"
            "# 归并原则\n- 具体词 → 范畴词（如\"蕨类\"→\"蕨类植物\"）\n"
            "- 只在同一语义维度内归并\n- 拿不准的宁可不合并\n\n"
            "# 输出格式\n严格只输出 JSON：{\"原始词\": \"规范词\"}"
        ),
        "step3_refined": (
            "# Role\n你是顶级的景观建筑 3D 资产标注专家。\n\n"
            "# Core Rules\n- 文件名优先：先解析文件名中的下划线分词\n"
            "- 预览图中的水印内容绝对不标注\n- 看不清或不存在的元素，宁可留空\n"
            "- 禁止主观评价词，禁止描述末尾添加总结句\n\n"
            "## 维度\n"
            "style: 现代/新中式/中式/轻奢/极简/古风/意式/欧式/日式/热带度假风/禅意/赛博朋克/宋式/田园/卡通风/工业风\n"
            "tags: 构筑元素数组\n"
            "plants: 植物类型数组\n"
            "material: 材质数组\n"
            "form: 造型特征(通常留空)\n\n"
            "## 受控词表\n{word_list_str}\n\n"
            "# 输出格式\n严格只输出JSON：{\"style\":\"\",\"tags\":[],\"plants\":[],\"material\":[],\"form\":\"\",\"description\":\"\"}"
        ),
    },
}

DEFAULT_FONT = ("Microsoft YaHei UI", 12)
BOLD_FONT = ("Microsoft YaHei UI", 12, "bold")
SMALL_FONT = ("Microsoft YaHei UI", 10)
MONO_FONT = ("Consolas", 10)


# ================= 工具函数 =================

def _real_path(ragflow_path):
    path = ragflow_path[1:] if ragflow_path.startswith("P") else ragflow_path
    return path.replace("_", os.sep)


def _safe_path(nas_base, rest_path):
    parts = rest_path.replace("\\", "/").split("/", 1)
    real_dir = _real_path(parts[0])
    src = os.path.realpath(os.path.join(nas_base, real_dir, parts[1] if len(parts) > 1 else ""))
    nas_real = os.path.realpath(nas_base)
    if os.path.commonpath([nas_real, src]) != nas_real:
        raise ValueError("路径越界")
    return src


def get_lan_ip():
    """获取本机局域网 IP"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# ================= 主应用 =================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("景观AI搜图 v2 — 管理台")
        self.root.geometry("1400x900")
        self.root.minsize(1000, 680)

        self.cfg = self._load_config()
        self.searcher = None
        self._svc_processes = {}

        self.log_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.preview_queue = queue.Queue()
        self._current_results = []
        self._preview_photo = None
        self._preview_current = None

        self._setup_ui()
        self.root.after(100, self._poll_queues)
        threading.Thread(target=self._check_all_services, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================= 配置读写 =================

    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULT_CFG.items()}

    def _save_config(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        self._log("配置已保存", "success")

    def _derived_paths(self):
        """从 NAS 根目录推导子路径。

        粗标 → NAS/粗标输出/
        精标 → 原图同目录（NAS 根目录本身）
        词表 → 粗标输出/规范词频_Top500.json
        CSV  → NAS 父目录
        """
        nas = self.cfg.get("paths", {}).get("nas_base", "")
        if not nas or not os.path.isdir(nas):
            return {}
        return {
            "nas_base": nas,
            "raw_label_dir": os.path.join(nas, "粗标输出"),
            "refined_label_dir": nas,
            "vocab": os.path.join(nas, "粗标输出", "规范词频_Top500.json"),
            "output_dir": os.path.dirname(os.path.abspath(nas)),
        }

    # ================= UI 布局 =================

    def _setup_ui(self):
        style = ttk.Style()
        style.layout("TNotebook.Tab", [])
        # 全局放大 ttk 组件
        style.configure(".", font=DEFAULT_FONT)
        style.configure("TLabel", font=DEFAULT_FONT)
        style.configure("TButton", font=DEFAULT_FONT)
        style.configure("TEntry", font=DEFAULT_FONT)
        style.configure("TCombobox", font=DEFAULT_FONT)
        style.configure("Treeview", font=DEFAULT_FONT, rowheight=26)
        style.configure("TNotebook", font=DEFAULT_FONT)
        style.configure("TLabelframe.Label", font=DEFAULT_FONT)

        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))

        # 左侧导航
        nav_frame = ttk.Frame(main_paned, width=150)
        main_paned.add(nav_frame, weight=0)

        ttk.Label(nav_frame, text="导航", font=BOLD_FONT).pack(pady=(10, 6), padx=10)
        self.nav_tree = ttk.Treeview(nav_frame, show="tree", selectmode="browse", height=4)
        self.nav_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.nav_tree.insert("", tk.END, iid="config", text="  配置")
        self.nav_tree.insert("", tk.END, iid="pipeline", text="  流水线")
        self.nav_tree.insert("", tk.END, iid="search", text="  搜索验证")
        self.nav_tree.insert("", tk.END, iid="services", text="  服务面板")
        self.nav_tree.selection_set("config")
        self.nav_tree.bind("<<TreeviewSelect>>", self._on_nav_select)

        # 右侧内容
        self.notebook = ttk.Notebook(main_paned)
        main_paned.add(self.notebook, weight=1)

        self.tab_config = ttk.Frame(self.notebook)
        self.tab_pipeline = ttk.Frame(self.notebook)
        self.tab_search = ttk.Frame(self.notebook)
        self.tab_services = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_config)
        self.notebook.add(self.tab_pipeline)
        self.notebook.add(self.tab_search)
        self.notebook.add(self.tab_services)

        self._build_config_tab()
        self._build_pipeline_tab()
        self._build_search_tab()
        self._build_services_tab()

        # 底部日志
        bottom = ttk.Frame(self.root)
        bottom.pack(fill=tk.X, padx=6, pady=(4, 4))

        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=140)
        self.progress.pack(side=tk.LEFT, padx=(0, 8))

        log_frame = ttk.LabelFrame(bottom, text="日志", padding=2)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED,
                                font=MONO_FONT, height=4, bg="#fafafa",
                                relief=tk.SOLID, borderwidth=1)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("error", foreground="#d32f2f")
        self.log_text.tag_configure("success", foreground="#2e7d32")
        self.log_text.tag_configure("warn", foreground="#e65100")
        self.log_text.tag_configure("info", foreground="#1565c0")

    def _on_nav_select(self, event):
        sel = self.nav_tree.selection()
        if sel:
            tab_map = {"config": 0, "pipeline": 1, "search": 2, "services": 3}
            self.notebook.select(tab_map.get(sel[0], 0))

    # ================= 标签 1：配置 =================

    def _build_config_tab(self):
        canvas = tk.Canvas(self.tab_config, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.tab_config, orient=tk.VERTICAL, command=canvas.yview)
        frame = ttk.Frame(canvas)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor=tk.NW, width=900)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind("<Enter>", lambda e: e.widget.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: e.widget.unbind_all("<MouseWheel>"))

        pad = {"padx": 8, "pady": 4}

        # 识图模型（Step1/3 标注）
        f_vision = ttk.LabelFrame(frame, text="识图模型 — 标注用 (Step1 / Step3)", padding=6)
        f_vision.pack(fill=tk.X, **pad)
        self._make_entry(f_vision, "vision_model", "api_key", "API Key", 0, 0, show="*")
        self._make_entry(f_vision, "vision_model", "base_url", "Base URL", 0, 1)
        self._make_entry(f_vision, "vision_model", "model", "模型", 0, 2)
        self._make_entry(f_vision, "vision_model", "temperature", "Temperature", 1, 0)
        self._make_think_cb(f_vision, "vision_model", 1, 1)

        # 文字模型（Step2/Step6 翻译归并）
        f_text = ttk.LabelFrame(frame, text="文字模型 — 翻译 / 归并用 (Step2 / Step6)", padding=6)
        f_text.pack(fill=tk.X, **pad)
        self._make_entry(f_text, "text_model", "api_key", "API Key", 0, 0, show="*")
        self._make_entry(f_text, "text_model", "base_url", "Base URL", 0, 1)
        self._make_entry(f_text, "text_model", "model", "模型", 0, 2)
        self._make_entry(f_text, "text_model", "temperature", "Temperature", 1, 0)
        self._make_think_cb(f_text, "text_model", 1, 1)

        # RAGFlow
        f_rf = ttk.LabelFrame(frame, text="RAGFlow（文档存储）", padding=6)
        f_rf.pack(fill=tk.X, **pad)
        self._make_entry(f_rf, "ragflow", "api_key", "API Key", 0, 0, show="*")
        self._make_entry(f_rf, "ragflow", "base_url", "Base URL", 0, 1)
        self._make_entry(f_rf, "ragflow", "dataset_ids", "数据集 ID（逗号分隔）", 0, 2)

        # Xinference
        f_xinf = ttk.LabelFrame(frame, text="Xinference 服务", padding=6)
        f_xinf.pack(fill=tk.X, **pad)
        self._make_entry(f_xinf, "xinference", "command", "启动命令", 0, 0)
        self._make_entry(f_xinf, "xinference", "host", "Host", 0, 1)
        self._make_entry(f_xinf, "xinference", "port", "端口", 0, 2)

        # BGE
        f_bge = ttk.LabelFrame(frame, text="BGE Embedding（Xinference）", padding=6)
        f_bge.pack(fill=tk.X, **pad)
        self._make_entry(f_bge, "bge", "base_url", "Base URL", 0, 0)
        self._make_entry(f_bge, "bge", "model", "模型", 0, 1)

        # 路径
        f_path = ttk.LabelFrame(frame, text="存储路径", padding=6)
        f_path.pack(fill=tk.X, **pad)
        self._make_path_row(f_path, "nas_base", "NAS 根目录", 0)
        # Step3 精标词表（可独立于 step2 产物）
        self._step3_vocab_var = tk.StringVar()
        ttk.Label(f_path, text="精标词表:", font=DEFAULT_FONT).grid(
            row=2, column=0, sticky=tk.W, padx=4, pady=3)
        ttk.Entry(f_path, textvariable=self._step3_vocab_var, width=64, font=DEFAULT_FONT).grid(
            row=2, column=1, sticky=tk.EW, padx=4, pady=3)
        ttk.Button(f_path, text="…", width=3,
                   command=lambda: self._browse_file(self._step3_vocab_var)).grid(
            row=2, column=2, padx=4, pady=3)
        ttk.Label(f_path, text="  可选：留空则自动用规范词表，也可指定已有词表JSON",
                  foreground="#888", font=("Microsoft YaHei UI", 8)).grid(
            row=3, column=0, columnspan=3, sticky=tk.W, padx=4, pady=(0, 4))

        # Web
        f_web = ttk.LabelFrame(frame, text="Web 服务", padding=6)
        f_web.pack(fill=tk.X, **pad)
        self._make_entry(f_web, "web", "port", "端口", 0, 0)
        self._lan_ip_var = tk.StringVar(value=f"本机地址: {get_lan_ip()}")
        ttk.Label(f_web, textvariable=self._lan_ip_var, foreground="#888", font=SMALL_FONT).grid(
            row=0, column=2, padx=12, pady=2)

        # 提示词模板
        f_prompt = ttk.LabelFrame(frame, text="提示词模板", padding=6)
        f_prompt.pack(fill=tk.X, **pad)
        self._prompt_widgets = {}
        prompt_labels = [
            ("step1_blind", "Step1 盲标提示词"),
            ("step2_merge", "Step2 归并提示词"),
            ("step3_refined", "Step3 精标提示词 (用 {word_list_str} 占位)"),
        ]
        for i, (key, label) in enumerate(prompt_labels):
            ttk.Label(f_prompt, text=label + ":", font=SMALL_FONT).pack(anchor=tk.W, pady=(6 if i > 0 else 0, 2))
            tw = tk.Text(f_prompt, wrap=tk.WORD, font=MONO_FONT, height=4,
                         bg="#fafafa", relief=tk.SOLID, borderwidth=1)
            tw.pack(fill=tk.X)
            self._prompt_widgets[key] = tw
            if key == "step3_refined":
                ttk.Label(f_prompt, text="  {word_list_str} 运行时会自动替换为规范词频_Top500.json 中的前500个词",
                          foreground="#888", font=("Microsoft YaHei UI", 8)).pack(anchor=tk.W, pady=(0, 4))

        # 方案按钮
        prompt_btns = ttk.Frame(f_prompt)
        prompt_btns.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(prompt_btns, text="保存方案...", command=self._on_save_prompt_preset).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(prompt_btns, text="加载方案...", command=self._on_load_prompt_preset).pack(side=tk.LEFT)
        ttk.Button(prompt_btns, text="恢复默认", command=self._on_reset_prompts).pack(side=tk.LEFT)

        # 保存/加载按钮
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, **pad)
        ttk.Button(btn_row, text="保存配置", command=self._on_save_config).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="从文件加载", command=self._on_load_config).pack(side=tk.LEFT)

        self._populate_config_fields()

    def _make_entry(self, parent, section, key, label, row, col, show=None):
        ttk.Label(parent, text=label + ":", font=DEFAULT_FONT).grid(
            row=row, column=col * 2, sticky=tk.W, padx=4, pady=3)
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=72, show=show or "", font=DEFAULT_FONT)
        entry.grid(row=row, column=col * 2 + 1, sticky=tk.EW, padx=4, pady=3)
        parent.columnconfigure(col * 2 + 1, weight=1)
        setattr(self, f"_cfg_{section}_{key}", var)

    def _make_think_cb(self, parent, section, row, col):
        var = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(parent, text="开启思考", variable=var)
        cb.grid(row=row, column=col * 2 + 1, sticky=tk.W, padx=4, pady=2)
        setattr(self, f"_cfg_{section}_thinking", var)

    def _make_path_row(self, parent, key, label, row):
        ttk.Label(parent, text=label + ":", font=DEFAULT_FONT).grid(
            row=row, column=0, sticky=tk.W, padx=4, pady=3)
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=64, font=DEFAULT_FONT)
        entry.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=3)
        parent.columnconfigure(1, weight=1)
        ttk.Button(parent, text="…", width=3,
                   command=lambda v=var: self._browse_dir(v)).grid(row=row, column=2, padx=4, pady=3)
        setattr(self, f"_cfg_paths_{key}", var)

    def _on_save_prompt_preset(self):
        """保存当前提示词为独立方案文件"""
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON Files", "*.json")],
            initialfile="提示词方案.json")
        if not path:
            return
        self._collect_config()
        preset = {"prompts": self.cfg.get("prompts", {})}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(preset, f, ensure_ascii=False, indent=2)
            self._log(f"提示词方案已保存: {os.path.basename(path)}", "success")
        except Exception as e:
            self._log(f"保存方案失败: {e}", "error")

    def _on_load_prompt_preset(self):
        """从方案文件加载提示词"""
        path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                preset = json.load(f)
            prompts = preset.get("prompts", {})
            for key in self.cfg.setdefault("prompts", {}):
                if key in prompts:
                    self.cfg["prompts"][key] = prompts[key]
                    tw = self._prompt_widgets.get(key)
                    if tw:
                        tw.delete("1.0", tk.END)
                        tw.insert("1.0", prompts[key])
            self._log(f"已加载方案: {os.path.basename(path)}", "success")
        except Exception as e:
            self._log(f"加载方案失败: {e}", "error")

    def _on_reset_prompts(self):
        """恢复默认提示词"""
        if not messagebox.askyesno("确认", "恢复为默认提示词？当前修改将丢失。"):
            return
        defaults = DEFAULT_CFG.get("prompts", {})
        for key in self.cfg.setdefault("prompts", {}):
            if key in defaults:
                self.cfg["prompts"][key] = defaults[key]
                tw = self._prompt_widgets.get(key)
                if tw:
                    tw.delete("1.0", tk.END)
                    tw.insert("1.0", defaults[key])
        self._log("提示词已恢复默认", "info")

    def _browse_dir(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _browse_file(self, var):
        path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if path:
            var.set(path)

    def _populate_config_fields(self):
        for section in ["vision_model", "text_model", "ragflow", "xinference", "bge"]:
            for key in self.cfg.get(section, {}):
                var = getattr(self, f"_cfg_{section}_{key}", None)
                if var:
                    val = self.cfg[section][key]
                    if isinstance(val, bool):
                        var.set(val)
                    elif isinstance(val, list):
                        var.set(", ".join(val))
                    else:
                        var.set(str(val) if val else "")
        for key in self.cfg.get("paths", {}):
            var = getattr(self, f"_cfg_paths_{key}", None)
            if var:
                var.set(str(self.cfg["paths"][key] or ""))
        for key in self.cfg.get("web", {}):
            var = getattr(self, f"_cfg_web_{key}", None)
            if var:
                var.set(str(self.cfg["web"][key]))
        for key in self.cfg.get("prompts", {}):
            tw = self._prompt_widgets.get(key)
            if tw:
                tw.delete("1.0", tk.END)
                tw.insert("1.0", self.cfg["prompts"][key])
        step3_vocab = self.cfg.get("paths", {}).get("step3_vocab", "")
        self._step3_vocab_var.set(step3_vocab)

    def _collect_config(self):
        for section in ["vision_model", "text_model", "ragflow", "xinference", "bge"]:
            sec = self.cfg.setdefault(section, {})
            for key in sec:
                var = getattr(self, f"_cfg_{section}_{key}", None)
                if var:
                    raw = var.get()
                    if isinstance(raw, bool):
                        val = raw
                    elif key == "dataset_ids":
                        val = [v.strip() for v in raw.strip().split(",") if v.strip()]
                    elif key == "temperature":
                        val = float(raw) if raw else 0.6
                    else:
                        val = raw.strip()
                    sec[key] = val
        for key in self.cfg.setdefault("paths", {}):
            var = getattr(self, f"_cfg_paths_{key}", None)
            if var:
                self.cfg["paths"][key] = var.get().strip()
        self.cfg["paths"]["step3_vocab"] = self._step3_vocab_var.get().strip()
        for key in self.cfg.setdefault("web", {}):
            var = getattr(self, f"_cfg_web_{key}", None)
            if var:
                val = var.get().strip()
                self.cfg["web"][key] = int(val) if key == "port" else val
        for key in self.cfg.setdefault("prompts", {}):
            tw = self._prompt_widgets.get(key)
            if tw:
                self.cfg["prompts"][key] = tw.get("1.0", "end-1c")

    def _on_save_config(self):
        self._collect_config()
        self._save_config()

    def _on_load_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    self.cfg = json.load(f)
                self._populate_config_fields()
                self._log(f"已加载配置: {path}", "success")
            except Exception as e:
                self._log(f"加载失败: {e}", "error")

    # ================= 标签 2：流水线 =================

    def _build_pipeline_tab(self):
        canvas = tk.Canvas(self.tab_pipeline, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.tab_pipeline, orient=tk.VERTICAL, command=canvas.yview)
        frame = ttk.Frame(canvas)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor=tk.NW, width=680)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind("<Enter>", lambda e: e.widget.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: e.widget.unbind_all("<MouseWheel>"))

        pad = {"padx": 8, "pady": 3}

        steps = [
            ("Step 0", "整理文件夹结构出 JSON", "扫描 NAS 目录 → 目录骨架与数量.json", True, self._run_step0),
            ("Step 1", "Kimi 批量盲标", "识图模型初轮打标 → 粗标输出/", True, self._run_step1),
            ("Step 2", "标签同义词归并 + 词频统计", "文字模型归并 → Top500词表", True, self._run_step2),
            ("Step 3", "Kimi 批量精标", "识图模型 + 受控词表约束二轮标注", True, self._run_step3),
            ("Step 4", "构建 RAGFlow 入库 CSV", "精标 JSON → ragflow_入库_*.csv", True, self._run_step4),
            ("Step 5", "搜索验证", "加载文档 + embedding → 验证搜索管线", True, self._run_step5),
        ]

        self._step_vars = {}
        for i, (label, title, desc, has_btn, cmd) in enumerate(steps):
            frm = ttk.LabelFrame(frame, text=f"{label}", padding=6)
            frm.pack(fill=tk.X, **pad)

            hdr = ttk.Frame(frm)
            hdr.pack(fill=tk.X)
            ttk.Label(hdr, text=title, font=BOLD_FONT).pack(side=tk.LEFT)
            if has_btn:
                ttk.Button(hdr, text="运行", command=cmd).pack(side=tk.RIGHT)

            status_var = tk.StringVar(value="检测中...")
            self._step_vars[i] = status_var
            ttk.Label(hdr, textvariable=status_var, foreground="#e65100", font=SMALL_FONT).pack(side=tk.RIGHT, padx=(8, 0))

            ttk.Label(frm, text=desc, foreground="#666", font=SMALL_FONT).pack(anchor=tk.W)

            if i == 4:
                self._split_var = tk.BooleanVar(value=True)
                ttk.Checkbutton(frm, text="按子目录拆分 CSV", variable=self._split_var).pack(anchor=tk.W, pady=(2, 0))

            self.root.after(300 + i * 200, lambda s=i: self._check_step_status(s))

    def _check_step_status(self, step):
        paths = self._derived_paths()
        checkers = {
            0: _get_step('step00_整理目录').check_done,
            1: _get_step('step01_盲标_kimi_batch').check_done,
            2: _get_step('step02_归并').check_done,
            3: _get_step('step03_精标_kimi_batch').check_done,
            4: _get_step('step04_构建入库CSV').check_done,
            5: _get_step('step05_search').check_done,
        }
        fn = checkers.get(step)
        if fn:
            done, detail = fn(**paths)
        else:
            done, detail = False, "需手动执行"
        var = self._step_vars.get(step)
        if var:
            prefix = "✓" if done else "○"
            var.set(f"{prefix} {detail}")

    def _run_step0(self):
        nas = self.cfg.get("paths", {}).get("nas_base", "")
        if not nas:
            self._log("NAS 路径未设置", "warn")
            return
        self._log("Step 0: 开始扫描目录结构...", "info")
        self.progress.start()
        threading.Thread(target=self._do_step0, args=(nas,), daemon=True).start()

    def _do_step0(self, nas):
        ok, msg, _ = _get_step('step00_整理目录').run(nas)
        self.log_queue.put(("success" if ok else "error", msg))
        self.root.after(0, self.progress.stop)
        self.root.after(100, lambda: self._check_step_status(0))

    def _run_step1(self):
        dp = self._derived_paths()
        if not dp:
            self._log("请先设置 NAS 路径", "warn")
            return
        self._log("Step 1: 开始盲标...", "info")
        self.progress.start()
        threading.Thread(target=self._do_step1, args=(dp["nas_base"], dp["raw_label_dir"]), daemon=True).start()

    def _do_step1(self, image_dir, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        prompt = self.cfg.get("prompts", {}).get("step1_blind") or None
        model_cfg = self.cfg.get("vision_model", {})
        ok, msg, _ = _get_step('step01_盲标_kimi_batch').run(
            image_dir, output_dir=output_dir, prompt=prompt, model_cfg=model_cfg)
        self.log_queue.put(("success" if ok else "error", msg))
        self.root.after(0, self.progress.stop)
        self.root.after(100, lambda: self._check_step_status(1))

    def _run_step2(self):
        dp = self._derived_paths()
        if not dp:
            self._log("请先设置 NAS 路径", "warn")
            return
        raw = dp["raw_label_dir"]
        if not os.path.isdir(raw):
            self._log("粗标目录不存在，将自动创建", "warn")
            os.makedirs(raw, exist_ok=True)
        text_cfg = self.cfg.get("text_model", {})
        self._log("Step 2: 开始同义词归并...", "info")
        self.progress.start()
        threading.Thread(target=self._do_step2, args=(raw, text_cfg), daemon=True).start()

    def _do_step2(self, raw, text_cfg):
        prompt = self.cfg.get("prompts", {}).get("step2_merge") or None
        ok, msg, path = _get_step('step02_归并').run(raw, text_cfg, merge_prompt=prompt)
        self.log_queue.put(("success" if ok else "error", msg))
        if path:
            self._log(f"词表已保存: {path}", "success")
        self.root.after(0, self.progress.stop)
        self.root.after(100, lambda: self._check_step_status(2))

    def _run_step3(self):
        dp = self._derived_paths()
        if not dp:
            self._log("请先设置 NAS 路径", "warn")
            return
        refined = dp["refined_label_dir"]
        os.makedirs(refined, exist_ok=True)
        # 优先用用户指定的精标词表，否则用自动推导的规范词表
        vocab = self._step3_vocab_var.get().strip()
        if not vocab:
            vocab = dp.get("vocab", "")
        if not vocab or not os.path.isfile(vocab):
            self._log("精标词表不存在，请指定词表JSON或先运行 Step2", "warn")
            return
        self._log("Step 3: 开始精标...", "info")
        self.progress.start()
        threading.Thread(target=self._do_step3, args=(refined, vocab), daemon=True).start()

    def _do_step3(self, refined, vocab):
        prompt = self.cfg.get("prompts", {}).get("step3_refined") or None
        model_cfg = self.cfg.get("vision_model", {})
        ok, msg, _ = _get_step('step03_精标_kimi_batch').run(
            refined, vocab, prompt_template=prompt, model_cfg=model_cfg)
        self.log_queue.put(("success" if ok else "error", msg))
        self.root.after(0, self.progress.stop)
        self.root.after(100, lambda: self._check_step_status(3))

    def _run_step4(self):
        dp = self._derived_paths()
        if not dp:
            self._log("请先设置 NAS 路径", "warn")
            return
        refined = dp["refined_label_dir"]
        if not os.path.isdir(refined):
            self._log("精标目录不存在，将自动创建", "warn")
            os.makedirs(refined, exist_ok=True)
        self._log("Step 4: 开始生成 CSV...", "info")
        self.progress.start()
        out = dp.get("output_dir")
        split = self._split_var.get()
        threading.Thread(target=self._do_step4, args=(refined, out, split), daemon=True).start()

    def _do_step4(self, refined, out, split):
        ok, msg, _ = _get_step('step04_构建入库CSV').run(refined, out, split)
        self.log_queue.put(("success" if ok else "error", msg))
        self.root.after(0, self.progress.stop)
        self.root.after(100, lambda: self._check_step_status(4))

    def _run_step5(self):
        dp = self._derived_paths()
        vocab = dp.get("vocab", "") if dp else ""
        rf_cfg = self.cfg.get("ragflow", {})
        bge_cfg = self.cfg.get("bge", {})
        self._log("Step 5: 开始搜索验证...", "info")
        self.progress.start()
        threading.Thread(target=self._do_step5, args=(vocab, rf_cfg, bge_cfg), daemon=True).start()

    def _do_step5(self, vocab, rf_cfg, bge_cfg):
        ok, msg, _ = _get_step('step05_search').run(
            vocab_path=vocab or None,
            ragflow_base=rf_cfg.get("base_url", ""),
            ragflow_key=rf_cfg.get("api_key", ""),
            ragflow_ds=rf_cfg.get("dataset_ids", []),
            bge_base=bge_cfg.get("base_url", ""),
            kimi_cfg=self.cfg.get("text_model", {}),
        )
        self.log_queue.put(("success" if ok else "error", msg))
        self.root.after(0, self.progress.stop)
        self.root.after(100, lambda: self._check_step_status(5))

    # ================= 标签 3：搜索验证 =================

    def _build_search_tab(self):
        pad = {"padx": 6, "pady": 3}

        top = ttk.Frame(self.tab_search)
        top.pack(fill=tk.X, **pad)
        self._search_status = ttk.Label(top, text="● 未初始化", foreground="red", font=DEFAULT_FONT)
        self._search_status.pack(side=tk.LEFT, padx=(0, 8))
        self._search_doc_count = ttk.Label(top, text="", font=DEFAULT_FONT)
        self._search_doc_count.pack(side=tk.LEFT)
        ttk.Button(top, text="初始化引擎", command=self._init_search).pack(side=tk.LEFT, padx=(12, 0))

        sf = ttk.Frame(self.tab_search)
        sf.pack(fill=tk.X, **pad)
        ttk.Label(sf, text="关键词:", font=DEFAULT_FONT).pack(side=tk.LEFT)
        self._query_var = tk.StringVar()
        qe = ttk.Entry(sf, textvariable=self._query_var, width=36, font=DEFAULT_FONT)
        qe.pack(side=tk.LEFT, padx=(4, 6))
        qe.bind("<Return>", lambda e: self._do_search())
        ttk.Button(sf, text="搜索", command=self._do_search).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(sf, text="分组:", font=DEFAULT_FONT).pack(side=tk.LEFT)
        self._group_var = tk.StringVar(value="全部")
        self._group_combo = ttk.Combobox(sf, textvariable=self._group_var,
                                          values=["全部"], state="readonly", width=18, font=DEFAULT_FONT)
        self._group_combo.pack(side=tk.LEFT, padx=(4, 6))
        self._group_combo.bind("<<ComboboxSelected>>", lambda e: self._filter_results())

        self._result_count = ttk.Label(sf, text="", font=DEFAULT_FONT)
        self._result_count.pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(self.tab_search, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, **pad)

        list_frame = ttk.LabelFrame(paned, text="搜索结果", padding=3)
        paned.add(list_frame, weight=3)

        cols = ("文件名", "分组", "风格", "得分", "匹配")
        self._result_tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, [180, 100, 80, 50, 80]):
            self._result_tree.heading(c, text=c)
            self._result_tree.column(c, width=w, minwidth=40)
        self._result_tree.column("得分", anchor=tk.CENTER)
        self._result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ts = ttk.Scrollbar(list_frame, command=self._result_tree.yview)
        self._result_tree.configure(yscrollcommand=ts.set)
        ts.pack(side=tk.RIGHT, fill=tk.Y)
        self._result_tree.bind("<<TreeviewSelect>>", self._on_search_select)
        self._result_tree.bind("<Double-1>", self._on_search_double)

        prev = ttk.LabelFrame(paned, text="预览", padding=3)
        paned.add(prev, weight=2)

        self._preview_canvas = tk.Canvas(prev, bg="#f0f0f0", width=360, height=270, highlightthickness=0)
        self._preview_canvas.pack(pady=(0, 4))

        self._preview_info = tk.Text(prev, wrap=tk.WORD, state=tk.DISABLED,
                                     font=DEFAULT_FONT, height=8,
                                     bg="#fafafa", relief=tk.SOLID, borderwidth=1)
        self._preview_info.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
        self._preview_info.tag_configure("label_key", font=BOLD_FONT)
        self._preview_info.tag_configure("file_info", foreground="#666")

        btn_row = ttk.Frame(prev)
        btn_row.pack(fill=tk.X)
        self._dl_btn = ttk.Button(btn_row, text="下载 ZIP", command=self._on_search_dl, state=tk.DISABLED)
        self._dl_btn.pack(side=tk.LEFT, padx=(0, 6))
        self._open_btn = ttk.Button(btn_row, text="打开原图", command=self._on_search_open, state=tk.DISABLED)
        self._open_btn.pack(side=tk.LEFT)

    def _init_search(self):
        text_cfg = self.cfg.get("text_model", {})
        rf_cfg = self.cfg.get("ragflow", {})
        bge_cfg = self.cfg.get("bge", {})
        vocab = self._derived_paths().get("vocab", "") or self.cfg.get("paths", {}).get("vocab", "")

        if not vocab or not os.path.exists(vocab):
            messagebox.showwarning("提示", "词表路径无效，请先配置工作目录或运行 Step2")
            return

        self._search_status.config(text="● 初始化中...", foreground="#e65100")
        self.progress.start()
        threading.Thread(target=self._do_init_search, args=(
            vocab, text_cfg, rf_cfg, bge_cfg), daemon=True).start()

    def _do_init_search(self, vocab, text_cfg, rf_cfg, bge_cfg):
        try:
            ds_ids = rf_cfg.get("dataset_ids", [])
            if isinstance(ds_ids, str):
                ds_ids = [v.strip() for v in ds_ids.split(",") if v.strip()]

            Searcher = _get_searcher()
            searcher = Searcher(
                vocab_path=vocab,
                ragflow_key=rf_cfg.get("api_key", ""),
                ragflow_ds_ids=ds_ids,
                ragflow_base=rf_cfg.get("base_url", ""),
                kimi_api_key=text_cfg.get("api_key", ""),
                kimi_base_url=text_cfg.get("base_url", ""),
                bge_base_url=bge_cfg.get("base_url", ""),
            )
            searcher.initialize()
            self.searcher = searcher
            groups = ["全部"] + searcher.get_groups()
            self.root.after(0, lambda: self._init_search_done(groups))
        except Exception as e:
            self.log_queue.put(("error", f"初始化失败: {e}"))
            self.root.after(0, lambda: self._search_status.config(text="● 初始化失败", foreground="red"))
            self.root.after(0, self.progress.stop)

    def _init_search_done(self, groups):
        self._group_combo["values"] = groups
        self._search_status.config(text="● 就绪", foreground="green")
        self._search_doc_count.config(text=f"文档: {len(self.searcher.doc_fns)} 条")
        self.progress.stop()
        self._log(f"搜索引擎就绪，{len(self.searcher.doc_fns)} 条文档", "success")

    def _do_search(self):
        if not self.searcher or not self.searcher.ready:
            messagebox.showwarning("提示", "请先初始化搜索引擎")
            return
        q = self._query_var.get().strip()
        if not q:
            return
        self.progress.start()
        self._log(f'搜索: "{q}"', "info")
        threading.Thread(target=self._run_search, args=(q,), daemon=True).start()

    def _run_search(self, q):
        try:
            result = self.searcher.search(q)
            self.result_queue.put(result)
        except Exception as e:
            self.log_queue.put(("error", f"搜索失败: {e}"))
            self.root.after(0, self.progress.stop)

    def _show_results(self, result):
        self._current_results = result
        self._filter_results()
        self.progress.stop()
        self._log(f"搜索完成: {result['n_hit']} 命中 + {result['n_semantic']} 语义", "success")

    def _filter_results(self):
        for row in self._result_tree.get_children():
            self._result_tree.delete(row)
        if not self._current_results:
            return

        gf = self._group_var.get()
        displayed = displayed_hit = displayed_sem = 0

        for r in self._current_results["results"]:
            cp = _real_path(r["path"])
            if gf != "全部" and cp != gf:
                continue
            style_m = re.search(r'风格:([^|]*)', r["tags"])
            style = style_m.group(1).strip() if style_m else "?"
            marks = []
            if r.get("fn_hit"):
                marks.append("文件名")
            if r.get("tag_hit"):
                marks.append("标签")
            match_str = "+".join(marks) if marks else "语义"
            tag = "hit" if marks else "sem"
            self._result_tree.insert("", tk.END, values=(
                r["filename"], cp, style, r["final_score"], match_str), tags=(tag,))
            if marks:
                displayed_hit += 1
            else:
                displayed_sem += 1
            displayed += 1

        self._result_tree.tag_configure("hit", foreground="#2e7d32")
        self._result_tree.tag_configure("sem", foreground="#666")
        self._result_count.config(text=f"{displayed_hit} 命中 + {displayed_sem} 语义 | {displayed} 条")

    def _on_search_select(self, event):
        sel = self._result_tree.selection()
        if not sel:
            return
        vals = self._result_tree.item(sel[0], "values")
        if vals:
            threading.Thread(target=self._load_preview, args=(vals[0], vals[1]), daemon=True).start()

    def _on_search_double(self, event):
        sel = self._result_tree.selection()
        if not sel:
            return
        vals = self._result_tree.item(sel[0], "values")
        if vals:
            self._open_file(vals[0], vals[1], ".jpg")

    def _load_preview(self, filename, group):
        nas = self.cfg.get("paths", {}).get("nas_base", "")
        if not nas:
            return
        try:
            full_path = group
            full_tags = ""
            if self.searcher:
                for i in range(len(self.searcher.doc_fns)):
                    if _real_path(self.searcher.doc_paths[i]) == group and self.searcher.doc_fns[i] == filename:
                        full_path = self.searcher.doc_paths[i]
                        full_tags = self.searcher.doc_tags_list[i]
                        break
            rest = f"{full_path}/{filename}.jpg".replace("\\", "/")
            src = _safe_path(nas, rest)
            if os.path.isfile(src):
                Img, ImgTk = _get_PIL()
                img = Img.open(src).convert("RGB")
                img.thumbnail((320, 240), Img.LANCZOS)
                photo = ImgTk.PhotoImage(img)
                self.preview_queue.put(("image", photo))
            else:
                self.preview_queue.put(("image", None))
            self.preview_queue.put(("info", full_tags))
            self.preview_queue.put(("file", filename, group, src))
        except Exception as e:
            self.preview_queue.put(("error", str(e)))

    def _on_search_dl(self):
        if self._preview_current:
            self._open_file(*self._preview_current, ".zip")

    def _on_search_open(self):
        if self._preview_current:
            self._open_file(*self._preview_current, ".jpg")

    def _open_file(self, filename, group, ext):
        nas = self.cfg.get("paths", {}).get("nas_base", "")
        if not nas:
            return
        try:
            full_path = group
            if self.searcher:
                for i in range(len(self.searcher.doc_fns)):
                    if _real_path(self.searcher.doc_paths[i]) == group and self.searcher.doc_fns[i] == filename:
                        full_path = self.searcher.doc_paths[i]
                        break
            rest = f"{full_path}/{filename}{ext}".replace("\\", "/")
            src = _safe_path(nas, rest)
            if os.path.isfile(src):
                os.startfile(src)
            else:
                messagebox.showwarning("提示", f"文件不存在:\n{src}")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    # ================= 标签 4：服务面板 =================

    def _build_services_tab(self):
        pad = {"padx": 8, "pady": 4}
        self._svc_widgets = {}
        lan_ip = get_lan_ip()
        web_port = self.cfg.get("web", {}).get("port", 8088)

        xinf_port = self.cfg.get("xinference", {}).get("port", 9997)
        services = [
            ("docker", "Docker Desktop", "docker info", None, False, None, None),
            ("ragflow", "RAGFlow", "http://127.0.0.1:9900", "http://127.0.0.1:9900", True,
             lambda: self._start_docker_service("ragflow"),
             lambda: self._stop_docker_service("ragflow")),
            ("xinference", "Xinference (bge-m3)", f"http://127.0.0.1:{xinf_port}",
             f"http://127.0.0.1:{xinf_port}/ui/#/launch_model/llm", True,
             lambda: self._start_xinference(),
             lambda: self._stop_service("xinference")),
            ("web", "Web 搜索服务", f"https://127.0.0.1:{web_port}", f"https://{lan_ip}:{web_port}", True,
             lambda: self._start_web(),
             lambda: self._stop_service("web")),
            ("feiq", "飞秋搜图Bot", f"飞秋 @ {lan_ip}:2425", None, True,
             lambda: self._start_feiq(),
             lambda: self._stop_service("feiq")),
        ]

        for svc_id, name, check_addr, open_url, manageable, start_cmd, stop_cmd in services:
            frm = ttk.LabelFrame(self.tab_services, text=name, padding=6)
            frm.pack(fill=tk.X, **pad)

            status_var = tk.StringVar(value="检测中...")
            status_label = ttk.Label(frm, textvariable=status_var, foreground="#666", font=DEFAULT_FONT, width=12)
            status_label.pack(side=tk.LEFT, padx=(0, 8))

            addr_label = ttk.Label(frm, text=check_addr, foreground="#888", font=SMALL_FONT)
            addr_label.pack(side=tk.LEFT)

            if manageable:
                if open_url:
                    ttk.Button(frm, text="打开", command=lambda u=open_url: os.startfile(u)).pack(
                        side=tk.RIGHT, padx=(2, 0))
                ttk.Button(frm, text="停止", command=stop_cmd).pack(side=tk.RIGHT, padx=(2, 0))
                ttk.Button(frm, text="启动", command=start_cmd).pack(side=tk.RIGHT, padx=(2, 0))

            self._svc_widgets[svc_id] = {
                "status_var": status_var, "status_label": status_label,
                "check_addr": check_addr, "open_url": open_url, "manageable": manageable,
            }

        btn_row = ttk.Frame(self.tab_services)
        btn_row.pack(fill=tk.X, **pad)
        ttk.Button(btn_row, text="一键全部启动", command=self._start_all).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="全部停止", command=self._stop_all).pack(side=tk.LEFT)

    def _check_all_services(self):
        for svc_id in self._svc_widgets:
            self._check_service(svc_id)

    def _check_service(self, svc_id):
        w = self._svc_widgets.get(svc_id)
        if not w:
            return
        addr = w["check_addr"]
        if addr.startswith("http"):
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = _get_requests().get(addr, timeout=2, verify=False)
                running = r.status_code < 500
            except Exception:
                # HTTP 检测失败时，改用 TCP 端口检测
                import socket as _sock
                try:
                    host_port = addr.replace("http://", "").replace("https://", "").split("/")[0]
                    host, port = host_port.rsplit(":", 1)
                    s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                    s.settimeout(1)
                    s.connect((host, int(port)))
                    s.close()
                    running = True
                except Exception:
                    running = False
        elif addr.startswith("飞秋"):
            proc = self._svc_processes.get(svc_id)
            running = proc is not None and proc.poll() is None
        elif addr == "docker info":
            try:
                subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
                running = True
            except Exception:
                running = False
        else:
            running = False

        self.root.after(0, lambda sv=w["status_var"], sl=w["status_label"], r=running: (
            sv.set("● 运行中"), sl.configure(foreground="green"))
            if r else (sv.set("● 未启动"), sl.configure(foreground="red")))

    def _start_docker_service(self, svc_id):
        if svc_id == "ragflow":
            self._log("启动 RAGFlow (docker compose up -d)...", "info")
            threading.Thread(target=self._run_docker_up, daemon=True).start()

    def _run_docker_up(self):
        try:
            ragflow_docker = os.path.abspath(os.path.join(BASE_DIR, "..", "ragflow", "docker"))
            if not os.path.isdir(ragflow_docker):
                self.log_queue.put(("error", f"RAGFlow docker 目录不存在: {ragflow_docker}"))
                return
            result = subprocess.run(["docker", "compose", "-f", "docker-compose.yml", "up", "-d"],
                                    cwd=ragflow_docker, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                self.log_queue.put(("success", "RAGFlow 已启动"))
            else:
                self.log_queue.put(("error", f"RAGFlow 启动失败: {result.stderr[:200]}"))
            self.root.after(2000, lambda: self._check_service("ragflow"))
        except Exception as e:
            self.log_queue.put(("error", f"启动 RAGFlow 失败: {e}"))

    def _stop_docker_service(self, svc_id):
        if svc_id == "ragflow":
            self._log("停止 RAGFlow...", "warn")
            threading.Thread(target=self._run_docker_down, daemon=True).start()

    def _run_docker_down(self):
        try:
            ragflow_docker = os.path.abspath(os.path.join(BASE_DIR, "..", "ragflow", "docker"))
            subprocess.run(["docker", "compose", "-f", "docker-compose.yml", "down"],
                          cwd=ragflow_docker, capture_output=True, text=True, timeout=30)
            self.log_queue.put(("success", "RAGFlow 已停止"))
            self.root.after(2000, lambda: self._check_service("ragflow"))
        except Exception as e:
            self.log_queue.put(("error", f"停止 RAGFlow 失败: {e}"))

    def _start_xinference(self):
        xinf_cfg = self.cfg.get("xinference", {})
        cmd = xinf_cfg.get("command", "xinference-local")
        host = xinf_cfg.get("host", "0.0.0.0")
        port = str(xinf_cfg.get("port", 9997))
        self._log(f"启动 Xinference ({cmd} --host {host} --port {port})...", "info")
        threading.Thread(target=self._run_xinference, args=(cmd, host, port), daemon=True).start()

    def _run_xinference(self, cmd, host, port):
        try:
            args = f'{cmd} --host {host} --port {port}'
            proc = subprocess.Popen(args, shell=True)
            self._svc_processes["xinference"] = proc
            self.log_queue.put(("success", f"Xinference 已启动 (PID: {proc.pid})"))
            self.root.after(3000, lambda: self._check_service("xinference"))
        except FileNotFoundError:
            self.log_queue.put(("error", f"找不到: {cmd}，请在配置页修改启动命令"))
        except Exception as e:
            self.log_queue.put(("error", f"启动 Xinference 失败: {e}"))

    def _start_web(self):
        port = self.cfg.get("web", {}).get("port", 8088)
        nas = self.cfg.get("paths", {}).get("nas_base", "")
        if not nas:
            self.log_queue.put(("warn", "NAS 路径未设置，Web 图片功能不可用"))

        web_script = os.path.abspath(os.path.join(BASE_DIR, "step06_web_server.py"))
        if not os.path.exists(web_script):
            self.log_queue.put(("error", f"找不到: {web_script}"))
            return

        self._log(f"启动 Web 服务 (端口 {port})...", "info")
        threading.Thread(target=self._run_web, args=(web_script, nas, port), daemon=True).start()

    def _run_web(self, script, nas, port):
        try:
            env = os.environ.copy()
            env["NAS_BASE"] = nas
            rf = self.cfg.get("ragflow", {})
            env["RAGFLOW_KEY"] = rf.get("api_key", "")
            env["RAGFLOW_BASE"] = rf.get("base_url", "")
            env["RAGFLOW_DS"] = ",".join(rf.get("dataset_ids", [])) if isinstance(rf.get("dataset_ids"), list) else rf.get("dataset_ids", "")
            text_cfg = self.cfg.get("text_model", {})
            env["KIMI_KEY"] = text_cfg.get("api_key", "")
            env["KIMI_BASE"] = text_cfg.get("base_url", "")
            bge = self.cfg.get("bge", {})
            env["BGE_BASE"] = bge.get("base_url", "")
            # 词表路径：优先用 step3_vocab，再求其次
            vocab = self.cfg.get("paths", {}).get("step3_vocab", "")
            if not vocab:
                dp = self._derived_paths()
                vocab = dp.get("vocab", "") if dp else ""
            if vocab:
                env["VOCAB_PATH"] = vocab
            proc = subprocess.Popen(
                [sys.executable, script],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self._svc_processes["web"] = proc
            self.log_queue.put(("success", f"Web 服务已启动 (PID: {proc.pid}, 端口 {port})"))
            threading.Thread(target=self._read_proc_stdout, args=(proc, "web"), daemon=True).start()
            self.root.after(2000, lambda: self._check_service("web"))
        except Exception as e:
            self.log_queue.put(("error", f"启动 Web 服务失败: {e}"))

    def _start_feiq(self):
        port = self.cfg.get("web", {}).get("port", 8088)
        bot_script = os.path.abspath(os.path.join(BASE_DIR, "step07_feiq_bot.py"))
        if not os.path.exists(bot_script):
            self.log_queue.put(("error", f"找不到: {bot_script}"))
            return
        self._log("启动飞秋 Bot...", "info")
        threading.Thread(target=self._run_feiq, args=(bot_script, port), daemon=True).start()

    def _run_feiq(self, script, web_port):
        try:
            env = os.environ.copy()
            env["WEB_BASE"] = f"https://{get_lan_ip()}:{web_port}"
            proc = subprocess.Popen(
                [sys.executable, script],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self._svc_processes["feiq"] = proc
            self.log_queue.put(("success", f"飞秋 Bot 已启动 (PID: {proc.pid})"))
            threading.Thread(target=self._read_proc_stdout, args=(proc, "feiq"), daemon=True).start()
            self.root.after(2000, lambda: self._check_service("feiq"))
        except Exception as e:
            self.log_queue.put(("error", f"启动飞秋 Bot 失败: {e}"))

    def _read_proc_stdout(self, proc, name):
        """读取子进程 stdout，启动错误记录到日志"""
        try:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self.log_queue.put(("info", f"[{name}] {line}"))
        except Exception:
            pass

    def _stop_service(self, svc_id):
        proc = self._svc_processes.get(svc_id)
        killed = False
        if proc and proc.poll() is None:
            proc.kill()
            killed = True
        # Xinference 兜底：按进程名强杀
        if svc_id == "xinference":
            try:
                subprocess.run(["taskkill", "/f", "/im", "xinference-local.exe"],
                              capture_output=True, timeout=5)
                killed = True
            except Exception:
                pass
            try:
                subprocess.run(["taskkill", "/f", "/im", "xinference.exe"],
                              capture_output=True, timeout=5)
                killed = True
            except Exception:
                pass
        if killed:
            self._svc_processes.pop(svc_id, None)
            self.log_queue.put(("warn", f"{svc_id} 已停止"))
        else:
            self.log_queue.put(("info", f"{svc_id} 未在运行"))
        self.root.after(1000, lambda: self._check_service(svc_id))

    def _start_all(self):
        self._log("一键启动全部服务...", "info")
        self._start_docker_service("ragflow")
        self.root.after(5000, self._start_xinference)
        self.root.after(8000, self._start_web)
        self.root.after(10000, self._start_feiq)

    def _stop_all(self):
        self._log("全部停止...", "warn")
        for svc_id in ["feiq", "web", "xinference", "ragflow"]:
            if svc_id == "ragflow":
                self._stop_docker_service("ragflow")
            else:
                self._stop_service(svc_id)

    # ================= 日志 & 队列 =================

    def _log(self, msg, level="info"):
        # 写入界面
        self.log_text.configure(state=tk.NORMAL)
        tag = {"error": "error", "success": "success", "warn": "warn", "info": "info"}.get(level)
        if tag:
            self.log_text.insert(tk.END, msg + "\n", tag)
        else:
            self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        # 写入文件
        try:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] [{level.upper()}] {msg}\n")
        except Exception:
            pass

    def _poll_queues(self):
        try:
            while True:
                level, msg = self.log_queue.get_nowait()
                self._log(msg, level)
        except queue.Empty:
            pass
        try:
            while True:
                result = self.result_queue.get_nowait()
                self._show_results(result)
        except queue.Empty:
            pass
        try:
            while True:
                item = self.preview_queue.get_nowait()
                if item[0] == "image":
                    self._show_preview_img(item[1])
                elif item[0] == "info":
                    self._show_preview_text(item[1])
                elif item[0] == "file":
                    self._show_preview_file(item[1], item[2], item[3])
                elif item[0] == "error":
                    self._log(f"预览: {item[1]}", "warn")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queues)

    def _show_preview_img(self, data):
        self._preview_canvas.delete("all")
        self._preview_photo = None
        if data is None:
            self._preview_canvas.create_text(180, 135, text="无预览图", fill="#999", font=DEFAULT_FONT)
        else:
            self._preview_photo = data
            self._preview_canvas.create_image(180, 135, image=data, anchor=tk.CENTER)

    def _show_preview_text(self, tags_text):
        self._preview_info.configure(state=tk.NORMAL)
        self._preview_info.delete("1.0", tk.END)
        if tags_text:
            for part in tags_text.split("|"):
                part = part.strip()
                if ":" in part:
                    k, v = part.split(":", 1)
                    self._preview_info.insert(tk.END, f"{k}: ", "label_key")
                    self._preview_info.insert(tk.END, f"{v.strip()}\n")
                else:
                    self._preview_info.insert(tk.END, f"{part}\n")
        self._preview_info.configure(state=tk.DISABLED)

    def _show_preview_file(self, filename, group, src):
        self._preview_info.configure(state=tk.NORMAL)
        self._preview_info.insert(tk.END, f"\n文件: {filename}.jpg\n", "file_info")
        self._preview_info.insert(tk.END, f"分组: {group}\n", "file_info")
        jpg_ok = "存在" if os.path.isfile(src) else "缺失"
        zip_path = src.replace(".jpg", ".zip")
        zip_ok = "存在" if os.path.isfile(zip_path) else "缺失"
        self._preview_info.insert(tk.END, f"JPG: {jpg_ok} | ZIP: {zip_ok}\n", "file_info")
        self._preview_info.configure(state=tk.DISABLED)
        self._preview_current = (filename, group)
        self._dl_btn.config(state=tk.NORMAL if os.path.isfile(zip_path) else tk.DISABLED)
        self._open_btn.config(state=tk.NORMAL if os.path.isfile(src) else tk.DISABLED)

    def _on_close(self):
        self.root.destroy()


# ================= 启动入口 =================

def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
