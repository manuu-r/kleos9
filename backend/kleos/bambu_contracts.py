"""Bambu digital preparation contracts; printer credentials stay on the local bridge."""

import ipaddress
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, StrictBool, field_validator, model_validator

from .contracts import WireModel

STUDIO_VERSION = "2.8.2.61"
MAX_PACKAGE_BYTES = 16 * 1024 * 1024


def private_printer_address(value):
    ip = ipaddress.ip_address(value)
    if not any(ip in ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("Use the printer's literal private IPv4 address")
    return str(ip)


class BambuAddressRequest(WireModel):
    address: str = Field(max_length=15)

    @field_validator("address")
    @classmethod
    def private_address(cls, value):
        return private_printer_address(value)


class BambuConnectionRequest(BambuAddressRequest):
    serial: str = Field(pattern=r"^[A-Za-z0-9_-]{6,64}$")
    access_code: SecretStr = Field(min_length=8, max_length=64)
    certificate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    certificate_verified: StrictBool
    model_id: str = Field(max_length=80)

    @model_validator(mode="after")
    def verified_identity(self):
        if not self.certificate_verified:
            raise ValueError("Verify the certificate identity before sending credentials")
        if not re.fullmatch(r"[A-Za-z0-9]{8,64}", self.access_code.get_secret_value()):
            raise ValueError("Invalid printer access code")
        if self.model_id not in {m["id"] for m in model_catalog()["models"]}:
            raise ValueError("Select a supported printer model")
        return self


class BambuCertificateView(WireModel):
    address: str
    certificate_sha256: str
    verified: bool = False


class BambuLocalState(WireModel):
    enabled: bool
    connected: bool = False
    status: str = "not_connected"
    message: str = "Local read-only connection is not configured"
    address: str | None = None
    serial: str | None = None
    model_id: str | None = None
    observation: dict = Field(default_factory=dict)
    credentials_stored: bool = False
    expires_after_seconds: int = 1800


def model_catalog():
    return json.loads(Path(__file__).with_name("bambu-models.json").read_text())


class BambuProfile(WireModel):
    printer_model_id: str = Field(min_length=1, max_length=80)
    nozzle_mm: float = Field(gt=0, le=1, allow_inf_nan=False)
    material: str = Field(min_length=1, max_length=80)
    studio_version: Literal["2.8.2.61"] = STUDIO_VERSION
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image: str = Field(pattern=r"^(sha256:[a-f0-9]{64}|[a-z0-9][A-Za-z0-9._/:-]*@sha256:[a-f0-9]{64})$")
    qualified: StrictBool = False


class PreparePrint(WireModel):
    release_id: str = Field(min_length=1, max_length=36)
    artifact_id: str = Field(min_length=1, max_length=36)
    profile_id: str = Field(min_length=1, max_length=100)
    expected_engineering_revision: int = Field(ge=1, strict=True)


class PreparedPrint(WireModel):
    artifact_id: str = Field(min_length=1, max_length=36)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BambuCatalogView(WireModel):
    studio_version: str
    models: list[dict]
    profiles: list[dict]
    limitations: list[str]


class PrintPreparationView(WireModel):
    id: str
    project_id: str
    engineering_revision: int
    status: str
    source_artifact_id: str
    source_sha256: str
    profile_id: str
    profile: BambuProfile
    prepared_artifact_id: str | None
    prepared_sha256: str | None
    measurements: dict
    physical_acceptance: bool = False


class PrintPreparationsView(WireModel):
    preparations: list[PrintPreparationView]
