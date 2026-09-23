from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], timeout: int = 180) -> str:
    try:
        out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return out.strip().splitlines()[-1].strip() if out.strip() else ""
    except Exception:
        return ""


def pick_file(kind: str) -> str:
    """Native file picker without importing Tkinter.

    Windows uses the in-box .NET WinForms dialog, macOS uses osascript, and
    Linux prefers zenity/kdialog. Empty string means cancelled/unavailable.
    """
    system = platform.system()
    if system == "Windows":
        title = "Select a GGUF model" if kind == "model" else "Select llama-server"
        filt = "GGUF model (*.gguf)|*.gguf|All files (*.*)|*.*" if kind == "model" else "llama-server (llama-server.exe)|llama-server.exe|All files (*.*)|*.*"
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d=New-Object System.Windows.Forms.OpenFileDialog; "
            f"$d.Title='{title.replace("'", "''")}'; $d.Filter='{filt.replace("'", "''")}'; "
            "$d.RestoreDirectory=$true; if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::WriteLine($d.FileName)}"
        )
        return _run(["powershell.exe", "-NoProfile", "-STA", "-Command", script])
    if system == "Darwin":
        prompt = "Choose a GGUF model" if kind == "model" else "Choose llama-server"
        script = f'POSIX path of (choose file with prompt "{prompt}")'
        return _run(["osascript", "-e", script])
    if shutil.which("zenity"):
        args = ["zenity", "--file-selection", "--title=Select a GGUF model" if kind == "model" else "--title=Select llama-server"]
        if kind == "model": args += ["--file-filter=GGUF models | *.gguf"]
        return _run(args)
    if shutil.which("kdialog"):
        return _run(["kdialog", "--getopenfilename", str(Path.home()), "*.gguf"] if kind == "model" else ["kdialog", "--getopenfilename", str(Path.home())])
    return ""


def pick_folder() -> str:
    system = platform.system()
    if system == "Windows":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description='Add model folder'; $d.ShowNewFolderButton=$true; "
            "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::WriteLine($d.SelectedPath)}"
        )
        return _run(["powershell.exe", "-NoProfile", "-STA", "-Command", script])
    if system == "Darwin":
        return _run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Add model folder")'])
    if shutil.which("zenity"):
        return _run(["zenity", "--file-selection", "--directory", "--title=Add model folder"])
    if shutil.which("kdialog"):
        return _run(["kdialog", "--getexistingdirectory", str(Path.home())])
    return ""
