"""scripts/demo.py must not TRUNCATE a non-local database without --force."""

import demo
import pytest


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@localhost:5432/d",
        "postgresql://u:p@127.0.0.1:54322/postgres",
        "postgresql://u:p@[::1]:5432/d",
        "postgresql://u:p@postgres:5432/d",
    ],
)
def test_local_hosts_allowed(dsn):
    assert demo.reset_allowed(dsn, False)


def test_remote_refused_unless_forced():
    dsn = "postgresql://u:p@aws-0-eu.pooler.supabase.com:6543/postgres"
    assert not demo.reset_allowed(dsn, False)
    assert demo.reset_allowed(dsn, True)
