from __future__ import annotations

import logging

import httpx

from app.settings import settings

logger = logging.getLogger(__name__)


async def send_magic_link_email(*, email: str, magic_link_url: str) -> None:
    provider = settings.email_provider.strip().lower()
    subject = 'Your SuperMarks sign-in link'
    text_body = (
        'Use this link to sign in to SuperMarks:\n\n'
        f'{magic_link_url}\n\n'
        f'This link expires in {max(settings.magic_link_token_ttl_seconds // 60, 1)} minutes.'
    )
    html_body = (
        '<p>Use this link to sign in to SuperMarks:</p>'
        f'<p><a href="{magic_link_url}">Sign in to SuperMarks</a></p>'
        f'<p>This link expires in {max(settings.magic_link_token_ttl_seconds // 60, 1)} minutes.</p>'
    )

    if provider == 'log':
        logger.info('Magic link for %s: %s', email, magic_link_url)
        return

    if provider == 'resend':
        api_key = (settings.email_api_key or '').strip()
        from_address = (settings.email_from_address or '').strip()
        if not api_key or not from_address:
            raise RuntimeError('Resend email delivery requires SUPERMARKS_EMAIL_API_KEY and SUPERMARKS_EMAIL_FROM_ADDRESS')
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.email_base_url.rstrip('/')}/emails",
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'from': from_address,
                    'to': [email],
                    'subject': subject,
                    'text': text_body,
                    'html': html_body,
                },
            )
        response.raise_for_status()
        return

    raise RuntimeError(f'Unsupported email provider: {settings.email_provider}')
