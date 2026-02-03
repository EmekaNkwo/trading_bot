from itertools import product


def generate_grid(param_dict):
    """
    Example:
    {
        "atr_period": [10,14,20],
        "sl_mult": [1.2,1.5],
        "rr": [2.0,2.5]
    }
    """

    keys = param_dict.keys()
    values = param_dict.values()

    configs = []

    for combo in product(*values):
        configs.append(dict(zip(keys, combo)))

    return configs
