"""Reviewed human-task preparation; no hiring or physical acceptance authority."""

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from .contracts import WireModel


class HumanTaskLocation(WireModel):
    city: str = Field(default="", max_length=100)
    country: str = Field(default="", max_length=2, pattern=r"^([A-Z]{2})?$")
    is_remote_allowed: StrictBool = True

    @model_validator(mode="after")
    def physical_location(self):
        if not self.is_remote_allowed and (not self.city.strip() or not self.country):
            raise ValueError("In-person tasks need a city and country")
        return self


class HumanTaskSpec(WireModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=5000)
    completion_criteria: str = Field(min_length=10, max_length=2000)
    price: float = Field(gt=0, le=10000, allow_inf_nan=False)
    price_type: Literal["fixed"] = "fixed"
    estimated_hours: float = Field(ge=0.083, le=168, allow_inf_nan=False)
    category: Literal["labor-gigs", "computer-gigs", "creative-gigs"] = "labor-gigs"
    skills_needed: list[str] = Field(default_factory=list, max_length=8)
    evidence_types: list[Literal["text", "photo", "video", "link"]] = Field(min_length=1, max_length=4)
    location: HumanTaskLocation = Field(default_factory=HumanTaskLocation)

    @model_validator(mode="after")
    def bounded_text(self):
        if (not self.title.strip() or len(self.description.strip()) < 10
                or len(self.completion_criteria.strip()) < 10
                or any(not s.strip() or len(s) > 80 for s in self.skills_needed)):
            raise ValueError("Use bounded nonempty task details and skills")
        return self


class HumanTaskDraftRequest(WireModel):
    release_id: str = Field(min_length=1, max_length=36)
    expected_engineering_revision: int = Field(ge=1, strict=True)
    task: HumanTaskSpec


class HumanTaskDraftAction(WireModel):
    draft_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class HumanTaskLinkRequest(HumanTaskDraftAction):
    bounty_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


class RentAHumanStatusView(WireModel):
    provider: str = "rentahuman.ai"
    account_configured: bool
    live_posting_enabled: bool = False
    capabilities: list[str]
    setup_url: str = "https://rentahuman.ai/account/api-keys"
    limitations: list[str]


class HumanSearchView(WireModel):
    humans: list[dict]
    has_more: bool
    next_cursor: str | None
    source: str = "rentahuman.ai"


class HumanBountiesView(WireModel):
    bounties: list[dict]
    has_more: bool
    next_cursor: str | None


class HumanTaskDraftView(WireModel):
    id: str
    project_id: str
    engineering_revision: int
    release_id: str
    status: str
    draft_sha256: str
    task: HumanTaskSpec
    preview: dict
    observation: dict
    bounty_id: str | None
    physical_acceptance: bool = False


class HumanTaskDraftsView(WireModel):
    drafts: list[HumanTaskDraftView]
