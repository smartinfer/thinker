package thinker

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/url"
	"strings"
)

// Route is an explicit provider/model configuration. No registry or router is run.
// Its JSON fields are compatible with the corresponding Python RegistryCall fields.
type Route struct {
	CallID       string   `json:"call_id"`
	Provider     string   `json:"provider"`
	ModelID      string   `json:"model_id"`
	Kind         string   `json:"kind"`
	Adapter      string   `json:"adapter"`
	PayloadStyle string   `json:"payload_style"`
	Endpoint     string   `json:"endpoint,omitempty"`
	Aliases      []string `json:"aliases,omitempty"`
	// TokenLimitField is an explicit per-route capability for the completion
	// token-limit parameter name. Empty keeps each protocol's existing field.
	// "max_completion_tokens" is verified for OpenAI Chat Completions models
	// (e.g. gpt-5.1) that reject the legacy "max_tokens". Never both.
	TokenLimitField string `json:"token_limit_field,omitempty"`
}

func protocolFor(r Route) (string, error) {
	switch r.PayloadStyle {
	case "chat_completions_v1":
		return "chat", nil
	case "responses_v1", "openai_responses_v1":
		return "responses", nil
	case "messages_v1":
		return "anthropic", nil
	case "generateContent_v1beta":
		return "gemini", nil
	case "model_turn_v1":
		if p := map[string]string{"openai": "responses", "anthropic": "anthropic", "gemini": "gemini"}[r.Adapter]; p != "" {
			return p, nil
		}
	}
	return "", errors.New("unsupported payload_style")
}

func partsFor(m Packet, p, provider string) ([]any, error) {
	out := []any{}
	for _, v := range array(m["parts"]) {
		part := object(v)
		switch str(part["type"]) {
		case "provider":
			if part["provider"] != provider {
				return nil, errors.New("opaque content belongs to another provider")
			}
			out = append(out, part["data"])
		case "text":
			if p == "gemini" {
				out = append(out, Packet{"text": part["text"]})
			} else {
				kind := "text"
				if p == "responses" {
					kind = "input_text"
					if m["role"] == "assistant" {
						kind = "output_text"
					}
				}
				out = append(out, Packet{"type": kind, "text": part["text"]})
			}
		case "image_url":
			if p == "chat" {
				out = append(out, part)
			} else if p == "responses" {
				image := object(part["image_url"])
				out = append(out, Packet{"type": "input_image", "image_url": image["url"], "detail": fallback(image["detail"], "auto")})
			} else {
				return nil, errors.New("use native provider parts for this media type/protocol")
			}
		default:
			return nil, errors.New("use native provider parts for this media type/protocol")
		}
	}
	return out, nil
}

// portableEfforts are the values of a portable routing.reasoning object
// {"effort": <value>}, mapped to each protocol's native field. Any other object
// is native and passes through unchanged.
var portableEfforts = map[string]bool{"none": true, "minimal": true, "low": true, "medium": true, "high": true, "xhigh": true, "max": true}

func portableEffort(reasoning any) (string, bool) {
	m, ok := reasoning.(Packet)
	if !ok || len(m) != 1 {
		return "", false
	}
	effort, isString := m["effort"].(string)
	if !isString || !portableEfforts[effort] {
		return "", false
	}
	return effort, true
}

