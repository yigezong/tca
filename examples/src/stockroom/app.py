from stockroom.checkout import Checkout
from stockroom.inventory import Inventory


def main() -> None:
    checkout = Checkout(Inventory({"lamp": 3, "mug": 5}))
    print(checkout.quote("lamp", 2))
    print("Order placement awaits the inventory reservation contract.")


if __name__ == "__main__":
    main()
