"""Modern LlamaForge entry point.

The legacy Tk interface was removed in 0.11. The application is the local web
control plane launched by :mod:`run`.
"""

def main():
    from run import main as launch
    return launch()

__all__ = ["main"]
