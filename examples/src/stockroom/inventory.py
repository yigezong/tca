from stockroom.contracts import ReservationResult


class Inventory:
    def __init__(self, quantities: dict[str, int]) -> None:
        self._quantities = dict(quantities)

    def available(self, sku: str) -> int:
        return self._quantities.get(sku, 0)

    def reserve(self, sku: str, quantity: int) -> ReservationResult:
        """Prototype: there is no request identity or retry policy yet."""
        if quantity <= 0 or self.available(sku) < quantity:
            return ReservationResult(ok=False)
        self._quantities[sku] -= quantity
        return ReservationResult(ok=True)
