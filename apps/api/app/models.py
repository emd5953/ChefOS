"""Model registry. Import every module's models here so Alembic autogenerate sees them.

When you add a module with tables, add one import line below.
"""

from app.audit import models as audit_models
from app.commerce import models as commerce_models
from app.identity import models as identity_models
from app.integrations import models as integrations_models
from app.inventory import models as inventory_models
from app.security import models as security_models
from app.team import models as team_models

__all__ = [
    "audit_models",
    "commerce_models",
    "identity_models",
    "integrations_models",
    "inventory_models",
    "security_models",
    "team_models",
]
