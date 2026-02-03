import yaml
import os

BASE_CONFIG = "config/strategy.yaml"
ROTATED_CONFIG = "config/strategy_rotated.yaml"


def load_config():

    path = ROTATED_CONFIG if os.path.exists(ROTATED_CONFIG) else BASE_CONFIG

    with open(path, "r") as f:
        cfg = yaml.safe_load(f)

    cfg["_meta"] = {
        "source": path
    }

    return cfg
