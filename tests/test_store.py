import re

import pytest

from app import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db")})
    result = app.test_cli_runner().invoke(args=["init-db", "--demo"])
    assert result.exit_code == 0, result.output
    return app


def csrf(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return re.search(rb'name="csrf" value="([^"]+)"', response.data)[1].decode()


def sign_in(client, username):
    response = client.post("/login", data={
        "csrf": csrf(client, "/login"),
        "username": username,
        "password": "fieldstore-demo",
    })
    assert response.status_code == 302
    assert "HttpOnly" in response.headers["Set-Cookie"]
    return client


def place_order(client, **fields):
    return client.post("/checkout/1", data={"csrf": csrf(client, "/checkout/1"), **fields})


def test_catalog_search_and_health(app):
    client = app.test_client()
    assert client.get("/health").json == {"status": "ok"}
    catalog = client.get("/", query_string={"q": "flask"})
    assert b"Steel Flask" in catalog.data
    assert b"Canvas Tote" not in catalog.data
    assert b"No products match" in client.get("/", query_string={"q": "' OR 1=1 --"}).data


def test_checkout_uses_server_price_and_enforces_order_ownership(app):
    alice = sign_in(app.test_client(), "alice")
    bob = sign_in(app.test_client(), "bob")
    response = place_order(alice, quantity=2, price=1)
    assert response.status_code == 303
    order_url = response.headers["Location"]
    assert "€48.00" in alice.get(order_url).get_data(as_text=True)
    assert order_url.encode() in alice.get("/orders").data
    assert bob.get(order_url).status_code == 404
    assert b"You have no orders yet" in bob.get("/orders").data
    assert b"10 in stock" in alice.get("/checkout/1").data


def test_checkout_rejects_missing_csrf_and_invalid_stock(app):
    alice = sign_in(app.test_client(), "alice")
    assert alice.post("/checkout/1", data={"quantity": 1}).status_code == 400
    assert place_order(alice, quantity=0).status_code == 400
    assert place_order(alice, quantity=13).status_code == 409
    assert b"12 in stock" in alice.get("/checkout/1").data
    assert b"You have no orders yet" in alice.get("/orders").data


def test_inventory_requires_admin_and_keeps_existing_order_price(app):
    alice = sign_in(app.test_client(), "alice")
    order_url = place_order(alice, quantity=1).headers["Location"]
    assert alice.get("/inventory").status_code == 403
    assert alice.post("/inventory/1", data={
        "csrf": csrf(alice, "/checkout/1"), "price": 4200, "stock": 4
    }).status_code == 403

    admin = sign_in(app.test_client(), "admin")
    response = admin.post("/inventory/1", data={
        "csrf": csrf(admin, "/inventory"), "price": 4200, "stock": 4
    })
    assert response.status_code == 303
    assert "€42.00" in alice.get("/checkout/1").get_data(as_text=True)
    assert b"4 in stock" in alice.get("/checkout/1").data
    assert "€24.00" in alice.get(order_url).get_data(as_text=True)
