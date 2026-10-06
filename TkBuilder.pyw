"""Double-click this file to start TkBuilder (no console window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    from app import main
    main()
except Exception:  # show startup problems instead of failing silently
    import traceback
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("TkBuilder", "TkBuilder couldn't start:\n\n" + traceback.format_exc()[-2000:])
