"""AccountService: the one account (from the environment) and the three predefined roles.

Read-only: accounts come from REDFISH_USERNAME / REDFISH_PASSWORD, so there is nothing to
create, change or delete here, and every write answers 405. The roles are DSP0266's
predefined ones with exactly the privileges the spec assigns them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import require_auth
from ..errors import RedfishError
from ..odata import ROOT, collection, link, resource
from ..protocol import RedfishJSONResponse, with_etag

SERVICE = f"{ROOT}/AccountService"
ACCOUNTS = f"{SERVICE}/Accounts"
ROLES = f"{SERVICE}/Roles"
ACCOUNT_ID = "1"  # the only account

# DSP0266 predefined roles -> their privileges (Privileges.v1_x "PrivilegeType").
ROLE_PRIVILEGES: dict[str, list[str]] = {
    "Administrator": [
        "Login",
        "ConfigureManager",
        "ConfigureUsers",
        "ConfigureComponents",
        "ConfigureSelf",
    ],
    "Operator": ["Login", "ConfigureComponents", "ConfigureSelf"],
    "ReadOnly": ["Login", "ConfigureSelf"],
}
ACCOUNT_ROLE = "Administrator"  # the single account can do everything the API offers

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get(SERVICE)
def account_service() -> dict[str, Any]:
    return resource(
        "AccountService",
        SERVICE,
        "AccountService",
        "Account Service",
        ServiceEnabled=True,
        Status={"State": "Enabled", "Health": "OK"},
        Accounts=link(ACCOUNTS),
        Roles=link(ROLES),
    )


@router.get(ACCOUNTS)
def account_collection() -> dict[str, Any]:
    return collection(
        "ManagerAccountCollection", ACCOUNTS, "Accounts", [f"{ACCOUNTS}/{ACCOUNT_ID}"]
    )


@router.get(f"{ACCOUNTS}/{{account_id}}")
def account(account_id: str, request: Request) -> RedfishJSONResponse:
    if account_id != ACCOUNT_ID:
        raise RedfishError(404, "ResourceNotFound", "ManagerAccount", account_id)
    # DSP0266: a ManagerAccount always carries an ETag, so a client can write it safely.
    body = resource(
        "ManagerAccount",
        f"{ACCOUNTS}/{ACCOUNT_ID}",
        ACCOUNT_ID,
        "User Account",
        UserName=request.app.state.settings.username,
        Password=None,  # the spec requires null on read
        RoleId=ACCOUNT_ROLE,
        Enabled=True,
        Locked=False,
        AccountTypes=["Redfish"],
        Links={"Role": link(f"{ROLES}/{ACCOUNT_ROLE}")},
    )
    return with_etag(body)


@router.get(ROLES)
def role_collection() -> dict[str, Any]:
    return collection("RoleCollection", ROLES, "Roles", [f"{ROLES}/{r}" for r in ROLE_PRIVILEGES])


@router.get(f"{ROLES}/{{role_id}}")
def role(role_id: str) -> dict[str, Any]:
    if role_id not in ROLE_PRIVILEGES:
        raise RedfishError(404, "ResourceNotFound", "Role", role_id)
    return resource(
        "Role",
        f"{ROLES}/{role_id}",
        role_id,
        f"{role_id} Role",
        RoleId=role_id,
        IsPredefined=True,
        AssignedPrivileges=ROLE_PRIVILEGES[role_id],
    )
