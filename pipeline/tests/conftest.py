"""Every test runs the laptop server against a fresh temporary folder, so no test can create or change anything
under data/ (the real invoices, Pilot exports and database)."""
import pytest


@pytest.fixture(autouse=True)
def server_settings(tmp_path, monkeypatch):
    from app import context, db, settings
    s = settings.Settings(app_data=tmp_path / "app_data", pilot_dir=tmp_path / "pilot", ocr_cache=tmp_path / "ocr_cache")
    monkeypatch.setattr(settings, "_current", s)
    context.reset()
    yield s
    db.close_all()
    context.reset()


@pytest.fixture
def shop(server_settings):
    from serverkit import install_shops
    return install_shops(server_settings)


@pytest.fixture
def api_client(shop):
    from app.server import app
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def phone(api_client):
    from serverkit import Phone
    return Phone(api_client)


@pytest.fixture
def reader(shop):
    from serverkit import Reader
    return Reader(shop)
