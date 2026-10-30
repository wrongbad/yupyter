# yupyter

A zen-mode notebook for any Jupyter kernel, with Claude as one more kernel. Notebooks are plain Markdown files.

<p align="center"><img src="examples/screenshot.png" width="480" alt="A yupyter notebook: a Python cell, a Claude cell asking for a sin(x) plot, and the cells Claude inserted and ran to make it"></p>

Above, a `claude` cell asks for a plot. Claude reads the notebook, inserts a cell and runs it. When that fails
because plotnine is missing, it adds a `%pip install` cell, runs both, and explains what it did. Its trace
(tool calls in grey, a failed run in red, thinking folded away) stays in the notebook.

## Why it's nice

- **Notebooks are plain Markdown.** No `.ipynb` JSON: the file reads like a document, diffs cleanly in git, and
  you (or Claude Code, or vim) can edit it while it's open. Changes on disk show up live. Images go in a
  sibling `notes.assets/` folder. Here's how [examples/notes.md](examples/notes.md), the notebook in the
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

- **Claude is a kernel, not a sidebar.** A `claude` (or `%%claude`) cell runs like any other cell. Claude sees
  the whole notebook, its outputs and the installed kernels, and works in the notebook itself: it inserts,
  edits and runs cells you can read, rerun or change. Each run is one undo step, from the button under the cell
  or Cmd/Ctrl+Z.
- **Any Jupyter kernel, mixed in one file.** Python, R, Julia... each cell picks its kernel, and each notebook
  gets its own kernel processes.
- **Quiet UI.** One centred column, no toolbars. Commands live in the palette (Ctrl/Cmd+K); a file drawer and
  save status sit in the corner.
- **Multiplayer (experimental).** Several people can edit the same notebook live, with avatars, remote cursors
  and per-user undo. It's built on Yjs CRDTs.

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
