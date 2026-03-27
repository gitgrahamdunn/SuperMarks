from __future__ import annotations

import logging

import pytest

pytest.importorskip('httpx')

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import db
from app.auth import APP_TOKEN_STORAGE_KEY, configured_oidc_providers, oidc_oauth_registry
from app.main import app
from app.settings import settings


def test_magic_link_request_and_verify_flow(tmp_path, monkeypatch, caplog) -> None:
    settings.data_dir = str(tmp_path / 'data')
    settings.sqlite_path = str(tmp_path / 'test.db')
    monkeypatch.delenv('BACKEND_API_KEY', raising=False)
    monkeypatch.setattr(settings, 'magic_link_login_enabled', True)
    monkeypatch.setattr(settings, 'email_provider', 'log')
    monkeypatch.setattr(settings, 'auth_allowed_return_origins', 'http://localhost:5173')
    monkeypatch.setattr(settings, 'oidc_providers_json', '')
    monkeypatch.setattr(settings, 'auth_session_secret', 'test-auth-secret')
    configured_oidc_providers.cache_clear()
    oidc_oauth_registry.cache_clear()

    db.engine = create_engine(settings.sqlite_url, connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client, caplog.at_level(logging.INFO):
        request_response = client.post(
            '/auth/magic-link/request',
            json={'email': 'Teacher@example.com', 'return_to': 'http://localhost:5173/auth/callback'},
        )
        assert request_response.status_code == 200
        assert request_response.json() == {'ok': True}

        log_message = next(
            record.getMessage()
            for record in caplog.records
            if 'Magic link for teacher@example.com:' in record.getMessage()
        )
        magic_link_url = log_message.split(': ', 1)[1]

        verify_response = client.get(magic_link_url, follow_redirects=False)
        assert verify_response.status_code == 302
        location = verify_response.headers['location']
        assert f'{APP_TOKEN_STORAGE_KEY}=' in location
        token = location.split(f'{APP_TOKEN_STORAGE_KEY}=', 1)[1]

        me_response = client.get('/auth/me', headers={'Authorization': f'Bearer {token}'})
        assert me_response.status_code == 200
        payload = me_response.json()
        assert payload['authenticated'] is True
        assert payload['auth_method'] == 'user'
        assert payload['user']['email'] == 'teacher@example.com'


def test_magic_link_cannot_be_reused(tmp_path, monkeypatch, caplog) -> None:
    settings.data_dir = str(tmp_path / 'data')
    settings.sqlite_path = str(tmp_path / 'test.db')
    monkeypatch.delenv('BACKEND_API_KEY', raising=False)
    monkeypatch.setattr(settings, 'magic_link_login_enabled', True)
    monkeypatch.setattr(settings, 'email_provider', 'log')
    monkeypatch.setattr(settings, 'auth_allowed_return_origins', 'http://localhost:5173')
    monkeypatch.setattr(settings, 'oidc_providers_json', '')
    monkeypatch.setattr(settings, 'auth_session_secret', 'test-auth-secret')
    configured_oidc_providers.cache_clear()
    oidc_oauth_registry.cache_clear()

    db.engine = create_engine(settings.sqlite_url, connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client, caplog.at_level(logging.INFO):
        client.post(
            '/auth/magic-link/request',
            json={'email': 'Teacher@example.com', 'return_to': 'http://localhost:5173/auth/callback'},
        )
        log_message = next(
            record.getMessage()
            for record in caplog.records
            if 'Magic link for teacher@example.com:' in record.getMessage()
        )
        magic_link_url = log_message.split(': ', 1)[1]

        first = client.get(magic_link_url, follow_redirects=False)
        assert first.status_code == 302

        second = client.get(magic_link_url, follow_redirects=False)
        assert second.status_code == 400
