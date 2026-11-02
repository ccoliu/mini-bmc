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
    "TaskService": "#TaskService.v1_3_0.TaskService",
    "TaskCollection": "#TaskCollection.TaskCollection",
    "Task": "#Task.v1_7_4.Task",
    "Message": "#Message.v1_4_0.Message",
    "UpdateService": "#UpdateService.v1_17_1.UpdateService",
    "SoftwareInventoryCollection": "#SoftwareInventoryCollection.SoftwareInventoryCollection",
    "SoftwareInventory": "#SoftwareInventory.v1_15_0.SoftwareInventory",
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


def registry_message(
    registry: str, messages: dict[str, tuple[str, str]], key: str, *args: str
) -> dict[str, Any]:
    """A Message object from a message registry, with %1..%n filled in from args.
    `registry` is "Prefix.Major.Minor", e.g. "Base.1.24"; `messages` maps each key to
    its (template, severity) as published in that registry."""
    template, severity = messages[key]
    text = template
    for i, arg in enumerate(args, start=1):
        text = text.replace(f"%{i}", arg)
    return {
        "@odata.type": TYPES["Message"],
        "MessageId": f"{registry}.{key}",
        "Message": text,
        "MessageArgs": list(args),
        "MessageSeverity": severity,
    }
