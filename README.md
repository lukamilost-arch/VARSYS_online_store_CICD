# Fieldstore

A small Flask and SQLite storefront for a CI/CD verification project. Customers search the catalog, buy one product at a time through simulated checkout, and view their orders. Administrators edit prices and stock. Orders store the purchase price and deduct inventory in one database transaction.

## Run the demo

With Docker Compose

```sh
docker compose up --build --wait
```

Open http://localhost:8000. To remove the demo containers and their database, run `docker compose down`. The database stays inside the container during that container's lifetime.

With Python 3.12

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m flask --app app init-db --demo
python -m flask --app app run --port 8000
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`. The default local database is `instance/store.db`.

| Username | Role |
|---|---|
| alice | Customer |
| bob | Customer |
| admin | Inventory administrator |

All three accounts use the local demo password `fieldstore-demo`. Use demo accounts only in an isolated test environment. The Compose service binds its host port to loopback and uses Flask's development server. A random signing key is created each time the app starts unless `STORE_SECRET_KEY` is set. Set `STORE_HTTPS=1` when serving the app over HTTPS to enable secure session cookies.

## Verification

```sh
python -m pytest -q
curl --fail http://localhost:8000/health
```

The tests use an isolated temporary database. They check catalog search, order ownership, server-side pricing, inventory consistency, CSRF rejection, and role enforcement.

For the pipeline, use `app.py` as the Semgrep target, `requirements.txt` for dependency scanning, and the running Compose service for dynamic scanning. A scanner running on the Compose network can reach `http://store:8000`. Catalog and login pages are public. The authenticated pytest checks exercise protected flows. The Compose service can be removed after each workflow run so every run starts from the same seed data.

For a controlled regression demo, create a branch and temporarily remove the user constraint from the SQL query in `order_detail`, adjusting its parameters accordingly. Run pytest and observe Bob's request for Alice's order return 200 where the test expects 404. Restore the ownership constraint and rerun the tests.
