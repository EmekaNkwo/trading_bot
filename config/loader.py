import yaml
import os

BASE_CONFIG = "config/strategy.yaml"
ROTATED_CONFIG = "config/strategy_rotated.yaml"


def _deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override

    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config():
    with open(BASE_CONFIG, "r") as f:
        cfg = yaml.safe_load(f) or {}

    source = BASE_CONFIG
    if os.path.exists(ROTATED_CONFIG):
        with open(ROTATED_CONFIG, "r") as f:
            rotated_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, rotated_cfg)
        source = f"{BASE_CONFIG}+{ROTATED_CONFIG}"

    cfg["_meta"] = {
        "source": source
    }

    return cfg
