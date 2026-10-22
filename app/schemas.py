from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ModelConfig(Strict):
    name: str = Field(min_length=1, max_length=100)
    provider: Literal['mock', 'openai'] = 'mock'
    base_url: str = Field(default='', max_length=2048)
    model_id: str = Field(default='mock-demo', min_length=1, max_length=200)
    api_key: str = Field(default='', max_length=4096)
    api_key_env: str = Field(default='', max_length=100)
    is_aggregator: bool  # Explicit declaration, no implicit assumption.
    temperature: float = Field(default=0, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=1024, ge=1, le=16384)


class PromptConfig(Strict):
    name: str = Field(min_length=1, max_length=100)
    system: str = Field(default='', max_length=20000)
    initial: str = Field(default='', max_length=20000)
    review: str = Field(default='请检查上一轮回答并给出最终选项。', max_length=20000)
    default_mode: Literal['single_pass', 'self_refine', 'majority_vote'] = 'single_pass'
    default_calls: int = Field(default=3, ge=1, le=100)
    early_stop: bool = False


class RunConfig(Strict):
    name: str = Field(min_length=1, max_length=100)
    model_id: str
    prompt_id: str
    mode: Literal['single_pass', 'self_refine', 'majority_vote'] = 'single_pass'
    calls: int = Field(default=3, ge=1, le=100)
    early_stop: bool = False
    official: bool = False
    dimension: Literal['L2_main', 'L3_main', 'L1_sub', 'L2_sub'] | None = None
    question_ids: list[str] = Field(default_factory=list, max_length=2000)
    limit: int | None = Field(default=None, ge=1, le=10000)


class Limits(Strict):
    max_calls_per_question: int = Field(ge=1, le=100)
    timeout_per_question_seconds: int = Field(ge=1, le=3600)


class ImageMapping(Strict):
    replay_id: str
    image_step: int = Field(ge=0)
    image_id: str


class InfraMark(Strict):
    question_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=5, max_length=1000)