func prepare(packet Packet, r Route) (protocol, endpoint string, body Packet, err error) {
	p, err := protocolFor(r)
	if err != nil {
		return "", "", nil, err
	}
	fail := func(message string) (string, string, Packet, error) { return "", "", nil, errors.New(message) }
	if packet["intent"] != "chat" || r.Kind != "chat" {
		return fail("ModelCall currently supports chat intent")
	}
	if len(array(packet["documents"])) > 0 {
		return fail("use native provider parts for documents")
	}
	routing := object(packet["routing"])
	if mode := str(routing["local_mode"]); (mode != "" && mode != "off") || has(routing, "local_route_id") {
		return fail("local-only and local-assist execution are not implemented")
	}
	if has(routing, "route_id") && routing["route_id"] != r.CallID || has(routing, "model") && routing["model"] != r.Provider+"/"+r.ModelID {
		return fail("request routing does not match supplied route")
	}
	if alias := str(routing["alias"]); alias != "" {
		found := false
		for _, a := range r.Aliases {
			if a == alias {
				found = true
			}
		}
		if !found {
			return fail("request alias does not match supplied route")
		}
	}
	options := object(packet["provider_options"])
	if stream := options["stream"]; stream != nil && stream != false {
		return fail("SSE event streaming is not implemented by ModelCall")
	}
	for _, key := range []string{"model", "messages", "input", "contents", "tools", "system", "systemInstruction", "instructions", "previous_response_id"} {
		if has(options, key) {
			return fail("provider_options cannot replace identity, transcript, tools, or continuation")
		}
	}
	messages := append([]any{}, array(packet["messages"])...)
	if results := array(packet["tool_results"]); len(results) > 0 {
		messages = append(messages, Packet{"role": "tool", "tool_results": results})
	}
	names := map[string]string{}
	for _, v := range messages {
		for _, v := range array(object(v)["tool_calls"]) {
			c := object(v)
			if id := str(c["call_id"]); id != "" {
				names[id] = str(c["name"])
			}
		}
	}
	native, systems := []any{}, []any{}
	for _, v := range messages {
		m := object(v)
		role := str(m["role"])
		parts, e := partsFor(m, p, r.Provider)
		if e != nil {
			return fail(e.Error())
		}
		if role == "system" && (p == "anthropic" || p == "gemini") {
			systems = append(systems, parts...)
			continue
		}
		calls, results := array(m["tool_calls"]), array(m["tool_results"])
		// Provider metadata must be an object when merged into a native object.
		if m["provider_data"] != nil && object(m["provider_data"]) == nil {
			return fail("message provider_data must be an object for replay")
		}
		for _, v := range calls {
			c := object(v)
			if c["provider_data"] != nil && object(c["provider_data"]) == nil {
				return fail("tool provider_data must be an object for replay")
			}
		}
		switch p {
		case "responses":
			content := []any{}
			for _, part := range parts {
				kind := str(object(part)["type"])
				if kind == "reasoning" || kind == "function_call" || kind == "function_call_output" || kind == "message" {
					if len(content) > 0 {
						native = append(native, Packet{"role": role, "content": content})
						content = []any{}
					}
					native = append(native, part)
				} else {
					content = append(content, part)
				}
			}
			if len(content) > 0 {
				native = append(native, Packet{"role": role, "content": content})
			}
			for _, v := range calls {
				c := object(v)
				item := copyObject(c["provider_data"])
				item["type"] = "function_call"
				item["call_id"] = c["call_id"]
				item["name"] = c["name"]
				item["arguments"] = stringOrJSON(c["arguments"])
				native = append(native, item)
			}
			for _, v := range results {
				result := object(v)
				output := result["output"]
				if result["is_error"] == true {
					output = Packet{"error": output}
				}
				native = append(native, Packet{"type": "function_call_output", "call_id": result["call_id"], "output": stringOrJSON(output)})
			}
		case "chat":
			if len(parts) > 0 || len(calls) > 0 {
				item := copyObject(m["provider_data"])
				item["role"] = role
				item["content"] = nil
				if len(parts) > 0 {
					item["content"] = parts
				}
				if len(calls) > 0 {
					tools := []any{}
					for _, v := range calls {
						c := object(v)
						tool := copyObject(c["provider_data"])
						tool["id"] = c["call_id"]
						tool["type"] = "function"
						tool["function"] = Packet{"name": c["name"], "arguments": stringOrJSON(c["arguments"])}
						tools = append(tools, tool)
					}
					item["tool_calls"] = tools
				}
				native = append(native, item)
			}
			for _, v := range results {
				result := object(v)
				output := result["output"]
				if result["is_error"] == true {
					output = Packet{"error": output}
				}
				native = append(native, Packet{"role": "tool", "tool_call_id": result["call_id"], "content": stringOrJSON(output)})
			}
		case "anthropic":
			for _, v := range calls {
				c := object(v)
				item := copyObject(c["provider_data"])
				item["type"] = "tool_use"
				item["id"] = c["call_id"]
				item["name"] = c["name"]
				item["input"] = c["arguments"]
				parts = append(parts, item)
			}
			for _, v := range results {
				result := object(v)
				parts = append(parts, Packet{"type": "tool_result", "tool_use_id": result["call_id"], "content": stringOrJSON(result["output"]), "is_error": fallback(result["is_error"], false)})
			}
			if role == "tool" {
				role = "user"
			}
			native = append(native, Packet{"role": role, "content": parts})
		case "gemini":
			for _, v := range calls {
				c := object(v)
				item := copyObject(c["provider_data"])
				call := Packet{"name": c["name"], "args": c["arguments"]}
				if str(c["call_id"]) != "" {
					call["id"] = c["call_id"]
				}
				item["functionCall"] = call
				parts = append(parts, item)
			}
			for _, v := range results {
				result := object(v)
				name := names[str(result["call_id"])]
				if name == "" {
					return fail("Gemini tool results require the matching named proposal in messages")
				}
				key := "output"
				if result["is_error"] == true {
					key = "error"
				}
				parts = append(parts, Packet{"functionResponse": Packet{"name": name, "id": result["call_id"], "response": Packet{key: result["output"]}}})
			}
			if role == "assistant" {
				role = "model"
			} else {
				role = "user"
			}
			native = append(native, Packet{"role": role, "parts": parts})
		}
	}
	settings := object(packet["instructions"])
	schema := settings["json_schema"]
	limit := fallback(settings["max_output_tokens"], 256)
	tools := array(packet["tools"])
	if has(settings, "json_schema_strict") && p != "chat" && p != "responses" {
		return fail("json_schema_strict is only mapped for Chat Completions and Responses")
	}
	reasoning := routing["reasoning"]
	effort, portable := portableEffort(reasoning)
	body = Packet{}
	base := strings.TrimRight(r.Endpoint, "/")
	suffix := ""
	version := "/v1"
	switch p {
	case "chat", "responses":
		input, output := "messages", "max_tokens"
		suffix = "/chat/completions"
		if p == "responses" {
			input, output = "input", "max_output_tokens"
			suffix = "/responses"
		}
		switch r.TokenLimitField {
		case "":
		case "max_completion_tokens":
			if p != "chat" {
				return fail("token_limit_field max_completion_tokens is only verified for Chat Completions routes")
			}
			output = "max_completion_tokens"
		default:
			return fail("unsupported route token_limit_field")
		}
		for _, k := range []string{"max_tokens", "max_completion_tokens", "max_output_tokens"} {
			if has(options, k) {
				return fail("provider_options cannot override the token limit; set instructions max_output_tokens and, if the model requires it, the route token_limit_field")
			}
		}
		body = Packet{"model": r.ModelID, input: native, output: limit, "stream": false}
		if len(tools) > 0 {
			definitions := []any{}
			for _, v := range tools {
				t := object(v)
				def := Packet{"name": t["name"], "description": fallback(t["description"], ""), "parameters": t["input_schema"]}
				if has(t, "strict") {
					def["strict"] = t["strict"]
				}
				if p == "chat" {
					definitions = append(definitions, Packet{"type": "function", "function": def})
				} else {
					def["type"] = "function"
					definitions = append(definitions, def)
				}
			}
			body["tools"] = definitions
		}
		if schema != nil {
			format := Packet{"name": "thinker_response", "schema": schema}
			if has(settings, "json_schema_strict") {
				format["strict"] = settings["json_schema_strict"]
			}
			if p == "chat" {
				body["response_format"] = Packet{"type": "json_schema", "json_schema": format}
			} else {
				format["type"] = "json_schema"
				body["text"] = Packet{"format": format}
			}
		}
		for _, k := range []string{"temperature", "top_p", "seed", "stop"} {
			if has(settings, k) {
				body[k] = settings[k]
			}
		}
		if portable && p == "chat" {
			// Chat Completions takes a top-level string, not a reasoning object.
			body["reasoning_effort"] = effort
		} else if truthy(reasoning) {
			body["reasoning"] = reasoning
		}
		if base == "" && r.Provider == "openai" {
			base = "https://api.openai.com"
		}
	case "anthropic":
		if r.TokenLimitField != "" {
			return fail("token_limit_field is not verified for this protocol")
		}
		if portable && effort != "none" {
			return fail("Anthropic thinking needs a native thinking object; only effort none is portable")
		}
		if has(settings, "seed") {
			return fail("Anthropic seed is unsupported")
		}
		body = Packet{"model": r.ModelID, "messages": native, "max_tokens": limit, "stream": false}
		if len(systems) > 0 {
			body["system"] = systems
		}
		if len(tools) > 0 {
			for _, v := range tools {
				if has(object(v), "strict") {
					return fail("Anthropic tool strict flag is not mapped by this adapter")
				}
			}
			body["tools"] = tools
		}
		if schema != nil {
			body["output_config"] = Packet{"format": Packet{"type": "json_schema", "schema": schema}}
		}
		for _, k := range []string{"temperature", "top_p"} {
			if has(settings, k) {
				body[k] = settings[k]
			}
		}
		if has(settings, "stop") {
			body["stop_sequences"] = settings["stop"]
		}
		// Omitting thinking is model-dependent, so effort none disables it explicitly.
		if portable {
			body["thinking"] = Packet{"type": "disabled"}
		} else if truthy(reasoning) {
			body["thinking"] = reasoning
		}
		if base == "" {
			base = "https://api.anthropic.com"
		}
		suffix = "/messages"
	case "gemini":
		if r.TokenLimitField != "" {
			return fail("token_limit_field is not verified for this protocol")
		}
		if portable && effort != "none" {
			return fail("Gemini thinking needs a native thinkingConfig; only effort none is portable")
		}
		generation := Packet{"maxOutputTokens": limit}
		for k, native := range map[string]string{"temperature": "temperature", "top_p": "topP", "seed": "seed", "stop": "stopSequences"} {
			if has(settings, k) {
				generation[native] = settings[k]
			}
		}
		if schema != nil {
			generation["responseMimeType"] = "application/json"
			generation["responseJsonSchema"] = schema
		}
		if portable {
			generation["thinkingConfig"] = Packet{"thinkingBudget": 0}
		} else if truthy(reasoning) {
			generation["thinkingConfig"] = reasoning
		}
		body = Packet{"contents": native, "generationConfig": generation}
		if len(systems) > 0 {
			body["systemInstruction"] = Packet{"parts": systems}
		}
		if len(tools) > 0 {
			definitions := []any{}
			for _, v := range tools {
				t := object(v)
				if has(t, "strict") {
					return fail("Gemini tool strict flag is unsupported")
				}
				// Full JSON Schema, unchanged; "parameters" accepts only an OpenAPI
				// subset and rejects e.g. additionalProperties.
				definitions = append(definitions, Packet{"name": t["name"], "description": fallback(t["description"], ""), "parametersJsonSchema": t["input_schema"]})
			}
			body["tools"] = []any{Packet{"functionDeclarations": definitions}}
		}
		if base == "" {
			base = "https://generativelanguage.googleapis.com"
		}
		version = "/v1beta"
		suffix = "/models/" + strings.ReplaceAll(url.QueryEscape(r.ModelID), "+", "%20") + ":generateContent"
	}
	if c := packet["continuation"]; c != nil {
		continuation := object(c)
		if p != "responses" || len(continuation) != 1 || !has(continuation, "response_id") {
			return fail("replay native provider parts for this continuation format")
		}
		body["previous_response_id"] = continuation["response_id"]
	}
	if base == "" {
		return fail("provider endpoint is required")
	}
	endpoint = base
	if !strings.HasSuffix(base, suffix) {
		if !strings.HasSuffix(base, version) {
			endpoint += version
		}
		endpoint += suffix
	}
	u, e := url.Parse(endpoint)
	if e != nil || u.Host == "" || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil {
		return fail("provider endpoint must be an HTTP(S) URL without userinfo")
	}
	for k, v := range options {
		if k != "stream" && has(body, k) {
			return fail("duplicate common and native provider settings")
		}
		if p == "gemini" && k == "stream" {
			continue
		}
		body[k] = v
	}
	return p, endpoint, body, nil
}

