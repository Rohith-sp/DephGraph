"""Print a dev Supabase-style JWT (HS256, aud=authenticated) signed with SUPABASE_JWT_SECRET.

The default user (DEV_USER_ID) is a member of every seeded team; --user-id mints a token for any
other user, e.g. one added to a single team with add_team_member.py.
"""
import argparse
import time
import uuid

import jwt
from _db import DEV_USER_ID, settings

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--user-id", type=uuid.UUID, default=uuid.UUID(DEV_USER_ID))
args = parser.parse_args()

now = int(time.time())
claims = {
    "sub": str(args.user_id),
    "email": f"{args.user_id}@dephgraph.local",
    "role": "authenticated",
    "aud": "authenticated",
    "iat": now,
    "exp": now + 7 * 86400,
}
print(jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256"))
