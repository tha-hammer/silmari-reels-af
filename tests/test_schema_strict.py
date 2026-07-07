"""Schema-strictness tests — every pydantic model used as an OpenAI/OpenRouter
`response_format` json_schema must satisfy strict mode: additionalProperties
false AND every property required. Would have caught the two schema bugs
without a live API call.
"""
import pytest

from reel_af.render.hooks import HookDraft, ImageMomentDraft, ImageMomentResponse


@pytest.mark.parametrize("model", [HookDraft, ImageMomentDraft, ImageMomentResponse])
def test_strict_structured_output_schema(model):
    js = model.model_json_schema()
    assert js.get("additionalProperties") is False, f"{model.__name__}: additionalProperties must be False"
    props = set(js.get("properties", {}))
    required = set(js.get("required", []))
    assert props == required, f"{model.__name__}: every property must be required (missing {props - required})"
