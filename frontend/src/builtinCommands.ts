// Built-in palette commands. Importing this module registers them.
import { registerCommand } from "./commands";
import { interruptKernel, listCells } from "./doc";
import { showShortcuts } from "./components/Palette";
import { renameSelf } from "./presence/peers";
import { deleteSelected, insertRelative, moveCell, runAll, runSelected } from "./ops";
import { getSelected } from "./presence/selection";
import { redo, undo } from "./undo";
import { saveNow, toggleDrawer } from "./files";
import { isDark, setTheme } from "./user";

const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl+";

registerCommand({ id: "user.setName", title: "Set your name", run: renameSelf });
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
  run: () => void saveNow(),
});
registerCommand({ id: "view.files", title: "Toggle file list", run: toggleDrawer });
registerCommand({ id: "edit.undo", title: "Undo cell operation or agent run", shortcut: mod + "Z", run: undo });
registerCommand({ id: "edit.redo", title: "Redo cell operation or agent run", shortcut: mod + "Shift+Z", run: redo });
registerCommand({
  id: "view.theme",
  title: "Toggle light/dark theme",
  run: () => setTheme(isDark() ? "light" : "dark"),
});
registerCommand({ id: "help.shortcuts", title: "Show shortcuts", shortcut: mod + "K", run: showShortcuts });
