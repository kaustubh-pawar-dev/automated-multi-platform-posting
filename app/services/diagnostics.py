"""Platform configuration diagnostics.

Reports which platforms can publish and which environment variables are missing.
Only variable *names* are ever reported, never values.
"""

from app.core.config import Settings
from app.platforms.registry import PlatformRegistry
from app.schemas.health import PlatformsResponse, PlatformStatus

# Required settings per platform. A test verifies this covers every registered
# adapter, so adding a platform without listing it here fails the test suite.
PLATFORM_SETTINGS: dict[str, tuple[str, ...]] = {
    "Mastodon": ("mastodon_instance_url", "mastodon_access_token"),
    "Bluesky": ("bluesky_handle", "bluesky_app_password"),
    "Telegram": ("telegram_bot_token", "telegram_chat_id"),
    "LinkedIn": ("linkedin_access_token", "linkedin_person_urn"),
    "Discord": ("discord_webhook_url",),
}


def build_platforms_report(registry: PlatformRegistry, settings: Settings) -> PlatformsResponse:
    """Describe the configuration state of every registered platform."""
    statuses: list[PlatformStatus] = []
    for adapter in registry.all():
        required = PLATFORM_SETTINGS.get(adapter.name, ())
        missing = [name.upper() for name in required if getattr(settings, name) is None]
        statuses.append(
            PlatformStatus(
                name=adapter.name,
                configured=adapter.is_configured(),
                missing_settings=missing,
                idempotent_retries=adapter.idempotent_retries,
            )
        )
    return PlatformsResponse(
        platforms=statuses,
        configured_count=sum(1 for status in statuses if status.configured),
        total_count=len(statuses),
        disabled_platforms=list(settings.disabled_platforms),
    )