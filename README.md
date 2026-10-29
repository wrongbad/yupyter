# yupyter

A zen-mode notebook on Jupyter kernels. Markdown files, multiple kernels, live multiplayer, and Claude as a kernel.

## Quickstart

Needs Python 3.10+ and Node 18+ (to build the frontend).

```bash
git clone https://github.com/wrongbad/yupyter
cd yupyter
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
yupyter notes.md
```

Then type in the prompt bar:

```
2 + 2
%%claude please graph y = 2 + x
```

`%%claude` uses your Claude Code login (run `claude` once) or `ANTHROPIC_API_KEY`.

Development: see [CONTRIBUTING.md](CONTRIBUTING.md).
