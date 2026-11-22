"""Declared goals and server-side judges for six ViewSpec fixtures."""

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT.parent / "fixtures"


@dataclass(frozen=True)
class Task:
    path: str
    goal: str
    endpoint: str
    expected: tuple[dict[str, str], ...]


LOCAL = {
    "spa": Task(
        "/spa_product.html?route=state",
        "Switch from Arc lamp to Globe lamp, then add exactly one Globe lamp to the cart. Do not add an Arc lamp or place an order.",
        "/cart-add",
        ({"item": "Globe lamp"},),
    ),
    "receipt-confirm": Task(
        "/order_receipt.html?dialog=confirm",
        "Place exactly one order for 3 units. Decline the optional email receipt. Verify the order went through before reporting.",
        "/orders",
        ({"quantity": "3"},),
    ),
    "receipt-prompt": Task(
        "/order_receipt.html?dialog=prompt",
        "Place exactly one order for 4 units. Dismiss the optional email-receipt prompt. Verify the order went through before reporting.",
        "/orders",
        ({"quantity": "4"},),
    ),
    "catalog": Task(
        "/catalog.html",
        "Add exactly one Vintage lamp numbered 241 and one Garden lamp numbered 291 to the cart. Do not add anything else or check out.",
        "/cart-add",
        ({"item": "Beam vintage lamp 241"}, {"item": "Dawn garden lamp 291"}),
    ),
    "checkout": Task(
        "/checkout.html",
        "Place one Arc lamp order under river@example.test with Express shipping, Spain as Country and Box gift wrap. Accept the terms. Leave the optional password blank.",
        "/orders",
        (
            {
                "email": "river@example.test",
                "ship": "express",
                "country": "Spain",
                "wrap": "Box",
                "terms": "on",
                "new_password": "",
            },
        ),
    ),
    "booking": Task(
        "/booking.html",
        "Book one stay for 3 guests, checking in December 3, 2026 and checking out December 6, 2026. Leave the optional date of birth blank.",
        "/book",
        ({"checkin": "2026-12-03", "checkout": "06/12/2026", "guests": "3", "dob": ""},),
    ),
}


# Changes confined to the benchmark copy. Use the click's selected item as application state.
def page(path):
    html = (FIXTURES / path.lstrip("/")).read_text()
    if path == "/spa_product.html":
        html = html.replace(
            "cart.push(product);",
            "cart.push(product); fetch('/cart-add', {method:'POST',body:new URLSearchParams({item:product})});",
        )
    if path == "/catalog.html":
        html = html.replace(
            'button.textContent = "Added";',
            "fetch('/cart-add', {method:'POST',body:new URLSearchParams({item:button.closest('li').querySelector('h3').textContent})});\n      button.textContent = 'Added';",
        )
    if path == "/order_receipt.html":
        old = """    if (kind === "prompt") window.prompt("Also email a receipt?");
    else window.confirm("Also email a receipt?");"""
        new = """    const accepted = kind === "prompt"
      ? window.prompt("Also email a receipt?") !== null
      : window.confirm("Also email a receipt?");
    if (accepted) {
      const receipt = new XMLHttpRequest();
      receipt.open("POST", "/receipt", false);
      receipt.send(new URLSearchParams({emailReceipt:"yes"}));
    }"""
        assert old in html
        html = html.replace(old, new)
    return html


def score(task, writes):
    remaining = list(writes)
    missing = 0
    for expected in task.expected:
        for stored in remaining:
            if stored["path"] == task.endpoint and all(stored["form"].get(k) == v for k, v in expected.items()):
                remaining.remove(stored)
                break
        else:
            missing += 1
    return not missing and not remaining, len(remaining)
