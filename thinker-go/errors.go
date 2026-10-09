package thinker

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var vocabulary = func() Packet {
	b, _ := contracts.ReadFile("contract/errors.json")
	v, err := decodeJSON(b)
	if err != nil {
		panic("invalid embedded error vocabulary")
	}
	return object(v)
}()

// ErrorInput describes a failed provider exchange, never generated assistant text.
type ErrorInput struct {
	Err           error
	Provider      string
	HTTPStatus    int
	Body          any
	Headers       http.Header
	StreamStarted bool
}

func containsAny(s string, patterns ...string) bool {
	for _, p := range patterns {
		if strings.Contains(s, p) {
			return true
		}
	}
	return false
}
func textValue(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
func first(values ...any) any {
	for _, v := range values {
		if v != nil && v != "" {
			return v
		}
	}
	return nil
}
func retrySeconds(v any) any {
	if v == nil {
		return nil
	}
	if _, ok := v.(bool); ok {
		return nil
	}
	n, err := strconv.ParseFloat(textValue(v), 64)
	if err != nil {
		t, e := http.ParseTime(textValue(v))
		if e != nil {
			return nil
		}
		n = time.Until(t).Seconds()
	}
	if math.IsInf(n, 0) || math.IsNaN(n) {
		return nil
	}
	return math.Max(0, n)
}

func exceptionCategory(err error, partial bool) string {
	if err == nil {
		return ""
	}
	if errors.Is(err, context.Canceled) {
		return "CANCELLED"
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "TIMEOUT"
	}
	var cert *tls.CertificateVerificationError
	var authority x509.UnknownAuthorityError
	var invalid x509.CertificateInvalidError
	var hostname x509.HostnameError
	if errors.As(err, &cert) || errors.As(err, &authority) || errors.As(err, &invalid) || errors.As(err, &hostname) {
		return "TLS_ERROR"
	}
	var network net.Error
	if errors.As(err, &network) && network.Timeout() {
		return "TIMEOUT"
	}
	var syntax *json.SyntaxError
	if errors.As(err, &syntax) {
		return "PROTOCOL_ERROR"
	}
	var op *net.OpError
	var dns *net.DNSError
	if errors.As(err, &op) || errors.As(err, &dns) || errors.Is(err, syscall.EPIPE) || errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.ECONNREFUSED) || errors.Is(err, syscall.ECONNABORTED) || errors.Is(err, syscall.ENETUNREACH) || errors.Is(err, syscall.EHOSTUNREACH) || errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
		if partial {
			return "BROKEN_STREAM"
		}
		return "CONNECTION_ERROR"
	}
	return ""
}

