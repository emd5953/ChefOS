"""Pure-Python Square provider for ChefOS (no FastAPI/SQLAlchemy/Temporal/DB dependency)."""

from app.integrations.providers.square.business_date import business_date
from app.integrations.providers.square.client import (
    SquareAuthError,
    SquareClient,
    SquareClientError,
    SquareError,
    SquareRateLimited,
    SquareTransientError,
)
from app.integrations.providers.square.fake_client import FakeSquareClient
from app.integrations.providers.square.http_client import HttpSquareClient
from app.integrations.providers.square.normalizer import (
    NORMALIZER_VERSION,
    OrderNormalization,
    TimecardNormalization,
    normalize_catalog_object,
    normalize_order,
    normalize_team_member,
    normalize_timecard,
)
from app.integrations.providers.square.types import (
    CanonicalMenuCategory,
    CanonicalMenuItem,
    CanonicalMenuVariant,
    CanonicalReturn,
    CanonicalSalesLine,
    CanonicalSalesOrder,
    CanonicalStaff,
    CanonicalTimecard,
    Channel,
    Credentials,
    MappingContext,
    MappingIssueSignal,
    Page,
    RawItem,
    WebhookEvent,
    Window,
)
from app.integrations.providers.square.webhooks import (
    WebhookParseError,
    parse_event,
    verify_signature,
)

__all__ = [
    "NORMALIZER_VERSION",
    "CanonicalMenuCategory",
    "CanonicalMenuItem",
    "CanonicalMenuVariant",
    "CanonicalReturn",
    "CanonicalSalesLine",
    "CanonicalSalesOrder",
    "CanonicalStaff",
    "CanonicalTimecard",
    "Channel",
    "Credentials",
    "FakeSquareClient",
    "HttpSquareClient",
    "MappingContext",
    "MappingIssueSignal",
    "OrderNormalization",
    "Page",
    "RawItem",
    "SquareAuthError",
    "SquareClient",
    "SquareClientError",
    "SquareError",
    "SquareRateLimited",
    "SquareTransientError",
    "TimecardNormalization",
    "WebhookEvent",
    "WebhookParseError",
    "Window",
    "business_date",
    "normalize_catalog_object",
    "normalize_order",
    "normalize_team_member",
    "normalize_timecard",
    "parse_event",
    "verify_signature",
]
