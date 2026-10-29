import { render } from "preact";
import { App } from "./components/App";
import "./style.css";
import { applyTheme } from "./user";

applyTheme();

render(<App />, document.getElementById("app")!);
