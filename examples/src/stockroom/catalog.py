from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int


CATALOG = {
    "lamp": Product("lamp", "Desk lamp", 2400),
    "mug": Product("mug", "Ceramic mug", 1200),
}
