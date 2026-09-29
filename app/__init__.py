from __future__ import annotations

import importlib.util
from pathlib import Path


_legacy_app_path = Path(__file__).resolve().parent.parent / "app.py"
_spec = importlib.util.spec_from_file_location("naijaclip_dashboard_app", _legacy_app_path)
_dashboard_module = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_dashboard_module)
app = _dashboard_module.app
