from pydantic import BaseModel


class BulkRowError(BaseModel):
    code: str
    message: str


class BulkUserRow(BaseModel):
    line: int
    email: str | None = None
    full_name: str | None = None
    role: str | None = None
    status: str
    errors: list[BulkRowError] = []


class BulkValidationResponse(BaseModel):
    valid: bool
    total: int
    rows: list[BulkUserRow]


class BulkInvitationFailure(BaseModel):
    email: str
    line: int


class BulkConfirmResponse(BaseModel):
    created: int
    invitations_sent: int
    invitations_failed: list[BulkInvitationFailure]