func tokenCount(v any) any {
	// Python deliberately rejects float-shaped usage values, even 11.0.
	if n, ok := v.(json.Number); ok && strings.ContainsAny(string(n), ".eE") {
		return nil
	}
	n, ok := number(v)
	if ok && n.IsInt() && n.Sign() >= 0 {
		return v
	}
	return nil
}
func reportedUsage(data Packet, p string) Packet {
	raw := object(data["usage"])
	if p == "gemini" {
		raw = object(data["usageMetadata"])
	}
	reported := raw != nil
	if raw == nil {
		raw = Packet{}
	}
	u := Packet{"input_tokens": nil, "output_tokens": nil, "total_tokens": nil, "cached_input_tokens": nil, "reasoning_tokens": nil, "provider_data": raw}
	input, output, total := "input_tokens", "output_tokens", "total_tokens"
	switch p {
	case "chat":
		input, output = "prompt_tokens", "completion_tokens"
		u["cached_input_tokens"] = tokenCount(object(raw["prompt_tokens_details"])["cached_tokens"])
		u["reasoning_tokens"] = tokenCount(object(raw["completion_tokens_details"])["reasoning_tokens"])
	case "responses":
		u["cached_input_tokens"] = tokenCount(object(raw["input_tokens_details"])["cached_tokens"])
		u["reasoning_tokens"] = tokenCount(object(raw["output_tokens_details"])["reasoning_tokens"])
	case "anthropic":
		u["cached_input_tokens"] = tokenCount(raw["cache_read_input_tokens"])
		u["cache_creation_input_tokens"] = tokenCount(raw["cache_creation_input_tokens"])
	case "gemini":
		input, total = "promptTokenCount", "totalTokenCount"
		output = ""
		// Gemini's proto3 JSON omits zero-valued counts: an absent field means 0,
		// a present but invalid value stays unknown.
		u["cached_input_tokens"] = geminiCount(raw, "cachedContentTokenCount", reported)
		u["reasoning_tokens"] = geminiCount(raw, "thoughtsTokenCount", reported)
	}
	u["input_tokens"], u["output_tokens"], u["total_tokens"] = tokenCount(raw[input]), tokenCount(raw[output]), tokenCount(raw[total])
	if p == "gemini" {
		visible, ok := number(tokenCount(raw["candidatesTokenCount"]))
		thinking, ok2 := number(geminiCount(raw, "thoughtsTokenCount", reported))
		if ok && ok2 {
			u["output_tokens"] = json.Number(new(big.Rat).Add(visible, thinking).Num().String())
		}
	}
	return u
}

