// Built-in palette commands. Importing this module registers them.
import { registerCommand } from "./commands";
import { awareness, interruptKernel, listCells } from "./doc";
import { askText, showShortcuts } from "./components/Palette";
import { deleteSelected, insertRelative, moveCell, runAll, runSelected } from "./ops";
import { getSelected } from "./presence/selection";
import { structUndo } from "./undo";
import { getUser, isDark, setTheme, setUserName, userState } from "./user";

const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl+";

registerCommand({
  id: "user.setName",
  title: "Set your name",
  run: async () => {
    const n = await askText("Your name", getUser().name);
    if (n && n.trim()) {
      setUserName(n);
      awareness.setLocalStateField("user", userState());
    }
  },
});
registerCommand({ id: "cell.insertAbove", title: "Insert cell above", shortcut: "A", run: () => void insertRelative("above") });
registerCommand({ id: "cell.insertBelow", title: "Insert cell below", shortcut: "B", run: () => void insertRelative("below") });
registerCommand({ id: "cell.delete", title: "Delete cell", shortcut: "D D", run: deleteSelected });
registerCommand({ id: "cell.moveUp", title: "Move cell up", shortcut: "Alt+↑", run: () => moveCell(-1) });
registerCommand({ id: "cell.moveDown", title: "Move cell down", shortcut: "Alt+↓", run: () => moveCell(1) });
registerCommand({ id: "run.cell", title: "Run cell", shortcut: "Shift+Enter", run: runSelected });
registerCommand({ id: "run.all", title: "Run all cells", run: runAll });
registerCommand({
  id: "run.interrupt",
  title: "Interrupt kernel",
  run: () => {
    const c = listCells().find((x) => x.id === getSelected());
    return void interruptKernel(c?.kernel ?? "python3");
  },
});
registerCommand({
  id: "doc.save",
  title: "Save",
  shortcut: mod + "S",
  run: () => void fetch("/api/save", { method: "POST" }),
});
registerCommand({ id: "edit.undo", title: "Undo cell operation", shortcut: mod + "Z", run: () => void structUndo.undo() });
registerCommand({ id: "edit.redo", title: "Redo cell operation", shortcut: mod + "Shift+Z", run: () => void structUndo.redo() });
registerCommand({
  id: "view.theme",
  title: "Toggle light/dark theme",
  run: () => setTheme(isDark() ? "light" : "dark"),
});
registerCommand({ id: "help.shortcuts", title: "Show shortcuts", shortcut: mod + "K", run: showShortcuts });
