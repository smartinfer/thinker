package thinker

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"sync/atomic"
	"time"
)

// Options are immutable during calls and may be reused across goroutines.
// Callbacks and supplied clients/stores must support concurrent use.
type Options struct {
	Client      *http.Client
	Credentials func(context.Context, string) (string, error)
	Recorder    *TraceRecorder
	Debug       bool
	// Metrics receives detached, redacted scalar observations. Nil disables it.
	Metrics func(context.Context, Packet) error
	// MaxResponseBytes defaults to 16 MiB. A larger body returns partial bytes
	// and PROTOCOL_ERROR, not a successful model answer.
	MaxResponseBytes int64
	// ObservationTimeout defaults to five seconds, separate from the model deadline.
	// Custom callbacks must honor their context; the library does not detach workers.
	ObservationTimeout time.Duration
}

var fallbackID atomic.Uint64

func invocationID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("%08x-%04x-4000-8000-%012x", uint32(time.Now().Unix()), uint16(time.Now().UnixNano()), fallbackID.Add(1))
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	s := hex.EncodeToString(b[:])
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:]
}
func digest(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
func packetError(code, message string) Packet {
	return Packet{"code": code, "message": message, "retryable": false}
}
func elapsed(start time.Time) float64 { return float64(time.Since(start)) / float64(time.Millisecond) }

var directClient = func() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	return &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}()

// EnvironmentCredentials resolves provider environment variables. Applications
// using a keychain can inject their own context-aware resolver.
func EnvironmentCredentials(ctx context.Context, provider string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	names := map[string][]string{
		"openai": {"OPENAI_API_KEY"}, "anthropic": {"ANTHROPIC_API_KEY"}, "gemini": {"GEMINI_API_KEY", "GOOGLE_API_KEY"}, "google": {"GEMINI_API_KEY", "GOOGLE_API_KEY"},
		"together": {"TOGETHER_API_KEY"}, "deepseek": {"DEEPSEEK_API_KEY"}, "moonshot": {"MOONSHOT_API_KEY"}, "dashscope": {"DASHSCOPE_API_KEY"}, "zhipu": {"ZHIPU_API_KEY"}, "groq": {"GROQ_API_KEY"}, "mistral": {"MISTRAL_API_KEY"}, "fireworks": {"FIREWORKS_API_KEY"}, "openrouter": {"OPENROUTER_API_KEY"}, "xai": {"XAI_API_KEY"}, "cohere": {"COHERE_API_KEY"}, "huggingface": {"HF_TOKEN"},
	}
	for _, name := range names[provider] {
		if value := os.Getenv(name); value != "" {
			return value, nil
		}
	}
	return "", nil
}

