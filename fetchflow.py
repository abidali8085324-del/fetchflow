"""
FetchFlow - free IDM-style download manager
- Direct files (jpg, png, zip, mp4, pdf ...): multi-connection fast download (8 segments)
- Video sites (YouTube, Facebook, Instagram, TikTok, X, Vimeo etc.): powered by yt-dlp
Run:  python fetchflow.py
"""
import os, sys, threading, shutil, subprocess, time, re
import urllib.request, urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

APP = "FetchFlow"
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "FetchFlow")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
FILE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".mp4", ".mkv",
            ".webm", ".avi", ".mov", ".mp3", ".wav", ".m4a", ".pdf", ".zip", ".rar",
            ".7z", ".exe", ".msi", ".iso", ".apk", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
QUALITIES = ["Best", "1080p", "720p", "480p", "360p", "Audio (MP3)"]


def fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', "_", s).strip() or "download"


def unique_path(folder, name):
    base, ext = os.path.splitext(name)
    p, i = os.path.join(folder, name), 1
    while os.path.exists(p):
        p = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return p


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, url, folder, quality):
        self.url, self.folder, self.quality = url, folder, quality
        self.name = url[:60]
        self.status, self.progress, self.speed, self.size = "Queued", 0.0, "", ""
        self.path = ""
        self.cancel = threading.Event()


