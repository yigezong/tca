from dataclasses import dataclass

from stockroom.catalog import CATALOG
from stockroom.inventory import Inventory


@dataclass(frozen=True)
class Quote:
    sku: str
    quantity: int
    total_cents: int
    available: bool


class Checkout:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory

    def quote(self, sku: str, quantity: int) -> Quote:
        product = CATALOG[sku]
        return Quote(
            sku=sku,
            quantity=quantity,
            total_cents=product.price_cents * quantity,
            available=quantity > 0 and self.inventory.available(sku) >= quantity,
        )

    def place_order(self, sku: str, quantity: int, order_id: str) -> object:
        """Implement after the inventory reservation interface is agreed."""
        raise NotImplementedError("checkout has not been connected to reservations")
