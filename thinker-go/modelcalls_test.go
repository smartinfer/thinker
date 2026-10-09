package thinker

import (
	"bytes"
	"context"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

type transportFunc func(*http.Request) (*http.Response, error)

func (f transportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func testRoute(p string) Route {
	provider := map[string]string{"chat": "openrouter", "responses": "openai", "anthropic": "anthropic", "gemini": "gemini"}[p]
	return Route{CallID: provider + ":fixture.chat", Provider: provider, ModelID: "fixture", Kind: "chat", Adapter: provider, PayloadStyle: map[string]string{"chat": "chat_completions_v1", "responses": "responses_v1", "anthropic": "messages_v1", "gemini": "generateContent_v1beta"}[p], Endpoint: "https://provider.invalid"}
}
func testPacket() Packet {
	return Packet{"version": "0.4", "request_id": "r1", "job_id": "j1", "turn_id": "t1", "intent": "chat", "messages": []any{Packet{"role": "user", "parts": []any{Packet{"type": "text", "text": "Hello"}}}}}
}
func testResponse(p, text string) Packet {
	tool := Packet{"name": "read", "arguments": `{"path":"main.c",}`}
	switch p {
	case "chat":
		return Packet{"choices": []any{Packet{"message": Packet{"content": text, "tool_calls": []any{Packet{"id": "tc1", "type": "function", "function": tool}}}, "finish_reason": "tool_calls"}}}
	case "responses":
		return Packet{"id": "resp1", "status": "completed", "output": []any{Packet{"type": "message", "content": []any{Packet{"type": "output_text", "text": text}}}, Packet{"type": "function_call", "call_id": "tc1", "name": "read", "arguments": tool["arguments"]}, Packet{"type": "reasoning", "encrypted_content": "opaque"}}}
	case "anthropic":
		return Packet{"content": []any{Packet{"type": "text", "text": text}, Packet{"type": "thinking", "signature": "opaque"}, Packet{"type": "tool_use", "id": "tc1", "name": "read", "input": Packet{"wrong": 7}}}, "stop_reason": "tool_use"}
	default:
		return Packet{"candidates": []any{Packet{"content": Packet{"parts": []any{Packet{"text": text}, Packet{"functionCall": Packet{"id": "tc1", "name": "read", "args": Packet{"wrong": 7}}, "thoughtSignature": "opaque"}}}, "finishReason": "STOP"}}}
	}
}
func reply(request *http.Request, status int, data any) *http.Response {
	b, _ := json.Marshal(data)
	return &http.Response{StatusCode: status, Header: http.Header{}, Body: io.NopCloser(bytes.NewReader(b)), Request: request}
}
func testOptions(send transportFunc) Options {
	return Options{Client: &http.Client{Transport: send}, Credentials: func(context.Context, string) (string, error) { return "fixture-secret", nil }}
}
func code(r Packet) string { return str(object(r["error"])["code"]) }
func validResult(t *testing.T, r Packet) {
	t.Helper()
	if _, err := ParseResponse(r); err != nil {
		t.Fatalf("invalid result envelope: %v\n%#v", err, r)
	}
}

func TestFourProtocolsAndOptionalConstraints(t *testing.T) {
	for _, protocol := range []string{"chat", "responses", "anthropic", "gemini"} {
		t.Run(protocol, func(t *testing.T) {
			for _, mode := range []string{"ordinary", "schema", "strict", "relaxed"} {
				t.Run(mode, func(t *testing.T) {
					packet := testPacket()
					schema := Packet{"type": "object", "properties": Packet{"decision": Packet{"type": "string", "enum": []any{"accept", "revise"}}}, "required": []any{"decision"}, "additionalProperties": false}
					if mode != "ordinary" {
						packet["instructions"] = Packet{"json_schema": schema}
					}
					if mode == "strict" || mode == "relaxed" {
						object(packet["instructions"])["json_schema_strict"] = mode == "strict"
					}
					before, _ := json.Marshal(packet)
					var sent Packet
					count := 0
					r := ModelCall(context.Background(), packet, testRoute(protocol), testOptions(func(req *http.Request) (*http.Response, error) {
						count++
						b, _ := io.ReadAll(req.Body)
						v, e := decodeJSON(b)
						if e != nil {
							t.Fatal(e)
						}
						sent = object(v)
						if protocol == "anthropic" && req.Header.Get("x-api-key") != "fixture-secret" {
							t.Fatal("missing Anthropic key")
						}
						if protocol == "gemini" && req.Header.Get("x-goog-api-key") != "fixture-secret" {
							t.Fatal("missing Gemini key")
						}
						return reply(req, 200, testResponse(protocol, `{"decision":"outside-enum",}`)), nil
					}))
					validResult(t, r)
					after, _ := json.Marshal(packet)
					if !bytes.Equal(before, after) {
						t.Fatal("caller packet changed")
					}
					if (mode == "strict" || mode == "relaxed") && (protocol == "anthropic" || protocol == "gemini") {
						if code(r) != "UNSUPPORTED_REQUEST" || count != 0 {
							t.Fatal(r)
						}
						return
					}
					if r["status"] != "completed" || r["content"] != `{"decision":"outside-enum",}` || count != 1 {
						t.Fatal(r)
					}
					if r["cost_usd"] != nil || object(r["usage"])["input_tokens"] != nil {
						t.Fatal("invented usage/cost")
					}
					var format Packet
					switch protocol {
					case "chat":
						format = object(object(sent["response_format"])["json_schema"])
					case "responses":
						format = object(object(sent["text"])["format"])
					case "anthropic":
						format = object(object(sent["output_config"])["format"])
					case "gemini":
						if value := object(sent["generationConfig"])["responseJsonSchema"]; value != nil {
							format = Packet{"schema": value}
						}
					}
					if mode == "ordinary" {
						if format != nil {
							t.Fatal("unsolicited format constraint")
						}
					} else {
						a, _ := json.Marshal(format["schema"])
						b, _ := json.Marshal(schema)
						if !bytes.Equal(a, b) {
							t.Fatal("schema rewritten")
						}
						if mode == "schema" && has(format, "strict") {
							t.Fatal("strict forced")
						}
						if mode == "strict" && format["strict"] != true {
							t.Fatal(format)
						}
						if mode == "relaxed" && format["strict"] != false {
							t.Fatal(format)
						}
					}
					tool := object(array(r["tool_calls"])[0])
					if protocol == "chat" || protocol == "responses" {
						if tool["arguments"] != `{"path":"main.c",}` {
							t.Fatal("raw arguments changed")
						}
					}
					if object(object(r["metadata"])["observability"])["trace"].(Packet)["status"] != "disabled" {
						t.Fatal("unexpected recording")
					}
				})
			}
		})
	}
}

func TestErrorFixtureParity(t *testing.T) {
	data, err := os.ReadFile("../thinker-core/thinker/tests/fixtures/thinkerql/errors.json")
	if os.IsNotExist(err) {
		t.Skip("Python fixture source not installed with standalone Go module")
	}
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []struct {
		Name  string
		Input struct {
			HTTPStatus    int `json:"http_status"`
			Provider      string
			Body          any
			Headers       http.Header
			StreamStarted bool `json:"stream_started"`
		}
		Expected string
	}
	if err = json.Unmarshal(data, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, f := range fixtures {
		t.Run(f.Name, func(t *testing.T) {
			r := ClassifyError(ErrorInput{HTTPStatus: f.Input.HTTPStatus, Provider: f.Input.Provider, Body: f.Input.Body, Headers: f.Input.Headers, StreamStarted: f.Input.StreamStarted})
			if r["code"] != f.Expected {
				t.Fatalf("want %s got %#v", f.Expected, r)
			}
		})
	}
	for reason, expected := range object(vocabulary["hermes"]) {
		t.Run("hermes/"+reason, func(t *testing.T) {
			r := ClassifyError(ErrorInput{Body: Packet{"error": Packet{"code": reason}}})
			if r["code"] != expected {
				t.Fatal(r)
			}
		})
	}
}

func TestHTTPFailuresNeverRetryOrDowngrade(t *testing.T) {
	for status, want := range map[int]string{400: "REQUEST_REJECTED", 401: "AUTHENTICATION_FAILED", 403: "ACCESS_DENIED", 408: "TIMEOUT", 409: "CONFLICT", 429: "RATE_LIMITED", 500: "PROVIDER_ERROR", 529: "PROVIDER_OVERLOADED"} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			count := 0
			p := testPacket()
			p["instructions"] = Packet{"json_schema": Packet{"unsupported-keyword": "caller-value"}}
			r := ModelCall(context.Background(), p, testRoute("chat"), testOptions(func(req *http.Request) (*http.Response, error) {
				count++
				b, _ := io.ReadAll(req.Body)
				if !bytes.Contains(b, []byte("unsupported-keyword")) {
					t.Fatal("schema removed")
				}
				r := reply(req, status, Packet{"error": Packet{"message": "provider failure"}})
				r.Header.Set("Retry-After", "75")
				return r, nil
			}))
			if code(r) != want || count != 1 || object(r["error"])["retry_after_seconds"] != float64(75) {
				t.Fatal(r)
			}
			validResult(t, r)
		})
	}
}

