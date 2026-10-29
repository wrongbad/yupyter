"""Hatch build hook: build the Vite frontend into yupyter/static/ for the wheel."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        root = Path(self.root)
        static = root / "yupyter" / "static"
        if os.environ.get("YUPYTER_SKIP_FRONTEND") == "1":
            return
        if (static / "index.html").exists():
            return
        frontend = root / "frontend"
        if not (frontend / "package.json").exists():
            return  # building from an sdist without sources
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "npm is required to build the yupyter frontend "
                "(or set YUPYTER_SKIP_FRONTEND=1 / pre-build yupyter/static)"
            )
        subprocess.check_call([npm, "ci"], cwd=frontend)
        subprocess.check_call([npm, "run", "build"], cwd=frontend)
        build_data["artifacts"].append("yupyter/static/**")
