import pytest
from st_msads_oci.config import Settings, DEFAULT_GOAL_NAMES, DEFAULT_INITIAL_WATERMARK


def make_settings(**over):
    base = dict(campaign_category="Paid Microsoft", goal_names=dict(DEFAULT_GOAL_NAMES),
                legacy_goal_names=("ServiceTitan Integrated Bookings - MS", "ServiceTitan Lead - MS"),
                initial_watermark=DEFAULT_INITIAL_WATERMARK,
                test_emails=("test.identity@example.com",), test_phones=("(555) 555-0199",),
                worker_url=None, tenant_id=None, notifications=())
    base.update(over)
    return Settings(**base)


@pytest.fixture
def settings():
    return make_settings()
