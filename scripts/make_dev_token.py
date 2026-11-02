"""Print a dev Supabase-style JWT (HS256, aud=authenticated) signed with SUPABASE_JWT_SECRET."""
import time
import uuid

from _db import settings
from jose import jwt

now = int(time.time())
claims = {
    "sub": str(uuid.UUID(int=1)),
    "email": "dev@dephgraph.local",
    "role": "authenticated",
    "aud": "authenticated",
    "iat": now,
    "exp": now + 7 * 86400,
}
print(jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256"))
