"""Schemathesis fuzzing of every API operation against the live OpenAPI schema.
Checks: no 5xx on any input, documented status codes/content types, schema-conformant
responses, valid data accepted and invalid data rejected. Framework-level HTTP
pedantry (OPTIONS ``Allow`` headers, auth, headers) is excluded: it is not platform behavior."""

import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis import checks

from oss_lab.core.api import create_app
from oss_lab.core.stack import Stack

checks.load_all_checks()
_SKIP = {
    "allow_header_conformance",
    "unsupported_method",
    "ignored_auth",
    "missing_required_header",
}
EXCLUDED = [c for c in checks.CHECKS.get_all() if c.__name__ in _SKIP]
schema = schemathesis.openapi.from_asgi("/openapi.json", create_app(Stack()))


@schema.parametrize()
@settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
def test_api_never_5xx_and_matches_schema(case):
    case.call_and_validate(excluded_checks=EXCLUDED)
