import unittest

from stockroom.checkout import Checkout
from stockroom.inventory import Inventory


class StarterTests(unittest.TestCase):
    def test_quote_uses_catalog_and_current_stock(self) -> None:
        inventory = Inventory({"lamp": 2})
        checkout = Checkout(inventory)
        assert checkout.quote("lamp", 2).total_cents == 4800
        assert checkout.quote("lamp", 2).available
        assert not checkout.quote("lamp", 3).available

    def test_order_placement_is_unfinished(self) -> None:
        checkout = Checkout(Inventory({"lamp": 2}))
        with self.assertRaises(NotImplementedError):
            checkout.place_order("lamp", 1, "order-1")