func TestPreflightFailures(t *testing.T) {
	for name, mutate := range map[string]func(Packet){
		"version": func(p Packet) { p["version"] = "0.3" }, "strict without schema": func(p Packet) { p["instructions"] = Packet{"json_schema_strict": true} }, "stream": func(p Packet) { p["provider_options"] = Packet{"stream": true} }, "identity override": func(p Packet) { p["provider_options"] = Packet{"model": "other"} }, "local assist": func(p Packet) { p["routing"] = Packet{"local_mode": "assist"} }, "different route": func(p Packet) { p["routing"] = Packet{"route_id": "wrong:route.chat"} }, "nonchat": func(p Packet) { p["intent"] = "embed" }, "unknown": func(p Packet) { p["unrecognized"] = true }, "tool role": func(p Packet) { p["messages"] = []any{Packet{"role": "user", "tool_calls": []any{Packet{}}}} },
	} {
		t.Run(name, func(t *testing.T) {
			p := testPacket()
			mutate(p)
			r := ModelCall(context.Background(), p, testRoute("chat"), testOptions(func(*http.Request) (*http.Response, error) { t.Fatal("unexpected network call"); return nil, nil }))
			if r["status"] != "failed" || len(array(r["invocations"])) != 0 {
				t.Fatal(r)
			}
			validResult(t, r)
		})
	}
	o := testOptions(nil)
	o.Credentials = func(context.Context, string) (string, error) { return "", nil }
	r := ModelCall(context.Background(), testPacket(), testRoute("chat"), o)
	if code(r) != "AUTHENTICATION_FAILED" {
		t.Fatal(r)
	}
}

