"""Span attribute names, as literals, in one place (ADR-007).

The ``gen_ai.*`` constants shipped in ``opentelemetry-semantic-conventions`` are marked deprecated
(the conventions moved to another repository and are still in *Development*), so we keep the
literals from the current specification here. If a name changes, this is the only file to edit.
"""

# --- OpenTelemetry GenAI conventions
GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_PROVIDER = "gen_ai.provider.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

OP_INVOKE_AGENT = "invoke_agent"
OP_CHAT = "chat"
OP_EXECUTE_TOOL = "execute_tool"

# --- OpenInference (what Phoenix uses for its views)
OI_SPAN_KIND = "openinference.span.kind"
OI_INPUT = "input.value"
OI_OUTPUT = "output.value"
OI_MODEL = "llm.model_name"
OI_PROVIDER = "llm.provider"
OI_TOKENS_PROMPT = "llm.token_count.prompt"
OI_TOKENS_COMPLETION = "llm.token_count.completion"
OI_TOKENS_TOTAL = "llm.token_count.total"
OI_TOOL_NAME = "tool.name"
OI_TOOL_PARAMETERS = "tool.parameters"
KIND_AGENT, KIND_LLM, KIND_TOOL = "AGENT", "LLM", "TOOL"

# --- ours
LB_PROFILE = "lb.profile"
LB_ROLE = "lb.role"
LB_ATTEMPT = "lb.attempt"
LB_ESCALATED = "lb.escalated"
LB_STATUS = "lb.status"
LB_ABSTAIN_REASON = "lb.abstain_reason"
LB_CONFIDENCE = "lb.confidence"
LB_VERIFIER_AGREEMENT = "lb.verifier_agreement"
LB_DEFECTS_TOUCHED = "lb.defects_touched"
LB_ROW_COUNT = "lb.row_count"
LB_TRUNCATED = "lb.truncated"
LB_SWAR_RISK = "lb.swar_risk"
LB_SWAR_LEVEL = "lb.swar_level"
LB_SWAR_SIGNALS = "lb.swar_signals"
LB_EARLY_WARNING = "lb.early_warning"
LB_TOOL_OK = "lb.tool.ok"
LB_TOOL_STAGE = "lb.tool.stage"
LB_LLM_RETRIES = "lb.llm.retries"
LB_REQUEST_ID = "lb.request_id"
