import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    StringConstraints,
    model_validator,
)

from app.routers.pr_gate import normalize_repo_url
from app.schemas.common import BigBodyId

Role = Literal["member", "admin"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Slug = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", min_length=2, max_length=80)
]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.ASCII)
_REPO = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+$", re.ASCII)
_ROOT = re.compile(r"^[\w.\-/]*$", re.ASCII)


def _email(v: str) -> str:
    if len(v) > 254 or not v.isascii() or not _EMAIL.match(v):
        raise ValueError("not a valid email address")
    return v.lower()


def _root_path(v: str) -> str:
    """Monorepo subdirectory: relative, no '..', no leading/trailing slash ('' = repo root)."""
    v = v.strip().strip("/")
    if len(v) > 200 or not _ROOT.match(v) or any(p in ("", ".", "..") for p in v.split("/") if v):
        raise ValueError("root_path must be a relative directory inside the repository")
    return v


def _repo_url(v: str) -> str:
    v = normalize_repo_url(v)  # same normal form as stored URLs and webhook payloads
    if not _REPO.match(v) or any(seg in (".", "..") for seg in v.split("/")[3:]):
        raise ValueError("repo_url must look like https://github.com/owner/repo")
    return v


Email = Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_email)]
RootPath = Annotated[str, AfterValidator(_root_path)]
RepoUrl = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=500), AfterValidator(_repo_url)
]


class TeamCreate(BaseModel):
    name: Name
    slug: Slug


class TeamOut(BaseModel):
    team_id: int
    name: str
    slug: str
    role: Role


class MemberOut(BaseModel):
    user_id: str
    email: str | None
    role: Role
    created_at: datetime


class MemberPatch(BaseModel):
    role: Role


class InviteCreate(BaseModel):
    email: Email
    role: Role = "member"


class InviteOut(BaseModel):
    email: str
    role: Role
    created_at: datetime
    expires_at: datetime


class ServiceCreate(BaseModel):
    team_id: BigBodyId
    repo_url: RepoUrl
    name: Name | None = None
    root_path: RootPath = ""


class ServicePatch(BaseModel):
    name: Name | None = None
    root_path: RootPath | None = None
    repo_url: RepoUrl | None = None

    @model_validator(mode="after")
    def not_empty(self) -> "ServicePatch":
        if self.name is None and self.root_path is None and self.repo_url is None:
            raise ValueError("nothing to change")
        return self


class ServiceBrief(BaseModel):
    service_id: int
    team_id: int
    name: str
    repo_url: str | None
    root_path: str
    # create / change of repo_url or root_path only: how the import of the default branch went
    # ("imported", "unchanged", "not_installed", "no_repo", "failed"); None = not attempted
    import_status: str | None = None


class TokenCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class TokenOut(BaseModel):
    token_id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenCreated(TokenOut):
    token: str  # shown once: only its hash is stored


class Done(BaseModel):
    ok: bool = True