// ModelCall performs one buffered native HTTP exchange and returns a ThinkerQL
// 0.4 packet. Call it from caller-owned goroutines for concurrency. It never
// routes, retries, repairs output, validates generated schemas, or runs tools.
// Cancellation and routine failures are returned in the packet, not as Go errors.
func ModelCall(ctx context.Context, packet Packet, route Route, options Options) (result Packet) {
	start := time.Now()
	id := invocationID()
	requestID := str(packet["request_id"])
	if requestID == "" {
		requestID = id
	}
	result = Packet{"version": Version, "request_id": requestID, "status": "failed", "usage": nil, "cost_usd": nil}
	for _, k := range []string{"job_id", "turn_id"} {
		if s := str(packet[k]); s != "" {
			result[k] = s
		}
	}
	attempted := false
	var key string
	var nativeRequest, nativeResponse Packet
	if ctx == nil {
		ctx = context.Background()
	}
	defer func() {
		result["started_at"], result["completed_at"], result["duration_ms"] = start.UTC().Format(time.RFC3339Nano), time.Now().UTC().Format(time.RFC3339Nano), elapsed(start)
		invocations := []any{}
		if attempted {
			invocation := copyObject(result)
			for _, k := range []string{"version", "request_id", "job_id", "turn_id"} {
				delete(invocation, k)
			}
			invocation["call_id"] = id
			invocation["stage"] = "direct"
			invocations = append(invocations, invocation)
		}
		result["invocations"] = invocations
		observe(ctx, result, packet, nativeRequest, nativeResponse, id, key, options)
	}()
	request, err := ParseRequest(packet)
	if err != nil {
		result["error"] = packetError("REQUEST_REJECTED", err.Error())
		return
	}
	packet = request
	if route.CallID == "" || route.Provider == "" || route.ModelID == "" {
		result["error"] = packetError("REQUEST_REJECTED", "route requires call_id, provider and model_id")
		return
	}
	// Detach the only mutable route field before any provider callback.
	route.Aliases = append([]string{}, route.Aliases...)
	protocol, endpoint, body, err := prepare(packet, route)
	if err != nil {
		result["error"] = packetError("UNSUPPORTED_REQUEST", err.Error())
		return
	}
	result["resolved_route"] = Packet{"route_id": route.CallID, "provider": route.Provider, "model": route.ModelID, "settings": Packet{"payload_style": route.PayloadStyle, "endpoint": endpoint, "instructions": fallback(packet["instructions"], Packet{}), "provider_options": fallback(packet["provider_options"], Packet{}), "reasoning": object(packet["routing"])["reasoning"]}}
	timeout := 30 * time.Second
	if value, ok := packet["timeout_ms"]; ok {
		n, ok := number(value)
		if !ok || !n.IsInt() || !n.Num().IsInt64() || n.Num().Int64() > int64((1<<63-1)/time.Millisecond) {
			result["error"] = packetError("REQUEST_REJECTED", "timeout_ms exceeds supported duration")
			return
		}
		timeout = time.Duration(n.Num().Int64()) * time.Millisecond
	}
	callCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	credentials := options.Credentials
	if credentials == nil {
		credentials = EnvironmentCredentials
	}
	key, err = credentials(callCtx, route.Provider)
	if callCtx.Err() != nil {
		err = callCtx.Err()
	}
	if err != nil {
		result["error"] = ClassifyError(ErrorInput{Err: err, Provider: route.Provider})
		if errors.Is(err, context.Canceled) {
			result["status"] = "cancelled"
		}
		return
	}
	if key == "" && route.Adapter != "openai_compatible_local" {
		result["error"] = ClassifyError(ErrorInput{Provider: route.Provider, HTTPStatus: 401, Body: Packet{"message": "provider credential unavailable"}})
		return
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		result["error"] = packetError("REQUEST_REJECTED", "native request is not JSON representable")
		return
	}
	req, err := http.NewRequestWithContext(callCtx, http.MethodPost, endpoint, bytes.NewReader(encoded))
	if err != nil {
		result["error"] = packetError("REQUEST_REJECTED", "invalid provider endpoint")
		return
	}
	req.Header.Set("Content-Type", "application/json")
	switch protocol {
	case "anthropic":
		req.Header.Set("x-api-key", key)
		req.Header.Set("anthropic-version", "2023-06-01")
	case "gemini":
		req.Header.Set("x-goog-api-key", key)
	default:
		if key != "" {
			req.Header.Set("Authorization", "Bearer "+key)
		}
	}
	if options.Debug {
		nativeRequest = Packet{"protocol": protocol, "url": endpoint, "headers": headersPacket(req.Header), "body": body, "raw_body_base64": base64.StdEncoding.EncodeToString(encoded), "raw_sha256": digest(encoded)}
	}
	client := directClient
	if options.Client != nil {
		owned := *options.Client
		owned.CheckRedirect = directClient.CheckRedirect
		client = &owned
	}
	attempted = true
	resp, err := client.Do(req)
	if err != nil {
		result["error"] = ClassifyError(ErrorInput{Err: err, Provider: route.Provider})
		if errors.Is(err, context.Canceled) {
			result["status"] = "cancelled"
		}
		return
	}
	limit := options.MaxResponseBytes
	if limit <= 0 {
		limit = 16 << 20
	}
	if limit == int64(^uint64(0)>>1) {
		limit--
	}
	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	closeErr := resp.Body.Close()
	if readErr == nil {
		readErr = closeErr
	}
	if options.Debug {
		nativeResponse = Packet{"http_status": resp.StatusCode, "headers": headersPacket(resp.Header), "raw_body_base64": base64.StdEncoding.EncodeToString(raw), "raw_sha256": digest(raw)}
	}
	retainRaw := func() {
		result["provider_data"] = Packet{"response": result["provider_data"], "raw_body_base64": base64.StdEncoding.EncodeToString(raw)}
	}
	if int64(len(raw)) > limit {
		result["status"] = "incomplete"
		result["error"] = packetError("PROTOCOL_ERROR", "provider response exceeds MaxResponseBytes")
		retainRaw()
		return
	}
	if readErr != nil {
		if len(raw) > 0 {
			result["status"] = "incomplete"
		}
		if errors.Is(readErr, context.Canceled) {
			result["status"] = "cancelled"
		}
		result["error"] = ClassifyError(ErrorInput{Err: readErr, Provider: route.Provider, HTTPStatus: resp.StatusCode, Headers: resp.Header, StreamStarted: len(raw) > 0})
		retainRaw()
		return
	}
	data, decodeErr := decodeJSON(raw)
	if decodeErr != nil && resp.StatusCode >= 200 && resp.StatusCode < 300 {
		result["error"] = packetError("PROTOCOL_ERROR", "could not decode provider response envelope")
		retainRaw()
		return
	}
	if decodeErr != nil {
		data = Packet{"message": string(raw)}
	}
	result["provider_data"] = data
	native := object(data)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 || truthy(native["error"]) || native["status"] == "failed" {
		result["error"] = ClassifyError(ErrorInput{Provider: route.Provider, HTTPStatus: resp.StatusCode, Body: data, Headers: resp.Header})
		retainRaw()
		return
	}
	normalized, err := lift(native, protocol)
	if err == nil {
		combined := copyObject(result)
		for k, v := range normalized {
			combined[k] = v
		}
		_, err = ParseResponse(combined)
	}
	if err != nil {
		result["error"] = packetError("PROTOCOL_ERROR", "could not decode provider response envelope")
		retainRaw()
		return
	}
	for k, v := range normalized {
		result[k] = v
	}
	modelKey := "model"
	if protocol == "gemini" {
		modelKey = "modelVersion"
	}
	if model := str(native[modelKey]); model != "" {
		object(result["resolved_route"])["model"] = model
	}
	return
}

func headersPacket(headers http.Header) Packet {
	p := Packet{}
	for k := range headers {
		p[k] = headers.Get(k)
	}
	return p
}
