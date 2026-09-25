"""OData / Redfish identifiers shared by every resource.

Schema versions are pinned to the DMTF Redfish-Publications 2026.2 bundle, which the
test suite validates responses against. Bump them together with that bundle.
"""

from __future__ import annotations

from typing import Any

REDFISH_VERSION = "1.15.0"
ROOT = "/redfish/v1"

# Resource type -> versioned "#Namespace.vX_Y_Z.Type" (collections are unversioned).
TYPES = {
    "ServiceRoot": "#ServiceRoot.v1_22_0.ServiceRoot",
    "ChassisCollection": "#ChassisCollection.ChassisCollection",
    "Chassis": "#Chassis.v1_29_0.Chassis",
    "SensorCollection": "#SensorCollection.SensorCollection",
    "Sensor": "#Sensor.v1_14_0.Sensor",
    "ComputerSystemCollection": "#ComputerSystemCollection.ComputerSystemCollection",
    "ComputerSystem": "#ComputerSystem.v1_29_0.ComputerSystem",
    "SessionService": "#SessionService.v1_2_0.SessionService",
    "SessionCollection": "#SessionCollection.SessionCollection",
    "Session": "#Session.v1_9_0.Session",
    "Message": "#Message.v1_4_0.Message",
}


def link(path: str) -> dict[str, str]:
    """A navigation property: {"@odata.id": path}."""
    return {"@odata.id": path}


def resource(type_name: str, path: str, id_: str, name: str, **props: Any) -> dict[str, Any]:
    """A singular resource with the common properties every Redfish resource carries."""
    return {
        "@odata.id": path,
        "@odata.type": TYPES[type_name],
        "Id": id_,
        "Name": name,
        **props,
    }


def collection(type_name: str, path: str, name: str, member_paths: list[str]) -> dict[str, Any]:
    return {
        "@odata.id": path,
        "@odata.type": TYPES[type_name],
        "Name": name,
        "Members": [link(p) for p in member_paths],
        "Members@odata.count": len(member_paths),
    }