// ClassifyError returns the shared Python/Go error vocabulary and retry hints.
// It does not perform retries, failover, output validation, or stateful recovery.
func ClassifyError(in ErrorInput) Packet {
	body := in.Body
	if s, ok := body.(string); ok {
		if v, err := decodeJSON([]byte(s)); err == nil {
			body = v
		} else {
			body = Packet{"message": s}
		}
	}
	data := object(body)
	detail := data
	if e, ok := data["error"]; ok {
		detail = object(e)
		if detail == nil {
			detail = Packet{"message": textValue(e)}
		}
	}
	nativeCode := first(detail["code"], detail["error_code"], detail["errorCode"])
	nativeType := first(detail["type"], detail["status"])
	code := strings.ToLower(textValue(first(nativeCode, nativeType)))
	status := in.HTTPStatus
	if status == 0 {
		for _, v := range []any{detail["status_code"], detail["http_status"], nativeCode, data["code"]} {
			n, e := strconv.Atoi(textValue(v))
			if e == nil && n >= 100 && n <= 599 {
				status = n
				break
			}
		}
	}
	message := textValue(first(detail["message"], data["message"], data["detail"]))
	if message == "" {
		if in.Err != nil {
			message = in.Err.Error()
		} else {
			message = "Provider request failed"
		}
	}
	lower := strings.ToLower(message)
	family := in.Provider
	switch family {
	case "google", "google-gemini", "vertex":
		family = "gemini"
	case "openai-codex":
		family = "openai"
	}
	retry := retrySeconds(in.Headers.Get("Retry-After"))
	if retry == nil {
		retry = retrySeconds(first(detail["retry_after"], data["retry_after"]))
	}
	if retry == nil {
		retry = retrySeconds(first(detail["resets_in_seconds"], data["resets_in_seconds"]))
	}
	category := exceptionCategory(in.Err, in.StreamStarted)
	if category == "" {
		for _, candidate := range []string{code, strings.ToLower(textValue(nativeType))} {
			category = str(first(object(vocabulary["native"])[candidate], object(vocabulary["hermes"])[candidate], object(object(vocabulary["providers"])[family])[candidate]))
			if category != "" {
				break
			}
		}
	}
	if category == "" {
		switch {
		case status == 400 && strings.Contains(lower, "long context beta") && strings.Contains(lower, "not yet available"):
			category = "OAUTH_LONG_CONTEXT_BETA_FORBIDDEN"
		case status == 429 && strings.Contains(lower, "extra usage") && strings.Contains(lower, "long context"):
			category = "LONG_CONTEXT_TIER"
		case status == 400 && strings.Contains(lower, "thinking") && containsAny(lower, "signature", "cannot be modified", "must remain as they were"):
			category = "THINKING_SIGNATURE_INVALID"
		}
	}
	if category == "" {
		for _, row := range array(vocabulary["messages"]) {
			pair := array(row)
			for _, phrase := range array(pair[1]) {
				if strings.Contains(lower, str(phrase)) {
					category = str(pair[0])
					break
				}
			}
			if category != "" {
				break
			}
		}
	}
	if category == "CONNECTION_ERROR" && in.StreamStarted {
		category = "BROKEN_STREAM"
	}
	if status == 403 && category == "" {
		category = "ACCESS_DENIED"
		if containsAny(lower, "cloudflare", "captcha", "waf", "access denied by proxy") {
			category = "UPSTREAM_BLOCKED"
		}
	}
	if status == 429 && (category == "" || category == "RATE_LIMITED") {
		if in.Provider == "openrouter" && object(detail["metadata"])["provider_name"] != nil {
			category = "UPSTREAM_RATE_LIMITED"
		} else if code == "usage_limit_reached" {
			category = "QUOTA_EXHAUSTED"
			if retry != nil || containsAny(lower, "reset", "try again", "rate limit") {
				category = "RATE_LIMITED"
			}
		}
	}
	if status == 402 && category == "" && retry != nil {
		category = "RATE_LIMITED"
	}
	if category == "" {
		category = map[int]string{400: "REQUEST_REJECTED", 401: "AUTHENTICATION_FAILED", 402: "QUOTA_EXHAUSTED", 404: "MODEL_UNAVAILABLE", 408: "TIMEOUT", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE", 422: "REQUEST_REJECTED", 429: "RATE_LIMITED", 503: "PROVIDER_OVERLOADED", 504: "TIMEOUT", 529: "PROVIDER_OVERLOADED"}[status]
	}
	if category == "" {
		category = "UNKNOWN"
		if status >= 500 && status < 600 {
			category = "PROVIDER_ERROR"
		} else if status >= 400 && status < 500 {
			category = "REQUEST_REJECTED"
		}
	}
	retryable := false
	switch category {
	case "RATE_LIMITED", "UPSTREAM_RATE_LIMITED", "PROVIDER_OVERLOADED", "PROVIDER_ERROR", "TIMEOUT", "CONNECTION_ERROR", "BROKEN_STREAM", "CONFLICT":
		retryable = true
	}
	var httpStatus, providerCode, providerType any
	if status >= 100 && status <= 599 {
		httpStatus = status
	}
	if nativeCode != nil {
		providerCode = textValue(nativeCode)
	}
	if nativeType != nil {
		providerType = textValue(nativeType)
	}
	return Packet{"code": category, "message": message, "provider_code": providerCode, "provider_type": providerType, "http_status": httpStatus, "retry_after_seconds": retry, "retryable": retryable, "partial_response": in.StreamStarted}
}
