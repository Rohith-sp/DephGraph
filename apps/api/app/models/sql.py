"""SQLAlchemy 2.0 ORM mirroring migrations/001_initial_schema.sql (23 tables)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _fk(target: str, cascade: str = "CASCADE") -> ForeignKey:
    return ForeignKey(target, ondelete=cascade)


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "team"
    team_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = _now()


class Service(Base):
    __tablename__ = "service"
    __table_args__ = (UniqueConstraint("team_id", "name"),)
    service_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(BigInteger, _fk("team.team_id"))
    name: Mapped[str] = mapped_column(String(120))
    repo_url: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()


class Package(Base):
    __tablename__ = "package"
    __table_args__ = (UniqueConstraint("ecosystem", "name"),)
    package_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ecosystem: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Maintainer(Base):
    __tablename__ = "maintainer"
    __table_args__ = (UniqueConstraint("platform", "username"),)
    maintainer_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(40), server_default="github")
    profile_url: Mapped[str | None] = mapped_column(String(500))


class SemanticCluster(Base):
    __tablename__ = "semantic_cluster"
    cluster_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(120))
    centroid_vec: Mapped[Any | None] = mapped_column(JSONB)
    member_count: Mapped[int] = mapped_column(Integer, server_default="0")
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    created_at: Mapped[datetime] = _now()


class Vulnerability(Base):
    __tablename__ = "vulnerability"
    vuln_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    osv_id: Mapped[str] = mapped_column(String(40), unique=True)
    severity: Mapped[str | None] = mapped_column(String(20))
    cvss_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    title: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class License(Base):
    __tablename__ = "license"
    license_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    spdx_id: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    is_copyleft: Mapped[bool] = mapped_column(Boolean, server_default="false")


class RemediationPlan(Base):
    __tablename__ = "remediation_plan"
    plan_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(BigInteger, _fk("team.team_id", "RESTRICT"))
    title: Mapped[str | None] = mapped_column(String(255))
    coverage_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default="0")
    created_at: Mapped[datetime] = _now()
    status: Mapped[str] = mapped_column(String(20), server_default="DRAFT")


class ScanRun(Base):
    __tablename__ = "scan_run"
    scan_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    service_id: Mapped[int] = mapped_column(BigInteger, _fk("service.service_id"))
    started_at: Mapped[datetime] = _now()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(20), server_default="manual")


class Deployment(Base):
    __tablename__ = "deployment"
    service_id: Mapped[int] = mapped_column(
        BigInteger, _fk("service.service_id"), primary_key=True, autoincrement=False
    )
    deploy_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    environment: Mapped[str] = mapped_column(String(40), server_default="production")
    deployed_at: Mapped[datetime] = _now()


class PackageVersion(Base):
    __tablename__ = "package_version"
    package_id: Mapped[int] = mapped_column(
        BigInteger, _fk("package.package_id"), primary_key=True, autoincrement=False
    )
    version_string: Mapped[str] = mapped_column(String(100), primary_key=True)
    license_id: Mapped[int | None] = mapped_column(
        BigInteger, _fk("license.license_id", "SET NULL")
    )
    cluster_id: Mapped[int | None] = mapped_column(
        BigInteger, _fk("semantic_cluster.cluster_id", "SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_yanked: Mapped[bool] = mapped_column(Boolean, server_default="false")


class RemediationAction(Base):
    __tablename__ = "remediation_action"
    plan_id: Mapped[int] = mapped_column(
        BigInteger,
        _fk("remediation_plan.plan_id"),
        primary_key=True,
        autoincrement=False,
    )
    action_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default="PENDING")


class PrReview(Base):
    __tablename__ = "pr_review"
    service_id: Mapped[int] = mapped_column(
        BigInteger, _fk("service.service_id"), primary_key=True, autoincrement=False
    )
    pr_number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    pr_url: Mapped[str | None] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(120))
    opened_at: Mapped[datetime] = _now()


class PrRiskVerdict(Base):
    __tablename__ = "pr_risk_verdict"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service_id", "pr_number"],
            ["pr_review.service_id", "pr_review.pr_number"],
            ondelete="CASCADE",
        ),
    )
    service_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    pr_number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    dep_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    risk_level: Mapped[str] = mapped_column(String(20))
    stage_reached: Mapped[int] = mapped_column(Integer)
    context_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = _now()


class MaintainedBy(Base):
    __tablename__ = "maintained_by"
    package_id: Mapped[int] = mapped_column(
        BigInteger, _fk("package.package_id"), primary_key=True, autoincrement=False
    )
    maintainer_id: Mapped[int] = mapped_column(
        BigInteger,
        _fk("maintainer.maintainer_id"),
        primary_key=True,
        autoincrement=False,
    )
    role: Mapped[str] = mapped_column(String(40), server_default="owner")


class ResolvesTo(Base):
    __tablename__ = "resolves_to"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service_id", "deploy_seq"],
            ["deployment.service_id", "deployment.deploy_seq"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["package_id", "version_string"],
            ["package_version.package_id", "package_version.version_string"],
            ondelete="CASCADE",
        ),
    )
    service_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    deploy_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    package_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    version_string: Mapped[str] = mapped_column(String(100), primary_key=True)
    depth: Mapped[int] = mapped_column(Integer, server_default="0")
    is_direct: Mapped[bool] = mapped_column(Boolean, server_default="false")


class DependsOn(Base):
    __tablename__ = "depends_on"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dep_package_id", "dep_version_string"],
            ["package_version.package_id", "package_version.version_string"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["ref_package_id", "ref_version_string"],
            ["package_version.package_id", "package_version.version_string"],
            ondelete="CASCADE",
        ),
    )
    dep_package_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    dep_version_string: Mapped[str] = mapped_column(String(100), primary_key=True)
    ref_package_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ref_version_string: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope: Mapped[str | None] = mapped_column(String(20))


class Affects(Base):
    __tablename__ = "affects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["package_id", "version_string"],
            ["package_version.package_id", "package_version.version_string"],
            ondelete="CASCADE",
        ),
    )
    package_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    version_string: Mapped[str] = mapped_column(String(100), primary_key=True)
    vuln_id: Mapped[int] = mapped_column(
        BigInteger, _fk("vulnerability.vuln_id"), primary_key=True, autoincrement=False
    )
    fixed_in: Mapped[str | None] = mapped_column(String(100))


class Resolves(Base):
    __tablename__ = "resolves"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "action_seq"],
            ["remediation_action.plan_id", "remediation_action.action_seq"],
            ondelete="CASCADE",
        ),
    )
    plan_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    action_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    vuln_id: Mapped[int] = mapped_column(
        BigInteger, _fk("vulnerability.vuln_id"), primary_key=True, autoincrement=False
    )


class WaivedFor(Base):
    __tablename__ = "waived_for"
    vuln_id: Mapped[int] = mapped_column(
        BigInteger, _fk("vulnerability.vuln_id"), primary_key=True, autoincrement=False
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, _fk("service.service_id"), primary_key=True, autoincrement=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    expires_on: Mapped[date | None] = mapped_column(Date)


class Observes(Base):
    __tablename__ = "observes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service_id", "deploy_seq"],
            ["deployment.service_id", "deployment.deploy_seq"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["package_id", "version_string"],
            ["package_version.package_id", "package_version.version_string"],
            ondelete="CASCADE",
        ),
    )
    service_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    deploy_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    scan_id: Mapped[int] = mapped_column(
        BigInteger, _fk("scan_run.scan_id"), primary_key=True, autoincrement=False
    )
    package_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    version_string: Mapped[str] = mapped_column(String(100), primary_key=True)
    exec_count: Mapped[int] = mapped_column(Integer, server_default="0")
    is_live: Mapped[bool] = mapped_column(Boolean, server_default="false")


class PackageAlias(Base):
    __tablename__ = "package_alias"
    package_id: Mapped[int] = mapped_column(
        BigInteger, _fk("package.package_id"), primary_key=True, autoincrement=False
    )
    alias: Mapped[str] = mapped_column(String(255), primary_key=True)


class MaintainerEmail(Base):
    __tablename__ = "maintainer_email"
    maintainer_id: Mapped[int] = mapped_column(
        BigInteger,
        _fk("maintainer.maintainer_id"),
        primary_key=True,
        autoincrement=False,
    )
    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default="false")
