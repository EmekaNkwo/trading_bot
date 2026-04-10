import json
import os
from pathlib import Path


class ParameterRollback:

    STATE_FILE = "optimizer/rotation_state.json"

    def load_state(self):
        if not os.path.exists(self.STATE_FILE):
            return {}

        try:
            with open(self.STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def save_state(self, state):
        with open(self.STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def stage_previous_state(self, config_path):
        path = Path(config_path)
        state = self.load_state()
        previous_text = None
        if path.exists():
            previous_text = path.read_text(encoding="utf-8")

        state.update({
            "previous_config": previous_text,
            "previous_config_path": str(path),
            "previous_last_rotation": state.get("last_rotation"),
            "previous_active_config": state.get("active_config"),
        })
        self.save_state(state)

    # -------------------------------------------------
    # NEW: record successful rotation
    # -------------------------------------------------

    def record_rotation(self, timestamp, config_path):
        """
        Records a successful parameter rotation.
        """

        state = self.load_state()

        state.update({
            "last_rotation": timestamp,
            "active_config": config_path
        })

        self.save_state(state)

    # -------------------------------------------------
    # Rollback (placeholder / future use)
    # -------------------------------------------------

    def rollback(self):
        state = self.load_state()

        prev = state.get("previous_config")
        config_path = state.get("previous_config_path")
        if not config_path:
            return None

        path = Path(config_path)
        if prev is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(prev, encoding="utf-8")

        if state.get("previous_last_rotation") is None:
            state.pop("last_rotation", None)
        else:
            state["last_rotation"] = state.get("previous_last_rotation")

        if state.get("previous_active_config") is None:
            state.pop("active_config", None)
        else:
            state["active_config"] = state.get("previous_active_config")

        for key in (
            "previous_config",
            "previous_config_path",
            "previous_last_rotation",
            "previous_active_config",
        ):
            state.pop(key, None)

        self.save_state(state)
        return str(path)
