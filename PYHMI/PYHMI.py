# -*- coding: utf-8 -*-
"""
Mini HMI 20 boutons - structures/UDT + presets
- Auto-install: pycomm3, pylogix, pymodbus
- Liste déroulante de tags (Controller / Program) + Filtre
- Explorateur de structures (UDT) pour descendre dans les membres (Cell.Cmd.Reset, etc.)
- Presets: sauvegarder/charger les mappings (JSON, chemin libre)
"""

import sys, subprocess, importlib, json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText
import threading
import time

# ===================== AUTO-INSTALL MODULES =====================
BACKEND_DEPS = {
    "pycomm3": ["pycomm3"],
    "pylogix": ["pylogix"],
    "modbus":  ["pymodbus"],
}
ALL_DEPS = sorted({p for lst in BACKEND_DEPS.values() for p in lst})

def _has_module(modname: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(modname) is not None
    except Exception:
        return False

def _ensure_pip():
    try:
        import pip  # noqa
        return
    except Exception:
        try:
            import ensurepip
            ensurepip.bootstrap()
        except Exception as e:
            print(f"[auto-install] Impossible d’amorcer pip: {e}")

def _pip_install(pkg: str) -> bool:
    cmds = [
        [sys.executable, "-m", "pip", "install", pkg],
        [sys.executable, "-m", "pip", "install", "--user", pkg],
    ]
    for cmd in cmds:
        try:
            print("[auto-install] Exécute:", " ".join(cmd))
            subprocess.check_call(cmd)
            importlib.invalidate_caches()
            return _has_module(pkg)
        except Exception as e:
            print(f"[auto-install] échec: {e}")
    return False

def ensure_dependencies(pkgs=None, logger=None):
    _ensure_pip()
    targets = list(pkgs or ALL_DEPS)
    ok, ko = [], []
    for p in targets:
        if _has_module(p):
            ok.append(p); continue
        msg = f"[auto-install] {p} manquant → installation…"
        print(msg); logger and logger(msg)
        if _pip_install(p):
            ok.append(p); msg2 = f"[auto-install] {p} installé ✓"
            print(msg2); logger and logger(msg2)
        else:
            ko.append(p); msg3 = f"[auto-install] {p} installation ÉCHOUÉE ✗"
            print(msg3); logger and logger(msg3)
    return ok, ko

# ===================== CONFIG PAR DÉFAUT =====================
DEFAULT_BACKEND = "pycomm3"   # "pycomm3", "modbus", "pylogix"
DEFAULT_PLC_IP = "192.168.1.10"
DEFAULT_MODBUS_UNIT_ID = 1
DEFAULT_MODBUS_PORT = 502
DEFAULT_TIMEOUT = 5.0

BTN_ROWS, BTN_COLS = 4, 5    # 4 x 5 = 20 boutons

# ===================== ABSTRACTION PLC =======================
def _norm_tag_name(t):
    if isinstance(t, str):
        return t
    for k in ("name", "TagName", "tag_name", "Name"):
        if hasattr(t, k):
            v = getattr(t, k, None)
            if v: return str(v)
    if isinstance(t, dict):
        for k in ("name", "TagName", "tag_name", "Name"):
            v = t.get(k)
            if v: return str(v)
    return None

class PLCClient:
    """
    Client polyvalent:
      - pycomm3  (Allen-Bradley Logix)
      - pylogix  (Allen-Bradley Logix)
      - modbus   (TCP)
    API: read_bool, write_bool, write_value, toggle_bool, pulse_bool
         read_any, list_tags(scope, program), list_programs(), explore_members(path)
    """
    def __init__(self, backend=DEFAULT_BACKEND, ip=DEFAULT_PLC_IP, timeout=DEFAULT_TIMEOUT,
                 modbus_unit_id=DEFAULT_MODBUS_UNIT_ID, modbus_port=DEFAULT_MODBUS_PORT):
        self.backend = backend
        self.ip = ip
        self.timeout = timeout
        self.modbus_unit_id = modbus_unit_id
        self.modbus_port = modbus_port
        self._client = None
        self._lock = threading.Lock()
        self.connect()

    def connect(self):
        self.close()
        try:
            if self.backend == "pycomm3":
                from pycomm3 import LogixDriver
                self._client = LogixDriver(self.ip, init_tags=True, timeout=self.timeout)
                self._client.open()
            elif self.backend == "pylogix":
                from pylogix import PLC
                self._client = PLC()
                self._client.IPAddress = self.ip
            elif self.backend == "modbus":
                from pymodbus.client import ModbusTcpClient
                self._client = ModbusTcpClient(self.ip, port=self.modbus_port, timeout=self.timeout)
                if not self._client.connect():
                    raise RuntimeError("Connexion Modbus échouée")
            else:
                raise ValueError("Backend inconnu")
        except Exception as e:
            raise RuntimeError(f"Erreur connexion backend={self.backend} -> {e}")

    def close(self):
        try:
            if self._client:
                self._client.close()
        except Exception:
            pass
        self._client = None

    # ----------- LOGIX: helpers ----------- #
    def _logix_read(self, tag):
        if self.backend == "pycomm3":
            r = self._client.read(tag)
            if getattr(r, "error", None):
                raise RuntimeError(f"pycomm3 read error: {r.error}")
            return r.value
        elif self.backend == "pylogix":
            r = self._client.Read(tag)
            if r.Status != "Success":
                raise RuntimeError(f"pylogix read error: {r.Status}")
            return r.Value
        else:
            raise RuntimeError("read (tag) non supporté pour ce backend")

    def _logix_write(self, tag, value):
        if self.backend == "pycomm3":
            wr = self._client.write((tag, value))
            if getattr(wr, "error", None):
                raise RuntimeError(f"pycomm3 write error: {wr.error}")
        elif self.backend == "pylogix":
            wr = self._client.Write(tag, value)
            if getattr(wr, "Status", "Success") != "Success":
                raise RuntimeError(f"pylogix write error: {wr.Status}")
        else:
            raise RuntimeError("write (tag,val) non supporté pour ce backend")

    # ----------- MODBUS: helpers ----------- #
    def _mb_write_coil(self, addr, state: bool):
        rr = self._client.write_coil(int(addr), bool(state), unit=self.modbus_unit_id)
        if not rr or rr.isError():
            raise RuntimeError(f"modbus write_coil error @ {addr}")

    def _mb_read_coil(self, addr):
        rr = self._client.read_coils(int(addr), 1, unit=self.modbus_unit_id)
        if not rr or rr.isError():
            raise RuntimeError(f"modbus read_coils error @ {addr}")
        return bool(rr.bits[0])

    def _mb_write_reg(self, addr, val: int):
        rr = self._client.write_register(int(addr), int(val), unit=self.modbus_unit_id)
        if not rr or rr.isError():
            raise RuntimeError(f"modbus write_register error @ {addr}")

    # ----------- API publique simple ----------- #
    def read_bool(self, ref):
        with self._lock:
            if self.backend in ("pycomm3", "pylogix"):
                v = self._logix_read(ref); return bool(v)
            elif self.backend == "modbus":
                return self._mb_read_coil(ref)
            else:
                raise RuntimeError("Backend inconnu")

    def write_bool(self, ref, state: bool):
        with self._lock:
            if self.backend in ("pycomm3", "pylogix"):
                self._logix_write(ref, bool(state))
            elif self.backend == "modbus":
                self._mb_write_coil(ref, bool(state))
            else:
                raise RuntimeError("Backend inconnu")

    def write_value(self, ref, value):
        with self._lock:
            if self.backend in ("pycomm3", "pylogix"):
                self._logix_write(ref, value)
            elif self.backend == "modbus":
                self._mb_write_reg(ref, int(value))
            else:
                raise RuntimeError("Backend inconnu")

    def toggle_bool(self, ref):
        cur = self.read_bool(ref)
        self.write_bool(ref, not cur)
        return not cur

    def pulse_bool(self, ref, ms=200):
        self.write_bool(ref, True)
        def _back():
            try: self.write_bool(ref, False)
            except Exception: pass
        t = threading.Timer(ms/1000.0, _back); t.daemon=True; t.start()

    def read_any(self, ref):
        """Lecture générique (Logix). Utile pour explorer les structures."""
        if self.backend not in ("pycomm3", "pylogix"):
            raise RuntimeError("Exploration non supportée pour Modbus")
        with self._lock:
            return self._logix_read(ref)

    # ----------- Programmes & Tags (LISTE) -----------
    def list_programs(self):
        """Liste des Programmes (Logix). pycomm3 sûr ; pylogix best-effort."""
        if self.backend == "modbus":
            return []
        with self._lock:
            progs = []
            if self.backend == "pycomm3":
                try:
                    progs = list(self._client.get_program_names() or [])
                except Exception:
                    try:
                        info = getattr(self._client, "info", {}) or {}
                        p = info.get("programs", {})
                        progs = list(p.keys())
                    except Exception:
                        progs = []
            elif self.backend == "pylogix":
                try:
                    if hasattr(self._client, "GetProgramsList"):
                        resp = self._client.GetProgramsList()
                        progs = resp.Value if hasattr(resp, "Value") else (resp if isinstance(resp, (list, tuple)) else [])
                    elif hasattr(self._client, "GetPrograms"):
                        resp = self._client.GetPrograms()
                        progs = resp.Value if hasattr(resp, "Value") else (resp if isinstance(resp, (list, tuple)) else [])
                except Exception:
                    progs = []
            return sorted(set(map(str, progs)), key=str.lower)

    def list_tags(self, scope="controller", program=None, max_count=20000):
        """
        scope: "controller" | "program"
        program: nom du programme si scope="program"
        NOTE: pour scope=program, on renvoie des chemins CIP réels: "Program:Prog.Tag"
        """
        if self.backend == "modbus":
            raise RuntimeError("Modbus: pas de tags (utilise des adresses coils/regs)")
        with self._lock:
            tags = []
            if self.backend == "pycomm3":
                try:
                    if scope == "program":
                        if not program:
                            return []
                        lst = self._client.get_tag_list(program=program)
                        for t in lst or []:
                            n = _norm_tag_name(t)
                            if n: tags.append(f"Program:{program}.{n}")
                    else:
                        lst = self._client.get_tag_list()
                        for t in lst or []:
                            n = _norm_tag_name(t)
                            if n: tags.append(n)
                except Exception as e:
                    raise RuntimeError(f"pycomm3 list_tags: {e}")

            elif self.backend == "pylogix":
                try:
                    # GetTagList: liste globale (souvent controller + program), on renvoie tel quel
                    resp = self._client.GetTagList()
                    lst = resp.Value if hasattr(resp, "Value") else (resp if isinstance(resp, (list, tuple)) else getattr(resp, "TagList", []))
                    for t in lst or []:
                        n = _norm_tag_name(t)
                        if not n: continue
                        if scope == "controller":
                            # Heuristique: exclure "Program:" si déjà qualifié
                            if not n.startswith("Program:"):
                                tags.append(n)
                        else:
                            if program:
                                # Si la lib ne préfixe pas, on ne peut pas deviner le programme → on laisse l'utilisateur filtrer via Explorer
                                # On inclut quand même les noms "Program:Prog.Tag" si présent
                                if n.startswith(f"Program:{program}."):
                                    tags.append(n)
                            else:
                                tags.append(n)
                except Exception as e:
                    raise RuntimeError(f"pylogix list_tags: {e}")

            tags = sorted(set(tags), key=str.lower)
            if len(tags) > max_count:
                tags = tags[:max_count]
            return tags

    # ----------- Exploration des MEMBRES (UDT) -----------
    def explore_members(self, base_path):
        """
        Retourne la liste des membres directs sous base_path.
        - Essaye de lire base_path: si dict-like, renvoie les clés.
        - Sinion (pycomm3) tente browse() si disponible.
        """
        if self.backend not in ("pycomm3", "pylogix"):
            raise RuntimeError("Exploration de structures seulement pour Logix")
        with self._lock:
            try:
                val = self._logix_read(base_path)
                # cas UDT lu comme dict
                if isinstance(val, dict):
                    return sorted(list(val.keys()), key=str.lower)
                # cas tableau → propose indices (limités)
                if hasattr(val, "__iter__") and not isinstance(val, (str, bytes, bytearray, dict)):
                    try:
                        L = len(val)
                        L = min(L, 64)
                        return [f"[{i}]" for i in range(L)]
                    except Exception:
                        return []
            except Exception:
                pass

            # Fallback pycomm3: browse si dispo
            if self.backend == "pycomm3":
                try:
                    br = getattr(self._client, "browse", None)
                    if br is not None:
                        nodes = br(base_path)  # peut retourner liste de noeuds
                        out = []
                        for n in nodes or []:
                            # n ou ses enfants: on tente d'en déduire un nom local
                            nm = getattr(n, "name", None) or getattr(n, "tag", None) or None
                            if nm:
                                # nm peut être qualifié; on garde le dernier segment
                                out.append(str(nm).split(".")[-1])
                        return sorted(set(out), key=str.lower)
                except Exception:
                    pass
            return []

# ===================== MAPPING & ACTIONS =====================
class ButtonMapping:
    def __init__(self, label="(non défini)", mode=None, ref="", value=None, pulse_ms=200):
        self.label = label
        self.mode = mode  # "BOOL_TRUE","BOOL_FALSE","PULSE_TRUE","TOGGLE","WRITE_VALUE"
        self.ref = ref
        self.value = value
        self.pulse_ms = pulse_ms

# ===================== UI PRINCIPALE =========================
class MiniHMI:
    def __init__(self, root):
        self.root = root
        self.root.title("Mini HMI 20 - Robotech")
        self.root.geometry("1060x700")
        self.edit_mode = tk.BooleanVar(value=False)

        # État PLC
        self.backend = tk.StringVar(value=DEFAULT_BACKEND)
        self.ip = tk.StringVar(value=DEFAULT_PLC_IP)
        self.mb_unit = tk.IntVar(value=DEFAULT_MODBUS_UNIT_ID)
        self.mb_port = tk.IntVar(value=DEFAULT_MODBUS_PORT)

        self.client = None

        # Cache des tags: clé (backend, ip, scope, program) -> liste
        self.tag_cache = []
        self.tag_cache_key = None  # tuple

        # ---- Haut: paramètres + boutons utilitaires
        top = ttk.Frame(root, padding=8); top.pack(fill="x")

        ttk.Label(top, text="Backend:").pack(side="left")
        self.backend_cmb = ttk.Combobox(top, width=10, textvariable=self.backend,
                                        values=["pycomm3", "pylogix", "modbus"], state="readonly")
        self.backend_cmb.pack(side="left", padx=(4,10))

        ttk.Label(top, text="PLC IP:").pack(side="left")
        ttk.Entry(top, textvariable=self.ip, width=16).pack(side="left", padx=(4,10))

        self.mb_fields = ttk.Frame(top)
        ttk.Label(self.mb_fields, text="Unit ID:").pack(side="left")
        ttk.Entry(self.mb_fields, textvariable=self.mb_unit, width=5).pack(side="left", padx=(4,10))
        ttk.Label(self.mb_fields, text="Port:").pack(side="left")
        ttk.Entry(self.mb_fields, textvariable=self.mb_port, width=6).pack(side="left", padx=(4,10))
        self.mb_fields.pack(side="left")

        ttk.Button(top, text="Reconnect PLC", command=self._connect_plc_safely).pack(side="left", padx=(10,6))
        ttk.Checkbutton(top, text="Mode Édit", variable=self.edit_mode).pack(side="left", padx=(10,6))
        ttk.Button(top, text="Vider mappings", command=self._clear_all_mappings).pack(side="left", padx=(6,6))
        ttk.Button(top, text="Sauver preset", command=self._save_preset).pack(side="left", padx=(6,6))
        ttk.Button(top, text="Charger preset", command=self._load_preset).pack(side="left", padx=(6,6))
        ttk.Button(top, text="Réinstaller modules", command=self._install_all_from_ui).pack(side="left", padx=(6,6))

        # Status
        self.status_var = tk.StringVar(value="État: non connecté")
        ttk.Label(top, textvariable=self.status_var, foreground="purple").pack(side="right")

        # ---- Grille des 20 boutons
        grid = ttk.Frame(root, padding=(8,0,8,8)); grid.pack(fill="both", expand=True)
        self.btn_widgets = []
        self.mappings = [ButtonMapping() for _ in range(BTN_ROWS * BTN_COLS)]
        for r in range(BTN_ROWS):
            grid.rowconfigure(r, weight=1)
            for c in range(BTN_COLS):
                grid.columnconfigure(c, weight=1)
                idx = r*BTN_COLS + c
                b = tk.Button(grid,
                              text=f"BTN {idx+1}\n(non défini)",
                              command=lambda i=idx: self._on_button(i),
                              wraplength=170,
                              height=3, width=22,
                              bg="#F2F2F2", relief="raised", bd=2)
                b.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
                self.btn_widgets.append(b)

        # ---- Log
        log_frame = ttk.Frame(root, padding=(8,0,8,8)); log_frame.pack(fill="x")
        ttk.Label(log_frame, text="Journal :").pack(anchor="w")
        self.log_box = ScrolledText(log_frame, height=9, state="disabled")
        self.log_box.pack(fill="both", expand=False)

        # Visibilité des champs Modbus selon backend
        self._refresh_modbus_fields_visibility()
        self.backend.trace_add("write", lambda *_: self._refresh_modbus_fields_visibility())

        # Auto-install ALL deps au premier lancement
        self._log("Auto-install des librairies (pycomm3, pylogix, pymodbus)…")
        try:
            ensure_dependencies(ALL_DEPS, logger=self._log)
        except Exception as e:
            self._log(f"Auto-install: exception: {e}")

        # Connexion initiale
        self._connect_plc_safely()

    # ----------------- Utilitaires UI -----------------
    def _set_status(self, text): 
        try: self.status_var.set(text)
        except Exception: pass

    def _log(self, msg):
        ts = time.strftime("%H:%M:%S")
        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{ts}] {msg}\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(0, _append)

    def _refresh_button_label(self, i):
        m = self.mappings[i]
        label = m.label or f"BTN {i+1}"
        mode = m.mode or "non défini"
        ref = m.ref or "-"
        self.btn_widgets[i].configure(text=f"{label}\n[{mode}]\n{ref}")

    def _refresh_all_labels(self):
        for i in range(len(self.mappings)):
            self._refresh_button_label(i)

    def _refresh_modbus_fields_visibility(self):
        if self.backend.get() == "modbus":
            self.mb_fields.pack(side="left")
        else:
            self.mb_fields.pack_forget()

    def _clear_all_mappings(self):
        if messagebox.askyesno("Confirmer", "Supprimer les mappings en mémoire (non sauvegardés) ?"):
            self.mappings = [ButtonMapping() for _ in range(BTN_ROWS*BTN_COLS)]
            self._refresh_all_labels()

    def _install_all_from_ui(self):
        def worker():
            self._log("Réinstallation des librairies demandée…")
            ensure_dependencies(ALL_DEPS, logger=self._log)
            self._log("Réinstallation terminée.")
        threading.Thread(target=worker, daemon=True).start()

    # ----------------- Connexion PLC ------------------
    def _connect_plc_safely(self):
        try:
            ensure_dependencies(BACKEND_DEPS.get(self.backend.get(), []), logger=self._log)
            self._set_status("Connexion PLC…")
            self.root.update_idletasks()
            self._safe_close()
            self.client = PLCClient(
                backend=self.backend.get(),
                ip=self.ip.get().strip(),
                timeout=DEFAULT_TIMEOUT,
                modbus_unit_id=self.mb_unit.get(),
                modbus_port=self.mb_port.get()
            )
            self._set_status(f"Connecté ({self.backend.get()} @ {self.ip.get()})")
            self._log(f"Connecté au PLC ({self.backend.get()} @ {self.ip.get()})")
            self._invalidate_tag_cache()
        except Exception as e:
            self.client = None
            self._set_status("Erreur connexion")
            self._log(f"Erreur connexion PLC: {e}")
            messagebox.showerror("PLC", f"Connexion échouée: {e}")

    def _invalidate_tag_cache(self):
        self.tag_cache = []
        self.tag_cache_key = None

    def _safe_close(self):
        try:
            if self.client: self.client.close()
        except Exception:
            pass
        self.client = None

    # ----------------- Actions boutons ----------------
    def _on_button(self, idx):
        if self.edit_mode.get():
            self._open_editor(idx); return
        m = self.mappings[idx]
        if not m.mode or not m.ref:
            messagebox.showwarning("Non configuré", "Ce bouton n'est pas configuré (Mode Édit)."); return
        if not self.client:
            messagebox.showerror("PLC", "Pas connecté au PLC."); return

        def worker():
            try:
                if m.mode == "BOOL_TRUE":
                    self.client.write_bool(m.ref, True);  self._log(f"[BTN {idx+1}] {m.ref} := TRUE")
                elif m.mode == "BOOL_FALSE":
                    self.client.write_bool(m.ref, False); self._log(f"[BTN {idx+1}] {m.ref} := FALSE")
                elif m.mode == "PULSE_TRUE":
                    ms = int(m.pulse_ms or 200)
                    self.client.pulse_bool(m.ref, ms=ms); self._log(f"[BTN {idx+1}] PULSE TRUE {m.ref} ({ms} ms)")
                elif m.mode == "TOGGLE":
                    newv = self.client.toggle_bool(m.ref); self._log(f"[BTN {idx+1}] TOGGLE {m.ref} -> {newv}")
                elif m.mode == "WRITE_VALUE":
                    val = m.value
                    if val is None: raise ValueError("Valeur vide")
                    if self.backend.get() == "modbus":
                        self.client.write_value(m.ref, int(float(val)))
                    else:
                        num = float(val)
                        self.client.write_value(m.ref, int(num) if abs(num-int(num))<1e-9 else num)
                    self._log(f"[BTN {idx+1}] WRITE {m.ref} := {val}")
                else:
                    raise ValueError("Mode inconnu")
            except Exception as e:
                self._log(f"[BTN {idx+1}] ERREUR: {e}")
                messagebox.showerror("Action", f"Erreur action bouton {idx+1}:\n{e}")
        threading.Thread(target=worker, daemon=True).start()

    # ----------------- Chargement des tags ----------------
    def _load_tags_async(self, scope="controller", program=None, on_done=None):
        if not self.client or self.backend.get() == "modbus":
            messagebox.showinfo("Tags", "Tags disponibles seulement pour pycomm3/pylogix.")
            return
        key = (self.backend.get(), self.ip.get(), scope, program or "")
        if self.tag_cache_key == key and self.tag_cache:
            on_done and on_done(self.tag_cache); return

        def worker():
            try:
                self._log(f"Chargement des tags ({scope}{' - '+program if program else ''})…")
                tags = self.client.list_tags(scope=scope, program=program)
                self.tag_cache = list(tags or [])
                self.tag_cache_key = key
                self._log(f"Tags chargés: {len(self.tag_cache)}")
                if on_done: self.root.after(0, lambda: on_done(self.tag_cache))
            except Exception as e:
                self._log(f"Erreur chargement tags: {e}")
                messagebox.showerror("Tags", f"Impossible de charger les tags:\n{e}")
        threading.Thread(target=worker, daemon=True).start()

    def _load_programs_async(self, on_done=None):
        if not self.client or self.backend.get() == "modbus":
            messagebox.showinfo("Programmes", "Programmes disponibles seulement pour pycomm3/pylogix.")
            return
        def worker():
            try:
                self._log("Chargement des programmes…")
                progs = self.client.list_programs()
                self._log(f"Programmes: {len(progs)} trouvés")
                if on_done: self.root.after(0, lambda: on_done(progs))
            except Exception as e:
                self._log(f"Erreur chargement programmes: {e}")
                messagebox.showerror("Programmes", f"Impossible de charger la liste des programmes:\n{e}")
        threading.Thread(target=worker, daemon=True).start()

    # ----------------- Explorateur de structures --------
    def _open_explorer(self, start_path, on_pick):
        if not self.client:
            messagebox.showerror("PLC", "Pas connecté."); return
        win = tk.Toplevel(self.root)
        win.title("Explorer la structure")
        win.geometry("640x480")
        win.transient(self.root); win.grab_set()

        top = ttk.Frame(win, padding=8); top.pack(fill="x")
        ttk.Label(top, text="Chemin:").pack(side="left")
        path_var = tk.StringVar(value=start_path)
        ttk.Entry(top, textvariable=path_var, width=60).pack(side="left", padx=6)
        ttk.Button(top, text="Charger", command=lambda: load_children(path_var.get().strip())).pack(side="left")

        mid = ttk.Frame(win, padding=(8,0,8,8)); mid.pack(fill="both", expand=True)
        lst = tk.Listbox(mid)
        lst.pack(fill="both", expand=True)
        status = tk.StringVar(value="Prêt")
        ttk.Label(win, textvariable=status, foreground="gray").pack(anchor="w", padx=8, pady=(0,8))

        btns = ttk.Frame(win, padding=8); btns.pack(fill="x")
        def up():
            p = path_var.get().strip()
            if not p: return
            # remonter: enlève le dernier ".xxx" ou "[i]"
            if p.endswith("]"):
                # array index -> retirer [n]
                i = p.rfind("[")
                if i>0: p = p[:i]
            else:
                i = p.rfind(".")
                if i>0: p = p[:i]
            path_var.set(p); load_children(p)
        ttk.Button(btns, text="Remonter", command=up).pack(side="left")
        ttk.Button(btns, text="Utiliser ce chemin", command=lambda: (on_pick(path_var.get().strip()), win.destroy())).pack(side="right")
        ttk.Button(btns, text="Annuler", command=win.destroy).pack(side="right", padx=(0,6))

        def load_children(base):
            if not base:
                status.set("Choisis d’abord un tag de base (controller/program) dans la liste, puis Explorer.")
                lst.delete(0, "end"); return
            status.set("Lecture…")
            lst.delete(0, "end")
            def worker():
                try:
                    members = self.client.explore_members(base)
                    def fill():
                        if not members:
                            status.set("Aucun membre détecté (non UDT, ou accès restreint).")
                        else:
                            status.set(f"{len(members)} membre(s)")
                            for m in members: lst.insert("end", m)
                    self.root.after(0, fill)
                except Exception as e:
                    self.root.after(0, lambda: status.set(f"Erreur: {e}"))
            threading.Thread(target=worker, daemon=True).start()
        # double-clic pour descendre
        def on_dbl(_):
            sel = lst.curselection()
            if not sel: return
            name = lst.get(sel[0])
            base = path_var.get().strip()
            # index?
            if name.startswith("["):
                path_var.set(f"{base}{name}")
            else:
                path_var.set(f"{base}.{name}")
            load_children(path_var.get().strip())
        lst.bind("<Double-1>", on_dbl)

        # charge initial si fourni
        if start_path:
            load_children(start_path)

    # ----------------- Éditeur de mapping --------------
    def _open_editor(self, idx):
        m = self.mappings[idx]
        win = tk.Toplevel(self.root)
        win.title(f"Configurer BTN {idx+1}")
        win.transient(self.root); win.grab_set()

        frm = ttk.Frame(win, padding=10); frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Libellé du bouton:").grid(row=0, column=0, sticky="w")
        label_var = tk.StringVar(value=m.label if m.label and m.label != "(non défini)" else f"BTN {idx+1}")
        ttk.Entry(frm, textvariable=label_var, width=32).grid(row=0, column=1, sticky="we")

        ttk.Label(frm, text="Mode:").grid(row=1, column=0, sticky="w", pady=(6,0))
        mode_var = tk.StringVar(value=m.mode or "")
        mode_cmb = ttk.Combobox(frm, textvariable=mode_var, width=20, state="readonly",
                                values=["BOOL_TRUE","BOOL_FALSE","PULSE_TRUE","TOGGLE","WRITE_VALUE"])
        mode_cmb.grid(row=1, column=1, sticky="w", pady=(6,0))

        # ---- Zone Référence (Logix: combo tags / Modbus: entry)
        ref_var = tk.StringVar(value=m.ref or "")
        ref_line = ttk.Frame(frm); ref_line.grid(row=2, column=0, columnspan=3, sticky="we", pady=(6,0))
        ref_line.grid_columnconfigure(1, weight=1)

        # Scope & Program (Logix only)
        scope_var = tk.StringVar(value="controller")  # "controller"|"program"
        program_var = tk.StringVar(value="")
        programs_combo = ttk.Combobox(ref_line, textvariable=program_var, values=[], width=24, state="readonly")
        load_prog_btn = ttk.Button(ref_line, text="Charger Programmes",
                                   command=lambda: self._load_programs_async(lambda progs: programs_combo.configure(values=progs)))

        ttk.Label(ref_line, text="Scope:").grid(row=0, column=0, sticky="w")
        scope_cmb = ttk.Combobox(ref_line, textvariable=scope_var, values=["controller","program"], width=12, state="readonly")
        scope_cmb.grid(row=0, column=1, sticky="w")
        ttk.Label(ref_line, text="Programme:").grid(row=0, column=2, sticky="w", padx=(8,0))
        programs_combo.grid(row=0, column=3, sticky="w")
        load_prog_btn.grid(row=0, column=4, padx=(6,0))

        # Ligne Tag
        ref_label_var = tk.StringVar()
        ttk.Label(ref_line, textvariable=ref_label_var).grid(row=1, column=0, sticky="w", pady=(6,0))
        tag_combo = ttk.Combobox(ref_line, textvariable=ref_var, values=[], width=46)
        tag_combo.grid(row=1, column=1, columnspan=3, sticky="we", pady=(6,0))
        refresh_btn = ttk.Button(ref_line, text="Charger/Rafraîchir",
                                 command=lambda: self._load_tags_async(
                                     scope=("program" if (self.backend.get()!="modbus" and scope_var.get()=="program") else "controller"),
                                     program=(program_var.get() or None) if scope_var.get()=="program" else None,
                                     on_done=lambda tags: tag_combo.configure(values=tags)
                                 ))
        refresh_btn.grid(row=1, column=4, padx=(6,0), pady=(6,0))

        # Explorer (membres UDT)
        ttk.Button(ref_line, text="Explorer…",
                   command=lambda: self._open_explorer(tag_combo.get().strip() or ref_var.get().strip(),
                                                       on_pick=lambda p: (ref_var.set(p), tag_combo.set(p)))
                   ).grid(row=2, column=4, padx=(6,0), pady=(6,0), sticky="e")

        # Filtre local
        ttk.Label(ref_line, text="Filtre:").grid(row=2, column=0, sticky="w", pady=(6,0))
        filter_var = tk.StringVar()
        filter_entry = ttk.Entry(ref_line, textvariable=filter_var, width=20)
        filter_entry.grid(row=2, column=1, sticky="w", pady=(6,0))
        def on_filter(*_):
            s = (filter_var.get() or "").lower()
            base = self.tag_cache if self.tag_cache else list(tag_combo.cget("values"))
            tag_combo.configure(values=[t for t in base if s in t.lower()])
        filter_var.trace_add("write", on_filter)

        # Pour Modbus: entry simple
        ref_entry = ttk.Entry(ref_line, textvariable=ref_var, width=46)

        def refresh_ref_ui(*_):
            be = self.backend.get()
            if be == "modbus":
                ref_label_var.set("Coil/Register (Modbus):")
                scope_cmb.grid_remove(); programs_combo.grid_remove(); load_prog_btn.grid_remove()
                tag_combo.grid_remove(); refresh_btn.grid_remove(); filter_entry.grid_remove()
                ref_entry.grid(row=1, column=1, columnspan=3, sticky="we", pady=(6,0))
            else:
                ref_label_var.set("Tag (Logix):")
                scope_cmb.grid(); programs_combo.grid(); load_prog_btn.grid()
                tag_combo.grid(); refresh_btn.grid(); filter_entry.grid()
                ref_entry.grid_remove()
        refresh_ref_ui()
        self.backend.trace_add("write", lambda *_: refresh_ref_ui())

        # Hints
        hint = tk.Label(frm, text="", fg="gray")
        hint.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8,6))
        def refresh_hint(*_):
            be = self.backend.get(); md = mode_var.get()
            if be == "modbus":
                if md in ("BOOL_TRUE","BOOL_FALSE","PULSE_TRUE","TOGGLE"):
                    hint.config(text="Modbus: adresse COIL (ex: 0,1,2…).")
                elif md == "WRITE_VALUE":
                    hint.config(text="Modbus: registre holding (int). Adresse ex: 40001.")
                else:
                    hint.config(text="")
            else:
                if md in ("BOOL_TRUE","BOOL_FALSE","PULSE_TRUE","TOGGLE"):
                    hint.config(text="Logix: tag BOOL (ex: Program:MonProg.HMI_Start ou Cell.Cmd.Reset).")
                elif md == "WRITE_VALUE":
                    hint.config(text="Logix: tag INT/DINT/REAL (ex: Program:MonProg.SpeedSetpoint).")
                else:
                    hint.config(text="")
        mode_var.trace_add("write", refresh_hint); refresh_hint()

        # Test lecture
        def test_read():
            try:
                p = (ref_var.get() or "").strip()
                if not p: 
                    messagebox.showwarning("Test", "Chemin vide."); return
                v = self.client.read_any(p)
                # Résumé propre
                if isinstance(v, dict):
                    keys = ", ".join(list(v.keys())[:10])
                    txt = f"OK (UDT: {len(v)} membre(s). Ex: {keys} …)"
                elif isinstance(v, (list, tuple)):
                    txt = f"OK (Array len={len(v)})"
                else:
                    txt = f"OK (valeur={v})"
                messagebox.showinfo("Test lecture", f"{p}\n{txt}")
            except Exception as e:
                messagebox.showerror("Test lecture", f"Erreur lecture:\n{e}")

        ttk.Button(frm, text="Test Lire", command=test_read).grid(row=5, column=0, columnspan=1, pady=(6,0), sticky="w")

        # Valeur (WRITE_VALUE)
        ttk.Label(frm, text="Valeur (WRITE_VALUE):").grid(row=3, column=0, sticky="w", pady=(6,0))
        val_var = tk.StringVar(value="" if m.value is None else str(m.value))
        ttk.Entry(frm, textvariable=val_var, width=32).grid(row=3, column=1, sticky="we", pady=(6,0))

        # Pulse (PULSE_TRUE)
        ttk.Label(frm, text="Pulse (ms) (PULSE_TRUE):").grid(row=4, column=0, sticky="w", pady=(6,0))
        pulse_var = tk.IntVar(value=int(m.pulse_ms or 200))
        ttk.Entry(frm, textvariable=pulse_var, width=12).grid(row=4, column=1, sticky="w", pady=(6,0))

        # Boutons action
        btns = ttk.Frame(frm); btns.grid(row=7, column=0, columnspan=3, pady=(10,0), sticky="e")
        def apply_and_close():
            try:
                new = ButtonMapping(
                    label = (label_var.get() or f"BTN {idx+1}"),
                    mode  = (mode_var.get() or None),
                    ref   = (ref_var.get() or "").strip(),
                    value = (None if val_var.get().strip()=="" else float(val_var.get().strip())),
                    pulse_ms = int(pulse_var.get() or 200),
                )
                self.mappings[idx] = new
                self._refresh_button_label(idx)
                win.destroy()
            except ValueError as e:
                messagebox.showerror("Entrées invalides", f"Erreur: {e}")

        ttk.Button(btns, text="OK", command=apply_and_close).pack(side="right", padx=4)
        ttk.Button(btns, text="Annuler", command=win.destroy).pack(side="right")

        ttk.Label(frm, text=f"Backend courant: {self.backend.get()} @ {self.ip.get()}",
                  foreground="purple").grid(row=8, column=0, columnspan=3, sticky="w", pady=(8,0))

    # ----------------- PRESETS ----------------
    def _save_preset(self):
        try:
            path = filedialog.asksaveasfilename(
                title="Sauver preset",
                defaultextension=".json",
                filetypes=[("Preset JSON","*.json"), ("Tous","*.*")]
            )
            if not path: return
            data = {
                "version": 1,
                "backend": self.backend.get(),
                "ip": self.ip.get(),
                "modbus": {"unit": self.mb_unit.get(), "port": self.mb_port.get()},
                "mappings": [
                    {
                        "label": m.label, "mode": m.mode, "ref": m.ref,
                        "value": m.value, "pulse_ms": m.pulse_ms
                    } for m in self.mappings
                ],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._log(f"Preset sauvegardé: {path}")
            messagebox.showinfo("Preset", "Preset sauvegardé.")
        except Exception as e:
            messagebox.showerror("Preset", f"Erreur sauvegarde:\n{e}")

    def _load_preset(self):
        try:
            path = filedialog.askopenfilename(
                title="Charger preset",
                filetypes=[("Preset JSON","*.json"), ("Tous","*.*")]
            )
            if not path: return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            maps = data.get("mappings")
            if not isinstance(maps, list) or len(maps) != BTN_ROWS*BTN_COLS:
                raise ValueError("Format de preset invalide (mappings manquants)")
            new = []
            for d in maps:
                new.append(ButtonMapping(
                    label=d.get("label","(non défini)"),
                    mode=d.get("mode"),
                    ref=d.get("ref",""),
                    value=d.get("value", None),
                    pulse_ms=int(d.get("pulse_ms", 200)),
                ))
            self.mappings = new
            self._refresh_all_labels()
            self._log(f"Preset chargé: {path}")
            messagebox.showinfo("Preset", "Preset chargé.")
        except Exception as e:
            messagebox.showerror("Preset", f"Erreur chargement:\n{e}")

    # ----------------- fermeture propre ----------------
    def close(self):
        self._safe_close()

# ===================== MAIN =============================
def main():
    try:
        ensure_dependencies(ALL_DEPS)
    except Exception as e:
        print(f"[auto-install] Exception initiale: {e}")

    root = tk.Tk()
    app = MiniHMI(root)
    def on_close():
        app.close()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

if __name__ == "__main__":
    main()
