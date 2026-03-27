from __future__ import annotations

import pytest

pytest.importorskip('httpx')

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import db
from app.auth import configured_oidc_providers, oidc_oauth_registry
from app.main import app
from app.settings import settings


def test_dev_login_flow(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / 'data')
    settings.sqlite_path = str(tmp_path / 'test.db')
    monkeypatch.delenv('BACKEND_API_KEY', raising=False)
    monkeypatch.setattr(settings, 'dev_login_enabled', True)
    monkeypatch.setattr(settings, 'dev_login_key', 'codex-secret')
    monkeypatch.setattr(settings, 'dev_login_email', 'codex-dev@supermarks.local')
    monkeypatch.setattr(settings, 'dev_login_name', 'Codex Dev')
    monkeypatch.setattr(settings, 'magic_link_login_enabled', False)
    monkeypatch.setattr(settings, 'auth_session_secret', 'test-auth-secret')
    monkeypatch.setattr(settings, 'oidc_providers_json', '')
    configured_oidc_providers.cache_clear()
    oidc_oauth_registry.cache_clear()

    db.engine = create_engine(settings.sqlite_url, connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        status_response = client.get('/auth/me')
        assert status_response.status_code == 200
        assert status_response.json()['dev_login_enabled'] is True

        login_response = client.post('/auth/dev-login', json={'key': 'codex-secret'})
        assert login_response.status_code == 200
        token = login_response.json()['token']

        me_response = client.get('/auth/me', headers={'Authorization': f'Bearer {token}'})
        assert me_response.status_code == 200
        payload = me_response.json()
        assert payload['authenticated'] is True
        assert payload['user']['email'] == 'codex-dev@supermarks.local'


def test_dev_login_rejects_wrong_key(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / 'data')
    settings.sqlite_path = str(tmp_path / 'test.db')
    monkeypatch.delenv('BACKEND_API_KEY', raising=False)
    monkeypatch.setattr(settings, 'dev_login_enabled', True)
    monkeypatch.setattr(settings, 'dev_login_key', 'codex-secret')
    monkeypatch.setattr(settings, 'auth_session_secret', 'test-auth-secret')
    monkeypatch.setattr(settings, 'oidc_providers_json', '')
    configured_oidc_providers.cache_clear()
    oidc_oauth_registry.cache_clear()

    db.engine = create_engine(settings.sqlite_url, connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        login_response = client.post('/auth/dev-login', json={'key': 'wrong-key'})
        assert login_response.status_code == 401