type brokenBody struct {
	sent   bool
	closed bool
	err    error
}

func (b *brokenBody) Read(p []byte) (int, error) {
	if !b.sent {
		b.sent = true
		return copy(p, []byte("{\"partial\":\"\xe2")), nil
	}
	return 0, b.err
}
func (b *brokenBody) Close() error { b.closed = true; return nil }
func TestTransportFailuresAndPartialBytes(t *testing.T) {
	for name, err := range map[string]error{"network": syscall.ECONNRESET, "timeout": context.DeadlineExceeded, "cancel": context.Canceled, "certificate": x509.UnknownAuthorityError{}} {
		t.Run(name, func(t *testing.T) {
			r := ModelCall(context.Background(), testPacket(), testRoute("chat"), testOptions(func(*http.Request) (*http.Response, error) { return nil, fmt.Errorf("wrapped: %w", err) }))
			want := map[string]string{"network": "CONNECTION_ERROR", "timeout": "TIMEOUT", "cancel": "CANCELLED", "certificate": "TLS_ERROR"}[name]
			if code(r) != want {
				t.Fatal(r)
			}
			validResult(t, r)
		})
	}
	for _, err := range []error{io.ErrUnexpectedEOF, context.DeadlineExceeded, context.Canceled} {
		t.Run(err.Error(), func(t *testing.T) {
			b := &brokenBody{err: err}
			r := ModelCall(context.Background(), testPacket(), testRoute("chat"), testOptions(func(req *http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: 200, Body: b, Header: http.Header{}, Request: req}, nil
			}))
			want := "BROKEN_STREAM"
			if errors.Is(err, context.DeadlineExceeded) {
				want = "TIMEOUT"
			}
			if errors.Is(err, context.Canceled) {
				want = "CANCELLED"
			}
			if code(r) != want || !b.closed {
				t.Fatal(r)
			}
			raw, e := base64.StdEncoding.DecodeString(str(object(r["provider_data"])["raw_body_base64"]))
			if e != nil || !bytes.Equal(raw, []byte("{\"partial\":\"\xe2")) {
				t.Fatal("lost partial bytes")
			}
			validResult(t, r)
		})
	}
}

