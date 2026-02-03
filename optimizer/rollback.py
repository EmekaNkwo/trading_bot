import json
import os
from datetime import datetime


class ParameterRollback:

    STATE_FILE = "optimizer/rotation_state.json"

    def load_state(self):
        if not os.path.exists(self.STATE_FILE):
            return {}

        with open(self.STATE_FILE, "r") as f:
            return json.load(f)

    def save_state(self, state):
        with open(self.STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

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
        if not prev:
            return None

        return prev
