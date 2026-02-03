class CapitalAllocator:

    def __init__(self, max_total_risk):
        self.max_total_risk = max_total_risk

    def allocate(self, symbol_risk, open_risk):
        available = self.max_total_risk - open_risk
        return max(0, min(symbol_risk, available))