// geminiCount reads one usageMetadata count; within a reported block an absent
// field is 0 (proto3 omits zeros), and without a block every count is unknown.
func geminiCount(raw Packet, key string, reported bool) any {
	if _, present := raw[key]; reported && !present {
		return json.Number("0")
	}
	return tokenCount(raw[key])
}

func lift(data Packet, p string) (Packet, error) {
	fail := func() (Packet, error) { return nil, errors.New("could not decode provider response envelope") }
	r := Packet{"status": "completed", "provider_data": data, "cost_usd": nil}
	calls := []any{}
	texts := []string{}
	var reason any
	add := func(id, name, args, native any) {
		if _, ok := id.(string); !ok {
			id = nil
		}
		if _, ok := name.(string); !ok {
			name = nil
		}
		calls = append(calls, Packet{"call_id": id, "name": name, "arguments": args, "provider_data": native})
	}
	switch p {
	case "chat":
		choices := array(data["choices"])
		if len(choices) == 0 {
			return fail()
		}
		choice := object(choices[0])
		m := object(choice["message"])
		if m == nil {
			return fail()
		}
		r["content"] = m["content"]
		if truthy(m["refusal"]) {
			r["status"] = "refused"
			r["content"] = m["refusal"]
		}
		if m["tool_calls"] != nil && array(m["tool_calls"]) == nil {
			return fail()
		}
		for _, v := range array(m["tool_calls"]) {
			c := object(v)
			f := object(c["function"])
			if c == nil {
				return fail()
			}
			add(c["id"], f["name"], f["arguments"], c)
		}
		reason = choice["finish_reason"]
		if reason == "length" {
			r["status"] = "incomplete"
		} else if reason == "content_filter" {
			r["status"] = "refused"
		}
	case "responses":
		if array(data["output"]) == nil {
			return fail()
		}
		for _, v := range array(data["output"]) {
			item := object(v)
			if item == nil {
				return fail()
			}
			switch item["type"] {
			case "function_call":
				add(item["call_id"], item["name"], item["arguments"], item)
			case "message":
				for _, v := range array(item["content"]) {
					part := object(v)
					if part == nil {
						return fail()
					}
					switch part["type"] {
					case "refusal":
						r["status"] = "refused"
						texts = append(texts, str(part["refusal"]))
					case "output_text":
						if _, ok := part["text"].(string); !ok {
							return fail()
						}
						texts = append(texts, str(part["text"]))
					}
				}
			}
		}
		reason = first(object(data["incomplete_details"])["reason"], data["status"])
		switch data["status"] {
		case "incomplete", "queued", "in_progress":
			r["status"] = "incomplete"
		case "cancelled":
			r["status"] = "cancelled"
		}
		if id := str(data["id"]); id != "" {
			r["continuation"] = Packet{"response_id": id}
		}
	case "anthropic":
		if array(data["content"]) == nil {
			return fail()
		}
		for _, v := range array(data["content"]) {
			item := object(v)
			if item == nil {
				return fail()
			}
			switch item["type"] {
			case "text":
				if _, ok := item["text"].(string); !ok {
					return fail()
				}
				texts = append(texts, str(item["text"]))
			case "tool_use":
				add(item["id"], item["name"], item["input"], item)
			}
		}
		reason = data["stop_reason"]
		switch reason {
		case "max_tokens", "pause_turn":
			r["status"] = "incomplete"
		case "refusal":
			r["status"] = "refused"
		}
	case "gemini":
		candidates := array(data["candidates"])
		blocked := object(data["promptFeedback"])["blockReason"]
		if len(candidates) == 0 && !truthy(blocked) {
			return fail()
		}
		candidate := Packet{}
		if len(candidates) > 0 {
			candidate = object(candidates[0])
			if candidate == nil {
				return fail()
			}
		}
		for _, v := range array(object(candidate["content"])["parts"]) {
			part := object(v)
			if part == nil {
				return fail()
			}
			if has(part, "text") && !truthy(part["thought"]) {
				text, ok := part["text"].(string)
				if !ok {
					return fail()
				}
				texts = append(texts, text)
			}
			if has(part, "functionCall") {
				call := object(part["functionCall"])
				if call == nil {
					return fail()
				}
				add(call["id"], call["name"], call["args"], part)
			}
		}
		reason = first(blocked, candidate["finishReason"])
		if reason == "MAX_TOKENS" {
			r["status"] = "incomplete"
		} else if truthy(blocked) {
			r["status"] = "refused"
		} else {
			switch reason {
			case "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY":
				r["status"] = "refused"
			}
		}
	default:
		return nil, fmt.Errorf("unknown protocol")
	}
	if p != "chat" {
		r["content"] = strings.Join(texts, "")
	}
	if _, ok := reason.(string); !ok {
		reason = nil
	}
	r["tool_calls"], r["finish_reason"], r["usage"] = calls, reason, reportedUsage(data, p)
	return r, nil
}
