"""Validated, project-specific criteria supplied with an audit request."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TECHNICAL_FIELD_TYPES = {
    "duration_seconds": "number",
    "file_size_bytes": "number",
    "width": "number",
    "height": "number",
    "frame_rate": "number",
    "has_audio": "boolean",
    "video_codec": "string",
    "format_name": "string",
    "video_bit_rate": "number",
    "container_bit_rate": "number",
    "audio_stream_count": "number",
}

TECHNICAL_OPERATORS = {
    "eq", "neq", "gt", "gte", "lt", "lte", "between", "in", "not_in"
}
NUMERIC_OPERATORS = {"gt", "gte", "lt", "lte", "between"}


class CriteriaRule(BaseModel):
    """One deterministic or AI-backed rule in a customer project rubric."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    type: Literal["technical", "visual", "semantic"]
    severity: Literal["critical", "warning", "info"] = "critical"
    field: str | None = Field(default=None, max_length=64)
    operator: str
    value: Any = None
    code: str | None = Field(default=None, min_length=2, max_length=96)
    description: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_rule_definition(self):
        if self.type == "technical":
            if self.field not in TECHNICAL_FIELD_TYPES:
                allowed = ", ".join(sorted(TECHNICAL_FIELD_TYPES))
                raise ValueError(f"technical field must be one of: {allowed}")
            if self.operator not in TECHNICAL_OPERATORS:
                raise ValueError(
                    "technical operator must be one of: "
                    + ", ".join(sorted(TECHNICAL_OPERATORS))
                )
            if self.code is not None:
                raise ValueError("technical rules use field, not code")

            field_type = TECHNICAL_FIELD_TYPES[self.field]
            if self.operator == "between":
                if (
                    not isinstance(self.value, list)
                    or len(self.value) != 2
                    or not all(_is_number(item) for item in self.value)
                ):
                    raise ValueError("between requires value as [minimum, maximum]")
                if self.value[0] > self.value[1]:
                    raise ValueError("between minimum cannot exceed maximum")
            elif self.operator in {"in", "not_in"}:
                if not isinstance(self.value, list) or not self.value:
                    raise ValueError("in/not_in requires a non-empty array value")
                self._validate_value_types(field_type, self.value)
            else:
                if self.value is None:
                    raise ValueError("technical rule requires value")
                self._validate_value_types(field_type, [self.value])
                if self.operator in NUMERIC_OPERATORS and field_type != "number":
                    raise ValueError(
                        f"{self.operator} can only be used with numeric fields"
                    )
            return self

        if self.field is not None:
            raise ValueError("visual and semantic rules use code, not field")
        if not self.code:
            raise ValueError("visual and semantic rules require code")

        if self.type == "visual":
            if self.operator not in {
                "finding_present", "finding_absent", "max_duration"
            }:
                raise ValueError(
                    "visual operator must be finding_present, finding_absent, or max_duration"
                )
            if self.operator == "max_duration":
                if not _is_number(self.value) or self.value <= 0:
                    raise ValueError("max_duration requires a positive numeric value")
            elif self.value is not None:
                raise ValueError("finding_present/finding_absent do not accept value")
            return self

        if self.operator not in {"defect_present", "defect_absent"}:
            raise ValueError("semantic operator must be defect_present or defect_absent")
        if self.value is not None:
            raise ValueError("semantic rules do not accept value")
        return self

    @staticmethod
    def _validate_value_types(field_type: str, values: list[Any]) -> None:
        if field_type == "number" and not all(_is_number(item) for item in values):
            raise ValueError("numeric technical fields require numeric values")
        if field_type == "boolean" and not all(
            isinstance(item, bool) for item in values
        ):
            raise ValueError("has_audio requires boolean values")
        if field_type == "string" and not all(
            isinstance(item, str) and item.strip() for item in values
        ):
            raise ValueError("text technical fields require non-empty strings")


class CriteriaSpec(BaseModel):
    """Versioned rule set attached to one audit/project."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Custom project", min_length=2, max_length=120)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    rules: list[CriteriaRule] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_rule_ids(self):
        identifiers = [rule.id for rule in self.rules]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("criteria rule IDs must be unique")
        return self


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
