"""Bambu digital preparation contracts; printer credentials stay on the local bridge."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool

from .contracts import WireModel

STUDIO_VERSION = "2.8.2.61"
MAX_PACKAGE_BYTES = 16 * 1024 * 1024


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
