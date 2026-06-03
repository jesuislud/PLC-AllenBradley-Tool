import sys
import subprocess
import importlib.util
import csv
import os
import tkinter as tk
from tkinter import filedialog, messagebox


def ensure_package(package_name):
    if importlib.util.find_spec(package_name) is not None:
        return

    commands = [
        [sys.executable, "-m", "pip", "install", package_name],
        [sys.executable, "-m", "pip", "install", "--user", package_name],
    ]

    last_error = None

    for cmd in commands:
        try:
            subprocess.check_call(cmd)
            if importlib.util.find_spec(package_name) is not None:
                return
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Impossible d’installer {package_name}: {last_error}")


ensure_package("pycomm3")

from pycomm3 import LogixDriver


DEFAULT_PLC_IP = "192.168.1.10"
DEFAULT_TAG = "Program:P060_Crown.CrownMesureTest"
DEFAULT_ARRAY_SIZE = 700


def choisir_fichier():
    path = filedialog.asksaveasfilename(
        title="Choisir la sortie CSV",
        defaultextension=".csv",
        filetypes=[("CSV", "*.csv"), ("Tous les fichiers", "*.*")]
    )

    if path:
        output_var.set(path)


def exporter_csv():
    plc_ip = ip_var.get().strip()
    tag_name = tag_var.get().strip()
    output_csv = output_var.get().strip()

    try:
        array_size = int(size_var.get().strip())
    except ValueError:
        messagebox.showerror("Erreur", "La grandeur du array doit être un nombre.")
        return

    if not plc_ip:
        messagebox.showerror("Erreur", "Adresse IP PLC manquante.")
        return

    if not tag_name:
        messagebox.showerror("Erreur", "Tag manquant.")
        return

    if not output_csv:
        messagebox.showerror("Erreur", "Choisis un fichier CSV de sortie.")
        return

    try:
        log_text.delete("1.0", tk.END)
        log(f"Connexion au PLC : {plc_ip}")
        log(f"Lecture : {tag_name}[0]{{{array_size}}}")

        with LogixDriver(plc_ip) as plc:
            result = plc.read(f"{tag_name}[0]{{{array_size}}}")

            log(f"Erreur PLC : {result.error}")
            log(f"Type : {result.type}")

            if result.error:
                raise RuntimeError(result.error)

            valeurs = result.value

        if valeurs is None:
            raise RuntimeError("Aucune valeur reçue du PLC.")

        log(f"Nombre de valeurs reçues : {len(valeurs)}")

        with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")

            writer.writerow([
                "Index",
                "Tag",
                "Valeur"
            ])

            for i, valeur in enumerate(valeurs):
                writer.writerow([
                    i,
                    f"{tag_name}[{i}]",
                    valeur
                ])

        log("Export terminé.")
        log(f"Fichier créé : {output_csv}")

        messagebox.showinfo("Succès", f"Export terminé :\n{output_csv}")

    except Exception as e:
        log(f"ERREUR : {e}")
        messagebox.showerror("Erreur", str(e))


def log(message):
    log_text.insert(tk.END, message + "\n")
    log_text.see(tk.END)
    root.update_idletasks()


root = tk.Tk()
root.title("Export PLC Array REAL vers CSV")
root.geometry("720x430")

ip_var = tk.StringVar(value=DEFAULT_PLC_IP)
tag_var = tk.StringVar(value=DEFAULT_TAG)
size_var = tk.StringVar(value=str(DEFAULT_ARRAY_SIZE))
output_var = tk.StringVar(
    value=os.path.join(os.path.expanduser("~/Desktop"), "CrownMesureTest.csv")
)

frame = tk.Frame(root, padx=12, pady=12)
frame.pack(fill="both", expand=True)

tk.Label(frame, text="Adresse IP PLC :").grid(row=0, column=0, sticky="w")
tk.Entry(frame, textvariable=ip_var, width=45).grid(row=0, column=1, sticky="we", padx=6)

tk.Label(frame, text="Tag à lire :").grid(row=1, column=0, sticky="w", pady=6)
tk.Entry(frame, textvariable=tag_var, width=45).grid(row=1, column=1, sticky="we", padx=6)

tk.Label(frame, text="Nombre d'éléments :").grid(row=2, column=0, sticky="w")
tk.Entry(frame, textvariable=size_var, width=12).grid(row=2, column=1, sticky="w", padx=6)

tk.Label(frame, text="Sortie CSV :").grid(row=3, column=0, sticky="w", pady=6)
tk.Entry(frame, textvariable=output_var, width=45).grid(row=3, column=1, sticky="we", padx=6)
tk.Button(frame, text="Parcourir...", command=choisir_fichier).grid(row=3, column=2, padx=6)

tk.Button(
    frame,
    text="Exporter CSV",
    command=exporter_csv,
    height=2,
    bg="#d9ead3"
).grid(row=4, column=1, sticky="we", pady=12)

tk.Label(frame, text="Journal :").grid(row=5, column=0, sticky="nw")

log_text = tk.Text(frame, height=10)
log_text.grid(row=5, column=1, columnspan=2, sticky="nsew", padx=6)

frame.columnconfigure(1, weight=1)
frame.rowconfigure(5, weight=1)

root.mainloop()