func TestDeadlineAndCancellationAbortTransport(t *testing.T) {
	for _, cancelled := range []bool{false, true} {
		t.Run(fmt.Sprint(cancelled), func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			started := make(chan struct{})
			stopped := make(chan struct{})
			p := testPacket()
			p["timeout_ms"] = 25
			opts := testOptions(func(r *http.Request) (*http.Response, error) {
				close(started)
				<-r.Context().Done()
				close(stopped)
				return nil, r.Context().Err()
			})
			done := make(chan Packet, 1)
			go func() { done <- ModelCall(ctx, p, testRoute("chat"), opts) }()
			<-started
			if cancelled {
				cancel()
			}
			select {
			case r := <-done:
				want := "TIMEOUT"
				if cancelled {
					want = "CANCELLED"
				}
				if code(r) != want {
					t.Fatal(r)
				}
			case <-time.After(time.Second):
				t.Fatal("transport did not stop")
			}
			<-stopped
		})
	}
}

func TestConcurrentCallsAndRecorder(t *testing.T) {
	const count = 16
	var arrived atomic.Int32
	ready := make(chan struct{})
	recorder := &TraceRecorder{Store: LocalTraceStore{Root: t.TempDir()}}
	var metrics atomic.Int32
	opts := testOptions(func(req *http.Request) (*http.Response, error) {
		b, _ := io.ReadAll(req.Body)
		v, _ := decodeJSON(b)
		body := object(v)
		text := str(object(array(object(array(body["messages"])[0])["content"])[0])["text"])
		if arrived.Add(1) == count {
			close(ready)
		}
		select {
		case <-ready:
		case <-req.Context().Done():
			return nil, req.Context().Err()
		}
		return reply(req, 200, testResponse("chat", text)), nil
	})
	opts.Recorder = recorder
	opts.Debug = true
	opts.Metrics = func(_ context.Context, p Packet) error {
		if has(p, "content") || has(p, "messages") {
			t.Error("metrics received model content")
		}
		metrics.Add(1)
		return nil
	}
	results := make([]Packet, count)
	var wg sync.WaitGroup
	for i := range results {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			p := testPacket()
			p["request_id"] = fmt.Sprint(i)
			object(array(object(array(p["messages"])[0])["parts"])[0])["text"] = fmt.Sprint(i)
			results[i] = ModelCall(context.Background(), p, testRoute("chat"), opts)
		}(i)
	}
	wg.Wait()
	ids := map[string]bool{}
	for i, r := range results {
		if r["content"] != fmt.Sprint(i) {
			t.Fatal(r)
		}
		validResult(t, r)
		receipt := object(object(r["metadata"])["observability"])
		id := str(receipt["invocation_id"])
		if ids[id] {
			t.Fatal("duplicate invocation ID")
		}
		ids[id] = true
		trace := object(receipt["trace"])
		if trace["status"] != "recorded" {
			t.Fatal(trace)
		}
		data, err := os.ReadFile(str(trace["uri"]))
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Contains(data, []byte("fixture-secret")) {
			t.Fatal("secret leaked")
		}
		v, _ := decodeJSON(data)
		raw, _ := base64.StdEncoding.DecodeString(str(object(object(v)["native_request"])["raw_body_base64"]))
		if bytes.Contains(raw, []byte("fixture-secret")) {
			t.Fatal("raw secret leaked")
		}
	}
	if metrics.Load() != count {
		t.Fatal("lost metric callback")
	}
}

