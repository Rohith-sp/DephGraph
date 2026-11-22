from typing import Literal

from pydantic import BaseModel

AlertSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class AlertSettingsOut(BaseModel):
    enabled: bool
    min_severity: AlertSeverity
    can_edit: bool  # the caller is an admin of the team


class AlertSettingsPatch(BaseModel):
    enabled: bool | None = None
    min_severity: AlertSeverity | None = None
