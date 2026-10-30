# yupyter

A zen-mode notebook for any Jupyter kernel, with Claude as one more kernel. Notebooks are plain Markdown files.

<p align="center"><img src="examples/screenshot.png" width="480" alt="A yupyter notebook: a Python cell, a Claude cell asking for a sin(x) plot, and the cells Claude inserted and ran to make it"></p>

Above, a `claude` cell asks for a plot. Claude reads the notebook, inserts a cell and runs it. When that fails
because plotnine is missing, it adds a `%pip install` cell, runs both, and explains what it did. Its trace
(tool calls in grey, a failed run in red, thinking folded away) stays in the notebook.

The notebook is just a Markdown file. Here's how [examples/notes.md](examples/notes.md), the one in the
screenshot, starts:

````markdown
---
yupyter: 1
---

```py {#d07397}
import numpy as np
np.pi
```

```output {for=d07397 type=result}
3.141592653589793
```

```claude {#bc8734}
Can you create a graph of y=sin(x) using plotnine
```
````

Images go in a `notes.assets/` folder next to it. Because it's plain text, it diffs cleanly in git, and you
can edit it in another editor while it's open; yupyter picks up the change.

Cells can use any installed Jupyter kernel, mixed in one notebook. A Claude cell (`%%claude` at the top of a
cell) is one more kind: Claude sees the notebook and works by inserting, editing and running ordinary
cells, so what it did is there to read and rerun. Each run can be undone as a whole, from the button under
the cell or with Cmd/Ctrl+Z.

Live editing with several people works too, but it's experimental.

## Quickstart

Needs Python 3.10+ and Node 18+ (to build the frontend).

```bash
git clone https://github.com/wrongbad/yupyter
cd yupyter
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cd examples && yupyter notes.md
```

That opens the example notebook in your browser. Plain `yupyter` serves the current directory with a file
drawer to open or create notebooks; nothing outside that directory is reachable.

Click "+ New cell", type `2 + 2` and press Shift+Enter. In another cell, try `%%claude graph y = x^2`.
Claude cells use your Claude Code login (run `claude` once) or `ANTHROPIC_API_KEY`.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and tests, and [docs/](docs/) for how it's built.

## License

[Apache 2.0](LICENSE)