func TestObservationFailuresDoNotReplaceModelResult(t *testing.T) {
	opts := testOptions(func(r *http.Request) (*http.Response, error) {
		return reply(r, 200, testResponse("chat", "usable")), nil
	})
	opts.Recorder = &TraceRecorder{Store: StoreFunc(func(context.Context, string, []byte) (string, error) {
		return "", errors.New("sensitive backend error fixture-secret")
	})}
	opts.Metrics = func(context.Context, Packet) error { return errors.New("sensitive metrics error fixture-secret") }
	r := ModelCall(context.Background(), testPacket(), testRoute("chat"), opts)
	if r["status"] != "completed" || r["content"] != "usable" {
		t.Fatal(r)
	}
	receipt := object(object(r["metadata"])["observability"])
	for _, sink := range []string{"trace", "metrics"} {
		if object(receipt[sink])["status"] != "failed" {
			t.Fatal(receipt)
		}
	}
	b, _ := json.Marshal(receipt)
	if bytes.Contains(b, []byte("fixture-secret")) {
		t.Fatal("storage error leaked")
	}
}

func TestDebugDefaultsAndRedaction(t *testing.T) {
	root := t.TempDir()
	t.Setenv("THINKER_TRACE_DIR", root)
	opts := testOptions(func(r *http.Request) (*http.Response, error) {
		return reply(r, 200, testResponse("chat", "fixture-secret")), nil
	})
	r := ModelCall(context.Background(), testPacket(), testRoute("chat"), opts)
	entries, _ := os.ReadDir(root)
	if len(entries) != 0 {
		t.Fatal("default call wrote a trace")
	}
	opts.Debug = true
	r = ModelCall(context.Background(), testPacket(), testRoute("chat"), opts)
	if r["content"] != "fixture-secret" {
		t.Fatal("caller response redacted")
	}
	receipt := object(object(r["metadata"])["observability"])
	if object(receipt["trace"])["status"] != "recorded" {
		t.Fatal(receipt)
	}
	data, e := os.ReadFile(str(object(receipt["trace"])["uri"]))
	if e != nil {
		t.Fatal(e)
	}
	if bytes.Contains(data, []byte("fixture-secret")) {
		t.Fatal("trace secret leaked")
	}
	safe, err := redact(Packet{"Authorization": "Bearer known-credential", "cookie": "session=cookie-value", "text": "known-credential cookie-value https://host/?X-Amz-Signature=signed-value", "raw_body_base64": base64.StdEncoding.EncodeToString([]byte("known-credential\xe2")), "thoughtSignature": "opaque-model-signature"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(safe)
	if containsAny(string(b), "known-credential", "cookie-value", "signed-value") {
		t.Fatal("redaction failed")
	}
	if object(safe)["thoughtSignature"] != "opaque-model-signature" {
		t.Fatal("model signature removed")
	}
	raw, _ := base64.StdEncoding.DecodeString(str(object(safe)["raw_body_base64"]))
	if !bytes.HasSuffix(raw, []byte{0xe2}) {
		t.Fatal("non-UTF8 byte corrupted")
	}
	opts.Debug = false
	opts.Recorder = &TraceRecorder{Store: StoreFunc(func(_ context.Context, _ string, data []byte) (string, error) {
		v, _ := decodeJSON(data)
		if has(object(v), "request") || has(object(v), "response") || bytes.Contains(data, []byte("fixture-secret")) {
			t.Fatal("summary contained model data")
		}
		return "memory://summary", nil
	})}
	ModelCall(context.Background(), testPacket(), testRoute("chat"), opts)
}

func TestLocalStoreCreateOnlyAndTraversal(t *testing.T) {
	store := LocalTraceStore{Root: t.TempDir()}
	path, err := store.Write(context.Background(), "job/turn/test.json", []byte("first"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = store.Write(context.Background(), "job/turn/test.json", []byte("second")); err == nil {
		t.Fatal("overwrote trace")
	}
	data, _ := os.ReadFile(path)
	if string(data) != "first" {
		t.Fatal("content changed")
	}
	if _, err = store.Write(context.Background(), "../escape.json", nil); err == nil {
		t.Fatal("traversal accepted")
	}
	out := t.TempDir()
	if err = os.Symlink(out, filepath.Join(store.Root, "link")); err != nil {
		t.Fatal(err)
	}
	if _, err = store.Write(context.Background(), "link/sub/escape.json", nil); err == nil {
		t.Fatal("symlink accepted")
	}
	entries, _ := os.ReadDir(out)
	if len(entries) != 0 {
		t.Fatal("created outside root")
	}
}

func TestMalformedEnvelopeAndBodyLimit(t *testing.T) {
	for _, body := range []string{`{"choices": [],"choices":[]}`, `{"not_a_completion":true}`, `{"choices":[]} trailing`} {
		t.Run(body, func(t *testing.T) {
			r := ModelCall(context.Background(), testPacket(), testRoute("chat"), testOptions(func(req *http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: http.Header{}, Request: req}, nil
			}))
			if code(r) != "PROTOCOL_ERROR" {
				t.Fatal(r)
			}
			validResult(t, r)
		})
	}
	opts := testOptions(func(req *http.Request) (*http.Response, error) {
		return reply(req, 200, testResponse("chat", strings.Repeat("x", 100))), nil
	})
	opts.MaxResponseBytes = 20
	r := ModelCall(context.Background(), testPacket(), testRoute("chat"), opts)
	if r["status"] != "incomplete" || code(r) != "PROTOCOL_ERROR" {
		t.Fatal(r)
	}
}

func TestNoRedirects(t *testing.T) {
	count := 0
	opts := testOptions(func(req *http.Request) (*http.Response, error) {
		count++
		r := reply(req, 302, Packet{})
		r.Header.Set("Location", "https://other.invalid")
		return r, nil
	})
	opts.Client.CheckRedirect = func(*http.Request, []*http.Request) error {
		t.Fatal("caller redirect policy should be overridden only for this call")
		return nil
	}
	r := ModelCall(context.Background(), testPacket(), testRoute("chat"), opts)
	if count != 1 || r["status"] != "failed" {
		t.Fatal(r)
	}
	if opts.Client.CheckRedirect == nil {
		t.Fatal("mutated caller client")
	}
}

func TestIncompleteRefusedAndMultipleChoices(t *testing.T) {
	for _, p := range []string{"chat", "responses", "anthropic", "gemini"} {
		t.Run(p, func(t *testing.T) {
			for _, status := range []string{"incomplete", "refused"} {
				t.Run(status, func(t *testing.T) {
					data := testResponse(p, "partial or refusal")
					switch p {
					case "chat":
						reason := "length"
						if status == "refused" {
							reason = "content_filter"
						}
						object(array(data["choices"])[0])["finish_reason"] = reason
					case "responses":
						data["status"] = "incomplete"
						if status == "refused" {
							data["status"] = "completed"
							data["output"] = []any{Packet{"type": "message", "content": []any{Packet{"type": "refusal", "refusal": "Unable to comply"}}}}
						}
					case "anthropic":
						data["stop_reason"] = "max_tokens"
						if status == "refused" {
							data["stop_reason"] = "refusal"
						}
					case "gemini":
						reason := "MAX_TOKENS"
						if status == "refused" {
							reason = "SAFETY"
						}
						object(array(data["candidates"])[0])["finishReason"] = reason
					}
					r := ModelCall(context.Background(), testPacket(), testRoute(p), testOptions(func(req *http.Request) (*http.Response, error) { return reply(req, 200, data), nil }))
					if r["status"] != status {
						t.Fatal(r)
					}
					validResult(t, r)
				})
			}
		})
	}
	p := testPacket()
	p["provider_options"] = Packet{"n": 2}
	data := testResponse("chat", "first")
	data["choices"] = append(array(data["choices"]), Packet{"message": Packet{"content": "second"}, "finish_reason": "stop"})
	r := ModelCall(context.Background(), p, testRoute("chat"), testOptions(func(req *http.Request) (*http.Response, error) { return reply(req, 200, data), nil }))
	if r["content"] != "first" || len(array(object(r["provider_data"])["choices"])) != 2 {
		t.Fatal("lost alternatives")
	}
}

func TestRetryMetadataAndUnknownCodes(t *testing.T) {
	r := ClassifyError(ErrorInput{HTTPStatus: 429, Headers: http.Header{"Retry-After": {time.Now().Add(time.Minute).UTC().Format(http.TimeFormat)}}})
	seconds, ok := r["retry_after_seconds"].(float64)
	if !ok || seconds < 58 || seconds > 61 {
		t.Fatal(r)
	}
	r = ClassifyError(ErrorInput{Body: Packet{"error": Packet{"code": "future_error", "message": "unknown condition"}}})
	if r["code"] != "UNKNOWN" || r["provider_code"] != "future_error" || r["retryable"] != false {
		t.Fatal(r)
	}
}

func TestLocalCredentialsAndCallerClientReuse(t *testing.T) {
	count := 0
	opts := testOptions(func(req *http.Request) (*http.Response, error) {
		count++
		if req.Header.Get("Authorization") != "" {
			t.Fatal("unexpected local auth")
		}
		return reply(req, 200, testResponse("chat", "local")), nil
	})
	opts.Credentials = func(context.Context, string) (string, error) { return "", nil }
	route := testRoute("chat")
	route.Adapter = "openai_compatible_local"
	for i := 0; i < 2; i++ {
		r := ModelCall(context.Background(), testPacket(), route, opts)
		if r["status"] != "completed" {
			t.Fatal(r)
		}
	}
	if count != 2 {
		t.Fatal(count)
	}
}

func TestCancellationIsRecordedAndObserverCannotMutateResult(t *testing.T) {
	opts := testOptions(func(*http.Request) (*http.Response, error) { return nil, context.Canceled })
	opts.Debug = true
	opts.Recorder = &TraceRecorder{Store: StoreFunc(func(ctx context.Context, _ string, data []byte) (string, error) {
		if ctx.Err() != nil {
			t.Fatal("recording inherited cancelled context")
		}
		v, _ := decodeJSON(data)
		if object(v)["status"] != "cancelled" {
			t.Fatal(v)
		}
		return "memory://cancel", nil
	})}
	opts.Metrics = func(_ context.Context, p Packet) error { object(p["resolved_route"])["model"] = "changed"; return nil }
	r := ModelCall(context.Background(), testPacket(), testRoute("chat"), opts)
	if r["status"] != "cancelled" || object(r["resolved_route"])["model"] != "fixture" {
		t.Fatal(r)
	}
	if object(object(object(r["metadata"])["observability"])["trace"])["status"] != "recorded" {
		t.Fatal(r)
	}
}

func TestEmbeddedEnvelopeKeywordsAreSupported(t *testing.T) {
	allowed := map[string]bool{}
	for _, k := range strings.Fields("$schema title description type properties required additionalProperties $defs $ref const enum items minItems minLength minimum maximum allOf anyOf oneOf not if then else dependentRequired") {
		allowed[k] = true
	}
	var visit func(Packet)
	visit = func(s Packet) {
		for k, v := range s {
			if !allowed[k] {
				t.Fatalf("new envelope keyword %q needs Go support", k)
			}
			switch k {
			case "properties", "$defs":
				for _, sub := range object(v) {
					visit(object(sub))
				}
			case "items", "not", "if", "then", "else":
				visit(object(v))
			case "allOf", "anyOf", "oneOf":
				for _, sub := range array(v) {
					visit(object(sub))
				}
			}
		}
	}
	for _, s := range envelopeSchemas {
		visit(s)
	}
}

func TestEmbeddedSchemasMatchPython(t *testing.T) {
	for _, kind := range []string{"request", "response"} {
		want, err := os.ReadFile("../thinker-core/thinker/thinkerql/" + kind + "_v0_4.schema.json")
		if os.IsNotExist(err) {
			t.Skip("Python sources not installed with standalone Go module")
		}
		if err != nil {
			t.Fatal(err)
		}
		got, _ := PacketSchema(kind)
		if !bytes.Equal(got, want) {
			t.Fatal("run tools/sync_go_contract.py")
		}
	}
}

// TestPythonParity receives offline fixtures generated from the actual Python
// prepare/lift/classify implementations by tools/check_go_parity.py.
func TestPythonParity(t *testing.T) {
	path := os.Getenv("THINKER_GO_PARITY_FILE")
	if path == "" {
		t.Skip("run tools/check_go_parity.py for differential checks")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := decodeJSON(data)
	if err != nil {
		t.Fatal(err)
	}
	for _, v := range array(decoded) {
		f := object(v)
		t.Run(str(f["name"]), func(t *testing.T) {
			if f["kind"] == "error" {
				in := object(f["input"])
				status, _ := strconvAtoi(in["http_status"])
				got := ClassifyError(ErrorInput{Provider: str(in["provider"]), HTTPStatus: status, Body: in["body"], StreamStarted: in["stream_started"] == true})
				assertJSON(t, got, f["expected"])
				return
			}
			if f["kind"] == "packet" {
				_, err := ParseRequest(object(f["request"]))
				if (err == nil) != (f["valid"] == true) {
					t.Fatalf("envelope mismatch: %v", err)
				}
				return
			}
			var route Route
			b, _ := json.Marshal(f["route"])
			if err := json.Unmarshal(b, &route); err != nil {
				t.Fatal(err)
			}
			request, err := ParseRequest(object(f["request"]))
			if err != nil {
				t.Fatal(err)
			}
			p, url, body, err := prepare(request, route)
			if err != nil {
				t.Fatal(err)
			}
			if p != f["protocol"] || url != f["url"] {
				t.Fatal(p, url)
			}
			assertJSON(t, body, f["body"])
			got, err := lift(object(f["response"]), p)
			if err != nil {
				t.Fatal(err)
			}
			assertJSON(t, got, f["result"])
		})
	}
}
func strconvAtoi(v any) (int, error) { var n int; _, err := fmt.Sscan(textValue(v), &n); return n, err }
func assertJSON(t *testing.T, got, want any) {
	t.Helper()
	g, e := clone(got)
	if e != nil {
		t.Fatal(e)
	}
	w, e := clone(want)
	if e != nil {
		t.Fatal(e)
	}
	if !sameJSON(g, w) {
		a, _ := json.Marshal(g)
		b, _ := json.Marshal(w)
		t.Fatalf("got %s\nwant %s", a, b)
	}
}

func sameJSON(a, b any) bool {
	if n, ok := number(a); ok {
		other, ok := number(b)
		return ok && n.Cmp(other) == 0
	}
	if p := object(a); p != nil {
		q := object(b)
		if q == nil || len(p) != len(q) {
			return false
		}
		for k, v := range p {
			if !has(q, k) || !sameJSON(v, q[k]) {
				return false
			}
		}
		return true
	}
	if p := array(a); p != nil {
		q := array(b)
		if q == nil || len(p) != len(q) {
			return false
		}
		for i, v := range p {
			if !sameJSON(v, q[i]) {
				return false
			}
		}
		return true
	}
	return reflect.DeepEqual(a, b)
}