# ---------------- direct download ----------------
def probe(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            h = r.headers
            return {
                "type": h.get("Content-Type", "").lower(),
                "size": int(h.get("Content-Length", 0) or 0),
                "ranges": h.get("Accept-Ranges", "").lower() == "bytes",
                "cd": h.get("Content-Disposition", ""),
                "url": r.geturl(),
            }
    except Exception:
        return None


def direct_download(job, info):
    url = info["url"]
    name = None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', info["cd"] or "")
    if m:
        name = urllib.parse.unquote(m.group(1))
    if not name:
        name = os.path.basename(urllib.parse.urlparse(url).path) or "file"
    name = safe_name(urllib.parse.unquote(name))
    if "." not in name:
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "video/mp4": ".mp4"}.get(info["type"].split(";")[0], "")
        name += ext
    job.name = name
    job.path = unique_path(job.folder, name)
    size = info["size"]
    job.size = fmt_size(size) if size else "?"
    done = [0]
    lock = threading.Lock()
    t0 = time.time()

    def tick():
        el = max(time.time() - t0, 0.1)
        job.speed = fmt_size(done[0] / el) + "/s"
        if size:
            job.progress = done[0] * 100 / size

    def fetch(start, end, fh_path, offset_mode):
        hdr = {"User-Agent": UA}
        if end is not None:
            hdr["Range"] = f"bytes={start}-{end}"
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers=hdr)
                with urllib.request.urlopen(req, timeout=30) as r, open(fh_path, "r+b" if offset_mode else "wb") as f:
                    if offset_mode:
                        f.seek(start)
                    while True:
                        if job.cancel.is_set():
                            raise Cancelled()
                        chunk = r.read(65536)
                        if not chunk:
                            return
                        f.write(chunk)
                        with lock:
                            done[0] += len(chunk)
                            tick()
            except Cancelled:
                raise
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1.5)
                # resume from where this segment stopped is complex; restart segment cleanly
                with lock:
                    pass

    if size > 4 * 1024 * 1024 and info["ranges"]:
        n = 8
        with open(job.path, "wb") as f:
            f.truncate(size)
        step = size // n
        errs, threads = [], []

        def worker(i):
            s = i * step
            e = size - 1 if i == n - 1 else (i + 1) * step - 1
            try:
                fetch(s, e, job.path, True)
            except Exception as ex:
                errs.append(ex)
                job.cancel.set()

        for i in range(n):
            t = threading.Thread(target=worker, args=(i,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        if errs:
            raise errs[0]
    else:
        fetch(0, None, job.path, False)
    job.progress = 100


# ---------------- yt-dlp download ----------------
def ytdlp_download(job):
    if yt_dlp is None:
        raise RuntimeError("yt-dlp install nahi hai. Command chalayein: pip install yt-dlp")
    app_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    local_ff = os.path.join(app_dir, "ffmpeg.exe")
    ff = local_ff if os.path.exists(local_ff) else shutil.which("ffmpeg")
    q = job.quality
    h = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}.get(q)
    post = []
    if q == "Audio (MP3)":
        fmt = "bestaudio/best"
        if ff:
            post = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    elif h:
        fmt = f"bv*[height<={h}]+ba/b[height<={h}]/b" if ff else f"b[height<={h}]/b"
    else:
        fmt = "bv*+ba/b" if ff else "b"

    def hook(d):
        if job.cancel.is_set():
            raise Cancelled()
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes", 0)
            if total:
                job.progress = got * 100 / total
                job.size = fmt_size(total)
            sp = d.get("speed")
            job.speed = (fmt_size(sp) + "/s") if sp else ""
            fn = d.get("filename")
            if fn:
                job.name = os.path.basename(fn)
                job.path = fn
        elif d["status"] == "finished":
            job.progress = 99
            job.status = "Processing..."

    opts = {
        "format": fmt,
        "outtmpl": os.path.join(job.folder, "%(title).150B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
        "progress_hooks": [hook],
        "postprocessors": post,
        "http_headers": {"User-Agent": UA},
    }
    if ff and os.path.exists(local_ff):
        opts["ffmpeg_location"] = app_dir
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([job.url])
    job.progress = 100


def run_job(job):
    job.status = "Downloading"
    try:
        os.makedirs(job.folder, exist_ok=True)
        ext = os.path.splitext(urllib.parse.urlparse(job.url).path)[1].lower()
        info = probe(job.url)
        is_direct = False
        if info and not info["type"].startswith("text/html"):
            is_direct = True
        elif ext in FILE_EXT and info:
            is_direct = True
        if is_direct:
            direct_download(job, info)
        else:
            ytdlp_download(job)
        job.status = "Done"
        job.speed = ""
    except Cancelled:
        job.status = "Cancelled"
        job.speed = ""
    except Exception as e:
        msg = str(e).replace("ERROR:", "").strip()
        job.status = "Error: " + msg[:120]
        job.speed = ""


# ---------------- GUI ----------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP} - Download Manager")
        self.geometry("920x520")
        self.minsize(760, 420)
        self.jobs = []
        self.folder = tk.StringVar(value=DEFAULT_DIR)
        self.quality = tk.StringVar(value="Best")
        self.url = tk.StringVar()
        self.build()
        self.refresh()
        if yt_dlp is None:
            messagebox.showwarning(APP, "Video sites ke liye yt-dlp chahiye.\n\nCommand prompt me chalayein:\npip install yt-dlp\n\n(Direct pic/file links iske baghair bhi chalein ge.)")

    def build(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Link:").grid(row=0, column=0, sticky="w")
        e = ttk.Entry(top, textvariable=self.url)
        e.grid(row=0, column=1, sticky="ew", padx=6)
        e.bind("<Return>", lambda _: self.add())
        ttk.Button(top, text="Paste", command=self.paste).grid(row=0, column=2)
        ttk.Combobox(top, textvariable=self.quality, values=QUALITIES, width=12, state="readonly").grid(row=0, column=3, padx=6)
        ttk.Button(top, text="Download", command=self.add).grid(row=0, column=4)
        ttk.Label(top, text="Save to:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top, textvariable=self.folder).grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Button(top, text="Browse", command=self.browse).grid(row=1, column=2, pady=(8, 0))
        ttk.Button(top, text="Open folder", command=self.open_folder).grid(row=1, column=3, pady=(8, 0), padx=6)
        top.columnconfigure(1, weight=1)

        cols = ("name", "size", "progress", "speed", "status")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (330, 90, 90, 100, 260)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10)
        self.tree.bind("<Double-1>", self.open_file)

        bot = ttk.Frame(self, padding=10)
        bot.pack(fill="x")
        ttk.Button(bot, text="Cancel selected", command=self.cancel).pack(side="left")
        ttk.Button(bot, text="Retry selected", command=self.retry).pack(side="left", padx=6)
        ttk.Button(bot, text="Clear finished", command=self.clear).pack(side="left")
        ttk.Button(bot, text="Update yt-dlp", command=self.update_ytdlp).pack(side="right")
        _dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
        self.note = ttk.Label(bot, text="" if (shutil.which("ffmpeg") or os.path.exists(os.path.join(_dir, "ffmpeg.exe"))) else
                              "Tip: ffmpeg install karein (best quality + MP3 ke liye)")
        self.note.pack(side="right", padx=10)

    def paste(self):
        try:
            self.url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder.get())
        if d:
            self.folder.set(d)

    def open_folder(self):
        os.makedirs(self.folder.get(), exist_ok=True)
        self.open_path(self.folder.get())

    @staticmethod
    def open_path(p):
        if sys.platform.startswith("win"):
            os.startfile(p)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])

    def add(self):
        urls = [u for u in re.split(r"\s+", self.url.get().strip()) if u]
        for u in urls:
            if not u.startswith(("http://", "https://")):
                messagebox.showerror(APP, "Sahi link daalein (http:// ya https:// se shuru).")
                return
            self.start(Job(u, self.folder.get(), self.quality.get()))
        self.url.set("")

    def start(self, job):
        self.jobs.append(job)
        threading.Thread(target=run_job, args=(job,), daemon=True).start()

    def sel(self):
        s = self.tree.selection()
        return self.jobs[int(s[0])] if s else None

    def cancel(self):
        j = self.sel()
        if j:
            j.cancel.set()

    def retry(self):
        j = self.sel()
        if j and (j.status.startswith("Error") or j.status == "Cancelled"):
            self.start(Job(j.url, j.folder, j.quality))

    def clear(self):
        self.jobs = [j for j in self.jobs if j.status in ("Queued", "Downloading", "Processing...")]

    def open_file(self, _=None):
        j = self.sel()
        if j and j.path and os.path.exists(j.path):
            self.open_path(j.path)

    def update_ytdlp(self):
        if getattr(sys, "frozen", False):
            messagebox.showinfo(APP, "Is app ko update karne ke liye build_exe.bat dobara chalayein, phir naya FetchFlow.exe use karein.")
            return

        def work():
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], check=True)
                self.after(0, lambda: messagebox.showinfo(APP, "yt-dlp update ho gaya. FetchFlow dobara kholein."))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(APP, f"Update fail: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for i, j in enumerate(self.jobs):
            self.tree.insert("", "end", iid=str(i),
                             values=(j.name, j.size, f"{j.progress:.0f}%", j.speed, j.status))
        if keep and keep[0] in self.tree.get_children():
            self.tree.selection_set(keep[0])
        self.after(500, self.refresh)


if __name__ == "__main__":
    App().mainloop()
