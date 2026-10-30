"""Small output contract for concept planning, separate from engineering code."""
import json

from pydantic import Field, field_validator, model_validator

from .contracts import ClarificationQuestion, ConceptScene, WireModel


class PlanningOutput(WireModel):
    summary: str = Field(min_length=1, max_length=1800)
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=6)
    questions: list[str] = Field(default_factory=list, max_length=6)
    requirements: dict = Field(default_factory=dict)
    concept_scene: ConceptScene
    research_required: bool = False
    sources: list[str] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("requirements")
    @classmethod
    def bounded_requirements(cls, value):
        if len(json.dumps(value)) > 16000:
            raise ValueError("Keep concept requirements compact; detailed engineering happens after approval")
        return value

    @model_validator(mode="after")
    def unique_question_ids(self):
        ids = [q.id for q in self.clarification_questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Planning question IDs must be unique")
        return self
