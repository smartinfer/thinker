package thinker

// Captured-wire tests for the explicit token-limit route capability
// (token_limit_field), upstreamed from Dolphin's vendored 0.4+tlf1 patch.

import (
	"encoding/json"
	"strings"
	"testing"
)

func capabilityPacket(extra Packet) Packet {
	packet := Packet{
		"intent": "chat",
		"messages": []any{
			Packet{"role": "user", "parts": []any{Packet{"type": "text", "text": "repair the file"}}},
		},
		"instructions": Packet{"max_output_tokens": float64(4096)},
	}
	for k, v := range extra {
		packet[k] = v
	}
	return packet
}

func openAIRoute(field string) Route {
	return Route{CallID: "builder", Provider: "openai", ModelID: "gpt-5.1", Kind: "chat",
		Adapter: "openai", PayloadStyle: "chat_completions_v1",
		Endpoint: "https://api.openai.com/v1/chat/completions", TokenLimitField: field}
}

func geminiCompatRoute() Route {
	return Route{CallID: "builder", Provider: "gemini", ModelID: "gemini-2.5-flash", Kind: "chat",
		Adapter: "openai", PayloadStyle: "chat_completions_v1",
		Endpoint: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"}
}

func TestOpenAIRouteEmitsMaxCompletionTokensOnly(t *testing.T) {
	_, _, body, err := prepare(capabilityPacket(nil), openAIRoute("max_completion_tokens"))
	if err != nil {
		t.Fatal(err)
	}
	if body["max_completion_tokens"] != float64(4096) {
		t.Fatalf("max_completion_tokens must carry the unchanged cap: %v", body["max_completion_tokens"])
	}
	if _, present := body["max_tokens"]; present {
		t.Fatal("max_tokens must be absent — never emit both")
	}
}

func TestGeminiCompatRoutePayloadPreserved(t *testing.T) {
	_, endpoint, body, err := prepare(capabilityPacket(nil), geminiCompatRoute())
	if err != nil {
		t.Fatal(err)
	}
	// Captured prior-behavior payload: identical keys and values as before the
	// capability existed.
	if body["max_tokens"] != float64(4096) {
		t.Fatalf("gemini-compat route must keep max_tokens: %v", body["max_tokens"])
	}
	if _, present := body["max_completion_tokens"]; present {
		t.Fatal("gemini-compat route must not gain max_completion_tokens")
	}
	if body["model"] != "gemini-2.5-flash" || body["stream"] != false {
		t.Fatalf("prior payload fields changed: %v", body)
	}
	if len(array(body["messages"])) != 1 {
		t.Fatalf("prior transcript rendering changed: %v", body["messages"])
	}
	if !strings.HasSuffix(endpoint, "/openai/chat/completions") {
		t.Fatalf("prior endpoint resolution changed: %s", endpoint)
	}
}

func TestConflictingTokenLimitOverridesRejected(t *testing.T) {
	for _, key := range []string{"max_tokens", "max_completion_tokens", "max_output_tokens"} {
		for _, route := range []Route{openAIRoute("max_completion_tokens"), geminiCompatRoute()} {
			packet := capabilityPacket(Packet{"provider_options": Packet{key: float64(100)}})
			_, _, _, err := prepare(packet, route)
			if err == nil || !strings.Contains(err.Error(), "cannot override the token limit") {
				t.Fatalf("provider_options %s on %s route must be rejected clearly, got %v", key, route.Provider, err)
			}
		}
	}
}

func TestTokenLimitFieldRejectedWhereUnverified(t *testing.T) {
	responses := openAIRoute("max_completion_tokens")
	responses.PayloadStyle = "responses_v1"
	responses.Endpoint = "https://api.openai.com/v1/responses"
	if _, _, _, err := prepare(capabilityPacket(nil), responses); err == nil ||
		!strings.Contains(err.Error(), "only verified for Chat Completions") {
		t.Fatalf("responses route must reject the chat-only capability, got %v", err)
	}
	unknown := openAIRoute("max_new_tokens")
	if _, _, _, err := prepare(capabilityPacket(nil), unknown); err == nil ||
		!strings.Contains(err.Error(), "unsupported route token_limit_field") {
		t.Fatalf("unknown capability value must be rejected, got %v", err)
	}
	anthropic := Route{CallID: "builder", Provider: "anthropic", ModelID: "m", Kind: "chat",
		Adapter: "anthropic", PayloadStyle: "messages_v1",
		Endpoint: "https://api.anthropic.com/v1/messages", TokenLimitField: "max_completion_tokens"}
	if _, _, _, err := prepare(capabilityPacket(nil), anthropic); err == nil ||
		!strings.Contains(err.Error(), "not verified for this protocol") {
		t.Fatalf("anthropic route must reject the capability, got %v", err)
	}
}

// Portable reasoning: routing.reasoning {"effort": X} maps to each protocol's
// native field; any other reasoning object is native and passes through.

func protocolRoute(style string) Route {
	r := Route{CallID: "builder", ModelID: "m", Kind: "chat", PayloadStyle: style}
	switch style {
	case "chat_completions_v1":
		r.Provider, r.Adapter, r.Endpoint = "openai", "openai", "https://api.openai.com/v1/chat/completions"
	case "responses_v1":
		r.Provider, r.Adapter, r.Endpoint = "openai", "openai", "https://api.openai.com/v1/responses"
	case "messages_v1":
		r.Provider, r.Adapter, r.Endpoint = "anthropic", "anthropic", "https://api.anthropic.com/v1/messages"
	case "generateContent_v1beta":
		r.Provider, r.Adapter, r.Endpoint = "gemini", "gemini", "https://generativelanguage.googleapis.com"
	}
	return r
}

func reasoningPacket(reasoning Packet) Packet {
	return capabilityPacket(Packet{"routing": Packet{"reasoning": reasoning}})
}

func TestPortableEffortNoneMapsPerProtocol(t *testing.T) {
	none := Packet{"effort": "none"}
	_, _, chat, err := prepare(reasoningPacket(none), protocolRoute("chat_completions_v1"))
	if err != nil || chat["reasoning_effort"] != "none" || has(chat, "reasoning") {
		t.Fatalf("chat must use top-level reasoning_effort only: %v %v", chat, err)
	}
	_, _, responses, err := prepare(reasoningPacket(none), protocolRoute("responses_v1"))
	if err != nil || str(object(responses["reasoning"])["effort"]) != "none" || has(responses, "reasoning_effort") {
		t.Fatalf("responses must use reasoning.effort: %v %v", responses, err)
	}
	_, _, anthropic, err := prepare(reasoningPacket(none), protocolRoute("messages_v1"))
	if err != nil || str(object(anthropic["thinking"])["type"]) != "disabled" || len(object(anthropic["thinking"])) != 1 {
		t.Fatalf("anthropic effort none must disable thinking explicitly: %v %v", anthropic, err)
	}
	_, _, gemini, err := prepare(reasoningPacket(none), protocolRoute("generateContent_v1beta"))
	config := object(object(gemini["generationConfig"])["thinkingConfig"])
	if err != nil || len(config) != 1 || config["thinkingBudget"] != 0 {
		t.Fatalf("gemini effort none must set thinkingBudget 0: %v %v", gemini, err)
	}
}

func TestPortableEffortOtherThanNoneRejectedWithoutNativeBudget(t *testing.T) {
	for _, style := range []string{"messages_v1", "generateContent_v1beta"} {
		if _, _, _, err := prepare(reasoningPacket(Packet{"effort": "high"}), protocolRoute(style)); err == nil ||
			!strings.Contains(err.Error(), "only effort none is portable") {
			t.Fatalf("%s must reject a portable non-none effort, got %v", style, err)
		}
	}
	_, _, chat, err := prepare(reasoningPacket(Packet{"effort": "high"}), protocolRoute("chat_completions_v1"))
	if err != nil || chat["reasoning_effort"] != "high" {
		t.Fatalf("chat maps any portable effort: %v %v", chat, err)
	}
}

func TestNativeReasoningPassesThroughUnchanged(t *testing.T) {
	cases := map[string]Packet{
		"chat_completions_v1":    {"effort": "low", "summary": "auto"},
		"responses_v1":           {"effort": "low", "summary": "auto"},
		"messages_v1":            {"type": "enabled", "budget_tokens": float64(2048)},
		"generateContent_v1beta": {"thinkingBudget": float64(512)},
	}
	for style, native := range cases {
		_, _, body, err := prepare(reasoningPacket(native), protocolRoute(style))
		if err != nil {
			t.Fatal(style, err)
		}
		got := map[string]any{
			"chat_completions_v1":    body["reasoning"],
			"responses_v1":           body["reasoning"],
			"messages_v1":            body["thinking"],
			"generateContent_v1beta": object(body["generationConfig"])["thinkingConfig"],
		}[style]
		if !jsonEqual(t, got, native) {
			t.Fatalf("%s native reasoning changed: %v", style, got)
		}
		if has(body, "reasoning_effort") {
			t.Fatalf("%s native reasoning must not add reasoning_effort", style)
		}
	}
}

func TestPortableEffortConflictsWithNativeOption(t *testing.T) {
	packet := reasoningPacket(Packet{"effort": "none"})
	packet["provider_options"] = Packet{"reasoning_effort": "low"}
	if _, _, _, err := prepare(packet, protocolRoute("chat_completions_v1")); err == nil ||
		!strings.Contains(err.Error(), "duplicate common and native provider settings") {
		t.Fatalf("a setting given both ways must be rejected, got %v", err)
	}
}

// jsonEqual keeps this file self-contained for hosts that vendor only it
// (Dolphin); json.Marshal orders map keys deterministically.
func jsonEqual(t *testing.T, got, want any) bool {
	t.Helper()
	a, errA := json.Marshal(got)
	b, errB := json.Marshal(want)
	if errA != nil || errB != nil {
		t.Fatal(errA, errB)
	}
	return string(a) == string(b)
}

func TestGeminiToolsSendFullJSONSchemaUnchanged(t *testing.T) {
	schema := Packet{"type": "object", "additionalProperties": false,
		"properties": Packet{"path": Packet{"type": []any{"string", "null"}}}, "required": []any{"path"}}
	packet := capabilityPacket(Packet{"tools": []any{Packet{"name": "read_file", "description": "d", "input_schema": schema}}})
	_, _, body, err := prepare(packet, protocolRoute("generateContent_v1beta"))
	if err != nil {
		t.Fatal(err)
	}
	declaration := object(array(object(array(body["tools"])[0])["functionDeclarations"])[0])
	if has(declaration, "parameters") || !jsonEqual(t, declaration["parametersJsonSchema"], schema) {
		t.Fatalf("Gemini tools must carry the caller schema as parametersJsonSchema: %v", declaration)
	}
}
