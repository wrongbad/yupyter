# yupyter

A zen-mode notebook on Jupyter kernels. Markdown files, multiple kernels, live multiplayer, and Claude as a kernel.

## Quickstart

Needs Python 3.10+ and Node 18+ (to build the frontend).

```bash
git clone https://github.com/wrongbad/yupyter
cd yupyter
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cd examples && yupyter
```

`yupyter notes.md` serves the current directory and opens `notes.md`; `yupyter some/dir` (or just `yupyter`)
serves that directory and shows the file drawer, where you can open any notebook under it or create one.
The server never reaches outside that root. One kernel per notebook; files edited on disk while open are
picked up (or flagged if you have unsaved changes).

Then click "+ New cell", type, and press Shift+Enter:

```
2 + 2
%%claude please graph y = 2 + x
```

`%%claude` uses your Claude Code login (run `claude` once) or `ANTHROPIC_API_KEY`.

Development: see [CONTRIBUTING.md](CONTRIBUTING.md).
